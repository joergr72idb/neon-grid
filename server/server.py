#!/usr/bin/env python3
"""NEON GRID game server: accounts, highscores and real-time light-cycle matches.

Apache serves the static files in public/ and proxies /api/ and /ws to this
server. For local testing, pass --static ../public to serve everything here.

Overview of how the pieces fit together
---------------------------------------
* ``DB``          – thin wrapper around a SQLite file: users, login sessions,
                    finished matches and the highscore query.
* ``RateLimiter`` – slows down password guessing and account spam.
* ``Conn``        – one browser that is connected via WebSocket and logged in.
* ``Lobby``       – pairs players: the first one waits, the second one starts
                    a ``Match`` with them.
* ``Match``       – the actual game. The server is the single source of truth:
                    it moves both cycles, detects crashes and tells both
                    browsers what happened ("authoritative server"). Browsers
                    only send steering commands and draw what they are told.
* Captive portal  – optional (--portal): requests for any other host name
                    are redirected to the game, so phones joining the game
                    access point open it automatically (scripts/neongrid-ap.sh).
* HTTP handlers   – ``/api/register``, ``/api/login``, ``/api/logout``,
                    ``/api/me``, ``/api/highscores`` (JSON) and ``/ws``
                    (WebSocket for the live game).

Everything runs in one process on one asyncio event loop, so no locks are
needed: code between two ``await`` statements is never interrupted.
See docs/ARCHITECTURE.md for the message protocol and docs/PAP.md for
flowcharts.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import WSMsgType, web

# --- Game and account settings ------------------------------------------------
# The arena is a grid of GRID_W x GRID_H cells. Each cycle moves exactly one
# cell per server "tick".
GRID_W, GRID_H = 120, 80
TICK_HZ_START, TICK_HZ_MAX = 14, 22   # the cycles speed up as the match goes on
SPEEDUP_EVERY = 120                    # ticks per +1 Hz
SESSION_SECONDS = 30 * 24 * 3600       # a login stays valid for 30 days
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,16}$")
MIN_PASSWORD = 6
MAX_PASSWORD = 128

# Direction name -> (dx, dy) step on the grid. y grows downwards, like on screen.
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
# Used to forbid 180° turns (they would drive straight into your own trail).
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}

log = logging.getLogger("neongrid")

# Database layout. "IF NOT EXISTS" makes it safe to run on every start: a new
# database file gets the tables, an existing one is left untouched.
#   users    – one row per account, including the win/loss/draw counters
#   sessions – login tokens (only their SHA-256 hash is stored, see hash_token)
#   matches  – history of every finished match; winner_id NULL means draw
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    pw_salt     BLOB NOT NULL,
    pw_hash     BLOB NOT NULL,
    wins        INTEGER NOT NULL DEFAULT 0,
    losses      INTEGER NOT NULL DEFAULT 0,
    draws       INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    id          INTEGER PRIMARY KEY,
    player1_id  INTEGER NOT NULL REFERENCES users(id),
    player2_id  INTEGER NOT NULL REFERENCES users(id),
    winner_id   INTEGER REFERENCES users(id),
    reason      TEXT NOT NULL,
    ticks       INTEGER NOT NULL,
    played_at   INTEGER NOT NULL
);
"""


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

def hash_password(password, salt):
    """Turn a password into a 32-byte hash with scrypt.

    scrypt is deliberately slow and memory-hungry, which makes guessing
    passwords from a stolen database expensive. The random salt makes equal
    passwords produce different hashes. Takes ~50 ms, so callers run it in a
    worker thread to keep the game loop smooth.
    """
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def hash_token(token):
    """Hash a session token before it is stored or looked up.

    The browser keeps the real token; the database only knows its hash. Someone
    who reads the database file therefore cannot log in as another user.
    (A fast hash is fine here: tokens are long random strings, not passwords.)
    """
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(row):
    """The part of a user row that may be sent to browsers (no password data)."""
    return {
        "id": row["id"],
        "username": row["username"],
        "wins": row["wins"],
        "losses": row["losses"],
        "draws": row["draws"],
        "played": row["wins"] + row["losses"] + row["draws"],
    }


