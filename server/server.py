#!/usr/bin/env python3
"""NEON GRID game server: accounts, highscores and real-time light-cycle matches.

Apache serves the static files in public/ and proxies /api/ and /ws to this
server. For local testing, pass --static ../public to serve everything here.
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

from aiohttp import WSMsgType, web

GRID_W, GRID_H = 120, 80
TICK_HZ_START, TICK_HZ_MAX = 14, 22   # the cycles speed up as the match goes on
SPEEDUP_EVERY = 120                    # ticks per +1 Hz
SESSION_SECONDS = 30 * 24 * 3600
USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,16}$")
MIN_PASSWORD = 6
MAX_PASSWORD = 128

DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}

log = logging.getLogger("neongrid")

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
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def public_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "wins": row["wins"],
        "losses": row["losses"],
        "draws": row["draws"],
        "played": row["wins"] + row["losses"] + row["draws"],
    }


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def get_user(self, user_id):
        return self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def get_user_by_name(self, username):
        return self.conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    def create_user(self, username, salt, pw_hash):
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO users (username, pw_salt, pw_hash, created_at) VALUES (?, ?, ?, ?)",
                (username, salt, pw_hash, int(time.time())))
        return self.get_user(cur.lastrowid)

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.conn:
            self.conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            self.conn.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (hash_token(token), user_id, now + SESSION_SECONDS))
        return token

    def user_by_token(self, token):
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
        rows = self.conn.execute(
            "SELECT * FROM users WHERE wins + losses + draws > 0 "
            "ORDER BY wins DESC, losses ASC, (wins + losses + draws) DESC, username "
            "LIMIT ?", (limit,)).fetchall()
        total = self.conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        return [public_user(r) for r in rows], total


class RateLimiter:
    """Allows `limit` hits per key within `window` seconds."""

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
    """One logged-in WebSocket connection."""

    def __init__(self, ws, user):
        self.ws = ws
        self.user_id = user["id"]
        self.username = user["username"]
        self.match = None
        self.index = None

    async def send(self, obj):
        if self.ws.closed:
            return
        try:
            await self.ws.send_str(json.dumps(obj))
        except (ConnectionResetError, RuntimeError):
            pass


class Match:
    def __init__(self, lobby, a, b, countdown):
        self.lobby = lobby
        self.players = [a, b]
        self.countdown = countdown
        self.done = False
        self.running = False
        self.tick = 0
        self.occupied = bytearray(GRID_W * GRID_H)
        self.pos = [(GRID_W // 4, GRID_H // 2), (GRID_W - 1 - GRID_W // 4, GRID_H // 2)]
        self.dir = ["right", "left"]
        self.queue = [deque(), deque()]
        for x, y in self.pos:
            self.occupied[y * GRID_W + x] = 1
        for i, p in enumerate(self.players):
            p.match, p.index = self, i

    async def broadcast(self, obj):
        await asyncio.gather(*(p.send(obj) for p in self.players))

    def turn(self, index, direction):
        if self.done:
            return
        q = self.queue[index]
        last = q[-1] if q else self.dir[index]
        if direction in (last, OPPOSITE[last]) or len(q) >= 3:
            return
        q.append(direction)

    def step(self):
        heads = []
        for i in (0, 1):
            if self.queue[i]:
                self.dir[i] = self.queue[i].popleft()
            dx, dy = DIRS[self.dir[i]]
            heads.append((self.pos[i][0] + dx, self.pos[i][1] + dy))
        crashed = [False, False]
        for i, (x, y) in enumerate(heads):
            if not (0 <= x < GRID_W and 0 <= y < GRID_H) or self.occupied[y * GRID_W + x]:
                crashed[i] = True
        if heads[0] == heads[1]:
            crashed = [True, True]
        for i, (x, y) in enumerate(heads):
            if not crashed[i]:
                self.occupied[y * GRID_W + x] = 1
                self.pos[i] = (x, y)
        self.tick += 1
        return heads, crashed

    async def run(self):
        try:
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
            for n in range(self.countdown, 0, -1):
                await self.broadcast({"type": "countdown", "n": n})
                await asyncio.sleep(1)
                if self.done:
                    return
            self.running = True
            await self.broadcast({"type": "countdown", "n": 0})

            loop = asyncio.get_running_loop()
            next_t = loop.time()
            while not self.done:
                heads, crashed = self.step()
                await self.broadcast({"type": "tick", "t": self.tick,
                                      "heads": heads, "crashed": crashed})
                if any(crashed):
                    winner = None if all(crashed) else (1 if crashed[0] else 0)
                    await self.finish(winner, "crash")
                    return
                hz = min(TICK_HZ_MAX, TICK_HZ_START + self.tick // SPEEDUP_EVERY)
                next_t += 1 / hz
                await asyncio.sleep(max(0.0, next_t - loop.time()))
        except Exception:
            log.exception("match crashed")
            await self.finish(None, "error")

    async def forfeit(self, index):
        await self.finish(1 - index, "forfeit")

    async def finish(self, winner_index, reason):
        if self.done:
            return
        self.done = True
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
            p.match = p.index = None
            if winner_index is None:
                result = "draw"
            else:
                result = "win" if i == winner_index else "lose"
            await p.send({
                "type": "end",
                "result": result,
                "reason": reason,
                "me": public_user(self.lobby.db.get_user(p.user_id)),
            })


class Lobby:
    """The first player to press PLAY waits; the next one starts a match."""

    def __init__(self, db, countdown):
        self.db = db
        self.countdown = countdown
        self.waiting = None
        self.active = {}          # user_id -> Conn
        self.matches = set()

    async def connect(self, conn):
        old = self.active.get(conn.user_id)
        self.active[conn.user_id] = conn
        if old is not None:
            await old.send({"type": "kicked"})
            await old.ws.close()

    async def disconnect(self, conn):
        if self.active.get(conn.user_id) is conn:
            del self.active[conn.user_id]
        if self.waiting is conn:
            self.waiting = None
        if conn.match is not None:
            await conn.match.forfeit(conn.index)

    async def join(self, conn):
        if conn.match is not None or self.waiting is conn:
            return
        other = self.waiting
        if other is not None and (other.ws.closed or other.user_id == conn.user_id):
            other = self.waiting = None
        if other is None:
            self.waiting = conn
            await conn.send({"type": "waiting"})
            return
        self.waiting = None
        match = Match(self, other, conn, self.countdown)
        self.matches.add(match)
        asyncio.create_task(match.run())

    async def leave(self, conn):
        if self.waiting is conn:
            self.waiting = None
            await conn.send({"type": "idle"})


# --------------------------------------------------------------------------- #
# HTTP / WebSocket handlers
# --------------------------------------------------------------------------- #

def json_error(status, message):
    return web.json_response({"error": message}, status=status)


def client_ip(request):
    peer = request.remote or ""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded and peer in ("127.0.0.1", "::1"):
        return forwarded.split(",")[-1].strip()
    return peer


def bearer_token(request):
    header = request.headers.get("Authorization", "")
    return header[7:] if header.startswith("Bearer ") else ""


async def read_credentials(request):
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
        return json_error(409, "That username is already taken.")
    app["register_limit"].hit(ip)
    return web.json_response({"token": db.create_session(user["id"]), "user": public_user(user)})


async def login(request):
    app = request.app
    ip = client_ip(request)
    if not app["login_limit"].allowed(ip):
        return json_error(429, "Too many failed logins. Wait a few minutes.")
    username, password = await read_credentials(request)
    if username is None:
        return json_error(400, "Invalid request.")
    db = app["db"]
    user = db.get_user_by_name(username)
    salt = user["pw_salt"] if user else secrets.token_bytes(16)
    candidate = await asyncio.to_thread(hash_password, password[:MAX_PASSWORD], salt)
    if user is None or not hmac.compare_digest(candidate, user["pw_hash"]):
        app["login_limit"].hit(ip)
        return json_error(401, "Wrong username or password.")
    return web.json_response({"token": db.create_session(user["id"]), "user": public_user(user)})


async def logout(request):
    token = bearer_token(request)
    if token:
        request.app["db"].delete_session(token)
    return web.json_response({"ok": True})


async def me(request):
    user = request.app["db"].user_by_token(bearer_token(request))
    if user is None:
        return json_error(401, "Not logged in.")
    return web.json_response({"user": public_user(user)})


async def highscores(request):
    rows, total = request.app["db"].highscores()
    return web.json_response({"highscores": rows, "total_matches": total})


async def websocket(request):
    app = request.app
    lobby = app["lobby"]
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
                if kind != "auth":
                    continue
                user = app["db"].user_by_token(data.get("token"))
                if user is None:
                    await ws.send_str(json.dumps({"type": "auth_failed"}))
                    break
                conn = Conn(ws, user)
                await lobby.connect(conn)
                await conn.send({"type": "auth_ok", "user": public_user(user)})
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


def make_app(db_path, static_dir=None, countdown=10):
    app = web.Application(client_max_size=16 * 1024)
    db = DB(db_path)
    app["db"] = db
    app["lobby"] = Lobby(db, countdown)
    app["login_limit"] = RateLimiter(limit=10, window=600)
    app["register_limit"] = RateLimiter(limit=5, window=3600)
    app.router.add_post("/api/register", register)
    app.router.add_post("/api/login", login)
    app.router.add_post("/api/logout", logout)
    app.router.add_get("/api/me", me)
    app.router.add_get("/api/highscores", highscores)
    app.router.add_get("/ws", websocket)
    if static_dir:
        static_dir = Path(static_dir).resolve()

        async def index(_request):
            return web.FileResponse(static_dir / "index.html")

        app.router.add_get("/", index)
        app.router.add_static("/", static_dir)
    return app


def main():
    parser = argparse.ArgumentParser(description="NEON GRID game server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="neongrid.sqlite3", help="SQLite database file")
    parser.add_argument("--static", help="also serve this folder (local testing without Apache)")
    parser.add_argument("--countdown", type=int, default=10, help="countdown seconds before a match")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    web.run_app(make_app(args.db, args.static, args.countdown),
                host=args.host, port=args.port, access_log=None)


if __name__ == "__main__":
    main()
