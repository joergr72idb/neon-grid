#!/usr/bin/env python3
"""Make the README screenshots: the start screen and a running match.

    .venv/bin/python tools/screenshots.py                 # writes docs/images/start.png + game.png
    .venv/bin/python tools/screenshots.py --out /tmp/shots --ticks 200

What it does:
  1. Starts its own game server on a free port with a TEMPORARY database, so
     no real accounts or scores ever appear in the pictures. The accounts are
     invented: Nova, Blaze, Pixel, Vector, Orbit.
  2. Fills the highscore list with a few quick bot-vs-bot matches.
  3. Opens the game in headless Chrome/Chromium as "Nova" and controls it over
     the Chrome DevTools Protocol (CDP, a WebSocket that aiohttp can speak).
  4. Lets the bot "Blaze" wait for a battle -> screenshot of the start screen
     with "Blaze is waiting for a battle!".
  5. Nova presses "Fight Blaze". The script steers both cycles: Blaze directly
     over the game WebSocket, Nova with arrow-key events in the browser.
     After --ticks game steps it takes the screenshot of the running match.
  6. If the match ends before that, everything starts again with the next
     random seed (up to --attempts times).

Needs: the project's .venv (aiohttp) and google-chrome or chromium.
"""

import argparse
import asyncio
import base64
import json
import random
import shutil
import socket
import subprocess
import sys
import tempfile
from collections import deque
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parent.parent
DIRS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
OPPOSITE = {"up": "down", "down": "up", "left": "right", "right": "left"}
KEYS = {"up": ("ArrowUp", 38), "down": ("ArrowDown", 40),
        "left": ("ArrowLeft", 37), "right": ("ArrowRight", 39)}
NAMES = ["Nova", "Blaze", "Pixel", "Vector", "Orbit"]   # max. 5: the server allows 5 new accounts per IP and hour
WARMUP = [("Blaze", "Pixel"), ("Vector", "Orbit"), ("Nova", "Blaze"), ("Pixel", "Orbit"),
          ("Blaze", "Vector"), ("Nova", "Orbit"), ("Pixel", "Vector"), ("Blaze", "Orbit"),
          ("Nova", "Pixel")]
WIDTH, HEIGHT = 1280, 860


class MatchTooShort(Exception):
    pass


# --- Steering bot -------------------------------------------------------------

def blocked(cell, occ, w, h):
    return cell in occ or not (0 <= cell[0] < w and 0 <= cell[1] < h)


def free_space(start, occ, w, h, limit):
    """Number of free cells reachable from `start` (flood fill, stops at `limit`)."""
    if blocked(start, occ, w, h):
        return -1
    seen, queue = {start}, deque([start])
    while queue and len(seen) < limit:
        x, y = queue.popleft()
        for dx, dy in DIRS.values():
            n = (x + dx, y + dy)
            if n not in seen and not blocked(n, occ, w, h):
                seen.add(n)
                queue.append(n)
    return len(seen)


def choose(rng, head, d, occ, w, h, limit, wander, ahead=1):
    """Pick the direction with the most free space; keep going straight unless
    `wander` says otherwise. ahead>1 also keeps the next cells in that
    direction free, for a player whose turn arrives a tick late (the browser)."""
    options = []
    for nd in (d, *[k for k in DIRS if k not in (d, OPPOSITE[d])]):
        dx, dy = DIRS[nd]
        score = free_space((head[0] + dx, head[1] + dy), occ, w, h, limit)
        if score > 0 and any(blocked((head[0] + dx * i, head[1] + dy * i), occ, w, h)
                             for i in range(2, ahead + 1)):
            score *= 0.05
        options.append((score, nd))
    best = max(s for s, _ in options)
    good = [nd for s, nd in options if s > 0 and s >= best * 0.9]
    if not good:
        return d
    if d in good and rng.random() > wander:
        return d
    return rng.choice(good)


