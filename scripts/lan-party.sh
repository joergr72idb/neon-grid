#!/usr/bin/env bash
# NEON GRID at a meetup: turns the notebook's Wi-Fi into a hotspot (internet comes
# from a phone via USB tethering) and starts the game server for everyone on it.
#
#   scripts/lan-party.sh                       # SSID NEON-GRID, password lightcycle
#   SSID=LUG-GRID PASSWORD=secret123 scripts/lan-party.sh
#
# Ctrl+C stops the game, removes the hotspot and reconnects your normal Wi-Fi.
set -euo pipefail

SSID="${SSID:-NEON-GRID}"
PASSWORD="${PASSWORD:-lightcycle}"
PORT="${PORT:-8765}"
CON="neongrid-hotspot"
DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${#PASSWORD}" -lt 8 ]; then
    echo "PASSWORD must be at least 8 characters." >&2
    exit 1
fi
if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "Python environment missing. Run: python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt" >&2
    exit 1
fi

WIFI_DEV="$(nmcli -t -f DEVICE,TYPE device | awk -F: '$2=="wifi"{print $1; exit}')"
if [ -z "$WIFI_DEV" ]; then
    echo "No Wi-Fi device found." >&2
    exit 1
fi
PREV_WIFI="$(nmcli -g GENERAL.CONNECTION device show "$WIFI_DEV" 2>/dev/null || true)"
[ "$PREV_WIFI" = "$CON" ] && PREV_WIFI=""

# 1. Wait for the phone's USB tethering (shows up as a wired connection).
uplink() {
    nmcli -t -f DEVICE,TYPE,STATE device | awk -F: '$2=="ethernet" && $3=="connected"{print $1; exit}'
}
UPLINK="$(uplink)"
if [ -z "$UPLINK" ]; then
    echo "Plug in your phone via USB and switch on 'USB tethering'…"
    for _ in $(seq 60); do
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

SERVER_PID=""
cleanup() {
    trap - INT TERM EXIT
    set +e
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
nmcli connection delete "$CON" >/dev/null 2>&1 || true
nmcli device wifi hotspot ifname "$WIFI_DEV" con-name "$CON" ssid "$SSID" band bg password "$PASSWORD" >/dev/null
nmcli connection modify "$CON" connection.autoconnect no

IP=""
for _ in $(seq 15); do
    IP="$(nmcli -g IP4.ADDRESS device show "$WIFI_DEV" | head -1 | cut -d/ -f1)"
    [ -n "$IP" ] && break
    sleep 1
done
IP="${IP:-10.42.0.1}"

if ping -c1 -W3 1.1.1.1 >/dev/null 2>&1; then NET="yes"; else NET="NO – check the phone's tethering"; fi

# 3. Game server, reachable by everyone on the hotspot.
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
wait "$SERVER_PID"
