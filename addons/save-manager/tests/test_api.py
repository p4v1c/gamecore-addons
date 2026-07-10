"""End-to-end API tests against a synthetic save tree — no emulator needed.

Builds a fake HOME + GAMECORE_PATH in a temp dir (mgba flat saves, a
DuckStation per-game-title card, a PCSX2 multi-game card, a standalone Dolphin
.gci, an RPCS3 savedata+trophy pair), then drives the FastAPI app in-process.
Run with:  python tests/test_api.py     (needs fastapi + httpx installed)
"""
import io
import os
import struct
import sys
import tempfile
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_TMP = tempfile.TemporaryDirectory()
ROOT = Path(_TMP.name)
os.environ["GAMECORE_HOME"] = str(ROOT / "home")
os.environ["GAMECORE_PATH"] = str(ROOT / "GameCore")

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
import test_memcard as cards  # noqa: E402 — synthetic card builders
import memcard as mc          # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import server                 # noqa: E402

client = TestClient(server.app)
HOME = ROOT / "home"
GC = ROOT / "GameCore"
FAILURES = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def make_sfo(pairs: dict) -> bytes:
    keys, data, idx = b"", b"", []
    for k, v in pairs.items():
        raw = v.encode() + b"\x00"
        idx.append((len(keys), 0x0204, len(raw), len(raw), len(data)))
        keys += k.encode() + b"\x00"
        data += raw
    key_tbl = 20 + 16 * len(idx)
    pad = -(key_tbl + len(keys)) % 4
    out = struct.pack("<4sIIII", b"\x00PSF", 0x101, key_tbl, key_tbl + len(keys) + pad, len(idx))
    for e in idx:
        out += struct.pack("<HHIII", *e)
    return out + keys + b"\x00" * pad + data


def build_tree():
    # mgba — flat .sav/.ss0 next to the ROMs
    d = GC / "emu/mgba"
    d.mkdir(parents=True)
    (d / "Golden Sun.gba").write_bytes(b"ROM")
    (d / "Golden Sun.sav").write_bytes(b"S" * 512)
    (d / "Golden Sun.ss0").write_bytes(b"st" * 100)

    # DuckStation — PerGameTitle card (no serial in the file name) + a state
    ds = HOME / ".local/share/duckstation"
    card = mc.import_save(cards.blank_ps1(), cards.make_mcs(), "t.mcs")
    (ds / "memcards").mkdir(parents=True)
    (ds / "memcards/Test Game (Europe).mcd").write_bytes(card)
    (ds / "savestates").mkdir()
    (ds / "savestates/SLES-12345_1.sav").write_bytes(b"state!" * 10)

    # PCSX2 — one shared card holding TWO games
    p2 = HOME / ".config/PCSX2"
    c2 = mc.import_save(cards.blank_ps2(), cards.make_psu()[0], "a.psu")
    c2 = mc.import_save(c2, cards.make_psu(name="BESLES-99999OTHER", title="OTHER GAME")[0], "b.psu")
    (p2 / "memcards").mkdir(parents=True)
    (p2 / "memcards/Mcd001.ps2").write_bytes(c2)
    (p2 / "sstates").mkdir()

    # Dolphin — GCI-folder mode: one standalone .gci
    dol = HOME / ".local/share/dolphin-emu"
    (dol / "GC/EUR/Card A").mkdir(parents=True)
    (dol / "GC/EUR/Card A/01-GALE-smash.gci").write_bytes(cards.make_gci())
    (dol / "StateSaves").mkdir()

    # RPCS3 — savedata with PARAM.SFO + a trophy set with the same title
    r3 = HOME / ".config/rpcs3/dev_hdd0/home/00000001"
    sd = r3 / "savedata/BLES01234-SAVE01"
    sd.mkdir(parents=True)
    (sd / "PARAM.SFO").write_bytes(make_sfo({"TITLE": "Demon Quest"}))
    (sd / "SAVE.DAT").write_bytes(b"x" * 2048)
    tr = r3 / "trophy/NPWR11111_00"
    tr.mkdir(parents=True)
    (tr / "TROPCONF.SFM").write_text("<trophyconf><title-name>Demon Quest</title-name></trophyconf>")
    (tr / "TROPUSR.DAT").write_bytes(b"t" * 128)

    # Ryujinx — one save with ExtraData, one identified via imkvdb.arc only
    ry = HOME / ".var/app/io.github.ryubing.Ryujinx/config/Ryujinx"
    MK8 = 0x0100152000022000                      # Mario Kart 8 (known title)
    s1 = ry / "bis/user/save/0000000000000001"
    (s1 / "0").mkdir(parents=True)
    (s1 / "0/game.sav").write_bytes(b"mk8" * 100)
    (s1 / "1").mkdir()
    extra = bytearray(0x200)
    struct.pack_into("<Q", extra, 0, MK8)
    extra[0x20] = 1                               # Account save
    (s1 / "ExtraData0").write_bytes(extra)
    s2 = ry / "bis/user/save/0000000000000002"    # no ExtraData → indexer
    (s2 / "0").mkdir(parents=True)
    (s2 / "0/other.bin").write_bytes(b"z" * 64)
    key = bytearray(0x40)
    struct.pack_into("<Q", key, 0, 0x01007EF00011E000)   # Zelda BOTW
    key[0x20] = 1
    val = bytearray(0x40)
    struct.pack_into("<Q", val, 0, 2)
    arc = b"IMKV" + b"\x00" * 4 + struct.pack("<i", 1)
    arc += b"IMEN" + struct.pack("<ii", 0x40, 0x40) + bytes(key) + bytes(val)
    idx = ry / "bis/system/save/8000000000000000/0"
    idx.mkdir(parents=True)
    (idx / "imkvdb.arc").write_bytes(arc)

    # Xenia — portable content dir with one save + its header + the profile
    xe = GC / "lib/xenia/content/E030000012345678"
    sv = xe / "415608F3/00000001/SaveGame00"
    sv.mkdir(parents=True)
    (sv / "savegame.dat").write_bytes(b"x360" * 64)
    hd = xe / "415608F3/Headers/00000001"
    hd.mkdir(parents=True)
    (hd / "SaveGame00.header").write_bytes(
        b"\x00" * 8 + "Test 360 Game".encode("utf-16-be") + b"\x00" * 200)
    prof = xe / "FFFE07D1/00010000"
    prof.mkdir(parents=True)
    (prof / "E030000012345678").write_bytes(b"profile" * 10)

    # shadPS4 — v0.16 layout, title from the save's own param.sfo
    p4 = HOME / ".var/app/net.shadps4.shadPS4/data/shadPS4/home/1/savedata/CUSA01234/SPRJ0005"
    (p4 / "sce_sys").mkdir(parents=True)
    (p4 / "sce_sys/param.sfo").write_bytes(make_sfo(
        {"MAINTITLE": "PS4 Test Game", "TITLE_ID": "CUSA01234"}))
    (p4 / "memory.dat").write_bytes(b"ps4" * 200)


