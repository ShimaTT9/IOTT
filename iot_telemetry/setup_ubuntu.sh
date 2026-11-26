#!/usr/bin/env bash
# setup_ubuntu.sh - install everything for the IoT Telemetry lab on a fresh
# Ubuntu 24.04 LTS, then check that it works.
#
#   bash ~/iot_telemetry/setup_ubuntu.sh
#
# Run it as your normal user (NOT with sudo); it asks for your password once.
# Takes 30-60 minutes, mostly the Mininet-WiFi build. Full log: ~/setup_iot.log
# Safe to run again: steps that are already done are skipped.

set -eo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$HOME/setup_iot.log"
MNWIFI_COMMIT=90c2d1d          # Mininet-WiFi version this project was checked against
APT="sudo env DEBIAN_FRONTEND=noninteractive apt-get -y -o DPkg::Lock::Timeout=900"
exec > >(tee -a "$LOG") 2>&1

step() {
    echo
    echo "=================================================================="
    echo " [$1/7] $2"
    echo "=================================================================="
}
fail() {
    echo
    echo "!! FAILED: $*"
    echo "!! Show the end of the log with:  tail -n 40 $LOG"
    exit 1
}

if [ "$(id -u)" -eq 0 ]; then
    echo "Run this as your normal user, without sudo:  bash $0"
    exit 1
fi
[ -f "$HERE/topology.py" ] || fail "setup_ubuntu.sh must stay inside the iot_telemetry folder"

. /etc/os-release
echo "System: $PRETTY_NAME, kernel $(uname -r), $(nproc) CPUs," \
     "$(free -g | awk '/Mem:/{print $2}') GB RAM"
[ "$VERSION_ID" = "24.04" ] || echo "WARNING: written for Ubuntu 24.04, this is $VERSION_ID"
[ "$(nproc)" -ge 2 ] || echo "WARNING: give the VM at least 2 CPUs (VirtualBox: Settings > System > Processor)"

sudo -v || fail "sudo password"
# keep sudo alive while the long build runs
( while kill -0 $$ 2>/dev/null; do sudo -n true; sleep 50; done ) 2>/dev/null &

# --------------------------------------------------------------------------
step 1 "Ubuntu packages"
echo "wireshark-common wireshark-common/install-setuid boolean true" | sudo debconf-set-selections
$APT update || fail "apt-get update"
$APT install git make gcc unzip curl python3-pip python3-matplotlib python3-numpy \
    iperf3 tcpdump wireshark openssh-server x11-xserver-utils \
    "linux-modules-extra-$(uname -r)" || fail "apt-get install"

# --------------------------------------------------------------------------
step 2 "Virtual WiFi radios (mac80211_hwsim kernel module)"
sudo modprobe mac80211_hwsim radios=2 || fail "mac80211_hwsim is missing for kernel $(uname -r)"
sudo modprobe -r mac80211_hwsim
echo "hwsim OK"

# --------------------------------------------------------------------------
step 3 "Mininet-WiFi (the longest step, 20-40 minutes)"
if python3 -c "import mn_wifi" 2>/dev/null && command -v mn >/dev/null \
        && command -v hostapd >/dev/null; then
    echo "Mininet-WiFi is already installed, skipping"
else
    cd "$HOME"
    [ -d mininet-wifi ] || git clone https://github.com/intrig/mininet-wifi \
        || fail "git clone mininet-wifi"
    cd mininet-wifi
    git checkout -q "$MNWIFI_COMMIT" || echo "(could not pin $MNWIFI_COMMIT, using the current version)"
    sudo util/install.sh -Wlnfv || fail "Mininet-WiFi util/install.sh"
fi
python3 -c "import mn_wifi" || fail "python3 cannot import mn_wifi"
echo "Mininet-WiFi OK"

# --------------------------------------------------------------------------
step 4 "Project files and tools"
cd "$HERE"
chmod +x ./*.py run_all.sh setup_ubuntu.sh
python3 -m pip install --user --break-system-packages python-pptx \
    || echo "(python-pptx not installed: only build_slides.py needs it)"
mkdir -p "$HOME/.local/lib/wireshark/plugins"
cp iot_dissector.lua "$HOME/.local/lib/wireshark/plugins/"
sudo usermod -aG wireshark "$USER"
echo "project folder: $HERE"

# --------------------------------------------------------------------------
step 5 "Test the programs without Mininet (local_test.py, about 1 minute)"
python3 local_test.py --duration 10 || fail "local_test.py"

# --------------------------------------------------------------------------
step 6 "Test in Mininet-WiFi: one mobile QoS 1 run of 30 s, with handover"
SMOKE=/tmp/iot_smoke
sudo rm -rf "$SMOKE"
sudo mn -c >/dev/null 2>&1 || true
sudo python3 topology.py --scenario mobile --qos 1 --run 1 --duration 30 --auto \
    --results "$SMOKE" || fail "topology.py --auto"
sudo chown -R "$USER": "$SMOKE"
CSV="$SMOKE/raw/mobile_qos1_run1/collector.csv"
[ -f "$CSV" ] || fail "no collector log at $CSV"
ROWS=$(( $(wc -l < "$CSV") - 1 ))
echo "collector received $ROWS datagrams (expected about 600)"
[ "$ROWS" -ge 400 ] || fail "too few packets reached the collector ($ROWS)"
python3 analyze.py --results "$SMOKE" --no-plots || fail "analyze.py"
sudo mn -c >/dev/null 2>&1 || true

# --------------------------------------------------------------------------
step 7 "Done"
cat <<EOF
Everything is installed and tested. Log: $LOG

Next (also in the Word guide):
  1. Log out and back in once (so Wireshark works without sudo).
  2. cd $HERE
  3. xhost +si:localuser:root
  4. sudo python3 topology.py --scenario mobile --demo --plot     # live demo
  5. sudo ./run_all.sh                                            # benchmark, about 15 min
  6. python3 analyze.py                                           # results/summary.csv + charts
EOF
