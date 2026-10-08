"""GameCore profiles: each profile's saves, the swap guard, copies between profiles.

Builds its own tree in a temp dir (owner saves at the emulators' usual places,
one profile "Sam" under <DATA>/emu/profile-saves/<id>/<system>/) and drives the
API in-process with the core's profile list stubbed. Run with:

    python tests/test_profiles.py

LAYOUT (profiles.py) is checked against tests/fixtures/core_profile_saves.json,
the `profileSaves` blocks copied from GamecoreRenew's catalog/*/pack.json.
With GAMECORE_SRC pointing at a GamecoreRenew checkout, the live packs are
checked too, so a pack that changes its layout fails here.
"""
import hashlib
import io
import json
import os
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
os.environ["GAMECORE_DATA"] = str(ROOT / "userdata")
os.environ["GAMECORE_BACKEND_PORT"] = "9"        # nothing listens: the stub below answers

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent.parent / "shared" / "py"))
import test_memcard as cards  # noqa: E402 — synthetic card builders
import memcard as mc          # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import profiles               # noqa: E402
import server                 # noqa: E402

client = TestClient(server.app)
HOME, GCD = ROOT / "home", ROOT / "userdata"
OWNER = {"id": "aaaa0000aaaa0000", "name": "Jimmy", "color": "#b8501b", "avatar": "cat", "primary": True}
SAM = {"id": "bbbb1111bbbb1111", "name": "Sam", "color": "#127a6d", "avatar": None, "primary": False}
PEOPLE = [OWNER, SAM]
profiles._real_listing = profiles.listing
profiles.listing = lambda: PEOPLE
SAM_DIR = profiles.ROOT / SAM["id"]
FAILURES = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


RPCS3 = HOME / ".config/rpcs3"
EDEN = HOME / ".var/app/dev.eden_emu.eden/data/eden"
RYU = HOME / ".var/app/io.github.ryubing.Ryujinx/config/Ryujinx"
# Eden left the core: its profile folders stay as the Eden-era pack laid them.
EDEN_ERA = {"eden"}
CEMU = HOME / ".var/app/info.cemu.Cemu/data/Cemu"
PCSX2 = HOME / ".config/PCSX2"
DUCK = HOME / ".local/share/duckstation"
MK8 = "0100152000022000"
USER = "0" * 31 + "1"
BOTW = "01007EF00011E000"


def ryujinx_save(folder: Path, title: str, data: bytes) -> None:
    """A Ryujinx container: data in 0/, the title in ExtraData0 (ryujinx.py)."""
    write(folder / "0/save.bin", data)
    extra = bytearray(0x200)
    extra[0:8] = int(title, 16).to_bytes(8, "little")
    extra[0x20] = 1
    write(folder / "ExtraData0", bytes(extra))