def games(emu):
    r = client.get(f"/api/games/{emu}")
    assert r.status_code == 200, r.text
    return r.json()


def test_listing():
    print("Listing")
    r = client.get("/api/emulators")
    check("GET /api/emulators", r.status_code == 200)
    emus = {e["id"]: e for e in r.json()}
    check("mgba available", emus["mgba"]["available"])
    check("duckstation available", emus["duckstation"]["available"])
    check("unavailable emus flagged", not emus["ppsspp"]["available"])

    g = games("mgba")
    check("mgba game found", len(g["games"]) == 1)
    check("mgba save+state grouped",
          g["games"][0]["saves"] == 1 and g["games"][0]["states"] == 1)


def test_duckstation_card():
    print("DuckStation per-game card")
    g = games("duckstation")
    keys = [x["key"] for x in g["games"]]
    check("card attributed to its game (single serial)", "SLES-12345" in keys, str(keys))
    game = next(x for x in g["games"] if x["key"] == "SLES-12345")
    card_rows = [e for e in game["entries"] if e.get("card")]
    incard_rows = [e for e in game["entries"] if e.get("in_card")]
    state_rows = [e for e in game["entries"] if e["kind"] == "state"]
    check("card row under the game", len(card_rows) == 1)
    check("in-card save row present", len(incard_rows) == 1)
    check("state grouped by serial", len(state_rows) == 1)

    # export just the in-card save
    e = incard_rows[0]
    r = client.get(f"/api/saves/duckstation/download", params={"id": e["id"], "save": e["save_key"]})
    check("in-card export", r.status_code == 200 and r.content[:1] == b"\x51")

    # delete just the in-card save (card stays, becomes empty → shared bucket)
    r = client.delete("/api/saves/duckstation", params={"id": e["id"], "save": e["save_key"]})
    check("in-card delete", r.status_code == 200)
    g2 = games("duckstation")
    check("save gone from the card",
          not any(x.get("in_card") for game in g2["games"] for x in game["entries"]))
    baks = list((HOME / ".local/share/duckstation/memcards").glob("*.bak-*"))
    check("card backed up before write", len(baks) == 1)
    # put it back for the later tests
    r = client.post("/api/saves/duckstation/upload",
                    params={"collection": 0, "card": "Test Game (Europe).mcd"},
                    files={"file": ("t.mcs", io.BytesIO(cards.make_mcs()))})
    check("in-card re-inject", r.status_code == 200, r.text)


