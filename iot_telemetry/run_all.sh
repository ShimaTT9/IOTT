#!/usr/bin/env bash
# Run the whole benchmark: 4 cases x RUNS runs, one Mininet-WiFi network per run.
#
#   Case A  stationary, QoS 0      Case C  mobile, QoS 0
#   Case B  stationary, QoS 1      Case D  mobile, QoS 1
#
# Usage:  sudo ./run_all.sh [runs] [seconds]      (default: 3 runs, 40 s)
# Time:   about 1 minute per run, so 12 to 15 minutes with the defaults.
# After:  python3 analyze.py

set -u
RUNS=${1:-3}
DURATION=${2:-40}

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo:  sudo ./run_all.sh $RUNS $DURATION"
    exit 1
fi

cd "$(dirname "$0")"
TOTAL=$((RUNS * 4))
N=0
for run in $(seq 1 "$RUNS"); do            # runs are interleaved so that a slow
    for scenario in stationary mobile; do  # moment of the VM does not hit only
        for qos in 0 1; do                 # one case
            N=$((N + 1))
            echo
            echo "=================================================================="
            echo " [$N/$TOTAL]  scenario=$scenario  qos=$qos  run=$run  (${DURATION}s)"
            echo "=================================================================="
            mn -c > /dev/null 2>&1
            python3 topology.py --scenario "$scenario" --qos "$qos" --run "$run" \
                --duration "$DURATION" --auto --pcap
            if [ $? -ne 0 ]; then
                echo "!! run failed: scenario=$scenario qos=$qos run=$run (continuing)"
            fi
        done
    done
done
mn -c > /dev/null 2>&1
echo
echo "All $TOTAL runs finished. Logs are in results/raw/. Next step:"
echo "    python3 analyze.py"