def build_tree():
    # Owner: saves where the emulators always kept them.
    write(GCD / "emu/gba/Golden Sun.gba", b"ROM")
    write(GCD / "emu/gba/Golden Sun.sav", b"owner-gs" * 64)
    write(GCD / "emu/gb/Tetris.gb", b"ROM")
    write(GCD / "emu/melonds/Pokemon Platinum.nds", b"ROM")
    write(GCD / "emu/melonds/Pokemon Platinum.sav", b"owner-pp" * 64)
    write(GCD / "emu/melonds/Pokemon Platinum.sav.2", b"player2" * 64)
    write(RPCS3 / "dev_hdd0/home/00000001/savedata/BLES01234-SAVE01/SAVE.DAT", b"owner-ps3" * 64)
    write(EDEN / f"nand/user/save/0000000000000000/{USER}/{MK8}/save.bin", b"owner-mk8" * 64)
    ryujinx_save(RYU / "bis/user/save/0000000000000001", MK8, b"owner-ryu-mk8" * 64)
    write(CEMU / "mlc01/usr/save/00050000/10101c00/user/save.dat", b"owner-wiiu" * 64)
    two_games = mc.import_save(cards.blank_ps2(), cards.make_psu()[0], "a.psu")
    two_games = mc.import_save(two_games, cards.make_psu(name="BESLES-99999OTHER", title="OTHER")[0], "b.psu")
    write(PCSX2 / "memcards/Mcd001.ps2", two_games)
    write(DUCK / "memcards/SLES-12345.mcd", mc.import_save(cards.blank_ps1(), cards.make_mcs(), "t.mcs"))
    write(HOME / ".local/share/dolphin-emu/StateSaves/GALE01.s01", b"state" * 64)
    # Emulators the owner has run but holds no save in: their folders exist.
    for d in (".local/share/gopher64", ".config/ppsspp/PSP", ".local/share/azahar-emu",
              ".local/share/shadPS4"):
        (HOME / d).mkdir(parents=True)

    # Sam: the same systems, in Sam's folder, laid out as the packs declare.
    write(SAM_DIR / "gba/Tetris.sav", b"sam-tetris" * 64)
    write(GCD / "emu/gbc/Super Mario Bros. Deluxe.gbc", b"ROM")
    write(SAM_DIR / "gba/Super Mario Bros. Deluxe.sav", b"sam-smb" * 64)
    write(SAM_DIR / "gba/states/Golden Sun.ss0", b"sam-state" * 64)
    write(SAM_DIR / "melonds/Pokemon Platinum.sav", b"sam-pp" * 64)
    write(SAM_DIR / "gopher64/sram/ZELDA MAJORA-0123456789ABCDEF.fla", b"sam-n64" * 64)
    write(SAM_DIR / "rpcs3/savedata/BLUS55555-SLOT0/SAVE.DAT", b"sam-ps3" * 64)
    write(SAM_DIR / f"switch/save/0000000000000000/{USER}/01006F8002326000/main.dat", b"sam-acnh" * 64)
    ryujinx_save(SAM_DIR / "switch/user-save/0000000000000001", BOTW, b"sam-botw" * 64)
    write(SAM_DIR / "cemu/save/10102000/user/save.dat", b"sam-wiiu" * 64)
    write(SAM_DIR / "ppsspp/SAVEDATA/ULUS10041DATA/DATA.BIN", b"sam-psp" * 64)
    write(SAM_DIR / "duckstation/savestates/SLES-12345_1.sav", b"sam-ps1" * 64)
    write(SAM_DIR / "pcsx2/memcards/Mcd001.ps2", cards.blank_ps2())
    write(SAM_DIR / "azahar/sdmc/Nintendo 3DS/id0/id1/title/00040000/00055d00/data/00000001/main",
          b"sam-3ds" * 64)
    write(SAM_DIR / "shadps4/savedata/CUSA01234/SPRJ0005/memory.dat", b"sam-ps4" * 64)


def games(emu, who=None):
    r = client.get(f"/api/games/{emu}", params={"profile": who["id"]} if who else {})
    assert r.status_code == 200, r.text
    return r.json()


def keys(payload):
    return {g["key"] for g in payload["games"]}


# ── LAYOUT against the core's packs ──────────────────────────────────────────

def _strip(value: str) -> str:
    return value.replace("@SAVES@", "").strip("/")


def _related(a: str, b: str) -> bool:
    pa, pb = Path("/x", a), Path("/x", b)
    return pa == pb or pa.is_relative_to(pb) or pb.is_relative_to(pa)


