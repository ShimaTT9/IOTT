#!/usr/bin/env python3
"""Test the sensor/collector programs without Mininet.

Runs on any machine with Python 3 (Linux, macOS or Windows). Everything
talks over 127.0.0.1. Between the sensors and the collector sits a small
UDP relay that imitates the network:

    sensor.py --> relay (delay, random loss, handover outage) --> collector.py
              <--                     ACKs                    <--

  stationary  5 ms delay, 0.2 % random loss
  mobile      same, plus weak signal (5 % loss) for 2 s around a handover
              outage of --gap seconds in the middle of the run

The script first checks the message format, then runs the four benchmark
cases (stationary/mobile x QoS 0/1), writes the logs in the same layout as
the Mininet runs, calls analyze.py and checks the results make sense.

Usage:
    python3 local_test.py                  # 4 cases x 1 run x 20 s (about 90 s)
    python3 local_test.py --duration 10    # quicker
"""

import argparse
import heapq
import json
import os
import random
import select
import socket
import subprocess
import sys
import threading
import time

import config
import protocol

HERE = os.path.dirname(os.path.abspath(__file__))
COLLECTOR_PORT = 5055      # different from 5005 so a real collector can keep running
RELAY_PORT = 6066


# ------------------------------------------------------------ the relay -----
class ImpairmentRelay(threading.Thread):
    """UDP relay that delays, drops and black-holes datagrams in both directions."""

    def __init__(self, listen_port, target, delay_ms, jitter_ms, loss,
                 outage=None, edge_loss=0.0, edge_time=0.0, seed=1):
        threading.Thread.__init__(self, daemon=True)
        self.front = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.front.bind(("127.0.0.1", listen_port))
        self.target = target
        self.delay = delay_ms / 1000.0
        self.jitter = jitter_ms / 1000.0
        self.loss = loss
        self.outage = outage            # (start, end) in seconds after start()
        self.edge_loss = edge_loss
        self.edge_time = edge_time
        self.rng = random.Random(seed)
        self.upstream = {}              # client address -> socket towards collector
        self.client_of = {}             # upstream socket -> client address
        self.queue = []
        self.counter = 0
        self.dropped = 0
        self.forwarded = 0
        self.stop_flag = threading.Event()
        self.t0 = None

    def drop(self, now):
        t = now - self.t0
        if self.outage:
            start, end = self.outage
            if start <= t < end:
                return True
            if start - self.edge_time <= t < end + self.edge_time:
                return self.rng.random() < self.edge_loss
        return self.rng.random() < self.loss

    def schedule(self, sock, data, dest, now):
        if self.drop(now):
            self.dropped += 1
            return
        when = now + self.delay + self.rng.uniform(0, self.jitter)
        self.counter += 1
        heapq.heappush(self.queue, (when, self.counter, sock, data, dest))

    def run(self):
        self.t0 = time.time()
        while not self.stop_flag.is_set():
            now = time.time()
            while self.queue and self.queue[0][0] <= now:
                _, _, sock, data, dest = heapq.heappop(self.queue)
                try:
                    sock.sendto(data, dest)
                    self.forwarded += 1
                except OSError:
                    pass
            timeout = 0.05
            if self.queue:
                timeout = max(0.0, min(timeout, self.queue[0][0] - time.time()))
            socks = [self.front] + list(self.client_of)
            readable, _, _ = select.select(socks, [], [], timeout)
            for s in readable:
                try:
                    data, addr = s.recvfrom(2048)
                except (ConnectionResetError, ConnectionRefusedError, BlockingIOError):
                    continue
                now = time.time()
                if s is self.front:
                    up = self.upstream.get(addr)
                    if up is None:
                        up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        up.bind(("127.0.0.1", 0))
                        self.upstream[addr] = up
                        self.client_of[up] = addr
                    self.schedule(up, data, self.target, now)
                else:
                    self.schedule(self.front, data, self.client_of[s], now)
        for s in [self.front] + list(self.client_of):
            s.close()

    def stop(self):
        self.stop_flag.set()
        self.join(2)


# ------------------------------------------------------- protocol checks ----
def check_protocol():
    data = protocol.pack_data(1, 7, 42, 1234.5, 25.5, 60.25)
    assert len(data) == 48, "DATA must be 48 bytes"
    m = protocol.unpack(data)
    assert (m.type, m.qos, m.sensor_id, m.seq, m.timestamp) == (0, 1, 7, 42, 1234.5)
    assert abs(m.temperature - 25.5) < 1e-6 and abs(m.humidity - 60.25) < 1e-6
    ack = protocol.pack_ack(7, 42, 1234.5)
    assert len(ack) == 16, "ACK must be 16 bytes"
    a = protocol.unpack(ack)
    assert (a.type, a.sensor_id, a.seq, a.timestamp) == (1, 7, 42, 1234.5)
    for bad in (b"", b"x" * 10, data[:-1], b"\x05" + data[1:]):
        try:
            protocol.unpack(bad)
        except protocol.ProtocolError:
            continue
        raise AssertionError("malformed datagram was accepted")
    print("[ok] protocol: DATA = 48 bytes, ACK = 16 bytes, malformed input rejected")


