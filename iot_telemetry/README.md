# IoT Telemetry over Mobile WiFi

977-337 Network Programming and Simulation, term project (IoT & Smart Wireless Networks).

Two moving WiFi sensors send temperature/humidity readings over a custom UDP
protocol to a collector. The project compares **QoS 0** (send once) with
**QoS 1** (ACK, timeout 0.2 s, up to 3 resends, duplicate filtering) while the
sensors hand over from one access point to another in Mininet-WiFi.

```
sta1 (sensor.py) ~~~ ap1 --+
                           +-- s1 ===== h1 (collector.py, 10.0.0.100:5005)
sta2 (sensor.py) ~~~ ap2 --+     10 Mbit/s, 5 ms, max_queue_size 100
```

## Files

| File | Purpose |
|---|---|
| `protocol.py` | 16-byte header + 32-byte payload, packed with `struct` |
| `sensor.py` | UDP client, QoS 0 / QoS 1 with retransmissions |
| `collector.py` | UDP server, ACKs, duplicate filter, CSV log |
| `topology.py` | Mininet-WiFi topology: 2 APs, 2 mobile stations, logDistance propagation, TCLink wired links; `--cli`, `--demo`, `--auto` modes |
| `config.py` | Shared experiment parameters (positions, ranges, rates) |
| `run_all.sh` | Runs the 4 cases x 3 runs benchmark |
| `analyze.py` | PDR, longest gap, latency, jitter, overhead; CSV + PNG charts |
| `build_slides.py` | Builds the PowerPoint deck, with results when given `--results` |
| `local_test.py` | Tests sensor/collector without Mininet (UDP relay with loss and an outage) |
| `iot_dissector.lua` | Wireshark dissector for UDP port 5005 |
| `setup_ubuntu.sh` | One-shot install and self-test on a fresh Ubuntu 24.04 (`bash setup_ubuntu.sh`) |

## Requirements

Ubuntu 22.04 or 24.04 with Mininet-WiFi (`sudo util/install.sh -Wlnfv`),
Python 3.8+, `python3-matplotlib`, and optionally `iperf3`, `tcpdump`,
`wireshark`, `python-pptx`.

## Run

```bash
python3 local_test.py --duration 10                          # no Mininet, about 1 minute
xhost +si:localuser:root                                     # allow root GUI windows (plot, xterm)
sudo python3 topology.py --scenario mobile --cli --plot      # explore the network
sudo python3 topology.py --scenario mobile --demo --plot     # live demo: sta1 QoS 0 vs sta2 QoS 1
sudo ./run_all.sh                                            # benchmark: 4 cases x 3 runs, about 15 minutes
python3 analyze.py                                           # results/summary.csv and results/fig_*.png
python3 build_slides.py --results results --out IoT_Telemetry_final.pptx
```

Clean up after a crash with `sudo mn -c`.

## Link and radio parameters

`topology.py` options: `--bw 10 --delay 5ms --loss 0 --queue 100`
(wired links), `--exp 3.5` (logDistance exponent), `--ap-range 25`
(smaller value = longer handover gap), `--wmediumd` (interference mode),
`--controller` (OpenFlow reference controller instead of standalone APs),
`--pcap` (tcpdump on h1).

## Metrics

* **PDR**: unique readings delivered / readings sent
* **Longest gap**: longest silence between two packets of one sensor at the collector
* **Latency**: collector receive time minus the timestamp in the reading (same host clock in Mininet)
* **Jitter**: mean absolute difference between consecutive latencies
* **Overhead**: bytes on the IP network (DATA 76 B and ACK 44 B including UDP/IP headers) / payload bytes delivered