def check_layout(packs: dict, source: str):
    print(f"LAYOUT against {source}")
    for emu, lay in profiles.LAYOUT.items():
        if emu in EDEN_ERA:
            continue
        spec = packs.get(lay["system"], {}).get("profileSaves")
        check(f"{emu}: the core separates {lay['system']}", spec is not None, str(spec))
        if lay["system"] == "melonds":
            check("melonds: per-instance in the core", spec == "per-instance", str(spec))
            continue
        if not isinstance(spec, dict):
            continue
        declared = [_strip(k["value"]) for k in spec.get("keys", []) if "@SAVES@" in k["value"]]
        for ci, folder in lay["keys"].items():
            check(f"{emu}: collection {ci} → '{folder}' is a declared option",
                  any(_related(folder, d) for d in declared), str(declared))
        for d in declared:
            check(f"{emu}: declared '{d}' is mirrored",
                  any(_related(folder, d) for folder in lay["keys"].values()), str(lay["keys"]))
        dirs = spec.get("dirs", [])
        check(f"{emu}: every swapped folder mirrored", len(dirs) == len(lay["dirs"]), f"{dirs} vs {lay['dirs']}")
        for entry in dirs:
            src = entry.get("config") or entry["path"]
            name = entry.get("as") or Path(src).name
            hit = [rel for rel, n in lay["dirs"].items() if n == name and src.endswith(rel)]
            check(f"{emu}: swapped {src} → {name}", len(hit) == 1, str(lay["dirs"]))
    for pid, data in packs.items():
        spec = data.get("profileSaves")
        if isinstance(spec, dict) and spec.get("supported") is False:
            check(f"{pid}: shared in the core, shared here",
                  all(lay["system"] != pid for lay in profiles.LAYOUT.values()))
    for emu in EDEN_ERA:
        lay = profiles.LAYOUT[emu]
        check(f"{emu}: its folder names clash with no current one",
              not set(lay["dirs"].values()) & {n for e, l in profiles.LAYOUT.items() if e not in EDEN_ERA
                                               and l["system"] == lay["system"] for n in l["dirs"].values()})
    for emu, owner in (("gb", "gba"), ("gbc", "gba"), ("dolphin", "gamecube")):
        check(f"{emu} shares {owner}'s folder", profiles.LAYOUT[emu]["system"] == owner)


def test_layout():
    fixture = json.loads((HERE / "fixtures/core_profile_saves.json").read_text())
    check_layout(fixture, "the fixture")
    src = os.environ.get("GAMECORE_SRC")
    if src:
        live = {}
        for f in Path(src, "catalog").glob("*/pack.json"):
            d = json.loads(f.read_text())
            live[f.parent.name] = {k: d[k] for k in ("profileSaves", "sharesEmulator") if k in d}
        check_layout(live, src)


# ── per-profile listing ──────────────────────────────────────────────────────

def test_profile_views():
    print("Each profile sees its own saves")
    r = client.get("/api/profiles")
    check("profiles listed for the page", [p["id"] for p in r.json()] == [OWNER["id"], SAM["id"]])
    expect = {
        "gb": "Tetris", "gba": "Golden Sun", "melonds": "Pokemon Platinum",
        "gopher64": "ZELDA MAJORA", "rpcs3": "BLUS55555", "switch": BOTW, "eden": "01006F8002326000",
        "cemu": "00050000/10102000", "ppsspp": "ULUS10041", "duckstation": "SLES-12345",
        "azahar": "00040000/00055d00", "shadps4": "CUSA01234",
    }
    for emu, key in expect.items():
        got = keys(games(emu, SAM))
        check(f"{emu}: Sam's save listed", key in got, str(got))
    check("gb: Sam's Game Boy save is not under Advance", "Tetris" not in keys(games("gba", SAM)))
    check("gbc: a ROM name with a dot still matches",
          keys(games("gbc", SAM)) == {"Super Mario Bros. Deluxe"}, str(keys(games("gbc", SAM))))
    check("gba: Sam's state found in states/", games("gba", SAM)["games"][0]["states"] == 1)
    check("owner never sees Sam's PS3 save", "BLUS55555" not in keys(games("rpcs3")))
    check("Sam never sees the owner's PS3 save", "Demon Quest" not in keys(games("rpcs3", SAM))
          and len(games("rpcs3", SAM)["games"]) == 1)
    mel = games("melonds", SAM)["games"][0]
    check("melonDS: Sam has player 1's save only", mel["saves"] == 1, str(mel))
    dol = games("dolphin", SAM)
    check("Dolphin: shared states are not Sam's", not dol["games"] and
          all(c["kind"] == "save" for c in dol["collections"]), str(dol["collections"]))
    x = games("xenia", SAM)
    check("Xenia: shared by every profile", x["shared"] and not x["games"])
    check("unknown profile refused",
          client.get("/api/games/rpcs3", params={"profile": "cccc"}).status_code == 404)
    check("primary's id is the owner's view", keys(games("rpcs3", OWNER)) == keys(games("rpcs3")))
    r = client.get("/api/saves/rpcs3/download", params={"id": "0/BLUS55555-SLOT0", "profile": SAM["id"]})
    check("download from Sam's folder", r.status_code == 200 and
          zipfile.ZipFile(io.BytesIO(r.content)).namelist() == ["BLUS55555-SLOT0/SAVE.DAT"], r.text[:200])