# ------------------------------------------------------------- one case -----
def run_case(scenario, qos, run, duration, gap, out_root):
    name = "%s_qos%d_run%d" % (scenario, qos, run)
    run_dir = os.path.join(out_root, "raw", name)
    os.makedirs(run_dir, exist_ok=True)

    outage = None
    if scenario == "mobile":
        start = duration / 2.0
        outage = (start, start + gap)
    relay = ImpairmentRelay(RELAY_PORT, ("127.0.0.1", COLLECTOR_PORT),
                            delay_ms=5, jitter_ms=2, loss=0.002, outage=outage,
                            edge_loss=0.05, edge_time=1.0,
                            seed=100 * run + 10 * qos + len(scenario))

    py = sys.executable
    collector = subprocess.Popen(
        [py, "-u", os.path.join(HERE, "collector.py"), "--bind", "127.0.0.1",
         "--port", str(COLLECTOR_PORT), "--log", os.path.join(run_dir, "collector.csv"),
         "--quiet"], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    time.sleep(0.8)
    relay.start()

    sensors = []
    for sta, (ip, sid, lane) in sorted(config.STATIONS.items()):
        sensors.append(subprocess.Popen(
            [py, "-u", os.path.join(HERE, "sensor.py"), "--server", "127.0.0.1",
             "--port", str(RELAY_PORT), "--id", str(sid), "--qos", str(qos),
             "--duration", str(duration), "--rate", str(config.RATE),
             "--timeout", str(config.ACK_TIMEOUT), "--retries", str(config.MAX_RETRIES),
             "--log", os.path.join(run_dir, "sensor%d.csv" % sid),
             "--summary", os.path.join(run_dir, "sensor%d.json" % sid), "--quiet"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
    outputs = [p.communicate(timeout=duration + 30)[0].decode().strip() for p in sensors]
    time.sleep(0.5)
    relay.stop()
    collector.terminate()
    collector.wait(5)

    meta = {"scenario": scenario, "qos": qos, "run": run, "duration": duration,
            "source": "local_test.py (relay emulation, not Mininet)",
            "params": {"delay_ms": 5, "loss": 0.002, "outage_s": outage,
                       "rate": config.RATE, "timeout": config.ACK_TIMEOUT,
                       "retries": config.MAX_RETRIES}}
    with open(os.path.join(run_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print("  %-22s relay dropped %4d, forwarded %5d | %s"
          % (name, relay.dropped, relay.forwarded, " / ".join(o.splitlines()[-1] for o in outputs)))
    for p in sensors:
        assert p.returncode == 0, "sensor exited with code %s" % p.returncode


# -------------------------------------------------------------- checks ------
def check_results(out_root):
    with open(os.path.join(out_root, "summary.json")) as fh:
        cases = json.load(fh)["cases"]
    ok = True

    def expect(cond, text):
        print("[%s] %s" % ("ok" if cond else "FAIL", text))
        return bool(cond)

    m0, m1 = cases["mobile_qos0"], cases["mobile_qos1"]
    s0, s1 = cases["stationary_qos0"], cases["stationary_qos1"]
    ok &= expect(m0["pdr_pct"] < s0["pdr_pct"],
                 "moving lowers QoS 0 PDR (%.1f%% -> %.1f%%)" % (s0["pdr_pct"], m0["pdr_pct"]))
    ok &= expect(m1["pdr_pct"] > m0["pdr_pct"],
                 "QoS 1 recovers losses while moving (%.1f%% vs %.1f%%)"
                 % (m1["pdr_pct"], m0["pdr_pct"]))
    ok &= expect(s1["pdr_pct"] > 99.5, "QoS 1 stationary PDR > 99.5%% (%.2f%%)" % s1["pdr_pct"])
    ok &= expect(m1["overhead_x"] > m0["overhead_x"],
                 "QoS 1 costs more bytes (%.2fx vs %.2fx)" % (m1["overhead_x"], m0["overhead_x"]))
    ok &= expect(m0["longest_gap_ms"] > 2 * s0["longest_gap_ms"],
                 "handover gap is visible (%.0f ms mobile vs %.0f ms stationary)"
                 % (m0["longest_gap_ms"], s0["longest_gap_ms"]))
    ok &= expect(m1["retransmissions"] > 0, "QoS 1 retransmits (%d per run)" % m1["retransmissions"])
    ok &= expect(all(c["delivered"] <= c["sent"] for c in cases.values()),
                 "duplicates never counted as delivered")
    ok &= expect(m1["latency_ms"] > m0["latency_ms"] or m1["latency_p95_ms"] > m0["latency_p95_ms"],
                 "retransmitted readings arrive later (mean %.1f vs %.1f ms)"
                 % (m1["latency_ms"], m0["latency_ms"]))
    for fig in ("fig_pdr.png", "fig_gap.png", "fig_overhead.png", "fig_latency_timeline.png"):
        ok &= expect(os.path.exists(os.path.join(out_root, fig)), "chart %s written" % fig)
    return ok


def main():
    p = argparse.ArgumentParser(description="Local test without Mininet")
    p.add_argument("--duration", type=float, default=20, help="seconds per run")
    p.add_argument("--runs", type=int, default=1, help="runs per case")
    p.add_argument("--gap", type=float, default=1.2, help="handover outage (s)")
    p.add_argument("--out", default="results_local", help="output folder")
    args = p.parse_args()

    check_protocol()
    print("Running 4 cases x %d run(s) x %.0f s through the relay ..." % (args.runs, args.duration))
    for run in range(1, args.runs + 1):
        for scenario in ("stationary", "mobile"):
            for qos in (0, 1):
                run_case(scenario, qos, run, args.duration, args.gap, args.out)

    print("\nAnalysing ...")
    subprocess.check_call([sys.executable, os.path.join(HERE, "analyze.py"),
                           "--results", args.out])
    print()
    if check_results(args.out):
        print("\nALL CHECKS PASSED - the programs work. Next: run them in Mininet-WiFi.")
    else:
        print("\nSOME CHECKS FAILED - see the lines marked FAIL above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
