#!/usr/bin/env bash
# NEON GRID game access point – no internet needed.
#
# Turns this computer's Wi-Fi into an access point and serves the game to
# everyone who joins. Phones, tablets and laptops open the game by
# themselves after connecting (captive portal / "jump page"), and every
# http:// address typed into a browser leads to the game as well.
#
#   scripts/neongrid-ap.sh                              # Wi-Fi Neongrid / 1234neongrid
#   SSID=Grid2 PASSWORD=secret123 scripts/neongrid-ap.sh
#   WIFI_DEV=wlx00c0ca123456 scripts/neongrid-ap.sh     # pick the Wi-Fi interface by hand
#
# The Wi-Fi interface is found automatically: the first one NetworkManager
# manages whose card supports access point (AP) mode.
# Ctrl+C stops the game and removes the access point again.
# Needs administrator rights (it asks for your sudo password).
#
# How it works (details and troubleshooting: docs/SETUP.md, section C;
# flowchart: docs/PAP.md, chart 12):
#   1. NetworkManager switches the Wi-Fi card to access-point mode and runs a
#      small DHCP/DNS server (dnsmasq) that hands out addresses 10.42.0.x.
#   2. An extra dnsmasq setting answers EVERY name with this computer's
#      address (and logs the lookups, see SETUP.md C.5 for checking them).
#   3. A firewall rule (nftables) forwards port 80 to the game server's port,
#      so the game is at plain http://10.42.0.1/.
#   4. The game server runs with --portal: the connectivity checks of phones
#      (e.g. connectivitycheck.gstatic.com, captive.apple.com) get a redirect
#      to the game, so the phone shows it as the network's login page.
#   5. On exit everything is undone (see cleanup()). The script does not
#      reconnect any other Wi-Fi afterwards.
#
# Requirements: Linux with NetworkManager, nftables and dnsmasq-base, a Wi-Fi
# card that supports access point (AP) mode, and the Python environment in
# .venv (see README).

# Stop on errors (-e), on unset variables (-u) and on failures inside pipes.
set -euo pipefail
# Admin tools like nft live in /usr/sbin. After a plain `su` (without "-")
# that folder is not in PATH on Debian, so add it to be safe.
export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"

SSID="${SSID:-Neongrid}"
PASSWORD="${PASSWORD:-1234neongrid}"
PORT="${PORT:-8765}"                       # internal port of the game server
WIFI_DEV="${WIFI_DEV:-}"                   # Wi-Fi interface; empty = find it automatically
IP="10.42.0.1"                             # this computer's address on the access point
CON="neongrid-ap"                          # name of the temporary NetworkManager connection
PORTAL_CONF="/etc/NetworkManager/dnsmasq-shared.d/neongrid-portal.conf"
NFT_TABLE="neongrid_portal"
# Project folder (one level above scripts/), so the script works from any directory.
DIR="$(cd "$(dirname "$0")/.." && pwd)"

# --- Administrator rights -----------------------------------------------------
# Creating the access point's DNS setting and the port-80 rule needs root.
# Restart this script through sudo; `env` passes the settings along.
if [ "$EUID" -ne 0 ]; then
    echo "Administrator rights are needed for the access point. Asking sudo…"
    exec sudo env SSID="$SSID" PASSWORD="$PASSWORD" PORT="$PORT" WIFI_DEV="$WIFI_DEV" "$0" "$@"
fi
# The game server itself runs as the normal user who called sudo, so the
# database file keeps belonging to them (and Python doesn't run as root).
# If started as root with `su` (no SUDO_USER), use the owner of the project folder.
OWNER="${SUDO_USER:-$(stat -c %U "$DIR")}"
RUN_AS=()
if [ "$OWNER" != "root" ]; then
    RUN_AS=(runuser -u "$OWNER" --)
fi

# --- Checks (nothing is changed if one fails) -----------------------------------
# WPA2 requires a Wi-Fi password of 8–63 characters.
if [ "${#PASSWORD}" -lt 8 ] || [ "${#PASSWORD}" -gt 63 ]; then
    echo "PASSWORD must be 8–63 characters." >&2
    exit 1
fi
if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "Python environment missing. Run: python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt" >&2
    exit 1
fi
for tool in nmcli nft; do
    if ! command -v "$tool" >/dev/null; then
        echo "'$tool' is missing. Install it: sudo apt install network-manager nftables" >&2
        exit 1
    fi
done
if ss -ltn "sport = :$PORT" | grep -q LISTEN; then
    echo "Port $PORT is already in use – is a game server still running? Stop it (pkill -f server.py) or use PORT=9000." >&2
    exit 1