def test_pcsx2_shared_card():
    print("PCSX2 shared card")
    g = games("pcsx2")
    check("two in-card games listed", len(g["games"]) == 2)
    check("multi-game card stays shared", len(g["other"]) == 1 and g["other"][0]["card"])
    e = next(x for game in g["games"] if game["key"] == "SLES-54321" for x in game["entries"])
    r = client.get("/api/saves/pcsx2/download", params={"id": e["id"], "save": e["save_key"]})
    check("in-card .psu export", r.status_code == 200 and len(r.content) > 1536)
    r = client.delete("/api/saves/pcsx2", params={"id": e["id"], "save": e["save_key"]})
    check("in-card delete", r.status_code == 200)
    g2 = games("pcsx2")
    check("one game left on the card", len(g2["games"]) == 1)
    # single game left → the card is now attributed to it, no longer "shared"
    check("card follows its last game", len(g2["other"]) == 0)


def test_dolphin_gci():
    print("Dolphin GCI folder")
    g = games("dolphin")
    check("standalone .gci is a game", any(x["key"] == "GALE" for x in g["games"]), str([x["key"] for x in g["games"]]))
    game = next(x for x in g["games"] if x["key"] == "GALE")
    check("title from the GCI header", game["title"] == "SuperSmashBros0110290334")
    check("plain file actions (not a card)", not game["entries"][0].get("card"))


def test_rpcs3_grouping():
    print("RPCS3 savedata + trophies")
    g = games("rpcs3")
    check("savedata and trophy grouped", len(g["games"]) == 1, str([x["key"] for x in g["games"]]))
    check("2 saves under the game", g["games"][0]["saves"] == 2)


def test_zip_roundtrip():
    print("Zip round-trips")
    # whole-game zip (mgba)
    r = client.get("/api/games/mgba/download", params={"key": "Golden Sun"})
    check("game zip", r.status_code == 200)
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    check("zip is base-relative", sorted(zf.namelist()) == ["Golden Sun.sav", "Golden Sun.ss0"])

    # destroy, then restore through upload-full
    sav = GC / "emu/mgba/Golden Sun.sav"
    orig = sav.read_bytes()
    client.delete("/api/saves/mgba", params={"id": "0/Golden Sun.sav"})
    check("deleted", not sav.exists())
    r2 = client.post("/api/saves/mgba/upload-full",
                     files={"file": ("backup.zip", io.BytesIO(r.content))})
    check("upload-full restores", r2.status_code == 200 and sav.read_bytes() == orig, r2.text)

    # full-emulator backup
    r = client.get("/api/saves/duckstation/download-all")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    check("full backup has card + state",
          any(n.startswith("memcards/") for n in names) and
          any(n.startswith("savestates/") for n in names), str(names))
    check("no backups bundled", not any(".bak-" in n for n in names))

    # zip with a single wrapping folder onto a flat collection → root stripped
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("batterysaves/Metroid.sav", b"M" * 128)
    buf.seek(0)
    r = client.post("/api/saves/mgba/upload", params={"collection": 0},
                    files={"file": ("saves.zip", buf)})
    check("flat-zip root stripped", r.status_code == 200 and (GC / "emu/mgba/Metroid.sav").is_file(), r.text)

    # upload-full refuses paths outside the save collections
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("dev_hdd0/evil.txt", b"nope")
    buf.seek(0)
    r = client.post("/api/saves/rpcs3/upload-full", files={"file": ("evil.zip", buf)})
    check("upload-full rejects stray paths", r.status_code == 400)

    # path traversal refused everywhere
    r = client.get("/api/saves/mgba/download", params={"id": "0/../../secret"})
    check("traversal refused", r.status_code == 403)


