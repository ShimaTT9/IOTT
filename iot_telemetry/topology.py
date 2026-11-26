#!/usr/bin/env python3
"""Mininet-WiFi topology for the IoT telemetry experiment.

    sta1 (sensor) ~~~ ap1 --+
                            +-- s1 ===== h1 (collector, 10.0.0.100)
    sta2 (sensor) ~~~ ap2 --+     10 Mbit/s, 5 ms, queue 100

  ~~~  802.11g radio, logDistance propagation (exp 3.5), AP range 25 m
  ---  wired links (TCLink)

Scenarios
  stationary  both sensors stay 11 m from ap1
  mobile      both sensors drive from x = 10 m to x = 100 m in 40 s and must
              hand over from ap1 to ap2 around x = 55 m

Modes
  --cli   (default) build the network and open the mininet-wifi> prompt.
          Mobile sensors keep driving back and forth so you have time to work.
  --demo  live demo: sta1 sends with QoS 0, sta2 with QoS 1, the collector
          output is shown on screen, then the CLI opens.
  --auto  one benchmark run: both sensors use --qos, logs are saved to
          results/raw/<scenario>_qos<q>_run<n>/ and the network stops.

Examples (run from this folder)
  sudo python3 topology.py --scenario mobile --cli --plot
  sudo python3 topology.py --scenario mobile --demo --plot
  sudo python3 topology.py --scenario mobile --qos 1 --run 1 --auto
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from mininet.log import setLogLevel, info
from mininet.link import TCLink
from mininet.node import Node
import mininet.cli
import mn_wifi.link
from mn_wifi.net import Mininet_wifi
from mn_wifi.cli import CLI

import config

HERE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable or "python3"


# -------------------------------------------------------- compatibility ------
# Two workarounds for Mininet-WiFi 2.7. They change nothing where they are not
# needed, and make the lab work on Ubuntu 26.04 (kernel 7.0) and on slow VMs.
#
# 1. Mininet runs every command of a node through one shell, and that shell is
#    not thread-safe. Mininet-WiFi's handover thread uses the station shells
#    while the main thread (network start, CLI) may use them at the same time.
#    That ends in "AssertionError: assert self.shell and not self.waiting" and
#    a dead handover thread. A lock per node lets one command run at a time.
# 2. Mininet-WiFi sends "iw dev staX-wlan0 connect" while the interface is
#    still down ("Network is down (-100)" on kernel 7.0), ignores the error and
#    never retries, so the station stays unassociated. Bring it up first.
_NODE_LOCKS = {}
_NODE_LOCKS_GUARD = threading.Lock()


def node_lock(node):
    with _NODE_LOCKS_GUARD:
        lock = _NODE_LOCKS.get(id(node))
        if lock is None:
            lock = _NODE_LOCKS[id(node)] = threading.RLock()
    return lock


def _install_workarounds():
    original_cmd = Node.cmd

    def locked_cmd(self, *args, **kwargs):
        with node_lock(self):
            return original_cmd(self, *args, **kwargs)
    Node.cmd = locked_cmd

    original_default = mininet.cli.CLI.default

    def locked_default(self, line):          # "sta1 iw dev ..." typed in the CLI
        words = line.split()
        node = self.mn.nameToNode.get(words[0]) if words else None
        if node is None:
            return original_default(self, line)
        with node_lock(node):
            return original_default(self, line)
    mininet.cli.CLI.default = locked_default

    original_connect = mn_wifi.link.IntfWireless.iw_connect

    def connect_when_up(self, ap_intf):
        self.pexec("ip link set %s up" % self.name)
        return original_connect(self, ap_intf)
    mn_wifi.link.IntfWireless.iw_connect = connect_when_up


_install_workarounds()


def parse_args():
    p = argparse.ArgumentParser(description="IoT telemetry topology (Mininet-WiFi)")
    p.add_argument("--scenario", choices=("stationary", "mobile"), default="mobile")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--cli", action="store_true", help="interactive CLI (default)")
    mode.add_argument("--demo", action="store_true", help="live QoS 0 vs QoS 1 demo")
    mode.add_argument("--auto", action="store_true", help="one benchmark run, then exit")
    p.add_argument("--qos", type=int, choices=(0, 1), default=0, help="QoS for --auto")
    p.add_argument("--run", type=int, default=1, help="run number for --auto")
    p.add_argument("--duration", type=float, default=config.DURATION,
                   help="seconds the sensors send (--auto / --demo)")
    p.add_argument("--rate", type=float, default=config.RATE, help="readings per second")
    p.add_argument("--results", default=os.path.join(HERE, "results"))
    # link parameters (wired part)
    p.add_argument("--bw", type=float, default=config.LINK_BW, help="Mbit/s")
    p.add_argument("--delay", default=config.LINK_DELAY, help="delay of the h1 link, e.g. 5ms")
    p.add_argument("--loss", type=float, default=config.LINK_LOSS, help="loss %% on the h1 link")
    p.add_argument("--queue", type=int, default=config.QUEUE_SIZE, help="max_queue_size")
    # radio parameters
    p.add_argument("--exp", type=float, default=config.PROPAGATION_EXP,
                   help="logDistance path-loss exponent")
    p.add_argument("--ap-range", type=int, default=config.AP_RANGE,
                   help="AP range in metres (smaller = longer handover gap)")
    p.add_argument("--plot", action="store_true", help="show the node positions in a window")
    p.add_argument("--pcap", action="store_true", help="capture UDP 5005 on h1 with tcpdump")
    p.add_argument("--wmediumd", action="store_true",
                   help="use wmediumd interference mode (signal-based loss)")
    p.add_argument("--controller", action="store_true",
                   help="use the OpenFlow reference controller instead of standalone APs")
    args = p.parse_args()
    if not (args.cli or args.demo or args.auto):
        args.cli = True
    return args


# ------------------------------------------------------------- network ------
def build_network(args):
    if args.wmediumd:
        from mn_wifi.link import wmediumd
        from mn_wifi.wmediumdConnector import interference
        net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)
    else:
        net = Mininet_wifi()

    info("*** Creating nodes\n")
    h1 = net.addHost("h1", ip="%s/8" % config.COLLECTOR_IP, mac="00:00:00:00:01:00")

    stations = []
    for name, (ip, sid, lane_y) in sorted(config.STATIONS.items()):
        params = {"ip": "%s/8" % ip, "mac": "00:00:00:00:00:%02x" % sid}
        # mobile sensors wait at the start of their path until they move
        x = config.STATIONARY_X if args.scenario == "stationary" else config.PATH_START_X
        params["position"] = "%d,%d,0" % (x, lane_y)
        stations.append(net.addStation(name, **params))

    ap_opts = {} if args.controller else {"failMode": "standalone"}
    aps = []
    for name, (x, y, channel) in sorted(config.ACCESS_POINTS.items()):
        aps.append(net.addAccessPoint(name, ssid="iot-%s" % name, mode="g",
                                      channel=str(channel), position="%d,%d,0" % (x, y),
                                      range=args.ap_range, **ap_opts))
    s1 = net.addSwitch("s1", **ap_opts)
    controllers = []
    if args.controller:
        controllers.append(net.addController("c0"))

    info("*** Propagation model: %s, exp=%.1f\n" % (config.PROPAGATION_MODEL, args.exp))
    net.setPropagationModel(model=config.PROPAGATION_MODEL, exp=args.exp)

    info("*** Configuring WiFi nodes\n")
    if hasattr(net, "configureNodes"):
        net.configureNodes()
    else:                                   # Mininet-WiFi before 2.6
        net.configureWifiNodes()

    info("*** Creating wired links (bw=%s Mbit/s, delay=%s, loss=%s%%, queue=%d)\n"
         % (args.bw, args.delay, args.loss, args.queue))
    for ap in aps:
        net.addLink(ap, s1, cls=TCLink, bw=args.bw, max_queue_size=args.queue)
    h1_link = {"bw": args.bw, "delay": args.delay, "max_queue_size": args.queue}
    if args.loss > 0:
        h1_link["loss"] = args.loss
    net.addLink(h1, s1, cls=TCLink, **h1_link)

    if args.plot:
        net.plotGraph(max_x=120, max_y=100)

    if args.scenario == "mobile":
        # --auto drives the path once; --cli/--demo keep driving back and forth
        reps = 1 if args.auto else 20
        net.startMobility(time=0, mob_rep=reps, reverse=reps > 1)
        for sta in stations:
            lane_y = config.STATIONS[sta.name][2]
            net.mobility(sta, "start", time=config.MOVE_START,
                         position="%d,%d,0" % (config.PATH_START_X, lane_y))
            net.mobility(sta, "stop", time=config.MOVE_START + config.MOVE_TIME,
                         position="%d,%d,0" % (config.PATH_END_X, lane_y))
        net.stopMobility(time=config.MOVE_START + config.MOVE_TIME + 1)

    info("*** Starting network\n")
    net.build()
    for c in controllers:
        c.start()
    for ap in aps:
        ap.start(controllers)
    s1.start(controllers)
    return net, h1, stations


def wait_associated(stations, timeout=30):
    """Wait until every station is associated with an AP.

    Uses pexec (a separate process) and never the node's shell, because the
    Mininet-WiFi mobility thread uses that shell during handovers."""
    deadline = time.time() + timeout
    pending = {sta.name: sta for sta in stations}
    while pending and time.time() < deadline:
        for name, sta in list(pending.items()):
            out = sta.pexec("iw dev %s-wlan0 link" % name)[0]
            if "Connected to" in out:
                bssid = re.search(r"Connected to ([0-9a-f:]+)", out)
                ssid = re.search(r"SSID: (\S+)", out)
                info("*** %s associated with %s (%s)\n" % (name, ssid.group(1) if ssid else "?",
                                                         bssid.group(1) if bssid else "?"))
                del pending[name]
        if pending:
            time.sleep(0.5)
    return sorted(pending)


def ping_rtt(sta):
    out = sta.pexec("ping -c 3 -i 0.2 -W 1 %s" % config.COLLECTOR_IP)[0]
    m = re.search(r"= [\d.]+/([\d.]+)/", out)
    return float(m.group(1)) if m else None


# ------------------------------------------------------------ workload -------
def run_workload(net, h1, stations, args, qos_of, run_dir, live):
    """Start the collector on h1 and one sensor per station, wait, stop."""
    if os.path.isdir(run_dir):
        shutil.rmtree(run_dir)
    os.makedirs(run_dir)

    missing = wait_associated(stations)
    if missing:
        info("*** WARNING: not associated after 30 s: %s\n" % ", ".join(missing))
    rtts = {sta.name: ping_rtt(sta) for sta in stations}
    info("*** ping RTT to the collector: %s\n" % rtts)

    tcpdump = None
    if args.pcap:
        tcpdump = h1.popen(["tcpdump", "-i", "h1-eth0", "-Z", "root", "-w",
                            os.path.join(run_dir, "capture.pcap"), "udp", "port",
                            str(config.COLLECTOR_PORT)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    collector = h1.popen([PYTHON, "-u", os.path.join(HERE, "collector.py"),
                          "--port", str(config.COLLECTOR_PORT),
                          "--log", os.path.join(run_dir, "collector.csv")]
                         + ([] if live else ["--quiet"]),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    collector_out = []
    pump = threading.Thread(target=pump_output, args=(collector, live, collector_out))
    pump.daemon = True
    pump.start()
    time.sleep(1.0)

    start = time.time()
    sensors, files = [], []
    for sta in stations:
        ip, sid, _ = config.STATIONS[sta.name]
        fh = open(os.path.join(run_dir, "sensor%d.out" % sid), "w")
        files.append(fh)
        sensors.append(sta.popen(
            [PYTHON, "-u", os.path.join(HERE, "sensor.py"),
             "--server", config.COLLECTOR_IP, "--port", str(config.COLLECTOR_PORT),
             "--id", str(sid), "--qos", str(qos_of[sta.name]),
             "--duration", str(args.duration), "--rate", str(args.rate),
             "--timeout", str(config.ACK_TIMEOUT), "--retries", str(config.MAX_RETRIES),
             "--log", os.path.join(run_dir, "sensor%d.csv" % sid),
             "--summary", os.path.join(run_dir, "sensor%d.json" % sid), "--quiet"],
            stdout=fh, stderr=subprocess.STDOUT))
        info("*** %s: sensor %d started with QoS %d\n" % (sta.name, sid, qos_of[sta.name]))

    for p in sensors:
        try:
            p.wait(timeout=args.duration + 30)
        except subprocess.TimeoutExpired:
            p.kill()
    for fh in files:
        fh.close()
    time.sleep(1.0)                       # let the last ACKs and packets land
    collector.terminate()
    try:
        collector.wait(timeout=5)
    except subprocess.TimeoutExpired:
        collector.kill()
    pump.join(2)
    if tcpdump is not None:
        tcpdump.terminate()
        tcpdump.wait()
    with open(os.path.join(run_dir, "collector.out"), "w") as fh:
        fh.write("".join(collector_out))

    meta = {
        "scenario": args.scenario, "qos": qos_of[stations[0].name], "run": args.run,
        "qos_per_station": qos_of, "duration": args.duration, "rate": args.rate,
        "start_time": start, "ping_rtt_ms": rtts, "not_associated": missing,
        "source": "topology.py (Mininet-WiFi)",
        "params": {"bw_mbit": args.bw, "delay": args.delay, "loss_pct": args.loss,
                   "queue": args.queue, "propagation": config.PROPAGATION_MODEL,
                   "exp": args.exp, "ap_range_m": args.ap_range,
                   "access_points": config.ACCESS_POINTS,
                   "path_x": [config.PATH_START_X, config.PATH_END_X],
                   "move_time_s": config.MOVE_TIME, "ack_timeout_s": config.ACK_TIMEOUT,
                   "max_retries": config.MAX_RETRIES, "wmediumd": args.wmediumd},
    }
    with open(os.path.join(run_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print_run_summary(run_dir)


def pump_output(proc, live, keep):
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode(errors="replace")
        keep.append(line)
        if live:
            sys.stdout.write(line)
            sys.stdout.flush()


def print_run_summary(run_dir):
    try:
        import analyze
        _, sensors = analyze.load_run(run_dir)
    except Exception as exc:                # never lose a run because of a report
        info("*** could not summarise the run: %s\n" % exc)
        return
    print("\n  sensor  QoS   sent  delivered    PDR   longest gap   retx  duplicates")
    for s in sorted(sensors, key=lambda s: s["sensor_id"]):
        pdr = 100.0 * s["delivered"] / s["sent"] if s["sent"] else 0.0
        print("  %6d  %3d  %5d  %9d  %5.1f%%  %9.0f ms  %5d  %10d" % (
            s["sensor_id"], s["qos"], s["sent"], s["delivered"], pdr,
            s["longest_gap_ms"], s["retransmissions"], s["duplicates"]))
    print("  logs: %s\n" % run_dir)


def give_files_to_user(path):
    """Results are written as root (sudo); hand them back to the real user."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if not uid or not os.path.exists(path):
        return
    uid, gid = int(uid), int(gid)
    for root, dirs, files in os.walk(path):
        os.chown(root, uid, gid)
        for name in files:
            os.chown(os.path.join(root, name), uid, gid)