# ── the swap guard ───────────────────────────────────────────────────────────

def swap(folder: Path, target: Path):
    """What the core does while Sam plays: park the owner's folder, link Sam's."""
    folder.rename(folder.with_name(folder.name + profiles.PRIMARY_SUFFIX))
    target.mkdir(parents=True, exist_ok=True)
    os.symlink(target, folder, target_is_directory=True)


def unswap(folder: Path):
    folder.unlink()
    folder.with_name(folder.name + profiles.PRIMARY_SUFFIX).rename(folder)


def test_swap_guard():
    print("A profile's game holds the saves")
    saves = RPCS3 / "dev_hdd0/home/00000001/savedata"
    swap(saves, SAM_DIR / "rpcs3/savedata")
    owner = games("rpcs3")
    check("owner's saves read from the parked folder", any(
        e["id"] == "0/BLES01234-SAVE01" for g in owner["games"] for e in g["entries"]), str(keys(owner)))
    check("Sam's save not given to the owner", "BLUS55555" not in keys(owner), str(keys(owner)))
    check("the page says who plays", owner["playing"] == "Sam is playing PlayStation 3: close the game first.",
          str(owner["playing"]))
    r = client.delete("/api/saves/rpcs3", params={"id": "0/BLES01234-SAVE01"})
    check("delete refused", r.status_code == 409 and "Sam is playing" in r.text, r.text)
    r = client.post("/api/saves/rpcs3/upload-full", files={"file": ("x.zip", io.BytesIO(b"PK"))})
    check("full restore refused", r.status_code == 409)
    r = client.post("/api/saves/rpcs3/upload", params={"collection": 0, "profile": SAM["id"]},
                    files={"file": ("x.bin", io.BytesIO(b"x"))})
    check("a write to Sam's side refused too", r.status_code == 409)
    r = client.get("/api/saves/rpcs3/download", params={"id": "0/BLES01234-SAVE01"})
    check("the owner's save still downloads", r.status_code == 200 and b"owner-ps3" in
          zipfile.ZipFile(io.BytesIO(r.content)).read("BLES01234-SAVE01/SAVE.DAT"))
    r = client.get("/api/saves/rpcs3/download", params={"id": "0/BLUS55555-SLOT0"})
    check("the owner's side can't reach Sam's save", r.status_code == 404, str(r.status_code))
    unswap(saves)
    check("writes allowed again after the game", games("rpcs3")["playing"] is None)

    # Cemu's swapped folder sits one level below the collection.
    wiiu = CEMU / "mlc01/usr/save/00050000"
    swap(wiiu, SAM_DIR / "cemu/save")
    owner = keys(games("cemu"))
    check("Cemu: owner's game from the parked folder", owner == {"00050000/10101c00"}, str(owner))
    check("Cemu: refused while Sam plays",
          client.delete("/api/saves/cemu", params={"id": "0/00050000/10101c00"}).status_code == 409)
    unswap(wiiu)

    # Ryujinx: bis/user/save (and its index) while Sam plays.
    save = RYU / "bis/user/save"
    swap(save, SAM_DIR / "switch/user-save")
    check("Ryujinx: owner's MK8 from the parked folder", keys(games("switch")) == {MK8})
    check("Ryujinx: Sam's BOTW still Sam's", keys(games("switch", SAM)) == {BOTW})
    check("Ryujinx: refused while Sam plays", client.delete(
        "/api/saves/switch", params={"id": "0/0000000000000001"}).status_code == 409)
    unswap(save)

    # Eden: a swap an Eden-era crash left behind.
    save = EDEN / "nand/user/save"
    swap(save, SAM_DIR / "switch/save")
    check("Eden: owner's MK8 from the parked folder", keys(games("eden")) == {MK8})
    check("Eden: Sam's island still Sam's", "01006F8002326000" in keys(games("eden", SAM)))
    unswap(save)

    # `keys` emulators: the core records the options it changed.
    ini = write(PCSX2 / "inis/PCSX2.ini", f'[Folders]\nMemoryCards = {SAM_DIR}/pcsx2/memcards\n'.encode())
    store = profiles.ROOT / profiles.STORE
    write(store, json.dumps({f"key:{ini}:Folders:MemoryCards": "memcards"}).encode())
    check("PCSX2: Sam's options in place = Sam plays", games("pcsx2")["playing"] is not None)
    store.unlink()
    check("PCSX2: options back = nobody plays", games("pcsx2")["playing"] is None)


