"""Starting point: prove your addon respects the code/data split.

Copy this with the rest of the template and extend it as you add paths. It is
short on purpose — the one thing it must establish is that your addon resolves
a data path under GAMECORE_DATA and does not reach for GAMECORE_PATH.

Why it has to set two different directories: GAMECORE_DATA *defaults* to
GAMECORE_PATH, so on a real box today the two are the same directory and both
spellings work. A test that lets them default proves nothing at all.

Run with:  python tests/test_paths.py
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
CODE.mkdir()
DATA.mkdir()

os.environ["GAMECORE_PATH"] = str(CODE)
os.environ["GAMECORE_DATA"] = str(DATA)
os.environ.pop("ADDON_DATA_DIR", None)

sys.path.insert(0, str(Path(__file__).parent.parent))
import server  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


print("Two distinct roots")
check("code and data really differ", CODE.resolve() != DATA.resolve(),
      "the rest of this file proves nothing otherwise")
check("GAMECORE_PATH honoured", server.GAMECORE_PATH == CODE, str(server.GAMECORE_PATH))
check("GAMECORE_DATA honoured", server.GAMECORE_DATA == DATA, str(server.GAMECORE_DATA))

print("The addon's own writable corner")
check("ADDON_DATA_DIR under DATA", server.ADDON_DATA_DIR == DATA / "addons" / "_template",
      str(server.ADDON_DATA_DIR))
check("ADDON_DATA_DIR NOT under PATH",
      CODE.resolve() not in server.ADDON_DATA_DIR.resolve().parents,
      str(server.ADDON_DATA_DIR))

# The manager normally hands ADDON_DATA_DIR over; honour it when it does.
os.environ["ADDON_DATA_DIR"] = str(DATA / "addons" / "custom")
importlib.reload(server)
check("ADDON_DATA_DIR from the manager wins",
      server.ADDON_DATA_DIR == DATA / "addons" / "custom", str(server.ADDON_DATA_DIR))
del os.environ["ADDON_DATA_DIR"]

# ── Add your own paths here ──────────────────────────────────────────────────
# For each one, assert BOTH directions: it lands where you meant, and it is not
# reachable under the other root. "Under DATA" alone still passes when the two
# roots are equal, which is exactly the case this file exists to rule out.

print("Fallback for a box that has not taken the P3 OTA")
# Its systemd unit passes GAMECORE_PATH only. Every path must resolve exactly
# where it did before — the split must cost nothing until the data moves.
del os.environ["GAMECORE_DATA"]
importlib.reload(server)
check("DATA falls back to PATH", server.GAMECORE_DATA == CODE, str(server.GAMECORE_DATA))
check("ADDON_DATA_DIR follows the fallback",
      server.ADDON_DATA_DIR == CODE / "addons" / "_template", str(server.ADDON_DATA_DIR))

# An empty GAMECORE_DATA must not resolve every path to "/" — `or` not `get`.
os.environ["GAMECORE_DATA"] = ""
importlib.reload(server)
check("empty GAMECORE_DATA falls back too", server.GAMECORE_DATA == CODE, str(server.GAMECORE_DATA))

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED: " + ", ".join(FAILURES))
    sys.exit(1)
print("All path tests passed.")