class Grid:
    """Occupied cells, heads and directions, built from match/tick messages."""

    def __init__(self, match):
        self.w, self.h = match["grid"]
        self.heads = [tuple(p) for p in match["starts"]]
        self.occ = set(self.heads)
        self.dirs = list(match["dirs"])

    def tick(self, msg):
        self.heads = [tuple(p) for p in msg["heads"]]
        self.occ.update(self.heads)

    def danger(self, player):
        """Occupied cells plus every cell the opponent could enter next."""
        ox, oy = self.heads[1 - player]
        return self.occ | {(ox + dx, oy + dy) for dx, dy in DIRS.values()}


# --- Game server client ---------------------------------------------------------

class Game:
    def __init__(self, session, base):
        self.s, self.base = session, base

    async def register(self, name):
        async with self.s.post(f"{self.base}/api/register",
                               json={"username": name, "password": "screenshot1"}) as r:
            data = await r.json()
        if "token" not in data:
            raise RuntimeError(f"register {name}: {data}")
        return data["token"]

    async def connect(self, token):
        ws = await self.s.ws_connect(self.base.replace("http", "ws") + "/ws")
        await ws.send_json({"type": "auth", "token": token})
        return ws


async def recv(ws):
    return json.loads((await ws.receive(timeout=30)).data)


async def bot_match(game, rng, tok_a, tok_b):
    """Two quick, not too clever bots play one match (for the highscore list)."""
    wa, wb = await game.connect(tok_a), await game.connect(tok_b)
    await wa.send_json({"type": "join"})
    await asyncio.sleep(0.2)
    await wb.send_json({"type": "join"})

    async def play(ws):
        grid = me = None
        while True:
            m = await recv(ws)
            if m["type"] == "match":
                grid, me = Grid(m), m["you"]
            elif m["type"] == "tick":
                grid.tick(m)
                nd = choose(rng, grid.heads[me], grid.dirs[me], grid.occ, grid.w, grid.h, 25, 0.08)
                if nd != grid.dirs[me]:
                    grid.dirs[me] = nd
                    await ws.send_json({"type": "turn", "dir": nd})
            elif m["type"] == "end":
                return m["result"]

    results = await asyncio.gather(play(wa), play(wb))
    await wa.close()
    await wb.close()
    return results


# --- Headless browser over the Chrome DevTools Protocol --------------------------

