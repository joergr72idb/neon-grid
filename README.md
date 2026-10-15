# NEON GRID

Browser game for two players: light-cycle duels on a neon grid, steered with the
arrow keys (or WASD). Players create an account, every match is counted, and the
start screen shows a highscore list.

- **public/** – the web page (HTML5 canvas, served by Apache)
- **server/** – Python game server (accounts, matchmaking, game logic, SQLite)
- **deploy/** – Apache snippet and systemd service
- **tests/smoke_test.py** – end-to-end test (two players, two matches)

## How a match works

1. The first player presses *Enter the grid* and waits.
2. As soon as a second player joins, a countdown runs from 10 to 0.
3. Whoever hits a wall or a light trail loses. Head-on crash = draw.
   Leaving a running match counts as a loss. The cycles get faster over time.

## Try it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
cd server && ../.venv/bin/python server.py --static ../public
```

Open http://127.0.0.1:8765/ in two browser windows with a different account in each
(e.g. one normal and one private window).

Run the test: `.venv/bin/python tests/smoke_test.py`

## Meetup mode: own Wi-Fi hotspot

Phone via USB with *USB tethering* on, then:

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