def cli_help(args):
    print("""
------------------------------------------------------------------------------
 Network is up (%s scenario). Useful commands at the mininet-wifi> prompt:
   nodes                                 list the nodes
   net                                   show the links
   sta1 iw dev sta1-wlan0 link           which AP is sta1 connected to?
   sta1 ping -c 3 10.0.0.100             reach the collector
   xterm h1 sta1 sta2                    open one terminal per node, then run:
       h1:   python3 collector.py --log results/demo/collector.csv
       sta1: python3 sensor.py --id 1 --qos 0 --count 400
       sta2: python3 sensor.py --id 2 --qos 1 --count 400
   exit                                  stop the network
 Mobile sensors drive between x=10 m and x=100 m (40 s each way).
 Run long programs inside xterm windows, not directly at this prompt.
------------------------------------------------------------------------------
""" % args.scenario)


def main():
    args = parse_args()
    if os.geteuid() != 0:
        sys.exit("Mininet-WiFi needs root: run this script with sudo.")
    setLogLevel("info")
    os.chdir(HERE)                          # so relative paths in the CLI work

    net, h1, stations = build_network(args)
    try:
        if args.auto:
            run_dir = os.path.join(args.results, "raw", "%s_qos%d_run%d"
                                   % (args.scenario, args.qos, args.run))
            run_workload(net, h1, stations, args,
                         {sta.name: args.qos for sta in stations}, run_dir, live=False)
        elif args.demo:
            run_dir = os.path.join(args.results, "demo",
                                   time.strftime("%Y%m%d-%H%M%S"))
            qos_of = {sta.name: (0 if i == 0 else 1) for i, sta in enumerate(stations)}
            print("\n*** DEMO: sta1 sends with QoS 0, sta2 with QoS 1, %.0f s\n"
                  % args.duration)
            run_workload(net, h1, stations, args, qos_of, run_dir, live=True)
            cli_help(args)
            CLI(net)
        else:
            cli_help(args)
            CLI(net)
    finally:
        info("*** Stopping network\n")
        net.stop()
        give_files_to_user(args.results)


if __name__ == "__main__":
    main()