def test_ryujinx():
    print("Ryujinx (Switch)")
    g = games("ryujinx")
    keys = {x["key"]: x for x in g["games"]}
    check("ExtraData save identified", "0100152000022000" in keys, str(list(keys)))
    check("known title resolved", keys.get("0100152000022000", {}).get("title") == "Mario Kart 8 Deluxe")
    check("indexer-only save identified", "01007EF00011E000" in keys)

    r = client.get("/api/games/ryujinx/download", params={"key": "0100152000022000"})
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    check("normalized switch-title zip",
          zf.namelist() == ["switch-title/0100152000022000/1/game.sav"], str(zf.namelist()))

    # wreck the save, then restore the normalized zip → lands in 0/ AND 1/
    base = HOME / ".var/app/io.github.ryubing.Ryujinx/config/Ryujinx"
    sdir = base / "bis/user/save/0000000000000001"
    (sdir / "0/game.sav").write_bytes(b"corrupted")
    r2 = client.post("/api/saves/ryujinx/upload-full",
                     files={"file": ("mk8.zip", io.BytesIO(r.content))})
    check("normalized restore ok", r2.status_code == 200, r2.text)
    check("restored into 0/", (sdir / "0/game.sav").read_bytes() == b"mk8" * 100)
    check("mirrored into 1/", (sdir / "1/game.sav").read_bytes() == b"mk8" * 100)

    # a title with no container on the box is refused with a clear message
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("switch-title/0100000000010000/1/save.bin", b"odyssey")
    buf.seek(0)
    r3 = client.post("/api/saves/ryujinx/upload-full", files={"file": ("x.zip", buf)})
    check("unknown container refused", r3.status_code == 400 and "launch the game" in r3.text)

    # normalized members are refused on the wrong system
    buf.seek(0)
    r4 = client.post("/api/saves/xenia/upload-full", files={"file": ("x.zip", buf)})
    check("switch zip refused on xenia", r4.status_code == 400)


def test_xenia():
    print("Xenia (Xbox 360)")
    g = games("xenia")
    game = next((x for x in g["games"] if x["key"] == "415608F3"), None)
    check("save found by title id", game is not None, str([x["key"] for x in g["games"]]))
    check("name read from .header", game and game["title"] == "Test 360 Game")
    check("profile package in shared", any("FFFE07D1" in e["name"] for e in g["other"]))

    r = client.get("/api/games/xenia/download", params={"key": "415608F3"})
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = sorted(zf.namelist())
    check("normalized x360-title zip",
          names == ["x360-title/415608F3/00000001/SaveGame00/savegame.dat",
                    "x360-title/415608F3/Headers/00000001/SaveGame00.header"], str(names))

    # restore is remapped onto the box's own profile (XUID)
    tid_dir = GC / "lib/xenia/content/E030000012345678/415608F3"
    (tid_dir / "00000001/SaveGame00/savegame.dat").write_bytes(b"old")
    r2 = client.post("/api/saves/xenia/upload-full",
                     files={"file": ("x360.zip", io.BytesIO(r.content))})
    check("normalized restore ok", r2.status_code == 200, r2.text)
    check("restored under the box profile",
          (tid_dir / "00000001/SaveGame00/savegame.dat").read_bytes() == b"x360" * 64)


def test_shadps4():
    print("shadPS4 (PS4)")
    g = games("shadps4")
    game = next((x for x in g["games"] if x["key"] == "CUSA01234"), None)
    check("save found by serial", game is not None, str([x["key"] for x in g["games"]]))
    check("title from save param.sfo", game and game["title"] == "PS4 Test Game")

    r = client.get("/api/games/shadps4/download", params={"key": "CUSA01234"})
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    check("normalized ps4-title zip",
          sorted(zf.namelist()) == ["ps4-title/CUSA01234/SPRJ0005/memory.dat",
                                    "ps4-title/CUSA01234/SPRJ0005/sce_sys/param.sfo"],
          str(zf.namelist()))

    # normalized restore of a game the box has never seen → created in place
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ps4-title/CUSA99999/SAVE00/data.bin", b"new game")
    buf.seek(0)
    r2 = client.post("/api/saves/shadps4/upload-full", files={"file": ("n.zip", buf)})
    dest = HOME / ".var/app/net.shadps4.shadPS4/data/shadPS4/home/1/savedata/CUSA99999/SAVE00/data.bin"
    check("new game restored", r2.status_code == 200 and dest.read_bytes() == b"new game", r2.text)


