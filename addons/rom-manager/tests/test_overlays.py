"""The bezel slots, and the boundary the addon must not cross.

Overlays live under `$GAMECORE_DATA/assets/overlays`, and that directory
belongs to the CORE. `api: 1` says an addon writes inside its own data
directory and nowhere else, so every write here is a relay to the core over
loopback — the core owns the destination, the filename and the validation.

The test that matters most is therefore a negative one: after an upload, the
addon must not have created a single file anywhere. It is easy to "fix" a
failing relay by writing the PNG directly, it would work on a box, and it would
quietly turn the contract into "an addon writes wherever it can reach".

Two genuinely distinct roots throughout. On a real box `GAMECORE_DATA` defaults
to `GAMECORE_PATH`, so a wrong root is invisible and a test that does not
separate them proves nothing.

Run with:  python tests/test_overlays.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = tempfile.TemporaryDirectory()
ROOT = Path(_TMP.name)
CODE = ROOT / "GameCore"          # the installation — read-only in production
DATA = ROOT / "userdata"          # the player's data — writable
(CODE / "config").mkdir(parents=True)
(DATA / "config").mkdir(parents=True)

# mgba declares three consoles, pcsx2 none — the two shapes this file is about.
(DATA / "config" / "systems.json").write_text(json.dumps([
    {"id": "mgba", "type": "emulator", "label": "Game Boy Advance",
     "romsPath": "emu/mgba/", "extensions": ["*.gba", "*.gbc", "*.gb", "*.zip"],
     "consoles": [
         {"id": "gba", "label": "Game Boy Advance", "extensions": ["*.gba"]},
         {"id": "gbc", "label": "Game Boy Color", "extensions": ["*.gbc"]},
         {"id": "gb", "label": "Game Boy", "extensions": ["*.gb"]},
     ]},
    {"id": "pcsx2", "type": "emulator", "label": "PlayStation 2",
     "romsPath": "emu/pcsx2/", "extensions": ["*.iso"]},
    # A grid that predates roms.consoles: the field arrives through the core's
    # catalogue merge, not through this addon, so "absent" must behave as
    # "one console" rather than as an error.
    {"id": "dolphin", "type": "emulator", "label": "GameCube/Wii",
     "romsPath": "emu/dolphin/", "extensions": ["*.iso"]},
]))

os.environ["GAMECORE_PATH"] = str(CODE)
os.environ["GAMECORE_DATA"] = str(DATA)

# A port nothing is listening on, chosen by the OS and released immediately.
#
# Not a detail, and it cost this file a wrong pass: the default is 8765, which
# on a development box is the REAL GameCore backend serving /opt/GameCore. The
# first run of these tests relayed into it and read its answers — the checks
# below were measuring a live machine's core instead of this addon, and would
# have reported whatever that core happened to say. Pinning a dead port is what
# makes "the relay could not reach the core" a fact of the test rather than a
# fact of whoever's laptop it runs on.
import socket  # noqa: E402

_probe = socket.socket()
_probe.bind(("127.0.0.1", 0))
_DEAD_PORT = _probe.getsockname()[1]
_probe.close()
os.environ["GAMECORE_BACKEND_PORT"] = str(_DEAD_PORT)

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label
          + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def tree(root: Path) -> set:
    return {p.relative_to(root) for p in root.rglob("*")}


print("Two distinct roots")
check("code and data really differ", CODE.resolve() != DATA.resolve(),
      "the rest of this file proves nothing otherwise")
check("the core port is not the real one", server.CORE_PORT == _DEAD_PORT,
      f"{server.CORE_PORT} — a live backend would answer these tests")


print("Which consoles a pack declares")
mgba = server.get_system("mgba")
check("mgba declares three", [c["id"] for c in server.consoles_of(mgba)] == ["gba", "gbc", "gb"])
check("pcsx2 declares none", server.consoles_of(server.get_system("pcsx2")) == [])
check("a grid without the field declares none",
      server.consoles_of(server.get_system("dolphin")) == [])
check("a grid with a junk value declares none",
      server.consoles_of({"consoles": "gba"}) == [])
check("entries without an id are dropped",
      server.consoles_of({"consoles": [{"label": "x"}, {"id": "gb"}]}) == [{"id": "gb"}])


print("A console the pack never declared is refused before the core is touched")
for bad in ("gbа", "sgb", "..", "../../etc", ""):
    try:
        server.require_console(mgba, bad)
        check(f"{bad!r} refused", False, "no HTTPException raised")
    except HTTPException as e:
        check(f"{bad!r} refused", e.status_code == 404, f"status {e.status_code}")
try:
    server.require_console(mgba, "gba")
    check("'gba' accepted", True)
except HTTPException:
    check("'gba' accepted", False)


print("The addon writes nothing — every write is relayed to the core")
client = TestClient(server.app)
before_data, before_code = tree(DATA), tree(CODE)

# No core is listening on the loopback port in a test run, so the relay fails.
# That is exactly the condition under which a wrong implementation would show
# itself: an addon that wrote the file itself would succeed here.
png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
r = client.post("/api/overlays/mgba/consoles/gba", files={"file": ("x.png", png, "image/png")})
check("upload without a core answers 503, not 500", r.status_code == 503, f"status {r.status_code}")
check("nothing was written under DATA", tree(DATA) == before_data,
      str(tree(DATA) - before_data))
check("nothing was written under PATH", tree(CODE) == before_code,
      str(tree(CODE) - before_code))
check("no assets/overlays was created at all",
      not (DATA / "assets" / "overlays").exists() and not (CODE / "assets").exists())

r = client.delete("/api/overlays/mgba/consoles/gba")
check("delete without a core answers 503", r.status_code == 503, f"status {r.status_code}")
r = client.get("/api/overlays/mgba/slots")
check("slots without a core answers 503", r.status_code == 503, f"status {r.status_code}")


print("Routing: the console must exist, and so must the system")
r = client.post("/api/overlays/mgba/consoles/sgb", files={"file": ("x.png", png, "image/png")})
check("undeclared console is a 404", r.status_code == 404, f"status {r.status_code}")
r = client.post("/api/overlays/pcsx2/consoles/ps2", files={"file": ("x.png", png, "image/png")})
check("a console on a mono-console pack is a 404", r.status_code == 404, f"status {r.status_code}")
r = client.delete("/api/overlays/nintendo-virtualboy/consoles/vb")
check("unknown system is a 404", r.status_code == 404, f"status {r.status_code}")
check("still nothing written", tree(DATA) == before_data and tree(CODE) == before_code)


print("The core's own words reach the browser")
# A refusal carries a sentence — "a bezel needs a transparent area" — and the
# relay must hand back the status AND the body. A generic failure would throw
# away the only explanation the player is ever given.
import httpx  # noqa: E402


class _FakeCore(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        return httpx.Response(422, json={"detail": "A bezel needs a transparent area"},
                              request=request)


_real = httpx.AsyncClient


class _Patched(_real):
    def __init__(self, *a, **kw):
        kw["transport"] = _FakeCore()
        super().__init__(*a, **kw)


httpx.AsyncClient = _Patched
try:
    r = client.post("/api/overlays/mgba/consoles/gb",
                    files={"file": ("x.png", png, "image/png")})
    check("the core's status is passed through", r.status_code == 422, f"status {r.status_code}")
    check("the core's explanation is passed through",
          "transparent" in r.text, r.text[:120])
    check("still nothing written by the addon",
          tree(DATA) == before_data and tree(CODE) == before_code)
finally:
    httpx.AsyncClient = _real

print("A core that predates per-console bezels")
# An addon updates on its own `git pull`, the core updates by OTA. Between the
# two, this addon runs against a core with no /slots endpoint — the normal
# state of a box mid-update. It must still manage the system bezel, or the
# update makes the screen worse than it was.


class _OldCore(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        return httpx.Response(404, json={"detail": "Not Found"}, request=request)


class _PatchedOld(_real):
    def __init__(self, *a, **kw):
        kw["transport"] = _OldCore()
        super().__init__(*a, **kw)


(DATA / "assets" / "overlays").mkdir(parents=True)
(DATA / "assets" / "overlays" / "mgba.png").write_bytes(b"\x89PNG\r\n\x1a\n")
after_seed = tree(DATA)

httpx.AsyncClient = _PatchedOld
try:
    body = client.get("/api/overlays/mgba/slots").json()
    check("an old core still answers 200", body.get("legacy_core") is True, str(body)[:120])
    check("only the system slot is offered",
          [s["level"] for s in body["slots"]] == ["system"], str(body["slots"]))
    check("the system bezel on disk is seen", body["slots"][0]["present"] is True)
    check("no hole is invented", body["slots"][0]["hole"] is None)
    check("the player is told why", "predates" in body.get("note", ""))
    check("still nothing written", tree(DATA) == after_seed)
finally:
    httpx.AsyncClient = _real



print("The page is never served from the browser's cache")
# `web/index.html` changes with every addon update and used to go out with no
# Cache-Control at all — the browser kept the OLD page a while after
# `gamecore-addon update`, and the operator saw one overlay slot where the
# server already offered four. Not a bug in the slots; a bug in the headers.
r = client.get("/")
check("index.html answers", r.status_code == 200 and "<html" in r.text.lower(), f"status {r.status_code}")
check("Cache-Control forbids storing it",
      "no-store" in r.headers.get("cache-control", ""), r.headers.get("cache-control", "<absent>"))
# A conditional request must not be answered 304: the browser would then keep
# what it has, which is exactly the copy this exists to retire.
r2 = client.get("/", headers={"If-None-Match": r.headers.get("etag", '"x"'),
                              "If-Modified-Since": r.headers.get("last-modified", "")})
check("a conditional request still gets the full page", r2.status_code == 200, f"status {r2.status_code}")

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED: " + ", ".join(FAILURES))
    sys.exit(1)
print("All overlay tests passed.")
