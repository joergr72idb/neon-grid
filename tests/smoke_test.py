#!/usr/bin/env python3
"""End-to-end check: two accounts, one full match over WebSocket, stats + highscores.

Run from the project folder:  .venv/bin/python tests/smoke_test.py
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import server  # noqa: E402


async def recv(ws, kind):
    while True:
        msg = json.loads((await ws.receive(timeout=10)).data)
        if msg["type"] == kind:
            return msg


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        app = server.make_app(Path(tmp) / "test.sqlite3", countdown=2)
        async with TestServer(app) as ts, ClientSession() as http:
            base = str(ts.make_url("/"))

            # accounts
            tokens = []
            for name in ("alice", "bob"):
                r = await http.post(base + "api/register", json={"username": name, "password": "secret1"})
                assert r.status == 200, await r.text()
                tokens.append((await r.json())["token"])
            r = await http.post(base + "api/register", json={"username": "ALICE", "password": "secret1"})
            assert r.status == 409, "duplicate username (case-insensitive) must be rejected"
            r = await http.post(base + "api/login", json={"username": "alice", "password": "wrong!!"})
            assert r.status == 401
            r = await http.post(base + "api/login", json={"username": "alice", "password": "secret1"})
            assert r.status == 200

            # match: alice waits, bob joins, countdown, alice drives into the top wall
            a = await http.ws_connect(base + "ws")
            b = await http.ws_connect(base + "ws")
            for ws, tok in ((a, tokens[0]), (b, tokens[1])):
                await ws.send_json({"type": "auth", "token": tok})
                await recv(ws, "auth_ok")
            await a.send_json({"type": "join"})
            await recv(a, "waiting")
            await b.send_json({"type": "join"})
            m = await recv(a, "match")
            assert m["you"] == 0 and [p["username"] for p in m["players"]] == ["alice", "bob"]
            counts = []
            while True:
                c = await recv(a, "countdown")
                counts.append(c["n"])
                if c["n"] == 0:
                    break
            assert counts == [2, 1, 0], counts
            await a.send_json({"type": "turn", "dir": "up"})
            await b.send_json({"type": "turn", "dir": "right"})   # reversing is ignored
            end_a = await recv(a, "end")
            end_b = await recv(b, "end")
            assert end_a["result"] == "lose" and end_b["result"] == "win", (end_a, end_b)
            assert end_b["me"]["wins"] == 1 and end_a["me"]["losses"] == 1

            # second match, decided by forfeit
            await a.send_json({"type": "join"})
            await recv(a, "waiting")
            await b.send_json({"type": "join"})
            await recv(b, "match")
            await a.close()
            end_b = await recv(b, "end")
            assert end_b["result"] == "win" and end_b["reason"] == "forfeit"
            await b.close()

            r = await http.get(base + "api/highscores")
            data = await r.json()
            assert data["total_matches"] == 2
            assert data["highscores"][0]["username"] == "bob" and data["highscores"][0]["wins"] == 2
            print("OK –", data)


asyncio.run(main())