fi

# --- Find the Wi-Fi interface -------------------------------------------------------
nmcli radio wifi on

# True if the card behind interface $1 can be an access point: `iw phy … info`
# lists "* AP" under "Supported interface modes". Without iw we can't check,
# so we assume yes (NetworkManager reports the error later if not).
supports_ap() {
    command -v iw >/dev/null || return 0
    local phy
    phy="$(cat "/sys/class/net/$1/phy80211/name" 2>/dev/null)" || return 0
    # awk reads the whole output (no early exit, so iw never gets SIGPIPE under
    # pipefail) and only looks at the lines of the "Supported interface modes" list.
    iw phy "$phy" info | awk '/Supported interface modes/ {f=1; next}
                              f && /^[[:space:]]+\* / {if ($0 ~ /\* AP$/) ap=1; next}
                              {f=0}
                              END {exit !ap}'
}

# All Wi-Fi interfaces with their NetworkManager state, e.g. "wlp2s0:connected".
WIFI_LIST="$(nmcli -t -f DEVICE,TYPE,STATE device | awk -F: '$2=="wifi"{print $1":"$3}')"
if [ -z "$WIFI_LIST" ]; then
    echo "No Wi-Fi interface found. Is the Wi-Fi switched off (rfkill list) or its firmware missing?" >&2
    exit 1
fi

if [ -n "$WIFI_DEV" ]; then
    # Chosen by hand: it must exist and be managed by NetworkManager.
    state="$(printf '%s\n' "$WIFI_LIST" | awk -F: -v d="$WIFI_DEV" '$1==d{print $2}')"
    if [ -z "$state" ]; then
        echo "WIFI_DEV=$WIFI_DEV is not a Wi-Fi interface. Available:" >&2
        printf '  %s\n' $WIFI_LIST >&2
        exit 1
    fi
    if [ "$state" = "unmanaged" ]; then
        echo "$WIFI_DEV is not managed by NetworkManager (see docs/INSTALL-DEBIAN.md, step 2.2)." >&2
        exit 1
    fi
    supports_ap "$WIFI_DEV" || echo "Warning: $WIFI_DEV does not list access point (AP) mode – trying anyway." >&2
else
    # Automatic: the first managed interface whose card supports AP mode.
    managed=0
    while IFS=: read -r dev state; do
        [ "$state" = "unmanaged" ] && continue
        managed=1
        if supports_ap "$dev"; then
            WIFI_DEV="$dev"
            break
        fi
    done <<< "$WIFI_LIST"
    if [ -z "$WIFI_DEV" ]; then
        if [ "$managed" = 0 ]; then
            echo "Wi-Fi interface(s) found, but NetworkManager doesn't manage them:" >&2
            printf '  %s\n' $WIFI_LIST >&2
            echo "See docs/INSTALL-DEBIAN.md, step 2.2." >&2
        else
            echo "None of the Wi-Fi cards supports access point (AP) mode:" >&2
            printf '  %s\n' $WIFI_LIST >&2
            echo "A USB Wi-Fi stick with AP support helps. Force one with WIFI_DEV=<interface>." >&2
        fi
        exit 1
    fi
fi
echo "Wi-Fi interface: $WIFI_DEV"

# --- Cleanup --------------------------------------------------------------------
# Runs on Ctrl+C, on kill and on any exit (also after errors): stop the
# server, remove the port rule and DNS setting, and delete the access point.
# Every step is safe to run even if the matching setup step never happened.
# No other Wi-Fi is reconnected; the interface is simply left free.
SERVER_PID=""
STOPPED_DNSMASQ=0
cleanup() {
    trap - INT TERM EXIT   # don't run twice
    set +e                 # carry on with the remaining steps even if one fails
    echo
    echo "Shutting down…"
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null && wait "$SERVER_PID" 2>/dev/null
    nft delete table ip "$NFT_TABLE" 2>/dev/null
    nmcli connection down "$CON" >/dev/null 2>&1
    nmcli connection delete "$CON" >/dev/null 2>&1
    rm -f "$PORTAL_CONF"
    if [ "$STOPPED_DNSMASQ" = 1 ]; then
        echo "Restarting the system dnsmasq service…"
        systemctl start dnsmasq
    fi
    echo "Access point removed."
}
trap cleanup INT TERM EXIT

