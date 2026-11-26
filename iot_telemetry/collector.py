#!/usr/bin/env python3
"""Telemetry collector: a UDP server that receives sensor readings.

* Replies with an ACK to every QoS 1 DATA message (also to duplicates,
  because the earlier ACK may have been lost).
* Detects duplicates by (sensor_id, seq, timestamp), so a retransmission is
  counted once even if it arrives several times.
* Logs every received datagram to CSV (one row per packet, flushed at once,
  so the log is complete even if the process is killed).
* Prints a status line every few seconds and warns when a sensor goes quiet,
  which is what a handover looks like from the server side.

Example (inside Mininet-WiFi, on h1):
    python3 collector.py --log results/demo/collector.csv
Stop it with Ctrl+C.
"""

import argparse
import csv
import os
import signal
import socket
import sys
import time

import protocol

CSV_FIELDS = ["recv_time", "sensor_id", "seq", "qos", "send_time",
              "latency_ms", "duplicate", "ack_sent", "bytes", "src_ip",
              "temperature", "humidity"]


class SensorStats(object):
    def __init__(self):
        self.packets = 0
        self.unique = 0
        self.duplicates = 0
        self.acks = 0
        self.last_rx = None
        self.longest_gap = 0.0


def parse_args():
    p = argparse.ArgumentParser(description="IoT telemetry collector (UDP server)")
    p.add_argument("--bind", default="0.0.0.0", help="address to listen on")
    p.add_argument("--port", type=int, default=5005, help="UDP port (default 5005)")
    p.add_argument("--log", default="collector.csv", help="CSV log file")
    p.add_argument("--report", type=float, default=2.0,
                   help="seconds between status lines (0 = off)")
    p.add_argument("--gap-warn", type=float, default=0.3,
                   help="warn when a sensor is silent longer than this (s)")
    p.add_argument("--quiet", action="store_true", help="only print the final summary")
    return p.parse_args()


def main():
    args = parse_args()
    stopping = {"flag": False}

    def request_stop(signum, frame):
        stopping["flag"] = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    log_dir = os.path.dirname(os.path.abspath(args.log))
    if not os.path.isdir(log_dir):
        os.makedirs(log_dir)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.5)          # wake up regularly to check the stop flag

    print("[collector] listening on UDP %s:%d, logging to %s"
          % (args.bind, args.port, args.log), flush=True)

    seen = set()
    stats = {}
    bad = 0
    start = time.time()
    next_report = start + args.report if args.report > 0 else None

    with open(args.log, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_FIELDS)
        fh.flush()

        while not stopping["flag"]:
            now = time.time()
            if next_report is not None and now >= next_report and not args.quiet:
                print_status(now - start, stats)
                next_report = now + args.report

            try:
                datagram, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except (ConnectionResetError, ConnectionRefusedError):
                # Windows reports ICMP "port unreachable" from an earlier ACK
                # to a sensor that has already exited. Not an error for us.
                continue
            except OSError:
                if stopping["flag"]:
                    break
                raise

            rx_time = time.time()
            try:
                msg = protocol.unpack(datagram)
            except protocol.ProtocolError as exc:
                bad += 1
                if not args.quiet:
                    print("[collector] dropped malformed datagram from %s: %s"
                          % (addr[0], exc), flush=True)
                continue
            if msg.type != protocol.MSG_DATA:
                continue

            st = stats.setdefault(msg.sensor_id, SensorStats())
            if st.last_rx is not None:
                gap = rx_time - st.last_rx
                if gap > st.longest_gap:
                    st.longest_gap = gap
                if gap >= args.gap_warn and not args.quiet:
                    print("[collector] sensor %d was silent for %.2f s "
                          "(handover or loss), back at seq %d"
                          % (msg.sensor_id, gap, msg.seq), flush=True)
            st.last_rx = rx_time
            st.packets += 1

            key = (msg.sensor_id, msg.seq, msg.timestamp)
            duplicate = key in seen
            if duplicate:
                st.duplicates += 1
            else:
                seen.add(key)
                st.unique += 1

            ack_sent = 0
            if msg.qos == 1:
                try:
                    sock.sendto(protocol.pack_ack(msg.sensor_id, msg.seq,
                                                  msg.timestamp), addr)
                    ack_sent = 1
                    st.acks += 1
                except OSError:
                    pass          # no route right now (e.g. mid-handover)

            writer.writerow([
                "%.6f" % rx_time, msg.sensor_id, msg.seq, msg.qos,
                "%.6f" % msg.timestamp,
                "%.3f" % ((rx_time - msg.timestamp) * 1000.0),
                int(duplicate), ack_sent, len(datagram), addr[0],
                "%.2f" % msg.temperature, "%.2f" % msg.humidity])
            fh.flush()

    sock.close()
    print("[collector] stopped after %.1f s, %d malformed datagrams"
          % (time.time() - start, bad), flush=True)
    print_status(time.time() - start, stats, final=True)


def print_status(elapsed, stats, final=False):
    if not stats:
        print("[collector] t=%5.1fs  waiting for data ..." % elapsed, flush=True)
        return
    parts = []
    for sid in sorted(stats):
        st = stats[sid]
        text = "sensor %d: %d unique, %d dup" % (sid, st.unique, st.duplicates)
        if final:
            text += ", %d ACKs, longest gap %.0f ms" % (st.acks, st.longest_gap * 1000)
        parts.append(text)
    prefix = "[collector] final" if final else "[collector] t=%5.1fs" % elapsed
    print(prefix + "  " + " | ".join(parts), flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
