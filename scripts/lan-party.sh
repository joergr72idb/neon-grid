#!/usr/bin/env bash
# NEON GRID at a meetup: turns the notebook's Wi-Fi into a hotspot (internet comes
# from a phone via USB tethering) and starts the game server for everyone on it.
#
#   scripts/lan-party.sh                       # SSID NEON-GRID, password lightcycle
#   SSID=LUG-GRID PASSWORD=secret123 scripts/lan-party.sh
#
# Ctrl+C stops the game, removes the hotspot and reconnects your normal Wi-Fi.
#
# How it works (details and troubleshooting: docs/SETUP.md, flowchart: docs/PAP.md):
#   1. Wait until the phone's USB tethering is up (= internet for the notebook).
#   2. Switch the notebook's Wi-Fi card from "client" to "access point" with
#      NetworkManager (nmcli). NetworkManager hands out IP addresses (DHCP) to
#      the players' devices and forwards their traffic to the phone (NAT).
#   3. Start the game server on all network interfaces, so players on the
#      hotspot can open http://<notebook-ip>:8765/.
#   4. On exit, undo everything (see cleanup()).
#
# Requirements: Linux with NetworkManager, a Wi-Fi card that supports access
# point (AP) mode, and the Python environment in .venv (see README).

# Stop on errors (-e), on unset variables (-u) and on failures inside pipes.
set -euo pipefail

SSID="${SSID:-NEON-GRID}"
PASSWORD="${PASSWORD:-lightcycle}"
PORT="${PORT:-8765}"
CON="neongrid-hotspot"                     # name of the temporary NetworkManager connection
# Project folder (one level above scripts/), so the script works from any directory.
DIR="$(cd "$(dirname "$0")/.." && pwd)"

# WPA2 requires a Wi-Fi password of 8–63 characters.
if [ "${#PASSWORD}" -lt 8 ]; then
    echo "PASSWORD must be at least 8 characters." >&2
    exit 1
fi
if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "Python environment missing. Run: python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt" >&2
    exit 1
fi

# Find the first Wi-Fi device (e.g. wlp2s0) and remember which Wi-Fi it is
# connected to right now, so we can reconnect to it at the end.
WIFI_DEV="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="wifi"{print $1; exit}')"
if [ -z "$WIFI_DEV" ]; then
    echo "No Wi-Fi device found." >&2
    exit 1
fi
PREV_WIFI="$(nmcli -g GENERAL.CONNECTION device show "$WIFI_DEV" 2>/dev/null || true)"
[ "$PREV_WIFI" = "$CON" ] && PREV_WIFI=""

# 1. Wait for the phone's USB tethering (shows up as a wired connection).
#    Android: "USB tethering"; iPhone: "Personal Hotspot" with the USB cable.
#    Prints the name of the first connected ethernet device, or nothing.
uplink() {
    nmcli -t -f DEVICE,TYPE,STATE device | awk -F: '$2=="ethernet" && $3=="connected"{print $1; exit}'
}
UPLINK="$(uplink)"
if [ -z "$UPLINK" ]; then
    echo "Plug in your phone via USB and switch on 'USB tethering'…"
    for _ in $(seq 60); do            # 60 x 2 s = wait up to 2 minutes
        sleep 2
        UPLINK="$(uplink)"
        [ -n "$UPLINK" ] && break
    done
fi
if [ -z "$UPLINK" ]; then
    echo "No USB tethering found after 2 minutes. Nothing was changed." >&2
    exit 1
fi
echo "Internet via:  $UPLINK"

# Runs on Ctrl+C, on kill and on any exit (also after errors): stop the
# server, delete the hotspot connection, and reconnect the previous Wi-Fi.
SERVER_PID=""
cleanup() {
    trap - INT TERM EXIT   # don't run twice
    set +e                 # carry on with the remaining steps even if one fails
    echo
    echo "Shutting down…"
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null && wait "$SERVER_PID" 2>/dev/null
    nmcli connection down "$CON" >/dev/null 2>&1 || true
    nmcli connection delete "$CON" >/dev/null 2>&1 || true
    if [ -n "$PREV_WIFI" ]; then
        echo "Reconnecting to '$PREV_WIFI'…"
        nmcli connection up "$PREV_WIFI" >/dev/null 2>&1 || echo "Could not reconnect – do it in the network menu."
    fi
}
trap cleanup INT TERM EXIT

# 2. Hotspot (2.4 GHz for best compatibility). NetworkManager handles DHCP and
#    shares the tethered internet connection automatically.
#    A leftover hotspot connection from a crashed earlier run is removed first.
#    "band bg" = 2.4 GHz; autoconnect no = it never starts by itself later.
nmcli connection delete "$CON" >/dev/null 2>&1 || true
nmcli device wifi hotspot ifname "$WIFI_DEV" con-name "$CON" ssid "$SSID" band bg password "$PASSWORD" >/dev/null
nmcli connection modify "$CON" connection.autoconnect no

# The hotspot's own IP address (NetworkManager normally uses 10.42.0.1).
# It can take a few seconds to appear, so poll for up to 15 s.
IP=""
for _ in $(seq 15); do
    IP="$(nmcli -g IP4.ADDRESS device show "$WIFI_DEV" | head -1 | cut -d/ -f1)"
    [ -n "$IP" ] && break
    sleep 1
done
IP="${IP:-10.42.0.1}"

# Quick internet check (does not stop the script: the game works without internet).
if ping -c1 -W3 1.1.1.1 >/dev/null 2>&1; then NET="yes"; else NET="NO – check the phone's tethering"; fi

# 3. Game server, reachable by everyone on the hotspot (--host 0.0.0.0 = listen
#    on all network interfaces). Runs in the background (&) so this script can
#    print the info box and later clean up. Scores go to the normal database.
"$DIR/.venv/bin/python" "$DIR/server/server.py" --host 0.0.0.0 --port "$PORT" \
    --static "$DIR/public" --db "$DIR/server/neongrid.sqlite3" &
SERVER_PID=$!
sleep 1

cat <<EOF

  ┌────────────────────────────────────────────┐
     NEON GRID is live

     Wi-Fi:     $SSID
     Password:  $PASSWORD
     Game:      http://$IP:$PORT/

     Internet:  $NET
     Stop:      Ctrl+C
  └────────────────────────────────────────────┘

EOF
# Block here until the server ends (normally: Ctrl+C -> cleanup()).
wait "$SERVER_PID"
