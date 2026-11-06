#!/usr/bin/env python3
"""Check the captive-portal redirect used on the game access point.

Phones test for internet with well-known URLs right after joining a Wi-Fi.
On the access point those requests reach our server (DNS points every name
at it) and must be redirected to the game; requests for the game itself and
for localhost must be served normally.

Run from the project folder:  .venv/bin/python tests/portal_test.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import server  # noqa: E402

PORTAL = "http://10.42.0.1/"

# (Host header, path) of the connectivity checks of common systems.
CHECKS = [
    ("connectivitycheck.gstatic.com", "/generate_204"),   # Android
    ("captive.apple.com", "/hotspot-detect.html"),        # iPhone, iPad, Mac
    ("www.msftconnecttest.com", "/connecttest.txt"),      # Windows
    ("detectportal.firefox.com", "/canonical.html"),      # Firefox
    ("nmcheck.gnome.org", "/check_network_status.txt"),   # Linux desktops
    ("example.org", "/"),                                 # anything a user types
]


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        app = server.make_app(Path(tmp) / "test.sqlite3", static_dir=ROOT / "public", portal=PORTAL)
        async with TestServer(app) as ts, ClientSession() as http:
            base = str(ts.make_url("/"))

            for host, path in CHECKS:
                r = await http.get(base.rstrip("/") + path, headers={"Host": host}, allow_redirects=False)
                assert r.status == 302, (host, r.status)
                assert r.headers["Location"] == PORTAL, (host, r.headers["Location"])
                assert r.headers["Cache-Control"] == "no-store"

            # The game itself, by IP (with or without port) and via localhost.
            for host in ("10.42.0.1", "10.42.0.1:8765", "localhost:8765", "127.0.0.1"):
                r = await http.get(base, headers={"Host": host}, allow_redirects=False)
                assert r.status == 200 and "NEON GRID" in await r.text(), host
                r = await http.get(base + "api/highscores", headers={"Host": host})
                assert r.status == 200, host

        # Without --portal nothing is redirected.
        app = server.make_app(Path(tmp) / "test2.sqlite3", static_dir=ROOT / "public")
        async with TestServer(app) as ts, ClientSession() as http:
            r = await http.get(str(ts.make_url("/")), headers={"Host": "captive.apple.com"},
                               allow_redirects=False)
            assert r.status == 200
    print("OK – captive portal redirects", len(CHECKS), "connectivity checks")


asyncio.run(main())
