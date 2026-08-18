"""Path resolution with two distinct roots — the whole point of api 1.

rom-manager is the only addon that *writes* the player's files, and what it
writes are ROMs. If it resolves `romsPath` under GAMECORE_PATH once the data
lives elsewhere, uploads land in a directory the core's scanner no longer
reads: the upload reports success and the game never appears.

On a real box today GAMECORE_DATA defaults to GAMECORE_PATH, so both spellings
work and the mistake is invisible. These tests therefore set two genuinely
different directories — that is the only configuration in which the assertion
means anything.

Run with:  python tests/test_paths.py     (needs the addon's requirements)
"""
import importlib
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

os.environ["GAMECORE_PATH"] = str(CODE)
os.environ["GAMECORE_DATA"] = str(DATA)

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def under(path: Path, root: Path) -> bool:
    return root.resolve() in path.resolve().parents or path.resolve() == root.resolve()


print("Two distinct roots")
check("code and data really differ", CODE.resolve() != DATA.resolve(),
      "the rest of this file proves nothing otherwise")
check("systems.json read from DATA", under(server.SYSTEMS_FILE, DATA), str(server.SYSTEMS_FILE))
check("systems.json NOT under PATH", not under(server.SYSTEMS_FILE, CODE), str(server.SYSTEMS_FILE))

print("romsPath resolution")
rel = server.roms_path_of({"romsPath": "emu/duckstation"})
check("relative romsPath lands under DATA", under(rel, DATA), str(rel))
check("relative romsPath NOT under PATH", not under(rel, CODE), str(rel))
check("relative romsPath keeps its subpath", rel == DATA / "emu" / "duckstation", str(rel))

absolute = server.roms_path_of({"romsPath": "/mnt/usb/roms/psx"})
check("absolute romsPath left alone", absolute == Path("/mnt/usb/roms/psx"), str(absolute))

check("empty romsPath → None", server.roms_path_of({"romsPath": ""}) is None)
check("missing romsPath → None", server.roms_path_of({}) is None)

print("Overlays are READ from the data root")
# Only read: the PNGs are written by the core through the loopback relay, and
# `api: 1` forbids this addon writing outside its own directory. But reading
# them from the wrong root is the quiet half of the same bug — the screen would
# report "no bezel" for every system on the day the data moves, with nothing to
# explain it.
check("overlays dir under DATA", under(server.OVERLAYS_DIR, DATA), str(server.OVERLAYS_DIR))
check("overlays dir NOT under PATH", not under(server.OVERLAYS_DIR, CODE),
      str(server.OVERLAYS_DIR))
check("overlays dir keeps its subpath",
      server.OVERLAYS_DIR == DATA / "assets" / "overlays", str(server.OVERLAYS_DIR))

print("Fallback for a box that has not taken the P3 OTA")
# The systemd unit there passes GAMECORE_PATH only. Every path must resolve to
# exactly where it did before — the split must cost nothing until P12 moves it.
del os.environ["GAMECORE_DATA"]
importlib.reload(server)
check("DATA falls back to PATH", server.GAMECORE_DATA == CODE, str(server.GAMECORE_DATA))
check("systems.json back under PATH", under(server.SYSTEMS_FILE, CODE), str(server.SYSTEMS_FILE))
legacy = server.roms_path_of({"romsPath": "emu/duckstation"})
check("relative romsPath back under PATH", legacy == CODE / "emu" / "duckstation", str(legacy))
check("overlays dir back under PATH", server.OVERLAYS_DIR == CODE / "assets" / "overlays",
      str(server.OVERLAYS_DIR))

# An empty GAMECORE_DATA must not resolve every path to "/" — `or` not `get`.
os.environ["GAMECORE_DATA"] = ""
importlib.reload(server)
check("empty GAMECORE_DATA falls back too", server.GAMECORE_DATA == CODE, str(server.GAMECORE_DATA))

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED: " + ", ".join(FAILURES))
    sys.exit(1)
print("All path tests passed.")