# ── copies between profiles ──────────────────────────────────────────────────

def copy(emu, key, src, dst):
    return client.post(f"/api/games/{emu}/copy", params={"key": key, "source": src["id"], "target": dst["id"]})


def test_copy():
    print("Copy a game's saves between profiles")
    owner_sav = GCD / "emu/melonds/Pokemon Platinum.sav"
    before = sha(owner_sav)
    sam_sav = SAM_DIR / "melonds/Pokemon Platinum.sav"
    r = copy("melonds", "Pokemon Platinum", OWNER, SAM)
    check("owner → Sam ok", r.status_code == 200, r.text)
    check("owner's save byte-identical", sha(owner_sav) == before)
    check("Sam has the owner's save", sha(sam_sav) == before)
    check("players 2-4 stay beside the ROM", not (SAM_DIR / "melonds/Pokemon Platinum.sav.2").exists())
    baks = client.get("/api/backups/melonds", params={"profile": SAM["id"]}).json()
    check("Sam's old save backed up first", any(b["name"] == "Pokemon Platinum.sav" for b in baks), str(baks))

    r = copy("rpcs3", "BLUS55555", SAM, OWNER)
    check("Sam → owner (folder save)", r.status_code == 200 and
          (RPCS3 / "dev_hdd0/home/00000001/savedata/BLUS55555-SLOT0/SAVE.DAT").read_bytes()
          == b"sam-ps3" * 64, r.text)
    r = copy("switch", MK8, OWNER, SAM)
    check("Ryujinx: no copy between profiles (folders numbered per index)", r.status_code == 400, r.text)
    r = copy("eden", MK8, OWNER, SAM)
    check("Eden: read only, nothing copied", r.status_code == 403 and
          not (SAM_DIR / f"switch/save/0000000000000000/{USER}/{MK8}").exists(), r.text)
    r = copy("cemu", "00050000/10101c00", OWNER, SAM)
    check("Cemu: owner → Sam lands in Sam's save folder", r.status_code == 200 and
          (SAM_DIR / "cemu/save/10101c00/user/save.dat").is_file(), r.text)
    r = copy("dolphin", "GALE", OWNER, SAM)
    check("Dolphin: shared states are not copied", r.status_code == 404, r.text)

    card = PCSX2 / "memcards/Mcd001.ps2"
    before = sha(card)
    r = copy("pcsx2", "SLES-54321", OWNER, SAM)
    check("PS2: one game into Sam's own card", r.status_code == 200, r.text)
    sam_card = [s["serial"] for s in mc.read_saves(SAM_DIR / "pcsx2/memcards/Mcd001.ps2")]
    check("Sam's card holds that game only", sam_card == ["SLES-54321"], str(sam_card))
    check("owner's card untouched", sha(card) == before)
    r = copy("pcsx2", "SLES-54321", OWNER, SAM)
    check("copying again replaces it", r.status_code == 200 and len(
        mc.read_saves(SAM_DIR / "pcsx2/memcards/Mcd001.ps2")) == 1, r.text)
    (SAM_DIR / "pcsx2/memcards/Mcd001.ps2").rename(SAM_DIR / "pcsx2/memcards/held.ps2")
    r = copy("pcsx2", "SLES-99999", OWNER, SAM)
    check("no card on Sam's side: refused, nothing written", r.status_code == 409 and
          not (SAM_DIR / "pcsx2/memcards/Mcd001.ps2").exists(), r.text)
    r = copy("duckstation", "SLES-12345", OWNER, SAM)
    check("PS1 per-game card copied whole", r.status_code == 200 and
          (SAM_DIR / "duckstation/memcards/SLES-12345.mcd").is_file(), r.text)
    check("same profile twice refused", copy("melonds", "Pokemon Platinum", SAM, SAM).status_code == 400)


# ── full backup ──────────────────────────────────────────────────────────────

