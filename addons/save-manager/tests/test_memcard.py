"""memcard.py round-trip tests — no dependencies, no fixtures on disk.

Builds synthetic-but-valid PS1 / PS2 / GameCube cards in memory, then runs the
full life cycle on each: empty → import → list → export → delete → re-import.
Run with:  python tests/test_memcard.py
"""
import struct
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):          # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))
import memcard as mc  # noqa: E402

FAILURES = []


def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        FAILURES.append(label)


def expect_raises(label, exc, fn, *args):
    try:
        fn(*args)
    except exc:
        check(label, True)
    except Exception as e:  # noqa: BLE001 — report the wrong type as a failure
        print(f"       ({type(e).__name__}: {e})")
        check(label, False)
    else:
        check(label, False)


# ── PS1 ─────────────────────────────────────────────────────────────────────────

def blank_ps1() -> bytes:
    card = bytearray(mc._PS1_LEN)
    f0 = bytearray(mc._PS1_FRAME)
    f0[0:2] = b"MC"
    mc._ps1_cksum(f0)
    card[0:mc._PS1_FRAME] = f0
    for slot in range(1, 16):
        f = bytearray(mc._PS1_FRAME)
        f[0] = 0xA0
        struct.pack_into("<H", f, 8, 0xFFFF)
        mc._ps1_cksum(f)
        card[slot * mc._PS1_FRAME:(slot + 1) * mc._PS1_FRAME] = f
    return bytes(card)


def make_mcs(name="BESLES-12345TEST", title="TEST SAVE", blocks=2) -> bytes:
    head = bytearray(mc._PS1_FRAME)
    head[0] = 0x51
    struct.pack_into("<I", head, 4, blocks * mc._PS1_BLOCK)
    struct.pack_into("<H", head, 8, 0xFFFF)
    head[0x0A:0x0A + len(name)] = name.encode()
    mc._ps1_cksum(head)
    body = bytearray(blocks * mc._PS1_BLOCK)
    body[0:2] = b"SC"
    body[4:4 + len(title)] = title.encode()
    body[mc._PS1_BLOCK:mc._PS1_BLOCK + 4] = b"DATA"     # second block payload
    return bytes(head + body)


def test_ps1():
    print("PS1")
    card = blank_ps1()
    check("blank card lists no save", mc.read_saves(card) == [])

    mcs = make_mcs()
    card2 = mc.import_save(card, mcs, "test.mcs")
    saves = mc.read_saves(card2)
    check("import → one save", len(saves) == 1)
    check("serial parsed", saves and saves[0]["serial"] == "SLES-12345")
    check("title parsed", saves and saves[0]["title"] == "TEST SAVE")

    fn, blob = mc.export_save(card2, "BESLES-12345TEST")
    check("export filename", fn.endswith(".mcs"))
    check("export body intact", blob[mc._PS1_FRAME:] == mcs[mc._PS1_FRAME:])
    reimport = mc.import_save(blank_ps1(), blob, fn)
    check("export re-imports", mc.read_saves(reimport) == saves)

    expect_raises("duplicate import refused", ValueError,
                  mc.import_save, card2, mcs, "test.mcs")

    card3 = mc.delete_save(card2, "BESLES-12345TEST")
    check("delete → card empty", mc.read_saves(card3) == [])
    check("delete by serial too", mc.read_saves(mc.delete_save(card2, "SLES-12345")) == [])
    card4 = mc.import_save(card3, mcs, "test.mcs")
    check("freed blocks reusable", len(mc.read_saves(card4)) == 1)
    expect_raises("delete missing → KeyError", KeyError,
                  mc.delete_save, card3, "SLES-99999")

    # card too small for the save
    big = make_mcs(blocks=15)
    two = mc.import_save(blank_ps1(), make_mcs(name="BESLES-11111AAAA"), "a.mcs")
    expect_raises("no room refused", ValueError, mc.import_save, two, big, "big.mcs")

    gme = b"\x00" * 3904 + blank_ps1()
    check("DexDrive .gme header located", mc._ps1_offset(gme) == 3904)


