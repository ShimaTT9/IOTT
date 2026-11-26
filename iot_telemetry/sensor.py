#!/usr/bin/env python3
"""IoT sensor: a UDP client that sends one reading every 1/rate seconds.

QoS 0  send each reading once and never wait for a reply.
QoS 1  keep every reading "in flight" until the collector ACKs it. If no ACK
       arrives within --timeout seconds the reading is sent again, up to
       --retries times, then it is given up. Readings keep being produced on
       schedule while older ones wait for their ACK (several in flight at
       once, like MQTT QoS 1), so a slow ACK never delays new data.

Example (inside Mininet-WiFi, on sta1):
    python3 sensor.py --server 10.0.0.100 --id 1 --qos 1 --count 200
"""

import argparse
import csv
import json
import math
import os
import random
import select
import socket
import sys
import time

import protocol


class Pending(object):
    """A QoS 1 reading that is waiting for its ACK."""
    __slots__ = ("packet", "attempts", "deadline", "last_tx")

    def __init__(self, packet, now, timeout):
        self.packet = packet
        self.attempts = 1
        self.last_tx = now
        self.deadline = now + timeout


def parse_args():
    p = argparse.ArgumentParser(description="IoT telemetry sensor (UDP client)")
    p.add_argument("--server", default="10.0.0.100", help="collector IP address")
    p.add_argument("--port", type=int, default=5005, help="collector UDP port")
    p.add_argument("--id", type=int, default=1, help="sensor id (1-65535)")
    p.add_argument("--qos", type=int, choices=(0, 1), default=0)
    p.add_argument("--count", type=int, default=200, help="readings to send")
    p.add_argument("--duration", type=float, default=None,
                   help="send for this many seconds instead of --count")
    p.add_argument("--rate", type=float, default=10.0, help="readings per second")
    p.add_argument("--timeout", type=float, default=0.2, help="ACK timeout (s), QoS 1")
    p.add_argument("--retries", type=int, default=3, help="resends per reading, QoS 1")
    p.add_argument("--log", default=None, help="per-reading CSV log (optional)")
    p.add_argument("--summary", default=None, help="JSON summary file (optional)")
    p.add_argument("--quiet", action="store_true", help="only print the final summary")
    p.add_argument("--seed", type=int, default=None, help="random seed for readings")
    return p.parse_args()


def make_reading(sensor_id, seq, rng):
    """Simulated temperature / humidity with a slow drift and some noise."""
    t = seq / 50.0 + sensor_id
    temperature = 28.0 + 3.0 * math.sin(t) + rng.uniform(-0.3, 0.3)
    humidity = 65.0 + 10.0 * math.cos(t / 2.0) + rng.uniform(-1.0, 1.0)
    return temperature, humidity


