# NEON GRID

**A light-cycle duel for two players, right in the browser.**

Two cycles race across a neon grid and leave a glowing light trail behind
them. Whoever hits a wall or a trail first loses. No app or plug-in is
needed: laptops play with the arrow keys or W A S D, phones and tablets by
swiping or with an on-screen arrow pad. Every match counts for the shared
highscore list.

NEON GRID is made for places where people meet: a LAN party, a user group,
a retro computer show, or just two browser windows at home. The notebook
can even open its own Wi-Fi, so players only join it and the game opens by
itself.

## Features

- **Two-player online duels** with matchmaking: the first player waits,
  the second one starts the match. A countdown runs, then the cycles get
  faster over time.
- **Accounts and highscores** (wins, losses, draws) stored in SQLite.
- **Fair by design:** the server is the referee. Browsers only send
  steering commands, so nobody can cheat by changing the JavaScript, and
  both players always see the same game.
- **Keyboard and touch:** arrow keys/WASD, swipe, or the arrow pad (below
  the arena in portrait, beside it in landscape).
- **Four ways to run it**, from one computer up to a public web server
  (see below).
- **Offline game access point:** the computer becomes a Wi-Fi access point
  without internet, and joining phones open the game by themselves, like a
  hotel Wi-Fi login page.
- **Privacy-friendly:** the font is served locally (no Google requests),
  passwords are stored as salted scrypt hashes, and login and registration
  are rate-limited.

## How a match works

1. Log in (or create an account) and press **Enter the grid**.
2. As soon as a second player joins, a countdown runs from 10 to 0.
3. Steer around the arena. Hitting a wall or any light trail loses,
   a head-on crash is a draw, and leaving a running match counts as a loss.
   The cycles speed up as the match goes on.

## Quick start (one computer)

Needs Linux with Python 3.9 or newer (`sudo apt install git python3 python3-venv`).

```bash
cd neon-grid
python3 -m venv .venv                              # one time only
.venv/bin/pip install -r server/requirements.txt   # one time only

cd server
../.venv/bin/python server.py --static ../public
```

Open **http://127.0.0.1:8765/** in two browser windows (for example a
normal and a private window), create a different account in each, and
press *Enter the grid* in both. **Ctrl+C** stops the server.

Tip for testing: add `--countdown 3` for a shorter countdown.

## Ways to run it

| Mode | Who can play | Command | Guide |
|---|---|---|---|
| **Local** | you, in two browser windows | `server.py --static ../public` | [SETUP A](docs/SETUP.md#a-local-on-one-computer) |
| **Home network** | everyone on your Wi-Fi/LAN | `server.py --static ../public --host 0.0.0.0` | [SETUP B](docs/SETUP.md#b-home-network-lan) |
| **Game access point** | everyone near the computer, no internet needed, game opens by itself; Wi-Fi card found automatically | `scripts/neongrid-ap.sh` | [SETUP C](docs/SETUP.md#c-game-access-point-own-wi-fi-jump-page) |
| **Public server** | everyone on the internet, Apache + HTTPS | systemd service | [INSTALL-DEBIAN, part 3](docs/INSTALL-DEBIAN.md#part-3--public-web-server-apache--https) |

Setting up a new machine from scratch? Follow
**[docs/INSTALL-DEBIAN.md](docs/INSTALL-DEBIAN.md)**: it goes from a freshly
installed Debian to a running game, step by step.

## Documentation

| Document | Content |
|---|---|
| [docs/INSTALL-DEBIAN.md](docs/INSTALL-DEBIAN.md) | **Fresh Debian install:** packages, getting the code, game access point notebook, public Apache server with HTTPS, updates, backups, uninstall |
| [docs/SETUP.md](docs/SETUP.md) | All operating modes in detail, with troubleshooting tables and a reference of every option |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the program is built: components, match sequence, HTTP API, WebSocket messages, database, security |
| [docs/PAP.md](docs/PAP.md) | Programmablaufplan: DIN 66001 flowcharts of all important functions |

The source code is commented for readers who are new to it. Start with the
docstring at the top of `server/server.py` and the header of `public/game.js`.

## Project layout

```
neon-grid/
├── public/              web page: index.html, style.css, game.js, fonts/ (Orbitron, OFL)
├── server/
│   ├── server.py        game server: accounts, matchmaking, game logic (Python + aiohttp)
│   └── requirements.txt
├── scripts/
│   └── neongrid-ap.sh   game access point (own Wi-Fi, captive-portal jump page)
├── deploy/
│   ├── neongrid.service       systemd unit (sandboxed, own user)
│   └── neongrid-apache.conf   Apache proxy snippet for /neongrid/
├── tests/
│   ├── smoke_test.py    two accounts, two full matches over WebSocket
│   └── portal_test.py   captive-portal redirects (Android, Apple, Windows, Firefox, Linux)
└── docs/                see "Documentation"
```

The database `server/neongrid.sqlite3` is created on the first start and is
not part of git (`.gitignore`). Delete it to reset all accounts and scores.

## Tests

```bash
.venv/bin/python tests/smoke_test.py    # last line: "OK – …"
.venv/bin/python tests/portal_test.py   # last line: "OK – captive portal redirects 6 connectivity checks"
```

The tests use a temporary database and never touch your real scores.

## Technology

- **Server:** Python 3 with [aiohttp](https://docs.aiohttp.org/): one process,
  one asyncio event loop, HTTP JSON API plus a WebSocket for the live game,
  SQLite for accounts and matches.
- **Client:** plain HTML5, CSS and JavaScript with `<canvas>`, no framework
  and no build step.
- **Networking extras:** NetworkManager (`nmcli`) for the access point, dnsmasq and nftables for the captive portal.
- **Tested on** Debian 13 (Python 3.13) and Ubuntu.

## Notes

- The design is an original 80s neon style. It deliberately uses no name,
  logo, font or imagery from the TRON films.
- The font Orbitron is licensed under the SIL Open Font License
  (`public/fonts/OFL.txt`).

## AI notice

This project was developed with AI assistance (Claude). The game page
shows a corresponding notice in its footer.
