# NEON GRID – Setup Guide

This guide explains how to install and run NEON GRID in every supported way.
Pick the scenario that fits you:

| Scenario | Who can play | Section |
|---|---|---|
| **A. Local** – everything on one computer | you, in two browser windows | [A](#a-local-on-one-computer) |
| **B. Home network** – one computer is the server | everyone on your Wi-Fi/LAN | [B](#b-home-network-lan) |
| **C. Meetup mode** – the notebook opens its own Wi-Fi hotspot | everyone near the notebook | [C](#c-meetup-mode-own-wi-fi-hotspot) |
| **C2. Game access point** – own Wi-Fi *Neongrid*, no internet, game opens by itself | everyone near the computer | [C2](#c2-game-access-point-without-internet-jump-page) |
| **D. Internet** – Apache web server with HTTPS | everyone on the internet | [D](#d-public-server-with-apache) |

Other documents: [ARCHITECTURE.md](ARCHITECTURE.md) explains how the program is
built, and [PAP.md](PAP.md) has the program flowcharts.

---

## 0. Requirements and one-time setup

**What you need**

- Linux (tested on Debian 13; Ubuntu works the same way)
- Python 3.9 or newer with the `venv` module (`sudo apt install python3 python3-venv`)
- A modern browser (Firefox, Chrome, Edge, Safari)
- **Controls:** on a computer, the arrow keys or W A S D. On phones and
  tablets, swipe in the direction you want to turn, or tap the on-screen arrow
  pad. The pad sits below the arena when the phone is held upright and to the
  right of it when held sideways. Turning the phone during a match is fine.

**Install the Python environment** (only once, or again after deleting `.venv/`):

```bash
cd ~/ClaudeCode/neon-grid
python3 -m venv .venv                              # private Python environment in .venv/
.venv/bin/pip install -r server/requirements.txt   # installs aiohttp (web server library)
```

**Check that it works:**

```bash
.venv/bin/python tests/smoke_test.py
.venv/bin/python tests/portal_test.py
```

Each test's last line should start with `OK –`. The test plays two complete matches
against a temporary database. It doesn't touch your real scores.

### What is where

| Path | Content |
|---|---|
| `server/server.py` | the game server (accounts, matchmaking, game logic) |
| `server/requirements.txt` | Python libraries the server needs |
| `server/neongrid.sqlite3` | **database** with accounts and scores (created on first start, not in git) |
| `public/` | the web page: `index.html`, `style.css`, `game.js`, font |
| `scripts/lan-party.sh` | meetup mode (hotspot + server in one command) |
| `scripts/neongrid-ap.sh` | game access point without internet, with jump page |
| `deploy/` | config files for the Apache server (scenario D) |
| `tests/smoke_test.py`, `tests/portal_test.py` | automatic tests |

---

## A. Local on one computer

```bash
cd ~/ClaudeCode/neon-grid/server
../.venv/bin/python server.py --static ../public
```

When you see `Running on http://127.0.0.1:8765`, open **http://127.0.0.1:8765/**
in two browser windows. Use a different account in each one, for example a normal
window and a private window: one account can only be connected once, and a second
window "kicks" the first. Create an account in both windows and press **Enter the grid** in both.

Stop the server with **Ctrl+C**.

> `--static ../public` makes the game server deliver the web page itself, so
> you don't need Apache. Tip for testing: `--countdown 3` shortens the 10-second
> countdown.

### Running it in the background

```bash
cd ~/ClaudeCode/neon-grid/server
nohup ../.venv/bin/python server.py --static ../public > neongrid.log 2>&1 &
tail -f neongrid.log            # watch the log (Ctrl+C only ends tail)
pkill -f "server.py --static"   # stop the server
```

---

## B. Home network (LAN)

Same as A, but the server listens on all network interfaces:

```bash
cd ~/ClaudeCode/neon-grid/server
../.venv/bin/python server.py --static ../public --host 0.0.0.0
```

1. Find the computer's IP address with `hostname -I`. The first entry, for example
   `192.168.178.23`, is usually the right one.
2. The other players open `http://192.168.178.23:8765/`.
3. If a firewall is active (`sudo ufw status` says *active*), allow the port:
   `sudo ufw allow 8765/tcp`.

> There is no HTTPS in this mode, so passwords travel unencrypted through your
> network. That's fine at home. Don't reuse important passwords.

---

## C. Meetup mode: own Wi-Fi hotspot

For meetups, LAN parties and user groups: the notebook opens its **own Wi-Fi**
called `NEON-GRID`, the players' laptops join it, and everybody plays on the
notebook's game server. The notebook's Wi-Fi card is busy being the access point,
so internet access comes from a **phone connected by USB cable** (USB tethering).
NetworkManager passes it on to everyone on the hotspot.

```mermaid
flowchart LR
    Internet(("Internet")) <-- "mobile data" --> Phone["Phone<br/>USB tethering"]
    Phone <-- "USB cable<br/>(looks like Ethernet)" --> NB["Notebook<br/>NetworkManager: DHCP + NAT<br/>game server :8765"]
    NB <-- "Wi-Fi 'NEON-GRID'<br/>2.4 GHz, WPA2" --> L1["Player laptop 1"]
    NB <-- "Wi-Fi" --> L2["Player laptop 2"]
    NB <-- "Wi-Fi" --> L3["Player laptop …"]
```

### C.1 What you need

| Item | Notes |
|---|---|
| Notebook with Linux and **NetworkManager** | standard on Debian/Ubuntu desktops; check with `nmcli --version` |
| Wi-Fi card that supports **access point (AP) mode** | check below; almost all Intel, Atheros and MediaTek cards do |
| `dnsmasq-base` package | NetworkManager uses it to hand out IP addresses: `sudo apt install dnsmasq-base` |
| Smartphone with mobile data and a **USB cable** | Android or iPhone |
| Power supply for the notebook | being a hotspot uses a lot of battery |
| Python environment | see [section 0](#0-requirements-and-one-time-setup) |

**Check AP support** (do this once at home):

```bash
iw list | grep -A 10 "Supported interface modes"
```

The list must contain `* AP`. If `iw` is missing, run `sudo apt install iw`.

### C.2 Try it at home first

Do a complete dry run at home, so there are no surprises at the meetup:

1. Connect the phone (C.3), start the script (C.4), and join the hotspot with a
   second device.
2. Open the game address on that device and play one round.
3. Press Ctrl+C and check that your normal Wi-Fi reconnects.

### C.3 Switch on USB tethering on the phone

Connect the phone to the notebook with the USB cable first, then:

- **Android:** *Settings → Network & internet → Hotspot & tethering → USB tethering* (switch on).
  The wording differs a little between manufacturers. Search the settings for "tethering".
- **iPhone:** *Settings → Personal Hotspot → Allow Others to Join* (switch on). When the
  phone asks *"Trust this computer?"*, tap **Trust**.

The notebook now shows a new **wired connection**. The script waits for it.

### C.4 Start

```bash
cd ~/ClaudeCode/neon-grid
scripts/lan-party.sh
```

With your own Wi-Fi name and password (the password needs at least 8 characters):

```bash
SSID=LUG-GRID PASSWORD=secret123 scripts/lan-party.sh
```

Other port: `PORT=9000 scripts/lan-party.sh`.

The script then does the following:

1. It waits up to 2 minutes for the USB tethering. Nothing is changed before it is there.
2. It switches the Wi-Fi card to hotspot mode on 2.4 GHz, the band every device supports.
   Your normal Wi-Fi disconnects during this.
3. It tests whether internet works.
4. It starts the game server and prints a box like this:

```
  ┌────────────────────────────────────────────┐
     NEON GRID is live

     Wi-Fi:     NEON-GRID
     Password:  lightcycle
     Game:      http://10.42.0.1:8765/

     Internet:  yes
     Stop:      Ctrl+C
  └────────────────────────────────────────────┘
```

Show this box to the players, for example on a projector, or write it on a flipchart.
Players can use laptops or **phones**: the phone just has to join the Wi-Fi and
open the game address. Phones steer by swiping or with the arrow pad.

### C.5 Players join

1. Connect to the Wi-Fi **NEON-GRID** with password **lightcycle**, or whatever you chose.
2. Open the **Game** address from the box in the browser, usually `http://10.42.0.1:8765/`.
3. Create an account and press **Enter the grid**. Players are paired in the order they join.

The highscore list is shared by everybody and stored on the notebook in
`server/neongrid.sqlite3`. It stays there after the meetup.

### C.6 Stop

Press **Ctrl+C** in the script's terminal. The script then:

- stops the game server,
- deletes the hotspot (the temporary connection `neongrid-hotspot`),
- reconnects to the Wi-Fi you were on before.

Afterwards you can switch off USB tethering on the phone.

### C.7 Troubleshooting meetup mode

| Problem | Cause / solution |
|---|---|
| `No Wi-Fi device found.` | Wi-Fi is switched off (`nmcli radio wifi on`) or there is no Wi-Fi card. |
| `No USB tethering found after 2 minutes.` | Tethering is not switched on, or the cable is charge-only. Try another cable. `nmcli device` should list an `ethernet … connected` device. |
| Error while creating the hotspot | The card does not support AP mode (see C.1), or `dnsmasq-base` is missing. |
| `Internet: NO – check the phone's tethering` | The phone has no mobile data, or tethering stopped. **The game still works**, just without internet for the players. |
| Players can't see the Wi-Fi | Some 5-GHz-only devices exist but are rare. Move closer, since the notebook antenna is small. |
| Players are connected but the page does not load | Check the address (http, not https, and include `:8765`). If a firewall is active: `sudo ufw allow in on <wifi-device>` (the device name is shown by `nmcli device`, e.g. `wlp2s0`). |
| Players get no IP address ("Obtaining IP address…" forever) | Usually the firewall blocks DHCP. Same fix as above. |
| `nmcli` says "Not authorized" | Run the script from the desktop session (not over SSH), or use `sudo -E scripts/lan-party.sh`. |
| The script was killed hard and the hotspot is still on | `nmcli connection delete neongrid-hotspot`, then reconnect your Wi-Fi in the network menu. |
| `address already in use` | A game server is already running: `pkill -f server.py`, or use `PORT=9000`. |

### C.8 Meetup without a phone (no internet at all)

`lan-party.sh` refuses to start without USB tethering. If you only need the game
and no internet, use the game access point instead (next section). It also opens
the game on the players' devices by itself.

---

## C2. Game access point without internet (jump page)

The computer opens its own Wi-Fi **Neongrid** and serves only the game. No
internet and no phone are needed. When players join the Wi-Fi, their phone or
laptop **opens the game by itself**, the same way hotel Wi-Fis open their login
page. That page is the "jump page". Any `http://` address typed into a browser
also leads to the game.

```mermaid
flowchart LR
    P1["Player phone"] -- "Wi-Fi 'Neongrid'<br/>password 1234neongrid" --> NB
    P2["Player laptop"] -- "Wi-Fi" --> NB
    subgraph NB["Game computer – 10.42.0.1"]
        DNS["dnsmasq: DHCP +<br/>DNS: every name → 10.42.0.1"]
        FW["nftables: port 80 → 8765"]
        GS["game server --portal<br/>foreign names → redirect to the game"]
        FW --> GS
    end
```

### C2.1 Start

```bash
cd ~/ClaudeCode/neon-grid
scripts/neongrid-ap.sh
```

The script asks for your **sudo password**, because creating the DNS setting and
the port-80 rule needs administrator rights. The game server itself still runs
as your normal user. The script then prints:

```
  ┌────────────────────────────────────────────┐
     NEON GRID access point is live

     Wi-Fi:       Neongrid
     Password:    1234neongrid
     IP address:  10.42.0.1
     Game:        http://10.42.0.1/
     …
  └────────────────────────────────────────────┘
```

If `qrencode` is installed (`sudo apt install qrencode`), a QR code follows.
Players can scan it with the phone camera to join the Wi-Fi without typing the
password.

Other name or password: `SSID=Grid2 PASSWORD=secret123 scripts/neongrid-ap.sh`.
The password needs 8–63 characters.

**Stop:** press **Ctrl+C**. The script removes the access point, the DNS setting
and the port rule again, and reconnects the Wi-Fi you were on before.

> **Before you start:**
> - Stop any game server that is already running (`pkill -f server.py`). The
>   script refuses to start while port 8765 is in use.
> - The Wi-Fi card can be either an access point or connected to another
>   Wi-Fi, not both. **This computer loses its normal Wi-Fi/internet while the
>   access point runs.** A cable (Ethernet) connection keeps working.
> - If another Wi-Fi with the same name **Neongrid** is nearby (for example a
>   phone hotspot with that name), switch it off. Otherwise devices may join
>   the wrong one.

### C2.2 What players see

1. Join the Wi-Fi **Neongrid** with password **1234neongrid**.
2. After a few seconds the phone shows **"Sign in to network"** (Android) or opens
   a login window by itself (iPhone, iPad, Mac). Windows opens the browser.
   The game appears in it.
3. If nothing opens: start the browser and enter **10.42.0.1**, or any `http://`
   address. They all lead to the game.

Notes for players:

- **iPhone/iPad:** the login window works for playing. If it is closed with
  *Cancel*, choose **"Use Without Internet"** to stay on the Wi-Fi, then open
  Safari at `10.42.0.1`.
- **Android:** the phone may say "No internet". Choose **"Stay connected"**. If the
  page doesn't load in Chrome, switch off **mobile data**: Android otherwise
  sends browser traffic over mobile data, because the Wi-Fi "has no internet".
- **https:// addresses** (for example a bookmarked https://google.com) can't be
  redirected and just fail. Type `10.42.0.1` instead.
- On the game computer itself, open `http://10.42.0.1/` or `http://127.0.0.1:8765/`.

### C2.3 How the jump page works

| Step | What happens |
|---|---|
| 1 | NetworkManager switches the Wi-Fi card to access-point mode (2.4 GHz, WPA2) with the fixed address 10.42.0.1. Its built-in dnsmasq hands out addresses 10.42.0.x to the players (DHCP). |
| 2 | The extra file `/etc/NetworkManager/dnsmasq-shared.d/neongrid-portal.conf` makes dnsmasq answer **every** DNS name with 10.42.0.1. It also announces the portal address `http://10.42.0.1/` (DHCP option 114, RFC 8910). |
| 3 | An nftables table `neongrid_portal` forwards port 80 (normal web traffic) to the game server on port 8765. |
| 4 | Right after joining, every device tests for internet by loading a known page, e.g. `http://connectivitycheck.gstatic.com/generate_204` (Android) or `http://captive.apple.com/hotspot-detect.html` (Apple). Because of steps 2 and 3, the test reaches the game server. |
| 5 | The game server runs with `--portal http://10.42.0.1/`. It answers every request for a foreign host name with a redirect to the game. The device takes that as a login page and shows it: the game. |

All of this exists only while the script runs. `tests/portal_test.py` checks
step 5 for Android, Apple, Windows, Firefox and Linux connectivity checks.

### C2.4 Troubleshooting

| Problem | Cause / solution |
|---|---|
| `Port 8765 is already in use` | Another game server is running: `pkill -f server.py`, then start again. |
| `No Wi-Fi device found.` | Wi-Fi is hard-blocked (airplane mode switch / key) or there's no Wi-Fi card. `rfkill list` shows blocks. |
| Error at "Starting access point" | The card doesn't support AP mode (check with `/sbin/iw list`, it needs `* AP`), or `dnsmasq-base` is missing (`sudo apt install dnsmasq-base`). |
| Players see the Wi-Fi but can't connect | Wrong password (case-sensitive), or a very old device without WPA2 support. |
| Players get no IP address | A firewall blocks DHCP/DNS. If `ufw` is active: `sudo ufw allow in on <wifi-device>` (device name from `nmcli device`). |
| No jump page appears, but `10.42.0.1` works in the browser | Some devices only check after a while or remember the network as "no internet". Forget the Wi-Fi on the device and join again, or open `10.42.0.1` by hand. |
| The script was killed hard (e.g. power loss) | Clean up by hand: `sudo nft delete table ip neongrid_portal; sudo rm -f /etc/NetworkManager/dnsmasq-shared.d/neongrid-portal.conf; nmcli connection delete neongrid-ap`. Starting the script again also cleans up leftovers. |

---

## D. Public server with Apache

In this setup Apache delivers the web page and passes the API and game connection
on to the Python server, which runs as a systemd service under its own user.

```mermaid
flowchart LR
    B["Browser"] -- "HTTPS /neongrid/" --> A["Apache"]
    A -- "static files" --> W["/var/www/neongrid<br/>(copy of public/)"]
    A -- "/neongrid/api/ → http://127.0.0.1:8765/api/" --> S["neongrid.service<br/>server.py"]
    A -- "/neongrid/ws → ws://127.0.0.1:8765/ws" --> S
    S --> DB[("/var/lib/neongrid/<br/>neongrid.sqlite3")]
```

### D.1 Install (Debian/Ubuntu)

```bash
cd ~/ClaudeCode/neon-grid

# 1. system user without login, folders, code
sudo useradd --system --home /var/lib/neongrid --shell /usr/sbin/nologin neongrid
sudo mkdir -p /opt/neongrid /var/lib/neongrid /var/www/neongrid
sudo cp -r server /opt/neongrid/
sudo cp -r public/. /var/www/neongrid/
sudo chown neongrid:neongrid /var/lib/neongrid

# 2. Python environment
sudo apt install python3-venv
sudo python3 -m venv /opt/neongrid/.venv
sudo /opt/neongrid/.venv/bin/pip install -r /opt/neongrid/server/requirements.txt

# 3. game server as a service (starts automatically at boot)
sudo cp deploy/neongrid.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now neongrid
sudo systemctl status neongrid

# 4. Apache: paste deploy/neongrid-apache.conf into your <VirtualHost *:443>, then
sudo a2enmod proxy proxy_http proxy_wstunnel
sudo apachectl configtest && sudo systemctl reload apache2
```

The game is then at `https://your-domain/neongrid/`.

**Use HTTPS.** Passwords are sent when logging in. If your site doesn't have a
certificate yet, run `sudo apt install certbot python3-certbot-apache && sudo certbot --apache`.

### D.2 Update to a new version

```bash
cd ~/ClaudeCode/neon-grid
sudo cp server/server.py server/requirements.txt /opt/neongrid/server/
sudo /opt/neongrid/.venv/bin/pip install -r /opt/neongrid/server/requirements.txt
sudo cp -r public/. /var/www/neongrid/
sudo systemctl restart neongrid
```

Matches that are running during a restart are aborted and not counted.
Accounts and scores are kept.

### D.3 Operation

| Task | Command |
|---|---|
| Live log | `journalctl -u neongrid -f` |
| Status / restart | `sudo systemctl status neongrid` / `sudo systemctl restart neongrid` |
| Database | `/var/lib/neongrid/neongrid.sqlite3` |
| Backup (safe while running) | `sudo sqlite3 /var/lib/neongrid/neongrid.sqlite3 ".backup /root/neongrid-$(date +%F).sqlite3"` (needs `sudo apt install sqlite3`) |

The service is sandboxed (`ProtectSystem=strict` etc. in `deploy/neongrid.service`).
It can only write to `/var/lib/neongrid`.

---

## E. Reference

### Server options (`server.py`)

| Option | Default | Purpose |
|---|---|---|
| `--host` | `127.0.0.1` | network address to listen on; `0.0.0.0` = all (LAN, hotspot) |
| `--port` | `8765` | TCP port |
| `--db` | `neongrid.sqlite3` | database file (relative to the current folder) |
| `--static` | – | also serve this folder as the web page (no Apache needed) |
| `--countdown` | `10` | seconds of countdown before a match |
| `--portal` | – | captive portal: redirect requests for other host names to this URL (used by `neongrid-ap.sh`) |

### Meetup script (`scripts/lan-party.sh`) – environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SSID` | `NEON-GRID` | name of the Wi-Fi |
| `PASSWORD` | `lightcycle` | Wi-Fi password (min. 8 characters) |
| `PORT` | `8765` | game server port |

### Game access point (`scripts/neongrid-ap.sh`) – environment variables

| Variable | Default | Purpose |
|---|---|---|
| `SSID` | `Neongrid` | name of the Wi-Fi |
| `PASSWORD` | `1234neongrid` | Wi-Fi password (8–63 characters) |
| `PORT` | `8765` | internal game server port (players always use port 80) |

The address is fixed at `10.42.0.1`.

### Game settings (constants at the top of `server/server.py`)

| Constant | Value | Meaning |
|---|---|---|
| `GRID_W`, `GRID_H` | 120 × 80 | arena size in cells |
| `TICK_HZ_START`, `TICK_HZ_MAX` | 14, 22 | cells per second at the start / at most |
| `SPEEDUP_EVERY` | 120 | +1 cell/s every 120 ticks (about every 8 s) |
| `SESSION_SECONDS` | 30 days | how long a login stays valid |
| `MIN_PASSWORD` | 6 | minimum password length |

Restart the server after changing them.

### General troubleshooting

| Problem | Solution |
|---|---|
| `address already in use` | A server is already running: `pkill -f server.py`, or use `--port 9000`. |
| `ModuleNotFoundError: aiohttp` | You used the system Python. Use `.venv/bin/python` (or redo [section 0](#0-requirements-and-one-time-setup)). |
| "You opened the game in another window." | The same account is logged in elsewhere. Use a second account for the second window. |
| "Too many failed logins" | 10 wrong passwords within 10 minutes from one IP. Wait 10 minutes, or restart the server. |
| Reset all accounts and highscores | Stop the server, then `rm server/neongrid.sqlite3*`. |
