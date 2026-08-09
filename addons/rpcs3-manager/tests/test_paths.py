"""Path resolution with two distinct roots — and the one path that must NOT move.

rpcs3-manager reads from both roots, and they are not interchangeable:

  systems.json      the player's, edited from the UI      → GAMECORE_DATA
  lib/rpcs3         the binary shipped with the release   → GAMECORE_PATH

The second is the interesting half. Migrating it along with everything else
would look consistent and would break the launcher on every box that sets the
roots apart: nothing ever puts a binary under the data root, so rpcs3_cmd()
would find nothing and PS3 games would silently stop launching. This file
fails if someone "finishes the migration" later.

A fake HOME keeps config_dir() away from the real ~/.var/app — this addon
writes RPCS3's own config, and tests must never touch the box's.

Run with:  python tests/test_paths.py     (needs the addon's requirements)
"""
import importlib
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
HOME = ROOT / "home"
(CODE / "config").mkdir(parents=True)
(DATA / "config").mkdir(parents=True)
(CODE / "lib").mkdir(parents=True)
HOME.mkdir()

# A native RPCS3 shipped with the install, and a systems.json that declares it.
NATIVE = CODE / "lib" / "rpcs3"
NATIVE.write_text("#!/bin/sh\n")
NATIVE.chmod(0o755)
(DATA / "config" / "systems.json").write_text(json.dumps(
    [{"id": "rpcs3", "path": "/opt/GameCore/lib/rpcs3"}]))
# The stale copy under the CODE root is the trap: if the addon still reads
# systems.json from GAMECORE_PATH it finds this one and reports "flatpak".
(CODE / "config" / "systems.json").write_text(json.dumps(
    [{"id": "rpcs3", "path": "flatpak"}]))

os.environ["HOME"] = str(HOME)
os.environ["GAMECORE_PATH"] = str(CODE)
os.environ["GAMECORE_DATA"] = str(DATA)
os.environ.pop("RPCS3_BIN", None)
os.environ.pop("RPCS3_CONFIG_DIR", None)

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "shared" / "py"))
import server  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


print("Two distinct roots")
check("code and data really differ", CODE.resolve() != DATA.resolve(),
      "the rest of this file proves nothing otherwise")
check("GAMECORE_DATA honoured", server.GAMECORE_DATA == DATA, str(server.GAMECORE_DATA))

print("systems.json — the player's file, on the DATA root")
# Reading the DATA copy yields the native path; the decoy under CODE says
# "flatpak". The returned value names which file was actually opened.
check("read from DATA, not PATH", server._declared_path() == "/opt/GameCore/lib/rpcs3",
      server._declared_path())

print("lib/rpcs3 — the release's binary, staying on the CODE root")
cmd = server.rpcs3_cmd()
check("launcher resolved", cmd is not None, "rpcs3_cmd() returned None")
if cmd:
    binary = Path(cmd[0])
    check("binary under GAMECORE_PATH", binary == NATIVE, str(binary))
    check("binary NOT under GAMECORE_DATA", DATA.resolve() not in binary.resolve().parents,
          str(binary))

print("Fallback for a box that has not taken the P3 OTA")
del os.environ["GAMECORE_DATA"]
importlib.reload(server)
check("DATA falls back to PATH", server.GAMECORE_DATA == CODE, str(server.GAMECORE_DATA))
check("systems.json back under PATH", server._declared_path() == "flatpak",
      server._declared_path())

os.environ["GAMECORE_DATA"] = ""
importlib.reload(server)
check("empty GAMECORE_DATA falls back too", server.GAMECORE_DATA == CODE, str(server.GAMECORE_DATA))

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED: " + ", ".join(FAILURES))
    sys.exit(1)
print("All path tests passed.")