class Browser:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send_json({"id": self.n, "method": method, "params": params})
        while True:
            m = json.loads((await self.ws.receive(timeout=30)).data)
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    async def js(self, expression):
        r = await self.call("Runtime.evaluate", expression=expression,
                            awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    async def screenshot(self, path):
        r = await self.call("Page.captureScreenshot", format="png")
        Path(path).write_bytes(base64.b64decode(r["data"]))
        print(f"  saved {path}")

    async def key(self, direction):
        key, code = KEYS[direction]
        for kind in ("keyDown", "keyUp"):
            await self.call("Input.dispatchKeyEvent", type=kind, key=key, code=key,
                            windowsVirtualKeyCode=code)


def find_chrome():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        if shutil.which(name):
            return name
    sys.exit("No Chrome/Chromium found. Install one, e.g.: sudo apt install chromium")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- One complete attempt ----------------------------------------------------------

async def attempt(seed, out, ticks, chrome_bin):
    rng = random.Random(seed)
    tmp = Path(tempfile.mkdtemp(prefix="neongrid-shots-"))
    port, cdp_port = free_port(), free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [str(REPO / ".venv/bin/python"), "server.py", "--static", "../public", "--port", str(port),
         "--countdown", "3", "--db", str(tmp / "screenshots.sqlite3")],
        cwd=REPO / "server", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chrome = None
    try:
        async with aiohttp.ClientSession() as s:
            game = Game(s, base)
            for _ in range(50):                      # wait until the server answers
                try:
                    async with s.get(f"{base}/api/highscores"):
                        break
                except aiohttp.ClientError:
                    await asyncio.sleep(0.2)
            tokens = {n: await game.register(n) for n in NAMES}

            print("  filling the highscore list …")
            for a, b in WARMUP:
                await bot_match(game, rng, tokens[a], tokens[b])

            chrome = subprocess.Popen(
                [chrome_bin, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                 f"--remote-debugging-port={cdp_port}", f"--user-data-dir={tmp / 'chrome'}",
                 f"--window-size={WIDTH},{HEIGHT}", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            page = None
            for _ in range(100):
                try:
                    async with s.get(f"http://127.0.0.1:{cdp_port}/json") as r:
                        page = next(t for t in await r.json() if t["type"] == "page")
                    break
                except (aiohttp.ClientError, StopIteration):
                    await asyncio.sleep(0.2)
            if page is None:
                raise RuntimeError("Chrome did not start")
            cdp_ws = await s.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0)
            browser = Browser(cdp_ws)
            await browser.call("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT,
                               deviceScaleFactor=1, mobile=False)

            # Log in as Nova by putting the token where game.js looks for it.
            await browser.call("Page.navigate", url=base + "/")
            await asyncio.sleep(1)
            await browser.js(f"localStorage.setItem('neongrid-token', {json.dumps(tokens['Nova'])})")
            await browser.call("Page.reload")
            await asyncio.sleep(2)

            # Blaze waits for a battle -> the menu shows the hint and "Fight Blaze".
            blaze = await game.connect(tokens["Blaze"])
            await blaze.send_json({"type": "join"})
            await asyncio.sleep(1.5)
            await browser.screenshot(out / "start.png")

            await browser.js("document.querySelector('#btn-play').click()")
            grid = me = nova = None
            last_tick = 0
            while True:
                m = await recv(blaze)
                if m["type"] == "match":
                    grid, me = Grid(m), m["you"]
                    nova = 1 - me
                elif m["type"] == "tick":
                    grid.tick(m)
                    last_tick = m["t"]
                    for p in (me, nova):
                        nd = choose(rng, grid.heads[p], grid.dirs[p], grid.danger(p), grid.w, grid.h,
                                    400, 0.04, ahead=3 if p == nova else 2)
                        if nd != grid.dirs[p]:
                            grid.dirs[p] = nd
                            if p == me:
                                await blaze.send_json({"type": "turn", "dir": nd})
                            else:
                                await browser.key(nd)
                    if m["t"] >= ticks:
                        await browser.screenshot(out / "game.png")
                        break
                elif m["type"] == "end":
                    raise MatchTooShort(f"match ended after {last_tick} steps")
            await blaze.close()
            await cdp_ws.close()
    finally:
        if chrome:
            chrome.terminate()
            chrome.wait()
        server.terminate()
        server.wait()
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "images",
                    help="output folder (default: docs/images)")
    ap.add_argument("--ticks", type=int, default=360,
                    help="game steps before the match screenshot (default: 360)")
    ap.add_argument("--seed", type=int, default=2, help="first random seed (default: 2)")
    ap.add_argument("--attempts", type=int, default=6,
                    help="how many seeds to try if the match ends too early (default: 6)")
    ap.add_argument("--chrome", help="browser binary (default: first of google-chrome, chromium)")
    args = ap.parse_args()

    if not (REPO / ".venv/bin/python").exists():
        sys.exit("Python environment missing: python3 -m venv .venv && .venv/bin/pip install -r server/requirements.txt")
    args.out.mkdir(parents=True, exist_ok=True)
    chrome_bin = args.chrome or find_chrome()

    for seed in range(args.seed, args.seed + args.attempts):
        print(f"attempt with seed {seed}")
        try:
            asyncio.run(attempt(seed, args.out, args.ticks, chrome_bin))
            print("done")
            return
        except MatchTooShort as e:
            print(f"  {e}, trying the next seed")
    sys.exit(f"No match lasted {args.ticks} steps. Try --ticks 200 or more --attempts.")


if __name__ == "__main__":
    main()
