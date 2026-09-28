"""Cover and Switch-title matching against the core's own file names.

Run with:  python tests/test_covers.py
"""
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import catalog  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


tmp = Path(tempfile.mkdtemp())
covers = tmp / "covers"
(covers / "wii").mkdir(parents=True)
(covers / "switch").mkdir()
# What the core writes: the title cut at its last dot, and tags kept on some.
(covers / "wii" / "New Super Mario Bros.webp").write_bytes(b"x")
(covers / "switch" / "FIFA 22 Legacy Edition [0100216014472000][v0][US].webp").write_bytes(b"x")
catalog.COVERS = covers

print("cover_for")
check("title cut at its last dot, like the core",
      catalog.cover_for("New Super Mario Bros. Wii (Europe) (En,Fr,De,Es,It)") is not None)
check("tags in the cover name are ignored", catalog.cover_for("FIFA 22 Legacy Edition") is not None)
check("an unknown game has none", catalog.cover_for("Metroid Prime") is None)

print("NSP ticket")
names = b"0123456789abcdef.cnmt.nca\x000100ea80032ea0000000000000000004.tik\x00"
nsp = tmp / "Game.nsp"
nsp.write_bytes(b"PFS0" + struct.pack("<III", 2, len(names), 0) + b"\0" * 48 + names)
check("title id read from the ticket name", catalog._nsp_ticket_tid(nsp) == "0100EA80032EA000",
      str(catalog._nsp_ticket_tid(nsp)))
(tmp / "junk.nsp").write_bytes(b"nope")
check("not a PFS0, no id", catalog._nsp_ticket_tid(tmp / "junk.nsp") is None)

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED: " + ", ".join(FAILURES))
    sys.exit(1)
print("All cover tests passed.")
