#!/usr/bin/env python3
"""Turn the raw benchmark logs into metrics, tables and charts.

Usage:
    python3 analyze.py                    # reads results/raw, writes into results/
    python3 analyze.py --results results_local

Input  (one folder per run, written by topology.py --auto or local_test.py):
    <results>/raw/<scenario>_qos<q>_run<n>/
        meta.json, collector.csv, sensor<id>.json, sensor<id>.csv

Output (in <results>/):
    runs.csv       one row per run
    summary.csv    one row per case (mean and standard deviation over runs)
    summary.json   the same numbers, read by build_slides.py
    timeline.json  latency-over-time data of one mobile run
    fig_pdr.png, fig_gap.png, fig_latency_timeline.png, fig_overhead.png

Metrics
    PDR          unique readings delivered / readings sent
    longest gap  longest silence between two packets of one sensor at the
                 collector (the handover outage when the sensor moves)
    latency      receive time - timestamp in the reading (one way, includes
                 the waiting time of retransmissions)
    jitter       mean absolute difference between consecutive latencies
    overhead     bytes on the IP network (DATA + ACK, with UDP/IP headers)
                 / payload bytes delivered
"""

import argparse
import csv
import glob
import json
import os
import statistics
import sys
import time

import protocol

DATA_WIRE = protocol.wire_bytes(protocol.DATA_SIZE)   # 76 bytes
ACK_WIRE = protocol.wire_bytes(protocol.ACK_SIZE)     # 44 bytes

SCENARIOS = ["stationary", "mobile"]
QOS_LEVELS = [0, 1]
COLOR = {0: "#2563C9", 1: "#E07B2E"}                   # same as the slides
INK = "#1A2333"
MUTED = "#5B6575"
GRID = "#DADDE2"


# ---------------------------------------------------------------- loading ---
def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def load_run(run_dir):
    with open(os.path.join(run_dir, "meta.json")) as fh:
        meta = json.load(fh)
    rows = read_csv(os.path.join(run_dir, "collector.csv"))
    sensors = []
    for path in sorted(glob.glob(os.path.join(run_dir, "sensor*.json"))):
        with open(path) as fh:
            summary = json.load(fh)
        sid = summary["sensor_id"]
        log = read_csv(os.path.join(run_dir, "sensor%d.csv" % sid))
        mine = [r for r in rows if int(r["sensor_id"]) == sid]
        sensors.append(sensor_metrics(summary, mine, log))
    return meta, sensors


def sensor_metrics(summary, rows, log):
    sent = int(summary["sent"])
    rows = sorted(rows, key=lambda r: float(r["recv_time"]))
    unique = [r for r in rows if r["duplicate"] == "0" and int(r["seq"]) < sent]
    arrivals = [float(r["recv_time"]) for r in rows]

    longest_gap, gap_at = 0.0, None
    for a, b in zip(arrivals, arrivals[1:]):
        if b - a > longest_gap:
            longest_gap, gap_at = b - a, a

    by_seq = sorted(unique, key=lambda r: int(r["seq"]))
    lat = [float(r["latency_ms"]) for r in by_seq]
    jitter = (statistics.mean(abs(b - a) for a, b in zip(lat, lat[1:]))
              if len(lat) > 1 else 0.0)
    acks = sum(int(r["ack_sent"]) for r in rows)
    delivered = len(unique)
    bytes_on_air = int(summary["transmissions"]) * DATA_WIRE + acks * ACK_WIRE
    payload = delivered * protocol.PAYLOAD_SIZE

    start = float(summary["start_time"])
    delivered_seqs = set(int(r["seq"]) for r in unique)
    sent_times = {int(r["seq"]): float(r["send_time"]) for r in log}
    timeline = {
        "points": [[round(float(r["send_time"]) - start, 3), round(float(r["latency_ms"]), 2)]
                   for r in by_seq],
        "lost": [round(sent_times[s] - start, 3) for s in sorted(sent_times)
                 if s not in delivered_seqs],
        "gap_start": round(gap_at - start, 3) if gap_at is not None else None,
        "gap_ms": round(longest_gap * 1000.0, 1),
    }

    return {
        "sensor_id": summary["sensor_id"], "qos": int(summary["qos"]),
        "sent": sent, "delivered": delivered,
        "transmissions": int(summary["transmissions"]),
        "retransmissions": int(summary["retransmissions"]),
        "duplicates": len(rows) - len([r for r in rows if r["duplicate"] == "0"]),
        "acks": acks, "bytes_on_air": bytes_on_air, "payload": payload,
        "longest_gap_ms": longest_gap * 1000.0,
        "latencies": lat, "jitter_ms": jitter, "timeline": timeline,
    }