# ── PS2 ─────────────────────────────────────────────────────────────────────────

_P2_PAGE, _P2_PPC, _P2_CLUSTERS, _P2_ALLOC = 512, 2, 512, 16
_P2_CL = _P2_PAGE * _P2_PPC


def blank_ps2() -> bytes:
    data = bytearray(_P2_CLUSTERS * _P2_CL)
    data[0:28] = b"Sony PS2 Memory Card Format "
    struct.pack_into("<HHHHIIII", data, 0x28,
                     _P2_PAGE, _P2_PPC, 16, 0,
                     _P2_CLUSTERS, _P2_ALLOC, _P2_CLUSTERS - _P2_ALLOC, 0)
    struct.pack_into("<I", data, 0x50, 8)               # ifc[0] → cluster 8
    struct.pack_into("<II", data, 8 * _P2_CL, 9, 10)    # indirect FAT → 9, 10
    for rel in range(512):                              # every FAT entry free…
        c, o = divmod(rel, _P2_CL // 4)
        struct.pack_into("<I", data, (9 + c) * _P2_CL + o * 4, mc._FAT_END)
    struct.pack_into("<I", data, 9 * _P2_CL, mc._FAT_USED | mc._FAT_END)  # …but root
    root = _P2_ALLOC * _P2_CL
    for i, name in enumerate((".", "..")):
        e = mc._mk_entry(mc._MODE_DIR, 2 if i == 0 else 0, 0, name)
        data[root + i * 512:root + (i + 1) * 512] = e
    return bytes(data)


def make_psu(name="BESLES-54321GAME", title="PS2 TEST GAME"):
    icon = bytearray(0xC0 + 68)
    icon[0:4] = b"PS2D"
    icon[0xC0:0xC0 + len(title)] = title.encode()
    payload = b"x" * 3000                                # spans 3 clusters
    out = bytearray()
    out += mc._mk_entry(mc._MODE_DIR, 4, 0, name)
    out += mc._mk_entry(mc._MODE_DIR, 4, 0, ".")
    out += mc._mk_entry(mc._MODE_DIR, 4, 0, "..")
    for fname, content in (("icon.sys", bytes(icon)), ("data.bin", payload)):
        out += mc._mk_entry(mc._MODE_FILE, len(content), 0, fname)
        out += content + b"\x00" * (-len(content) % 1024)
    return bytes(out), bytes(icon), payload


def test_ps2():
    print("PS2")
    card = blank_ps2()
    check("blank card parses + lists no save", mc.read_saves(card) == [])

    psu, icon, payload = make_psu()
    card2 = mc.import_save(card, psu, "game.psu")
    saves = mc.read_saves(card2)
    check("import → one save", len(saves) == 1)
    check("serial parsed", saves and saves[0]["serial"] == "SLES-54321")
    check("title from icon.sys", saves and saves[0]["title"] == "PS2 TEST GAME")
    check("size = sum of files", saves and saves[0]["size"] == len(icon) + len(payload))

    fn, blob = mc.export_save(card2, "SLES-54321")
    check("export filename", fn.endswith(".psu"))
    folder, dot, dotdot, files = mc._parse_psu(blob)
    check("export keeps both files", [f[1] for f in files] == [icon, payload])
    reimport = mc.import_save(blank_ps2(), blob, fn)
    check("export re-imports", mc.read_saves(reimport) == saves)

    expect_raises("duplicate import refused", ValueError,
                  mc.import_save, card2, psu, "game.psu")

    card3 = mc.delete_save(card2, "SLES-54321")
    check("delete → card empty", mc.read_saves(card3) == [])
    card4 = mc.import_save(card3, psu, "game.psu")
    check("freed clusters reusable", len(mc.read_saves(card4)) == 1)
    expect_raises("delete missing → KeyError", KeyError,
                  mc.delete_save, card3, "SLES-99999")

    # same card with 16 spare bytes per page = ECC layout: reads fine, writes refused
    ecc = b"".join(card2[i * 512:(i + 1) * 512] + b"\x00" * 16
                   for i in range(len(card2) // 512))
    check("ECC card still lists saves", mc.read_saves(ecc) == saves)
    expect_raises("ECC import refused", ValueError, mc.import_save, ecc, psu, "game.psu")
    expect_raises("ECC delete refused", ValueError, mc.delete_save, ecc, "SLES-54321")


# ── GameCube ────────────────────────────────────────────────────────────────────

_GC_BLOCKS = 64          # 4 Mbit card


def blank_gc() -> bytes:
    card = bytearray(_GC_BLOCKS * mc._GC_BLOCK)
    d = bytearray(b"\xFF" * mc._GC_BLOCK)
    struct.pack_into(">H", d, 0x1FFA, 1)
    mc._gc_write_dir(card, d, 1)
    bat = bytearray(mc._GC_BLOCK)
    struct.pack_into(">H", bat, 0x0006, _GC_BLOCKS - 5)   # free blocks
    struct.pack_into(">H", bat, 0x0008, 4)                # last allocated
    mc._gc_write_bat(card, bat, 1)
    return bytes(card)


def make_gci(code="GALE01", name="SuperSmashBros0110290334", blocks=2) -> bytes:
    e = bytearray(mc._GC_ENT)
    e[0:6] = code.encode()
    e[0x08:0x08 + len(name)] = name.encode()
    struct.pack_into(">H", e, 0x36, 5)
    struct.pack_into(">H", e, 0x38, blocks)
    body = bytearray(blocks * mc._GC_BLOCK)
    body[:4] = b"GCSV"
    return bytes(e + body)


def test_gc():
    print("GameCube")
    card = blank_gc()
    check("blank card recognised", mc._is_gc(card))
    check("blank card lists no save", mc.read_saves(card) == [])

    gci = make_gci()
    card2 = mc.import_save(card, gci, "smash.gci")
    saves = mc.read_saves(card2)
    check("import → one save", len(saves) == 1)
    check("game code", saves and saves[0]["serial"] == "GALE")
    free_after = mc._be16(mc._gc_bat(card2)[2], 0x0006)
    check("free count decremented", free_after == _GC_BLOCKS - 5 - 2)

    fn, blob = mc.export_save(card2, "SuperSmashBros0110290334")
    check("export filename", fn.endswith(".gci"))
    check("export body intact", blob[mc._GC_ENT:] == gci[mc._GC_ENT:])
    reimport = mc.import_save(blank_gc(), blob, fn)
    check("export re-imports", mc.read_saves(reimport) == saves)

    expect_raises("duplicate import refused", ValueError,
                  mc.import_save, card2, gci, "smash.gci")

    card3 = mc.delete_save(card2, "SuperSmashBros0110290334")
    check("delete → card empty", mc.read_saves(card3) == [])
    check("free count restored", mc._be16(mc._gc_bat(card3)[2], 0x0006) == _GC_BLOCKS - 5)
    card4 = mc.import_save(card3, gci, "smash.gci")
    check("freed blocks reusable", len(mc.read_saves(card4)) == 1)
    expect_raises("delete missing → KeyError", KeyError,
                  mc.delete_save, card3, "NOPE")


# ── cross-format guards ─────────────────────────────────────────────────────────

def test_cross():
    print("Cross-format")
    psu = make_psu()[0]
    gci = make_gci()
    expect_raises(".psu on a PS1 card refused", ValueError,
                  mc.import_save, blank_ps1(), psu, "a.psu")
    expect_raises(".gci on a PS2 card refused", ValueError,
                  mc.import_save, blank_ps2(), gci, "a.gci")
    expect_raises(".mcs on a GC card refused", ValueError,
                  mc.import_save, blank_gc(), make_mcs(), "a.mcs")
    check("garbage never raises", mc.read_saves(b"\x00" * 4096) == [])


if __name__ == "__main__":
    test_ps1()
    test_ps2()
    test_gc()
    test_cross()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All memcard tests passed.")