class DB:
    """All database access. Every method is a short, synchronous SQLite call."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row   # rows behave like dicts: row["username"]
        # WAL mode: readers don't block the writer (creates the -wal/-shm side files).
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def get_user(self, user_id):
        return self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def get_user_by_name(self, username):
        # COLLATE NOCASE on the column makes this lookup case-insensitive.
        return self.conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def create_user(self, username, salt, pw_hash):
        # "with self.conn" = one transaction: committed on success, rolled back on error.
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO users (username, pw_salt, pw_hash, created_at) VALUES (?, ?, ?, ?)",
                (username, salt, pw_hash, int(time.time())))
        return self.get_user(cur.lastrowid)

    def create_session(self, user_id):
        """Log a user in: create a random token, store its hash, return the token."""
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.conn:
            # Housekeeping: drop expired sessions of all users while we are here.
            self.conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            self.conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (hash_token(token), user_id, now + SESSION_SECONDS))
        return token

    def user_by_token(self, token):
        """Return the user a (still valid) session token belongs to, else None."""
        if not isinstance(token, str) or not token:
            return None
        return self.conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at >= ?",
            (hash_token(token), int(time.time()))).fetchone()

    def delete_session(self, token):
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))

    def record_match(self, p1_id, p2_id, winner_id, reason, ticks):
        """Save a finished match and update both players' counters in one transaction."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO matches (player1_id, player2_id, winner_id, reason, ticks, played_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (p1_id, p2_id, winner_id, reason, ticks, int(time.time())))
            if winner_id is None:
                self.conn.execute(
                    "UPDATE users SET draws = draws + 1 WHERE id IN (?, ?)", (p1_id, p2_id))
            else:
                loser_id = p2_id if winner_id == p1_id else p1_id
                self.conn.execute("UPDATE users SET wins = wins + 1 WHERE id = ?", (winner_id,))
                self.conn.execute("UPDATE users SET losses = losses + 1 WHERE id = ?", (loser_id,))

    def highscores(self, limit=10):
        """Top players (most wins first, then fewest losses) and the total match count.

        Accounts that never played are left out of the list.
        """
        rows = self.conn.execute(
            "SELECT * FROM users WHERE wins + losses + draws > 0 "
            "ORDER BY wins DESC, losses ASC, (wins + losses + draws) DESC, username "
            "LIMIT ?", (limit,)).fetchall()
        total = self.conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        return [public_user(r) for r in rows], total


class RateLimiter:
    """Allows `limit` hits per key within `window` seconds.

    The key is the client's IP address. For every key we keep a queue of
    timestamps; old ones fall out of the window. Checking (`allowed`) and
    counting (`hit`) are separate so that e.g. only *failed* logins count.
    State lives in memory only and is reset when the server restarts.
    """

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self.hits = defaultdict(deque)

    def allowed(self, key):
        q = self.hits[key]
        now = time.monotonic()
        while q and q[0] < now - self.window:
            q.popleft()
        return len(q) < self.limit

    def hit(self, key):
        self.hits[key].append(time.monotonic())


# --------------------------------------------------------------------------- #
# Game
# --------------------------------------------------------------------------- #

class Conn:
    """One logged-in WebSocket connection.

    While the player is in a match, `match` points to it and `index` says
    which player they are there (0 = cyan/left, 1 = orange/right).
    """

    def __init__(self, ws, user):
        self.ws = ws
        self.user_id = user["id"]
        self.username = user["username"]
        self.match = None
        self.index = None

    async def send(self, obj):
        """Send a JSON message; silently ignore browsers that are already gone."""
        if self.ws.closed:
            return
        try:
            await self.ws.send_str(json.dumps(obj))
        except (ConnectionResetError, RuntimeError):
            pass