# ------------------------------------------------------------ aggregation ---
def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def run_metrics(meta, sensors):
    sent = sum(s["sent"] for s in sensors)
    delivered = sum(s["delivered"] for s in sensors)
    payload = sum(s["payload"] for s in sensors)
    lat = [x for s in sensors for x in s["latencies"]]
    return {
        "scenario": meta["scenario"], "qos": int(meta["qos"]), "run": int(meta["run"]),
        "sensors": len(sensors), "sent": sent, "delivered": delivered,
        "pdr_pct": 100.0 * delivered / sent if sent else 0.0,
        "longest_gap_ms": statistics.mean(s["longest_gap_ms"] for s in sensors),
        "latency_ms": statistics.mean(lat) if lat else None,
        "latency_p95_ms": percentile(lat, 95),
        "jitter_ms": statistics.mean(s["jitter_ms"] for s in sensors),
        "overhead_x": (sum(s["bytes_on_air"] for s in sensors) / payload) if payload else None,
        "transmissions": sum(s["transmissions"] for s in sensors),
        "retransmissions": sum(s["retransmissions"] for s in sensors),
        "duplicates": sum(s["duplicates"] for s in sensors),
        "acks": sum(s["acks"] for s in sensors),
    }


METRICS = ["pdr_pct", "longest_gap_ms", "latency_ms", "latency_p95_ms",
           "jitter_ms", "overhead_x", "retransmissions", "duplicates",
           "sent", "delivered"]


def summarise(runs):
    cases = {}
    for scenario in SCENARIOS:
        for qos in QOS_LEVELS:
            group = [r for r in runs if r["scenario"] == scenario and r["qos"] == qos]
            if not group:
                continue
            case = {"scenario": scenario, "qos": qos, "runs": len(group)}
            for m in METRICS:
                vals = [r[m] for r in group if r[m] is not None]
                case[m] = statistics.mean(vals) if vals else None
                case[m + "_sd"] = statistics.stdev(vals) if len(vals) > 1 else 0.0
            cases["%s_qos%d" % (scenario, qos)] = case
    return cases


# ----------------------------------------------------------------- charts ---
def setup_matplotlib():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "font.size": 11, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
        "xtick.color": MUTED, "ytick.color": MUTED, "axes.titleweight": "bold",
        "axes.titlesize": 13, "axes.titlecolor": INK, "axes.spines.top": False,
        "axes.spines.right": False, "legend.frameon": False,
    })
    return plt