def main():
    args = parse_args()
    if args.duration is not None:
        args.count = max(1, int(round(args.duration * args.rate)))
    interval = 1.0 / args.rate
    max_attempts = 1 + args.retries
    rng = random.Random(args.seed if args.seed is not None else args.id)
    target = (args.server, args.port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)

    stats = {"transmissions": 0, "retransmissions": 0, "acked": 0,
             "gave_up": 0, "late_acks": 0, "send_errors": 0}
    records = {}         # seq -> dict written to the CSV log
    inflight = {}        # seq -> Pending (QoS 1 only)
    rtts = []

    def transmit(packet):
        """Send one datagram. A failure counts as a (lost) transmission."""
        stats["transmissions"] += 1
        try:
            sock.sendto(packet, target)
        except OSError:
            # e.g. ENETUNREACH / ENOBUFS while the station is between APs
            stats["send_errors"] += 1

    if not args.quiet:
        print("[sensor %d] QoS %d -> %s:%d, %d readings at %.1f/s"
              % (args.id, args.qos, args.server, args.port, args.count, args.rate),
              flush=True)

    start = time.time()
    next_send = start
    next_report = start + 1.0
    seq = 0

    try:
        while seq < args.count or inflight:
            now = time.time()

            # 1) produce new readings on schedule
            while seq < args.count and now >= next_send:
                stamp = time.time()
                temperature, humidity = make_reading(args.id, seq, rng)
                packet = protocol.pack_data(args.qos, args.id, seq, stamp,
                                            temperature, humidity)
                transmit(packet)
                records[seq] = {"seq": seq, "send_time": "%.6f" % stamp,
                                "attempts": 1, "status": "sent",
                                "ack_time": "", "rtt_ms": ""}
                if args.qos == 1:
                    inflight[seq] = Pending(packet, stamp, args.timeout)
                seq += 1
                next_send += interval
                now = time.time()

            # 2) retransmit readings whose ACK timer expired
            for s in sorted(inflight):
                p = inflight[s]
                if now < p.deadline:
                    continue
                if p.attempts >= max_attempts:
                    del inflight[s]
                    stats["gave_up"] += 1
                    records[s]["status"] = "lost"
                    if not args.quiet:
                        print("[sensor %d] gave up on seq %d after %d attempts"
                              % (args.id, s, p.attempts), flush=True)
                    continue
                transmit(p.packet)
                stats["retransmissions"] += 1
                p.attempts += 1
                p.last_tx = now
                p.deadline = now + args.timeout
                records[s]["attempts"] = p.attempts

            if not args.quiet and now >= next_report:
                print_progress(args, seq, stats, inflight, now - start)
                next_report += 1.0

            if args.qos == 0 and seq >= args.count:
                break

            # 3) sleep until the next event, but wake up for any ACK
            wake = next_send if seq < args.count else now + 1.0
            if inflight:
                wake = min(wake, min(p.deadline for p in inflight.values()))
            wait = max(0.0, wake - time.time())
            readable, _, _ = select.select([sock], [], [], wait)
            if readable:
                drain_acks(sock, args, inflight, records, stats, rtts)
    except KeyboardInterrupt:
        print("[sensor %d] interrupted" % args.id, flush=True)
    finally:
        end = time.time()
        sock.close()

    for s in inflight:                      # interrupted before giving up
        records[s]["status"] = "lost"

    summary = {
        "sensor_id": args.id, "qos": args.qos, "server": args.server,
        "port": args.port, "rate": args.rate, "count": args.count,
        "timeout": args.timeout, "retries": args.retries,
        "sent": seq,
        "transmissions": stats["transmissions"],
        "retransmissions": stats["retransmissions"],
        "acked": stats["acked"], "gave_up": stats["gave_up"],
        "late_acks": stats["late_acks"], "send_errors": stats["send_errors"],
        "data_bytes": protocol.DATA_SIZE,
        "bytes_on_air": stats["transmissions"] * protocol.wire_bytes(protocol.DATA_SIZE),
        "avg_rtt_ms": round(1000.0 * sum(rtts) / len(rtts), 3) if rtts else None,
        "start_time": start, "end_time": end,
    }

    if args.log:
        ensure_dir(args.log)
        with open(args.log, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["seq", "send_time", "attempts",
                                                    "status", "ack_time", "rtt_ms"])
            writer.writeheader()
            for s in sorted(records):
                writer.writerow(records[s])
    if args.summary:
        ensure_dir(args.summary)
        with open(args.summary, "w") as fh:
            json.dump(summary, fh, indent=2)

    line = ("[sensor %d] done: sent %d readings, %d transmissions (%d retransmissions)"
            % (args.id, summary["sent"], summary["transmissions"],
               summary["retransmissions"]))
    if args.qos == 1:
        line += ", %d ACKed, %d given up" % (summary["acked"], summary["gave_up"])
    print(line, flush=True)


def drain_acks(sock, args, inflight, records, stats, rtts):
    """Read every ACK that is waiting in the socket buffer."""
    while True:
        try:
            datagram, _ = sock.recvfrom(2048)
        except (BlockingIOError, InterruptedError):
            return
        except (ConnectionResetError, ConnectionRefusedError):
            continue              # ICMP port unreachable: collector not up yet
        except OSError:
            return
        try:
            msg = protocol.unpack(datagram)
        except protocol.ProtocolError:
            continue
        if msg.type != protocol.MSG_ACK or msg.sensor_id != args.id:
            continue
        p = inflight.pop(msg.seq, None)
        if p is None:
            stats["late_acks"] += 1           # duplicate ACK or already given up
            continue
        now = time.time()
        stats["acked"] += 1
        rtt = now - p.last_tx
        rtts.append(rtt)
        rec = records[msg.seq]
        rec["status"] = "acked"
        rec["ack_time"] = "%.6f" % now
        rec["rtt_ms"] = "%.3f" % (rtt * 1000.0)


def print_progress(args, seq, stats, inflight, elapsed):
    line = "[sensor %d] t=%5.1fs sent=%d tx=%d" % (args.id, elapsed, seq,
                                                    stats["transmissions"])
    if args.qos == 1:
        line += " acked=%d in_flight=%d retx=%d gave_up=%d" % (
            stats["acked"], len(inflight), stats["retransmissions"], stats["gave_up"])
    print(line, flush=True)


def ensure_dir(path):
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        os.makedirs(folder)


if __name__ == "__main__":
    main()