# --- 0. Free DNS port 53 ----------------------------------------------------------
# If the full "dnsmasq" package is installed, its system service listens on
# port 53 of ALL interfaces. NetworkManager's own dnsmasq for the access point
# then fails with "Address already in use" and the access point won't start.
# Pause the service while the access point runs; cleanup() starts it again.
if command -v systemctl >/dev/null && systemctl is-active --quiet dnsmasq 2>/dev/null; then
    echo "Pausing the system dnsmasq service (it blocks DNS port 53)…"
    systemctl stop dnsmasq
    STOPPED_DNSMASQ=1
fi
if ss -lun "sport = :53" | grep -q ':53 '; then
    echo "Something else is using DNS port 53:" >&2
    ss -lunp "sport = :53" >&2
    echo "Stop that program, then run this script again." >&2
    exit 1
fi

# --- 1. DNS: every name points to this computer ---------------------------------
# NetworkManager's dnsmasq for access points reads extra settings from
# dnsmasq-shared.d. The file only exists while this script runs.
mkdir -p "$(dirname "$PORTAL_CONF")"
cat > "$PORTAL_CONF" <<EOF
# Written by NEON GRID scripts/neongrid-ap.sh – removed again when it stops.
# Answer every DNS name with the game computer's address:
address=/#/$IP
# Log every lookup (journalctl), so a test shows whether phones use this DNS.
log-queries
# No DHCP option 114 (RFC 8910) on purpose: it must point to an HTTPS JSON API
# with a valid certificate, not to an HTML page. Phones use their normal
# connectivity check instead, which the game server redirects.
EOF

# --- 2. Access point ---------------------------------------------------------------
#    2.4 GHz ("bg") for best compatibility, WPA2 (rsn/ccmp), fixed address
#    10.42.0.1/24, IPv6 off (only IPv4 is redirected), never starts by itself.
#    A leftover connection from a crashed earlier run is removed first.
echo "Starting access point '$SSID' on $WIFI_DEV…"
nmcli connection delete "$CON" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname "$WIFI_DEV" con-name "$CON" autoconnect no ssid "$SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    ipv4.method shared ipv4.addresses "$IP/24" ipv6.method disabled \
    wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
    wifi-sec.pmf disable wifi-sec.psk "$PASSWORD" >/dev/null
nmcli connection up "$CON" >/dev/null

# --- 3. Port 80 -> game server --------------------------------------------------
#    prerouting: requests from the Wi-Fi to port 80 go to the game server.
#    output:     the same for browsers on this computer (http://10.42.0.1/).
nft delete table ip "$NFT_TABLE" 2>/dev/null || true
nft -f - <<EOF
table ip $NFT_TABLE {
    chain prerouting {
        type nat hook prerouting priority dstnat; policy accept;
        iifname "$WIFI_DEV" tcp dport 80 redirect to :$PORT
    }
    chain output {
        type nat hook output priority -100; policy accept;
        ip daddr $IP tcp dport 80 redirect to :$PORT
    }
}
EOF

# --- 4. Game server ------------------------------------------------------------------
#    Listens on all interfaces; --portal switches on the jump-page redirect.
#    Runs in the background (&) so this script can print the info box and
#    clean up later. Scores go to the normal database.
"${RUN_AS[@]}" "$DIR/.venv/bin/python" "$DIR/server/server.py" --host 0.0.0.0 --port "$PORT" \
    --static "$DIR/public" --db "$DIR/server/neongrid.sqlite3" --portal "http://$IP/" &
SERVER_PID=$!
sleep 1
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "The game server did not start (see the error above)." >&2
    exit 1
fi

# --- 5. Info for the players ------------------------------------------------------
cat <<EOF

  ┌────────────────────────────────────────────┐
     NEON GRID access point is live

     Wi-Fi:       $SSID
     Password:    $PASSWORD
     Interface:   $WIFI_DEV
     IP address:  $IP
     Game:        http://$IP/

     Phones open the game by themselves after
     joining ("Sign in to network"). Any http://
     address in a browser leads there too.

     Stop:        Ctrl+C
  └────────────────────────────────────────────┘
EOF
# Optional: a QR code phones can scan to join the Wi-Fi
# (sudo apt install qrencode). Special characters must be escaped with "\".
if command -v qrencode >/dev/null; then
    esc() { printf '%s' "$1" | sed 's/[\\;,:"]/\\&/g'; }
    echo "     Scan to join the Wi-Fi:"
    qrencode -t ANSIUTF8 -m 2 "WIFI:T:WPA;S:$(esc "$SSID");P:$(esc "$PASSWORD");;"
fi
echo

# Block here until the server ends (normally: Ctrl+C -> cleanup()).
wait "$SERVER_PID"