def grouped_bars(plt, cases, metric, ylabel, title, fmt, path, ymax=None):
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=200)
    width = 0.34
    for i, qos in enumerate(QOS_LEVELS):
        xs, ys, errs = [], [], []
        for j, scenario in enumerate(SCENARIOS):
            case = cases.get("%s_qos%d" % (scenario, qos))
            if case and case[metric] is not None:
                xs.append(j + (i - 0.5) * (width + 0.02))
                ys.append(case[metric])
                errs.append(case[metric + "_sd"])
        show_err = any(e > 0 for e in errs)          # only with several runs
        bars = ax.bar(xs, ys, width, yerr=errs if show_err else None,
                      color=COLOR[qos], label="QoS %d" % qos,
                      error_kw={"ecolor": MUTED, "capsize": 3, "elinewidth": 1})
        for b, v in zip(bars, ys):
            ax.annotate(fmt % v, (b.get_x() + b.get_width() / 2, b.get_height()),
                        xytext=(0, 4), textcoords="offset points", ha="center",
                        va="bottom", fontsize=10, color=INK)
    ax.set_xticks(range(len(SCENARIOS)))
    ax.set_xticklabels([s.capitalize() for s in SCENARIOS])
    ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left")
    if ymax is not None:
        ax.set_ylim(0, ymax)
    else:
        ax.set_ylim(0, ax.get_ylim()[1] * 1.15)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.1), ncol=2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def timeline_chart(plt, timeline, path):
    """Two small panels (QoS 0 above QoS 1) on the same axes: a dot per
    delivered reading, a cross under the axis for every lost reading."""
    levels = [q for q in QOS_LEVELS if timeline.get("qos%d" % q)]
    fig, axes = plt.subplots(len(levels), 1, figsize=(7.2, 4.2), dpi=200,
                             sharex=True, sharey=True, squeeze=False)
    top = max([p[1] for q in levels for p in timeline["qos%d" % q]["points"]] + [10])
    for ax, qos in zip(axes[:, 0], levels):
        series = timeline["qos%d" % qos]
        xs = [p[0] for p in series["points"]]
        ys = [p[1] for p in series["points"]]
        ax.plot(xs, ys, linestyle="none", marker="o", markersize=2.6,
                color=COLOR[qos], label="delivered")
        if series["lost"]:
            ax.plot(series["lost"], [-0.12 * top] * len(series["lost"]),
                    linestyle="none", marker="x", markersize=6, color=INK,
                    label="lost (%d)" % len(series["lost"]))
        ax.set_ylim(-0.25 * top, top * 1.15)
        ax.set_ylabel("ms")
        ax.yaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.text(0.01, 0.95, "QoS %d" % qos, transform=ax.transAxes, va="top",
                fontweight="bold", color=COLOR[qos])
        ax.legend(loc="upper right", fontsize=8, ncol=2)
    axes[0, 0].set_title("One-way latency per reading, mobile run (sensor %s)"
                         % timeline.get("sensor_id", "?"), loc="left")
    axes[-1, 0].set_xlabel("time since the sensor started (s)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ------------------------------------------------------------------- main ---
def main():
    p = argparse.ArgumentParser(description="Analyse the IoT telemetry benchmark")
    p.add_argument("--results", default="results", help="results folder")
    p.add_argument("--no-plots", action="store_true", help="skip the PNG charts")
    args = p.parse_args()

    run_dirs = sorted(d for d in glob.glob(os.path.join(args.results, "raw", "*"))
                      if os.path.exists(os.path.join(d, "meta.json")))
    if not run_dirs:
        sys.exit("No runs found in %s/raw. Run run_all.sh (or local_test.py) first."
                 % args.results)

    runs, timeline, params = [], {}, None
    for d in run_dirs:
        try:
            meta, sensors = load_run(d)
        except (OSError, ValueError, KeyError) as exc:
            print("skipping %s: %s" % (d, exc))
            continue
        if not sensors:
            print("skipping %s: no sensor summary" % d)
            continue
        params = params or meta.get("params")
        runs.append(run_metrics(meta, sensors))
        # latency timeline: first mobile run of each QoS level, sensor 1
        if meta["scenario"] == "mobile":
            key = "qos%d" % int(meta["qos"])
            s1 = sorted(sensors, key=lambda s: s["sensor_id"])[0]
            if key not in timeline or int(meta["run"]) < timeline[key]["run"]:
                timeline[key] = dict(s1["timeline"], run=int(meta["run"]))
                timeline["sensor_id"] = s1["sensor_id"]

    runs.sort(key=lambda r: (SCENARIOS.index(r["scenario"]), r["qos"], r["run"]))
    cases = summarise(runs)

    os.makedirs(args.results, exist_ok=True)
    run_fields = ["scenario", "qos", "run", "sensors", "sent", "delivered", "pdr_pct",
                  "longest_gap_ms", "latency_ms", "latency_p95_ms", "jitter_ms",
                  "overhead_x", "transmissions", "retransmissions", "duplicates", "acks"]
    with open(os.path.join(args.results, "runs.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=run_fields, extrasaction="ignore")
        w.writeheader()
        for r in runs:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})

    case_fields = ["scenario", "qos", "runs"]
    for m in METRICS:
        case_fields += [m, m + "_sd"]
    with open(os.path.join(args.results, "summary.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=case_fields)
        w.writeheader()
        for c in cases.values():
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in c.items()})

    with open(os.path.join(args.results, "summary.json"), "w") as fh:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M"), "params": params,
                   "cases": cases, "runs": runs}, fh, indent=2)
    with open(os.path.join(args.results, "timeline.json"), "w") as fh:
        json.dump(timeline, fh)

    print_table(cases)
    print_slide_numbers(cases, runs)

    if not args.no_plots:
        plt = setup_matplotlib()
        out = lambda name: os.path.join(args.results, name)
        grouped_bars(plt, cases, "pdr_pct", "packet delivery ratio (%)",
                     "Packet delivery ratio", "%.1f%%", out("fig_pdr.png"), ymax=110)
        grouped_bars(plt, cases, "longest_gap_ms", "longest silence (ms)",
                     "Longest gap at the collector", "%.0f", out("fig_gap.png"))
        grouped_bars(plt, cases, "overhead_x", "bytes on air / payload delivered",
                     "Overhead", "%.2fx", out("fig_overhead.png"))
        if timeline.get("qos0") or timeline.get("qos1"):
            timeline_chart(plt, timeline, out("fig_latency_timeline.png"))
        print("\nCharts written to %s/fig_*.png" % args.results)