def test_full_backup():
    print("Full backup carries every profile")
    r = client.get("/api/saves/rpcs3/download-all")
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    label = f"profiles/Sam-{SAM['id']}/"
    check("owner's saves under their usual names",
          "dev_hdd0/home/00000001/savedata/BLES01234-SAVE01/SAVE.DAT" in names, str(names))
    check("Sam's saves under Sam's label",
          f"{label}dev_hdd0/home/00000001/savedata/BLUS55555-SLOT0/SAVE.DAT" in names, str(names))
    sam_save = SAM_DIR / "rpcs3/savedata/BLUS55555-SLOT0/SAVE.DAT"
    owner_save = RPCS3 / "dev_hdd0/home/00000001/savedata/BLES01234-SAVE01/SAVE.DAT"
    owner_before = owner_save.read_bytes()
    sam_save.write_bytes(b"broken")
    r2 = client.post("/api/saves/rpcs3/upload-full", files={"file": ("all.zip", io.BytesIO(r.content))})
    check("restoring it puts Sam's save back in Sam's folder", r2.status_code == 200 and
          sam_save.read_bytes() == b"sam-ps3" * 64, r2.text)
    check("and the owner's in the owner's", owner_save.read_bytes() == owner_before)
    r = client.get("/api/saves/rpcs3/download-all", params={"profile": SAM["id"]})
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    check("Sam's own backup uses plain names", all(n.startswith("dev_hdd0/") for n in names), str(names))
    r = client.get("/api/saves/eden/download-all", params={"profile": SAM["id"]})
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    check("Sam's Eden backup stays normalized",
          "switch-title/01006F8002326000/1/main.dat" in names, str(names))
    r2 = client.post("/api/saves/eden/upload-full", params={"profile": SAM["id"]},
                     files={"file": ("sw.zip", io.BytesIO(r.content))})
    check("but Eden takes no restore", r2.status_code == 403, r2.text)
    r = client.get("/api/saves/switch/download-all", params={"profile": SAM["id"]})
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    check("Sam's Ryujinx backup is normalized", f"switch-title/{BOTW}/1/save.bin" in names, str(names))


# ── a box without profiles ───────────────────────────────────────────────────

def test_no_profiles():
    print("No profiles: as before")
    profiles.listing = lambda: []
    check("no profile for the page", client.get("/api/profiles").json() == [])
    names = zipfile.ZipFile(io.BytesIO(client.get("/api/saves/rpcs3/download-all").content)).namelist()
    check("backup has no profile folder", not any(n.startswith("profiles/") for n in names), str(names))
    check("a profile id is unknown", client.get("/api/games/rpcs3",
          params={"profile": SAM["id"]}).status_code == 404)
    profiles.listing = lambda: PEOPLE


def test_core_answers():
    print("Reading the core")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    answer = {}

    class Core(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    core = HTTPServer(("127.0.0.1", 0), Core)
    threading.Thread(target=core.serve_forever, daemon=True).start()
    real = profiles._real_listing
    profiles.CORE_PROFILES = f"http://127.0.0.1:{core.server_port}/api/profiles"

    def ask(payload):
        answer.clear()
        answer.update(payload)
        profiles._cache = (-profiles.CACHE_S - 1, [])
        return real()

    check("an unnamed primary alone is no profiles",
          ask({"profiles": [{"id": "a1", "name": "", "primary": True}]}) == [])
    got = ask({"profiles": [{"id": "a1", "name": "Jimmy", "primary": True, "color": "#b8501b"},
                            {"id": "b2", "name": "Sam", "color": "red;x"},
                            {"id": "../x", "name": "Evil"}]})
    check("named profiles listed, a bad id dropped", [p["id"] for p in got] == ["a1", "b2"], str(got))
    check("a colour that isn't #rrggbb replaced", got[1]["color"] == profiles._DEFAULT_COLOR)
    core.shutdown()
    core.server_close()
    check("core unreachable = no profiles", ask({}) == [])


if __name__ == "__main__":
    build_tree()
    test_layout()
    test_profile_views()
    test_swap_guard()
    test_copy()
    test_full_backup()
    test_no_profiles()
    test_core_answers()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All profile tests passed.")