def test_card_game_zip():
    print("Card-game zip round-trips")
    # A game living INSIDE the shared PCSX2 card: its "⬇ all" zip must use
    # base-relative paths (memcards/…) and restore through upload-full.
    r = client.get("/api/games/pcsx2/download", params={"key": "SLES-99999"})
    check("card-only game zip", r.status_code == 200)
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    check("card path is base-relative", names == ["memcards/Mcd001.ps2"], str(names))
    r2 = client.post("/api/saves/pcsx2/upload-full",
                     files={"file": ("card.zip", io.BytesIO(r.content))})
    check("card-only game zip restorable", r2.status_code == 200, r2.text)

    # A game with a savestate AND a card attributed at the server layer:
    # the zip must bundle BOTH (the card used to be silently dropped).
    r = client.get("/api/games/duckstation/download", params={"key": "SLES-12345"})
    names = sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    check("state + card bundled",
          any(n.startswith("savestates/") for n in names) and
          any(n.startswith("memcards/") for n in names), str(names))


def test_backups_section():
    import time
    print("Backups section")
    r = client.get("/api/backups/mgba")
    check("backups listed", r.status_code == 200 and len(r.json()) >= 1)

    sav = GC / "emu/mgba/Golden Sun.sav"
    orig = sav.read_bytes()
    client.delete("/api/saves/mgba", params={"id": "0/Golden Sun.sav"})
    baks = client.get("/api/backups/mgba").json()
    latest = next(b for b in baks if b["name"] == "Golden Sun.sav")
    check("deleted original flagged", latest["orig_exists"] is False)

    r = client.post("/api/backups/mgba/restore", params={"id": latest["id"]})
    check("restore ok", r.status_code == 200, r.text)
    check("content back", sav.read_bytes() == orig)

    # restoring over a modified file first backs the modified version up
    sav.write_bytes(b"MODIFIED")
    time.sleep(1.1)                          # distinct backup timestamp
    n_before = len(client.get("/api/backups/mgba").json())
    r = client.post("/api/backups/mgba/restore", params={"id": latest["id"]})
    check("restore over existing ok", r.status_code == 200 and sav.read_bytes() == orig)
    check("pre-restore backup taken",
          len(client.get("/api/backups/mgba").json()) == n_before + 1)

    r = client.delete("/api/backups/mgba", params={"id": latest["id"]})
    check("backup deleted", r.status_code == 200)
    check("gone from the list",
          all(b["id"] != latest["id"] for b in client.get("/api/backups/mgba").json()))
    check("payload has backups", isinstance(games("mgba").get("backups"), list))

    # full-restore backs up at the save-folder level, never dev_hdd0 wholesale
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("dev_hdd0/home/00000001/savedata/BLES01234-SAVE01/SAVE.DAT", b"y" * 64)
    buf.seek(0)
    r = client.post("/api/saves/rpcs3/upload-full", files={"file": ("s.zip", buf)})
    sd = HOME / ".config/rpcs3/dev_hdd0/home/00000001"
    check("granular full-restore ok", r.status_code == 200 and
          (sd / "savedata/BLES01234-SAVE01/SAVE.DAT").read_bytes() == b"y" * 64, r.text)
    check("backup at save level, not dev_hdd0",
          any(p.name.startswith("BLES01234-SAVE01.bak-") for p in (sd / "savedata").iterdir())
          and not any(p.name.startswith("dev_hdd0.bak-")
                      for p in (HOME / ".config/rpcs3").iterdir()))


def test_backup_pruning():
    print("Backup pruning")
    sav = GC / "emu/mgba/Golden Sun.sav"
    for i in range(6):
        server._backup(sav)
        os.utime(sav)                     # distinct mtimes not needed; names differ by ts
        # force distinct timestamps in the name
        import time
        time.sleep(1.1) if i < 5 else None
    baks = [p for p in sav.parent.iterdir() if p.name.startswith("Golden Sun.sav.bak-")]
    check("at most 3 backups kept", len(baks) <= 3, str(len(baks)))


if __name__ == "__main__":
    build_tree()
    test_listing()
    test_duckstation_card()
    test_pcsx2_shared_card()
    test_dolphin_gci()
    test_rpcs3_grouping()
    test_ryujinx()
    test_xenia()
    test_shadps4()
    test_zip_roundtrip()
    test_card_game_zip()
    test_backups_section()
    test_backup_pruning()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All API tests passed.")