def fmt(v, pattern):
    return "-" if v is None else pattern % v


def print_table(cases):
    print("\n%-11s %-4s %4s %8s %9s %9s %8s %8s %6s %5s"
          % ("scenario", "qos", "runs", "PDR %", "gap ms", "lat ms", "p95 ms",
             "jitter", "ovh x", "retx"))
    for c in cases.values():
        print("%-11s %-4d %4d %8s %9s %9s %8s %8s %6s %5s" % (
            c["scenario"], c["qos"], c["runs"], fmt(c["pdr_pct"], "%.2f"),
            fmt(c["longest_gap_ms"], "%.0f"), fmt(c["latency_ms"], "%.1f"),
            fmt(c["latency_p95_ms"], "%.1f"), fmt(c["jitter_ms"], "%.2f"),
            fmt(c["overhead_x"], "%.2f"), fmt(c["retransmissions"], "%.0f")))


def print_slide_numbers(cases, runs):
    def get(key, metric, pattern):
        c = cases.get(key)
        return fmt(c[metric], pattern) if c else "-"
    gaps = lambda sc: [cases[k]["longest_gap_ms"] for k in cases if k.startswith(sc)]
    print("\nNumbers for the slides")
    print("  slide 7  Mobile QoS 0 PDR        :", get("mobile_qos0", "pdr_pct", "%.1f %%"))
    print("  slide 7  Mobile QoS 1 PDR        :", get("mobile_qos1", "pdr_pct", "%.1f %%"))
    for sc in SCENARIOS:
        g = gaps(sc)
        print("  slide 8  Longest gap, %-10s:" % sc,
              ("%.0f ms" % statistics.mean(g)) if g else "-")
    print("  slide 8  Mobile latency QoS 0/1  :", get("mobile_qos0", "latency_ms", "%.1f ms"),
          "vs", get("mobile_qos1", "latency_ms", "%.1f ms"))
    print("  slide 9  Mobile overhead QoS 0   :", get("mobile_qos0", "overhead_x", "%.2f x"))
    print("  slide 9  Mobile overhead QoS 1   :", get("mobile_qos1", "overhead_x", "%.2f x"))
    print("  slide 9  Retransmissions per run :", get("mobile_qos1", "retransmissions", "%.0f"))


if __name__ == "__main__":
    main()
