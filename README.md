# NEON GRID

Browser game for two players: light-cycle duels on a neon grid, steered with the
arrow keys (or WASD) on a computer, or by swiping / an on-screen arrow pad on
phones and tablets. Players create an account, every match is counted, and the
start screen shows a highscore list.

- **public/** – the web page (HTML5 canvas, served by Apache)
- **server/** – Python game server (accounts, matchmaking, game logic, SQLite)
- **deploy/** – Apache snippet and systemd service
- **tests/smoke_test.py** – end-to-end test (two players, two matches)
- **docs/** – documentation (see below)

## Documentation

| Document | Content |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Setup guide: local, home network, **meetup hotspot** (step by step, with troubleshooting), Apache server, reference of all options |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the program is built: components, match sequence, HTTP API, WebSocket messages, database, security |
| [docs/PAP.md](docs/PAP.md) | Programmablaufplan: DIN 66001 flowcharts of all important functions |

The source code itself is commented for readers who are new to it. Start with the
docstring at the top of `server/server.py` and the header of `public/game.js`.

## How a match works

1. The first player presses *Enter the grid* and waits.
2. As soon as a second player joins, a countdown runs from 10 to 0.
3. Whoever hits a wall or a light trail loses. Head-on crash = draw.
   Leaving a running match counts as a loss. The cycles get faster over time.

## Start the server locally (shell)

### One-time setup

Only needed the first time (or after deleting `.venv/`):

```bash
cd ~/ClaudeCode/neon-grid
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
```

### Start

```bash
cd ~/ClaudeCode/neon-grid/server
../.venv/bin/python server.py --static ../public
```

`--static ../public` makes the game server deliver the web page itself, so you don't
need Apache locally. When it is running you'll see `Running on http://127.0.0.1:8765`.
The server stays in the foreground and logs to this terminal.

Then open **http://127.0.0.1:8765/** in two browser windows, with a different
account in each (e.g. one normal and one private window), and press *Enter the grid*
in both.

### Stop

Press **Ctrl+C** in the terminal where the server runs.

### Useful options

| Option | Default | Purpose |
|---|---|---|
| `--port 9000` | `8765` | use a different port |
| `--host 0.0.0.0` | `127.0.0.1` | also reachable from other machines in the LAN (`http://<your-ip>:8765/`) |
| `--countdown 3` | `10` | shorter countdown before a match (handy for testing) |
| `--db test.sqlite3` | `neongrid.sqlite3` | use a separate database for accounts/scores |

Example for quick testing: `../.venv/bin/python server.py --static ../public --countdown 3`

### Run in the background

```bash
cd ~/ClaudeCode/neon-grid/server
nohup ../.venv/bin/python server.py --static ../public > neongrid.log 2>&1 &
tail -f neongrid.log            # watch the log (Ctrl+C ends only tail)
pkill -f "server.py --static"   # stop the server
```

### Troubleshooting

- **`address already in use`**: a server is already running. Stop it with
  `pkill -f server.py` or pick another port with `--port`.
- **`ModuleNotFoundError: aiohttp`**: you used the system Python. Use
  `../.venv/bin/python` (or repeat the one-time setup).
- **Reset accounts and highscores**: stop the server, then
  `rm server/neongrid.sqlite3*`.

### Test

```bash
cd ~/ClaudeCode/neon-grid
.venv/bin/python tests/smoke_test.py
```

## Meetup mode: own Wi-Fi hotspot

Phone via USB with *USB tethering* on (full step-by-step guide with
requirements and troubleshooting: [docs/SETUP.md, section C](docs/SETUP.md#c-meetup-mode-own-wi-fi-hotspot)), then:

```bash
scripts/lan-party.sh                               # Wi-Fi NEON-GRID / lightcycle
SSID=LUG-GRID PASSWORD=secret123 scripts/lan-party.sh
```

The notebook becomes an access point with internet from the phone, and the game runs
at the address the script prints (usually http://10.42.0.1:8765/). Ctrl+C stops
everything and reconnects your normal Wi-Fi. Scores are kept in `server/neongrid.sqlite3`.

## Install on the Apache server (Debian/Ubuntu)

```bash
# 1. user, folders, code
sudo useradd --system --home /var/lib/neongrid --shell /usr/sbin/nologin neongrid
sudo mkdir -p /opt/neongrid /var/lib/neongrid /var/www/neongrid
sudo cp -r server /opt/neongrid/
sudo cp -r public/. /var/www/neongrid/
sudo chown neongrid:neongrid /var/lib/neongrid

# 2. Python environment
sudo apt install python3-venv
sudo python3 -m venv /opt/neongrid/.venv
sudo /opt/neongrid/.venv/bin/pip install -r /opt/neongrid/server/requirements.txt

# 3. game server as a service
sudo cp deploy/neongrid.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now neongrid
sudo systemctl status neongrid

# 4. Apache: paste deploy/neongrid-apache.conf into your <VirtualHost>, then
sudo a2enmod proxy proxy_http proxy_wstunnel
sudo apachectl configtest && sudo systemctl reload apache2
```

The game is then at `https://your-domain/neongrid/`. Use HTTPS, because passwords
are sent at login.

Logs: `journalctl -u neongrid -f`. Database: `/var/lib/neongrid/neongrid.sqlite3`
(back this file up to keep accounts and scores).

## Notes

- Passwords are stored as salted scrypt hashes; login and registration are rate-limited.
- The font (Orbitron, SIL Open Font License) is served from your own server, so the
  page makes no requests to Google, which keeps it GDPR-friendly.
- The design is an original 80s-neon style. It deliberately uses no name, logo,
  font or imagery from the TRON films.