class Match:
    """A running duel between two connections.

    Game state:
      occupied – one byte per grid cell, 1 = a light trail (or start point) is there
      pos      – current head position (x, y) of each cycle
      dir      – current driving direction of each cycle
      queue    – turns a player pressed that are not applied yet (max. 3).
                 Queueing lets fast double-taps like "up, left" both count even
                 if they arrive within the same tick.
    """

    def __init__(self, lobby, a, b, countdown):
        self.lobby = lobby
        self.players = [a, b]
        self.countdown = countdown
        self.done = False
        self.running = False
        self.tick = 0
        self.occupied = bytearray(GRID_W * GRID_H)   # cell (x, y) is at index y * GRID_W + x
        # Player 0 starts on the left quarter facing right, player 1 mirrored on the right.
        self.pos = [(GRID_W // 4, GRID_H // 2), (GRID_W - 1 - GRID_W // 4, GRID_H // 2)]
        self.dir = ["right", "left"]
        self.queue = [deque(), deque()]
        for x, y in self.pos:
            self.occupied[y * GRID_W + x] = 1
        for i, p in enumerate(self.players):
            p.match, p.index = self, i

    async def broadcast(self, obj):
        """Send the same message to both players at the same time."""
        await asyncio.gather(*(p.send(obj) for p in self.players))

    def turn(self, index, direction):
        """Queue a steering command from player `index`.

        Ignored if it would not change anything (same direction), would be a
        180° turn, or if 3 turns are already waiting.
        """
        if self.done:
            return
        q = self.queue[index]
        # Compare with the last *queued* direction, not the current one, so that
        # e.g. "up" then "left" while driving right is accepted as two turns.
        last = q[-1] if q else self.dir[index]
        if direction in (last, OPPOSITE[last]) or len(q) >= 3:
            return
        q.append(direction)

    def step(self):
        """Advance the game by one tick: move both cycles one cell and detect crashes.

        Both cycles move *simultaneously*: first both new head positions are
        computed, then both are checked, and only then the trails are written.
        That way neither player has an advantage from being processed first.
        Returns the new heads and which cycle crashed, e.g. ([(31, 40), (88, 40)], [False, True]).
        """
        heads = []
        for i in (0, 1):
            if self.queue[i]:
                self.dir[i] = self.queue[i].popleft()   # apply at most one turn per tick
            dx, dy = DIRS[self.dir[i]]
            heads.append((self.pos[i][0] + dx, self.pos[i][1] + dy))
        crashed = [False, False]
        for i, (x, y) in enumerate(heads):
            # Crash = left the arena, or the cell already holds a light trail.
            if not (0 <= x < GRID_W and 0 <= y < GRID_H) or self.occupied[y * GRID_W + x]:
                crashed[i] = True
        if heads[0] == heads[1]:
            # Both drive into the same empty cell: head-on collision, both lose.
            crashed = [True, True]
        for i, (x, y) in enumerate(heads):
            if not crashed[i]:
                self.occupied[y * GRID_W + x] = 1
                self.pos[i] = (x, y)
        self.tick += 1
        return heads, crashed

    async def run(self):
        """The life of a match: announce it, count down, then run the game loop.

        Runs as its own asyncio task (started by Lobby.join).
        """
        try:
            # 1. Tell each player who they are and where everybody starts.
            users = [self.lobby.db.get_user(p.user_id) for p in self.players]
            for i, p in enumerate(self.players):
                await p.send({
                    "type": "match",
                    "you": i,
                    "grid": [GRID_W, GRID_H],
                    "players": [public_user(u) for u in users],
                    "starts": self.pos,
                    "dirs": self.dir,
                })
            # 2. Countdown, one message per second. A player may leave meanwhile
            #    (then finish() has already set done and we just stop).
            for n in range(self.countdown, 0, -1):
                await self.broadcast({"type": "countdown", "n": n})
                await asyncio.sleep(1)
                if self.done:
                    return
            self.running = True
            await self.broadcast({"type": "countdown", "n": 0})   # 0 = "Go!"

            # 3. Game loop: one step per tick until somebody crashes.
            #    next_t is the planned time of the next tick. Planning ahead
            #    (instead of simply sleeping 1/hz) keeps the speed steady even
            #    if sending messages took a few milliseconds.
            loop = asyncio.get_running_loop()
            next_t = loop.time()
            while not self.done:
                heads, crashed = self.step()
                await self.broadcast({"type": "tick", "t": self.tick,
                                      "heads": heads, "crashed": crashed})
                if any(crashed):
                    # Exactly one crashed -> the other one wins; both -> draw (None).
                    winner = None if all(crashed) else (1 if crashed[0] else 0)
                    await self.finish(winner, "crash")
                    return
                # Speed: starts at 14 ticks/s, +1 every 120 ticks, at most 22.
                hz = min(TICK_HZ_MAX, TICK_HZ_START + self.tick // SPEEDUP_EVERY)
                next_t += 1 / hz
                await asyncio.sleep(max(0.0, next_t - loop.time()))
        except Exception:
            # A bug must never leave two players stuck: end the match as "error"
            # (not recorded in the statistics).
            log.exception("match crashed")
            await self.finish(None, "error")

    async def forfeit(self, index):
        """Player `index` left (closed the tab, lost connection): the other one wins."""
        await self.finish(1 - index, "forfeit")

    async def finish(self, winner_index, reason):
        """End the match exactly once, store the result and tell both players.

        winner_index: 0 or 1, or None for a draw.
        reason: "crash", "forfeit" or "error".
        """
        if self.done:
            return
        self.done = True     # also stops the countdown / game loop in run()
        self.lobby.matches.discard(self)
        a, b = self.players
        winner_id = None if winner_index is None else self.players[winner_index].user_id
        if reason != "error":
            self.lobby.db.record_match(a.user_id, b.user_id, winner_id, reason, self.tick)
        log.info("match %s vs %s: winner=%s reason=%s ticks=%d",
                 a.username, b.username,
                 None if winner_index is None else self.players[winner_index].username,
                 reason, self.tick)
        for i, p in enumerate(self.players):
            p.match = p.index = None   # player is free to join the next match
            if winner_index is None:
                result = "draw"
            else:
                result = "win" if i == winner_index else "lose"
            # Send the updated statistics along so the menu can show them at once.
            await p.send({
                "type": "end",
                "result": result,
                "reason": reason,
                "me": public_user(self.lobby.db.get_user(p.user_id)),
            })


class Lobby:
    """The first player to press PLAY waits; the next one starts a match.

    There is at most one waiting player at any time (simple first-come,
    first-served matchmaking). Each account may be connected only once:
    opening the game in a second window "kicks" the first one.
    """

    def __init__(self, db, countdown):
        self.db = db
        self.countdown = countdown
        self.waiting = None       # Conn of the player waiting for an opponent; change it only via set_waiting()
        self.active = {}          # user_id -> Conn
        self.matches = set()      # running matches (keeps a reference to their tasks' objects)
        self._tasks = set()       # background "lobby" broadcasts still being sent

    def lobby_state(self):
        """Message telling a browser who is waiting for a battle (None = nobody)."""
        name = self.waiting.username if self.waiting is not None else None
        return {"type": "lobby", "waiting": name}

    def set_waiting(self, conn):
        """Change the waiting player and tell everyone online, so players in
        the menu see "<name> is waiting for a battle" and can join at once.

        The change itself happens immediately (no await), so two players
        pressing PLAY at the same moment can never both become "waiting".
        Only the sending runs in the background.
        """
        if self.waiting is conn:
            return
        self.waiting = conn
        msg = self.lobby_state()
        task = asyncio.create_task(self._send_all(msg))
        self._tasks.add(task)                       # keep a reference until it is done
        task.add_done_callback(self._tasks.discard)

    async def _send_all(self, msg):
        await asyncio.gather(*(c.send(msg) for c in list(self.active.values())))

    async def connect(self, conn):
        """Register a freshly logged-in connection; close an older one of the same user."""
        old = self.active.get(conn.user_id)
        self.active[conn.user_id] = conn
        if old is not None:
            await old.send({"type": "kicked"})
            await old.ws.close()   # its handler then calls disconnect() -> forfeit if in a match

    async def disconnect(self, conn):
        """Clean up after a closed connection. Leaving a running match = forfeit."""
        if self.active.get(conn.user_id) is conn:
            del self.active[conn.user_id]
        if self.waiting is conn:
            self.set_waiting(None)
        if conn.match is not None:
            await conn.match.forfeit(conn.index)

    async def join(self, conn):
        """Player pressed 'Enter the grid': wait, or start a match with the waiting player."""
        if conn.match is not None or self.waiting is conn:
            return   # already playing or already waiting: ignore double clicks
        other = self.waiting
        # Forget a waiting player whose connection died, or who is the same
        # account (you cannot play against yourself).
        if other is not None and (other.ws.closed or other.user_id == conn.user_id):
            other = None
        if other is None:
            self.set_waiting(conn)
            await conn.send({"type": "waiting"})
            return
        # Opponent found. The one who waited becomes player 0 (cyan, left).
        self.set_waiting(None)
        match = Match(self, other, conn, self.countdown)
        self.matches.add(match)
        asyncio.create_task(match.run())   # runs in the background; join() returns at once

    async def leave(self, conn):
        """Player pressed 'Cancel' while waiting."""
        if self.waiting is conn:
            self.set_waiting(None)
            await conn.send({"type": "idle"})


# --------------------------------------------------------------------------- #
# HTTP / WebSocket handlers
# --------------------------------------------------------------------------- #

def json_error(status, message):
    """Error response the browser shows as-is, e.g. {"error": "Wrong username or password."}."""
    return web.json_response({"error": message}, status=status)


def client_ip(request):
    """The player's IP address, used as the rate-limit key.

    Behind Apache every request comes from 127.0.0.1, so in that case we take
    the address Apache appended to X-Forwarded-For (the last entry, the only
    one Apache vouches for). The header is ignored for direct connections,
    because anybody could fake it.
    """
    peer = request.remote or ""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded and peer in ("127.0.0.1", "::1"):
        return forwarded.split(",")[-1].strip()
    return peer


def bearer_token(request):
    """Read the session token from an 'Authorization: Bearer <token>' header."""
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


async def read_credentials(request):
    """Parse {"username": ..., "password": ...} from the request body.

    Returns (None, None) if the body is not a JSON object.
    """
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    return username, password


async def register(request):
    """POST /api/register – create an account and log it in right away.

    Max. 5 new accounts per IP address and hour.
    """
    app = request.app
    ip = client_ip(request)
    if not app["register_limit"].allowed(ip):
        return json_error(429, "Too many new accounts from your network. Try again later.")
    username, password = await read_credentials(request)
    if username is None:
        return json_error(400, "Invalid request.")
    if not USERNAME_RE.match(username):
        return json_error(400, "Username: 3–16 characters, letters, digits, _ or -.")
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        return json_error(400, f"Password must be at least {MIN_PASSWORD} characters.")
    db = app["db"]
    if db.get_user_by_name(username):
        return json_error(409, "That username is already taken.")
    salt = secrets.token_bytes(16)
    pw_hash = await asyncio.to_thread(hash_password, password, salt)
    try:
        user = db.create_user(username, salt, pw_hash)
    except sqlite3.IntegrityError:
        # Two registrations for the same name raced each other during hashing.
        return json_error(409, "That username is already taken.")
    app["register_limit"].hit(ip)
    return web.json_response({"token": db.create_session(user["id"]), "user": public_user(user)})


async def login(request):
    """POST /api/login – check the password and hand out a session token.

    Max. 10 failed attempts per IP address within 10 minutes.
    """
    app = request.app
    ip = client_ip(request)
    if not app["login_limit"].allowed(ip):
        return json_error(429, "Too many failed logins. Wait a few minutes.")
    username, password = await read_credentials(request)
    if username is None:
        return json_error(400, "Invalid request.")
    db = app["db"]
    user = db.get_user_by_name(username)
    # Hash even for unknown usernames (with a throwaway salt), so the answer
    # takes the same time either way and does not reveal which names exist.
    salt = user["pw_salt"] if user else secrets.token_bytes(16)
    candidate = await asyncio.to_thread(hash_password, password[:MAX_PASSWORD], salt)
    # compare_digest takes the same time no matter where the bytes differ.
    if user is None or not hmac.compare_digest(candidate, user["pw_hash"]):
        app["login_limit"].hit(ip)
        return json_error(401, "Wrong username or password.")
    return web.json_response({"token": db.create_session(user["id"]), "user": public_user(user)})


async def logout(request):
    """POST /api/logout – invalidate the session token (always answers ok)."""
    token = bearer_token(request)
    if token:
        request.app["db"].delete_session(token)
    return web.json_response({"ok": True})


async def me(request):
    """GET /api/me – who am I? Used by the page on load to restore a saved login."""
    user = request.app["db"].user_by_token(bearer_token(request))
    if user is None:
        return json_error(401, "Not logged in.")
    return web.json_response({"user": public_user(user)})


async def highscores(request):
    """GET /api/highscores – top 10 and the number of matches ever played (no login needed)."""
    rows, total = request.app["db"].highscores()
    return web.json_response({"highscores": rows, "total_matches": total})


async def websocket(request):
    """GET /ws – the live game connection of one browser.

    The first message must be {"type": "auth", "token": ...}; everything sent
    before that is ignored. After a successful login the browser may send
    "join", "leave" and "turn". Unknown or malformed messages are ignored.
    When the loop ends (browser closed, network gone, kicked), the `finally`
    block makes sure the lobby forgets the player (and a running match is
    forfeited).
    """
    app = request.app
    lobby = app["lobby"]
    # heartbeat=20: ping every 20 s so dead connections (e.g. phone left the
    # Wi-Fi) are noticed. max_msg_size: our messages are tiny; reject big ones.
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
    await ws.prepare(request)
    conn = None
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            kind = data.get("type")
            if conn is None:
                # Not logged in yet: only "auth" is accepted.
                if kind != "auth":
                    continue
                user = app["db"].user_by_token(data.get("token"))
                if user is None:
                    await ws.send_str(json.dumps({"type": "auth_failed"}))
                    break
                conn = Conn(ws, user)
                await lobby.connect(conn)
                await conn.send({"type": "auth_ok", "user": public_user(user)})
                await conn.send(lobby.lobby_state())   # is someone waiting right now?
            elif kind == "join":
                await lobby.join(conn)
            elif kind == "leave":
                await lobby.leave(conn)
            elif kind == "turn":
                direction = data.get("dir")
                if conn.match is not None and direction in DIRS:
                    conn.match.turn(conn.index, direction)
    finally:
        if conn is not None:
            await lobby.disconnect(conn)
    return ws


def captive_portal_middleware(portal_url):
    """Redirect every request for a foreign host name to the game (captive portal).

    On the game access point (scripts/neongrid-ap.sh) the DNS server answers
    *every* name with this computer's address. When a phone joins the Wi-Fi it
    checks for internet by loading a well-known page, e.g.
    http://connectivitycheck.gstatic.com/generate_204 (Android) or
    http://captive.apple.com/hotspot-detect.html (Apple). That request arrives
    here with a foreign Host header. Answering with a redirect instead of the
    expected reply makes the phone think it found a login page ("captive
    portal") and open it: the game appears by itself.

    Requests for the portal address itself or for localhost are served normally.
    """
    allowed = {urlsplit(portal_url).hostname, "localhost", "127.0.0.1", "::1"}

    @web.middleware
    async def middleware(request, handler):
        if request.url.host not in allowed:
            # no-store: devices must not cache the redirect and repeat it after
            # the access point is gone.
            raise web.HTTPFound(portal_url, headers={"Cache-Control": "no-store"})
        return await handler(request)

    return middleware


def make_app(db_path, static_dir=None, countdown=10, portal=None):
    """Build the web application: database, lobby, rate limits and URL routes.

    Separate from main() so tests can create an app with a temporary database.
    portal: URL of the game (e.g. "http://10.42.0.1/") to switch on the
    captive-portal redirect, see captive_portal_middleware.
    """
    middlewares = [captive_portal_middleware(portal)] if portal else []
    # requests are small; reject >16 KB
    app = web.Application(client_max_size=16 * 1024, middlewares=middlewares)
    db = DB(db_path)
    app["db"] = db
    app["lobby"] = Lobby(db, countdown)
    app["login_limit"] = RateLimiter(limit=10, window=600)      # 10 failed logins / 10 min
    app["register_limit"] = RateLimiter(limit=5, window=3600)   # 5 new accounts / hour
    app.router.add_post("/api/register", register)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/me", me)
    app.router.add_get("/api/highscores", highscores)
    app.router.add_get("/ws", websocket)
    if static_dir:
        # Without Apache (local play, game access point) this server also delivers the page.
        static_dir = Path(static_dir).resolve()

        async def index(_request):
            return web.FileResponse(static_dir / "index.html")

        app.router.add_get("/", index)
        app.router.add_static("/", static_dir)
    return app


def main():
    """Command-line entry point. See `python server.py --help` for the options."""
    parser = argparse.ArgumentParser(description="NEON GRID game server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="neongrid.sqlite3", help="SQLite database file")
    parser.add_argument("--static", help="also serve this folder (local testing without Apache)")
    parser.add_argument("--countdown", type=int, default=10, help="countdown seconds before a match")
    parser.add_argument("--portal", metavar="URL",
                        help="captive portal: redirect requests for other host names to this URL "
                             "(used by scripts/neongrid-ap.sh, e.g. http://10.42.0.1/)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    web.run_app(make_app(args.db, args.static, args.countdown, args.portal),
                host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
