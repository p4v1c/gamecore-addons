"""Where each emulator stores saves and save states, and how each entry maps
back to a game — the per-emulator identity resolver.

Two natures of data (see README):
  - "save"  = native saves (memory card / battery / save data). Portable.
  - "state" = save states (RAM snapshot). Version-specific, NOT portable.

A "collection" = one directory to scan:
  subpath : path relative to the emulator's base dir ("" = base)
  mode    : "files" — plain files in the directory
            "dirs"  — game directories matched by `glob` (each system nests
                      its games at a different depth — that glob encodes it)
            "cards" — shared memory-card files, scanned recursively
            "any"   — files or directories at the top level
  exts    : lowercase extensions for files/cards (empty = any)
  kind    : "save" | "state"
  group   : which resolver turns an entry into (game_key, game_title, icon).
            game_key "" = not tied to one game → "Shared & system files".

Identity sources per system:
  Sony     — full serial incl. dash (SLES-03736). RPCS3/PSP savedata folders
             each carry their own PARAM.SFO (TITLE) + ICON0.PNG; RPCS3 slots
             of one game are grouped by that TITLE, trophies by TROPCONF.SFM.
             shadPS4 saves key on the CUSA serial; the title comes from the
             installed game's param.sfo (fallback: the save's own MAINTITLE).
  Nintendo — title-id trees at the right depth: Wii <hi>/<lo> (lo = ASCII game
             code, matched to the RVZ headers of the box's ROMs), Wii U
             <hi>/<lo> (meta/meta.xml longname + iconTex.tga), 3DS
             title/<hi>/<lo> (matched to .3ds NCSD media ids), Switch
             <TITLE-ID> (well-known ids + ids found in ROM file names).
             citron-neo (yuzu-family) names each save dir after its title id
             directly. Standalone Dolphin .gci files are read for their game
             code.
  X360     — content/<XUID>/<TitleID>; the save's display name is read from
             its Headers/…/*.header package metadata.
  N64      — internal cartridge name (ROM header), matched to the box's ROMs
             for the display name and cover.
  GBA/DS   — ROM base name, matched to the GameCore covers. Unchanged.
"""
import json
import os
import re
from pathlib import Path, PurePosixPath

import memcard
import sfo

# GAMECORE_HOME lets tests (and unusual setups) point the scan somewhere else.
HOME = Path(os.environ.get("GAMECORE_HOME") or Path.home())
GC = Path(os.environ.get("GAMECORE_PATH", "/opt/GameCore"))
COVERS = GC / "emu" / "covers"
ROMS = GC / "emu"


def C(subpath, mode, kind, exts=(), group="none", glob="*"):
    return {"subpath": subpath, "mode": mode, "kind": kind,
            "exts": [e.lower() for e in exts], "group": group, "glob": glob}


CATALOG = {
    "mgba": {"label": "Game Boy Advance", "bases": [GC / "emu/mgba"], "collections": [
        C("", "files", "save", [".sav", ".srm"], "rom"),
        C("", "files", "state", [f".ss{i}" for i in range(10)], "rom"),
    ]},
    "melonds": {"label": "Nintendo DS", "bases": [GC / "emu/melonds"], "collections": [
        C("", "files", "save", [".sav", ".nvm"], "rom"),
        C("", "files", "state", [".mln"] + [f".ml{i}" for i in range(1, 9)], "rom"),
    ]},
    "gopher64": {"label": "Nintendo 64", "bases": [
        HOME / ".var/app/io.github.gopher64.gopher64/data/gopher64", HOME / ".local/share/gopher64"], "collections": [
        C("saves", "files", "save", [".eep", ".mpk", ".sra", ".fla", ".srm"], "n64"),
        C("states", "files", "state", (), "n64"),
    ]},
    "ppsspp": {"label": "PSP", "bases": [
        HOME / ".var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP", HOME / ".config/ppsspp/PSP"], "collections": [
        C("SAVEDATA", "dirs", "save", (), "psp_save"),
        C("PPSSPP_STATE", "files", "state", (), "psp_state"),
    ]},
    "duckstation": {"label": "PlayStation 1", "bases": [
        HOME / ".local/share/duckstation", HOME / ".var/app/org.duckstation.DuckStation/data/duckstation"], "collections": [
        C("memcards", "cards", "save", [".mcd", ".mcr", ".ps"], "card_or_serial"),
        C("savestates", "files", "state", (), "ps_serial"),
    ]},
    "pcsx2": {"label": "PlayStation 2", "bases": [
        HOME / ".config/PCSX2", HOME / ".var/app/net.pcsx2.PCSX2/config/PCSX2"], "collections": [
        C("memcards", "cards", "save", [".ps2", ".mcd", ".mcr"], "card_or_serial"),
        C("sstates", "files", "state", (), "ps_serial"),
    ]},
    "dolphin": {"label": "GameCube / Wii", "bases": [
        HOME / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu", HOME / ".local/share/dolphin-emu"], "collections": [
        C("Wii/title", "dirs", "save", (), "wii", glob="*/*/data"),
        C("GC", "cards", "save", [".raw", ".gci", ".sav"], "gc_card"),
        C("StateSaves", "files", "state", (), "dolphin_state"),
    ]},
    "rpcs3": {"label": "PlayStation 3", "bases": [
        HOME / ".config/rpcs3", HOME / ".var/app/net.rpcs3.RPCS3/config/rpcs3"], "collections": [
        C("dev_hdd0/home/00000001/savedata", "dirs", "save", (), "rpcs3_save"),
        C("dev_hdd0/home/00000001/trophy", "dirs", "save", (), "rpcs3_trophy"),
        C("savestates", "any", "state", (), "rpcs3_state"),
    ]},
    "azahar": {"label": "Nintendo 3DS", "bases": [
        HOME / ".var/app/org.azahar_emu.Azahar/data/azahar-emu",
        HOME / ".local/share/azahar-emu", HOME / ".local/share/citra-emu"], "collections": [
        C("sdmc/Nintendo 3DS", "dirs", "save", (), "n3ds", glob="*/*/title/*/*"),
        # extdata carries real progress for some games (Animal Crossing NL…);
        # extdata ids don't map cleanly to title ids, so it stays "shared"
        C("sdmc/Nintendo 3DS", "dirs", "save", (), "shared", glob="*/*/extdata/*/*"),
        C("states", "files", "state", (), "n3ds_state"),
    ]},
    "cemu": {"label": "Wii U", "bases": [
        HOME / ".var/app/info.cemu.Cemu/data/Cemu", HOME / ".local/share/Cemu"], "collections": [
        C("mlc01/usr/save", "dirs", "save", (), "wiiu", glob="*/*"),
    ]},
    "citron-neo": {"label": "Nintendo Switch", "bases": [
        # citron-neo (yuzu-family) still uses the historical citron data dir
        HOME / ".local/share/citron"], "collections": [
        # yuzu-family layout: the folder name IS the title id
        C("nand/user/save", "dirs", "save", (), "switch", glob="0000000000000000/*/*"),
        C("nand/user/save/cache", "dirs", "save", (), "shared"),
    ]},
    "xenia": {"label": "Xbox 360", "bases": [GC / "lib/xenia"], "collections": [
        # Canary is portable on Windows builds (also under Wine): saves live in
        # content/<profile XUID>/<TitleID>/ next to the exe. That per-title dir
        # holds 00000001/ (the save packages) AND Headers/ (their metadata) —
        # both are needed for a transfer, so the title dir is the save unit.
        C("content", "dirs", "save", (), "x360", glob="*/*"),
    ]},
    "shadps4": {"label": "PlayStation 4", "bases": [
        HOME / ".var/app/net.shadps4.shadPS4/data/shadPS4",
        HOME / ".local/share/shadPS4"], "collections": [
        C("home/1/savedata", "dirs", "save", (), "ps4_save", glob="*/*"),   # ≥ v0.16
        C("savedata/1", "dirs", "save", (), "ps4_save", glob="*/*"),        # ≤ v0.15
    ]},
}


def _load_local_bases() -> None:
    """Merge machine-specific save locations from an optional, gitignored
    local_bases.json next to this module — extra data dirs a particular box
    keeps that don't belong in the shared repo (e.g. a legacy Switch emulator).
    Format: {"<emu id>": ["/absolute/path", ...]}. Paths are prepended so
    resolve_base still weighs them by save count, not order."""
    import json
    p = Path(__file__).parent / "local_bases.json"
    try:
        extra = json.loads(p.read_text())
    except (OSError, ValueError):
        return
    for emu_id, paths in extra.items():
        if emu_id in CATALOG and isinstance(paths, list):
            CATALOG[emu_id]["bases"] = [Path(x) for x in paths] + CATALOG[emu_id]["bases"]


_load_local_bases()


def _base_savecount(emu_id: str, base: Path) -> int:
    """Cheap count of save entries a base would yield (globs only, no resolvers
    or metadata reads) — used to disambiguate between several existing bases."""
    n = 0
    for col in CATALOG[emu_id]["collections"]:
        cdir = base / col["subpath"] if col["subpath"] else base
        if not cdir.is_dir():
            continue
        try:
            n += sum(1 for _ in _candidates(cdir, col))
        except OSError:
            pass
    return n


_base_choice: dict = {}


def _declared_flatpak(emu_id: str):
    """True/False when the box's systems.json declares this emulator flatpak/
    native, None when it doesn't say (absent, unreadable, or no such id)."""
    try:
        systems = json.loads((GC / "config" / "systems.json").read_text())
        path = next((s.get("path", "") for s in systems if s.get("id") == emu_id), "")
    except (OSError, ValueError):
        return None
    return path == "flatpak" if path else None


def resolve_base(emu_id: str) -> Path | None:
    """The emulator's data dir. When several candidate bases exist (e.g. a
    Switch box with both Ryujinx and a yuzu-family emulator installed), pick the
    one that actually holds the most saves rather than blindly the first — so a
    freshly installed, empty emulator never hides an established save library.
    Ties go to the install systems.json declares (flatpak vs native): after a
    native→flatpak migration both trees hold identical copies, and the frozen
    native backup must not shadow the tree the emulator actually writes to.
    The save-count globs are memoized on the candidate dirs' mtimes: scan()
    runs on every request and re-globbing each time would be wasteful."""
    existing = [b for b in CATALOG[emu_id]["bases"] if b.exists()]
    if len(existing) <= 1:
        return existing[0] if existing else None
    declared = _declared_flatpak(emu_id)
    if declared is not None:
        # stable: declared variant first, so max() picks it on equal counts
        existing.sort(key=lambda b: (".var/app" in str(b)) != declared)
    sig = (declared,) + tuple((str(b), b.stat().st_mtime_ns) for b in existing)
    hit = _base_choice.get(emu_id)
    if hit is None or hit[0] != sig:
        hit = (sig, max(existing, key=lambda b: _base_savecount(emu_id, b)))
        _base_choice[emu_id] = hit
    return hit[1]


# ── caches ────────────────────────────────────────────────────────────────────
# ROM-header maps and the cover index are recomputed only when their source
# directory changes (the server is long-running).

_cache: dict = {}


def _cached(key: str, dep: Path, build):
    try:
        stamp = dep.stat().st_mtime_ns
    except OSError:
        stamp = None
    hit = _cache.get(key)
    if hit is not None and hit[0] == stamp:
        return hit[1]
    val = build() if stamp is not None else {}
    _cache[key] = (stamp, val)
    return val


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def cover_for(*candidates: str) -> Path | None:
    """Match a game to a GameCore cover. Covers are named after the ROM display
    name; compare ignoring case/punctuation so 'Zelda: BOTW' ≈ 'Zelda_BOTW'."""
    idx = _cached("covers", COVERS,
                  lambda: {_norm(p.stem): p for p in COVERS.glob("*.png")})
    for c in candidates:
        if c and (p := idx.get(_norm(c))):
            return p
    return None


def _prettify(stem: str) -> str:
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip()


def _clean_stem(stem: str) -> str:
    """ROM stem → display name: drop '(Europe) (En,Fr,…)' style blocks."""
    return _prettify(re.sub(r"\([^)]*\)", "", stem)) or _prettify(stem)


def _collapse(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


# ── PlayStation disc serials (map a save's serial back to a real game) ─────────
# The save side only knows the serial (SLES-53557); the real title + cover live
# with the ROM. A PS1/PS2 disc records its serial in SYSTEM.CNF (BOOT/BOOT2 =
# cdrom0:\SLES_535.57;1). Read it straight from the disc image — ISO9660, either
# 2048-byte logical sectors (.iso) or 2352-byte raw CD sectors (.bin).

_PS_DISC_EXTS = (".iso", ".bin", ".img")


def disc_serial(path: Path) -> str | None:
    """The disc serial (normalized SLES-53557) read from a PS1/PS2 image, or
    None for anything we can't read (compressed .chd/.cso, non-disc, errors)."""
    try:
        with path.open("rb") as f:
            head = f.read(16)
            if head[:12] == b"\x00" + b"\xff" * 10 + b"\x00":   # raw CD sync
                size, off = 2352, (24 if head[15] == 2 else 16)   # MODE2 vs MODE1
            else:
                size, off = 2048, 0

            def sect(lba: int) -> bytes:
                f.seek(lba * size + off)
                return f.read(2048)

            pvd = sect(16)                          # ISO9660 primary volume desc
            if pvd[1:6] != b"CD001":
                return None
            root = pvd[156:190]
            ext = int.from_bytes(root[2:6], "little")
            length = int.from_bytes(root[10:14], "little")
            data = b"".join(sect(ext + i) for i in range((length + 2047) // 2048))

            cnf = None
            i = 0
            while i < len(data):
                rlen = data[i]
                if rlen == 0:                       # padding → next sector
                    i = ((i // 2048) + 1) * 2048
                    continue
                name = data[i + 33:i + 33 + data[i + 32]].split(b";")[0].upper()
                if name == b"SYSTEM.CNF":
                    cnf = (int.from_bytes(data[i + 2:i + 6], "little"),
                           int.from_bytes(data[i + 10:i + 14], "little"))
                    break
                i += rlen
            if not cnf:
                return None
            cdata = b"".join(sect(cnf[0] + i) for i in range((cnf[1] + 2047) // 2048))
            m = re.search(rb"([A-Za-z]{4})_(\d{3})\.(\d{2})", cdata[:cnf[1]])
            return f"{m.group(1).decode().upper()}-{m.group(2).decode()}{m.group(3).decode()}" if m else None
    except Exception:
        return None


def _ps_titles() -> dict:
    """serial → ROM stem, across every PlayStation ROM dir on the box."""
    out = {}
    for sub in ("duckstation", "pcsx2"):
        d = ROMS / sub

        def build(d=d):
            m = {}
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in _PS_DISC_EXTS and (s := disc_serial(f)):
                    m.setdefault(s, f.stem)
            return m
        out.update(_cached(f"ps:{sub}", d, build))
    return out


def sony_game(serial: str) -> tuple[str, Path | None]:
    """(real title, cover Path|None) for a PS serial, or ("", None) if no ROM."""
    stem = _ps_titles().get(serial)
    if not stem:
        return "", None
    title = _clean_stem(stem)
    return title, cover_for(stem, title)


# ── ROM-header maps (title-id / internal name → display name + cover) ─────────

def _n64_names() -> dict:
    """Internal cartridge name (ROM header @0x20) → ROM stem."""
    d = ROMS / "gopher64"

    def build():
        out = {}
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in (".z64", ".n64", ".v64"):
                continue
            try:
                h = bytearray(f.open("rb").read(0x40))
            except OSError:
                continue
            if h[:4] == b"\x37\x80\x40\x12":      # v64: byte-swapped pairs
                h[::2], h[1::2] = h[1::2], h[::2]
            elif h[:4] == b"\x40\x12\x37\x80":     # n64: little-endian words
                for i in range(0, len(h), 4):
                    h[i:i + 4] = h[i:i + 4][::-1]
            if h[:4] != b"\x80\x37\x12\x40":
                continue
            name = h[0x20:0x34].decode("ascii", "ignore").strip()
            if name:
                out[name.upper()] = f.stem
        return out
    return _cached("n64", d, build)


def _wii_names() -> dict:
    """4-char disc game code → ROM stem (RVZ/WIA store the disc header, whose
    game id sits at 0x58; plain ISO/GCM have it at 0)."""
    d = ROMS / "dolphin"

    def build():
        out = {}
        for f in sorted(d.iterdir()):
            ext = f.suffix.lower()
            if ext not in (".rvz", ".wia", ".iso", ".gcm"):
                continue
            try:
                h = f.open("rb").read(0x60)
            except OSError:
                continue
            gid = h[0x58:0x5E] if h[:4] in (b"RVZ\x01", b"WIA\x01") else h[0:6]
            code = gid[:4].decode("ascii", "ignore")
            if len(code) == 4 and code.isalnum():
                out[code.upper()] = f.stem
        return out
    return _cached("wii", d, build)


def _3ds_names() -> dict:
    """'<hi>/<lo>' title id → ROM stem (NCSD media id @0x108 of a .3ds)."""
    d = ROMS / "azahar"

    def build():
        out = {}
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in (".3ds", ".cci"):
                continue
            try:
                h = f.open("rb").read(0x110)
            except OSError:
                continue
            if h[0x100:0x104] != b"NCSD":
                continue
            tid = f"{int.from_bytes(h[0x108:0x110], 'little'):016x}"
            out[f"{tid[:8]}/{tid[8:]}"] = f.stem
        return out
    return _cached("3ds", d, build)


# Well-known Switch title ids (base games) — used when the id can't be found
# in a ROM file name on the box.
_SWITCH_KNOWN = {
    "01007EF00011E000": "The Legend of Zelda: Breath of the Wild",
    "0100F2C0115B6000": "The Legend of Zelda: Tears of the Kingdom",
    "0100152000022000": "Mario Kart 8 Deluxe",
    "01006F8002326000": "Animal Crossing: New Horizons",
    "0100000000010000": "Super Mario Odyssey",
    "01006A800016E000": "Super Smash Bros. Ultimate",
}

_TID_RE = re.compile(r"(?<![0-9A-Fa-f])(01[0-9A-Fa-f]{14})(?![0-9A-Fa-f])")


def _switch_dir_names(d: Path, cache_key: str) -> dict:
    """Base title id → display name, from update/DLC ids in NSP file names
    (updates end in …800, DLC ids live one 0x1000 block above the base)."""
    def build():
        out = {}
        for f in sorted(d.iterdir()):
            m = _TID_RE.search(f.name)
            if not m:
                continue
            tid = int(m.group(1), 16)
            if tid & 0xFFF == 0x800:
                base = tid - 0x800
            elif tid & 0xFFF:
                base = ((tid >> 12) - 1) << 12
            else:
                base = tid
            name = f.name[:m.start()]
            name = re.sub(r"\[[^\]]*\]?", " ", name)          # [UPD], [v65536]…
            name = re.sub(r"[._]", " ", name)
            name = re.sub(r"\bv\d[\d. ]*$", "", _collapse(name)).strip(" -")
            if name:
                out[f"{base:016X}"] = name
        return out
    return _cached(cache_key, d, build)


def _switch_names() -> dict:
    out = dict(_SWITCH_KNOWN)
    for i, d in enumerate((ROMS / "citron-neo", ROMS / "Switch DLC & Updates")):
        for tid, name in _switch_dir_names(d, f"switch{i}").items():
            out.setdefault(tid, name)
    return out


# ── per-emulator title/icon sources read from the save tree itself ────────────

_SONY_ID_RE = re.compile(r"([A-Za-z]{4})[-_]?(\d{3})\.?(\d{2})")
_RPCS3_SERIAL_RE = re.compile(r"[A-Z]{4}\d{5}")


def _sony_serial(name: str) -> str | None:
    """Full serial, normalized: SLES-03736 / SLES_037.36 → SLES-03736."""
    m = _SONY_ID_RE.search(name)
    return f"{m.group(1).upper()}-{m.group(2)}{m.group(3)}" if m else None


def _sfo_title(d: Path) -> str:
    return _collapse(sfo.parse(d / "PARAM.SFO").get("TITLE", ""))


def _savedata_index(base: Path, subpath: str, cache_key: str) -> dict:
    """serial → (TITLE, ICON0 path) built from PARAM.SFOs in a savedata tree.
    Lets save *states* named by serial inherit the real title and icon."""
    d = base / subpath

    def build():
        out = {}
        for g in sorted(p for p in d.iterdir() if p.is_dir()):
            m = _RPCS3_SERIAL_RE.match(g.name.upper())
            if not m or m.group(0) in out:
                continue
            title = _sfo_title(g)
            icon = g / "ICON0.PNG"
            if title:
                out[m.group(0)] = (title, icon if icon.is_file() else None)
        return out
    return _cached(f"{cache_key}:{d}", d, build)


def _wiiu_longname(meta_xml: Path) -> str:
    try:
        txt = meta_xml.read_text(errors="ignore")
    except OSError:
        return ""
    m = re.search(r"<longname_en[^>]*>([^<]+)</longname_en>", txt)
    return _collapse(m.group(1)) if m else ""


def _hex_ascii(lo: str) -> str | None:
    """Wii title-id low word is the 4-char game code in hex (524d4350→RMCP)."""
    try:
        s = bytes.fromhex(lo).decode("ascii")
    except ValueError:
        return None
    return s.upper() if len(s) == 4 and s.isalnum() else None


# ── resolvers ─────────────────────────────────────────────────────────────────
# Each takes (base, cdir, rel) and returns (game_key, game_title, icon Path|None).
# game_key "" → the entry goes to "Shared & system files".

def _res_rom(base, cdir, rel):
    stem = rel.stem
    return stem, _prettify(stem), cover_for(stem, _prettify(stem))


def _res_n64(base, cdir, rel):
    internal = re.sub(r"-[0-9A-Fa-f]{16,}$", "", rel.stem)  # drop the ROM hash
    romstem = _n64_names().get(internal.upper())
    title = _clean_stem(romstem) if romstem else internal
    return internal, title, cover_for(romstem or "", title)


def _res_ps_serial(base, cdir, rel):
    s = _sony_serial(rel.name)
    if not s:
        # serial-less states (resume.sav, savestate_1.sav…) are global, not a game
        return "", "", None
    title, icon = sony_game(s)             # real name + cover if the ROM is here
    return s, title or s, icon


def _res_card_or_serial(base, cdir, rel):
    s = _sony_serial(rel.name)             # per-game card (SLES-03736.mcd)
    if not s:
        return "", "", None
    title, icon = sony_game(s)
    return s, title or s, icon


def _res_gc_card(base, cdir, rel):
    """Dolphin's GC dir mixes raw .raw cards (shared) with GCI-folder saves
    (GC/<region>/Card A/<one .gci per save> — the modern default). A standalone
    .gci is one game's save: read its 64-byte header for the game code."""
    if rel.suffix.lower() == ".gci":
        info = memcard.gci_info(cdir / rel)
        if info:
            stem = _wii_names().get(info["code"])
            title = _clean_stem(stem) if stem else (info["name"] or info["code"])
            return info["code"], title, cover_for(stem or "", title)
    return "", "", None


def _res_shared(base, cdir, rel):
    return "", "", None


def _res_rpcs3_save(base, cdir, rel):
    d = cdir / rel
    title = _sfo_title(d)                   # groups _0/_P/-AUTOSAVE slots
    m = _RPCS3_SERIAL_RE.match(rel.name.upper())
    key = title or (m.group(0) if m else rel.name)
    icon = d / "ICON0.PNG"
    return key, title or key, icon if icon.is_file() else None


def _match_savedata_title(base: Path, title: str) -> str:
    """The savedata game a trophy title belongs to, or "". A trophy's
    TROPCONF name and the savedata's PARAM.SFO TITLE rarely match byte-for-byte
    (localization, ® / ™, "…Trophies" suffixes), so compare normalized and
    accept a containment either way (guarded by length to avoid silly hits)."""
    want = _norm(title)
    if len(want) < 4:
        return ""
    idx = _savedata_index(base, "dev_hdd0/home/00000001/savedata", "rpcs3")
    for real, _icon in idx.values():
        n = _norm(real)
        if not n:
            continue
        if n == want or (len(want) >= 6 and want in n) or (len(n) >= 6 and n in want):
            return real
    return ""


def _res_rpcs3_trophy(base, cdir, rel):
    """Trophy set (dev_hdd0/.../trophy/NPWRxxxxx_00). TROPCONF.SFM names the
    game; group the trophy with that game's savedata when we can map it, else
    send it to "Shared & system files" rather than inventing a phantom game."""
    d = cdir / rel
    title = ""
    for conf in ("TROPCONF.SFM", "TROP.SFM"):
        try:
            txt = (d / conf).read_text(errors="ignore")
        except OSError:
            continue
        m = re.search(r"<title-name>\s*([^<]+?)\s*</title-name>", txt)
        if m:
            title = _collapse(m.group(1))
            break
    game = _match_savedata_title(base, title) if title else ""
    if not game:
        return "", "", None
    icon = d / "ICON0.PNG"
    return game, game, icon if icon.is_file() else None


def _res_rpcs3_state(base, cdir, rel):
    m = _RPCS3_SERIAL_RE.search(rel.name.upper())
    if not m:
        return rel.name, rel.name, None
    hit = _savedata_index(base, "dev_hdd0/home/00000001/savedata", "rpcs3").get(m.group(0))
    if hit:
        return hit[0], hit[0], hit[1]       # same key as the savedata group
    return m.group(0), m.group(0), None


def _res_psp_save(base, cdir, rel):
    d = cdir / rel
    m = _RPCS3_SERIAL_RE.match(rel.name.upper())
    key = m.group(0) if m else rel.name
    title = _sfo_title(d)
    icon = d / "ICON0.PNG"
    return key, title or key, icon if icon.is_file() else None


def _res_psp_state(base, cdir, rel):
    m = _RPCS3_SERIAL_RE.search(rel.name.upper())
    if not m:
        key = re.split(r"[_\-.]", rel.name)[0]
        return key, key, None
    hit = _savedata_index(base, "SAVEDATA", "psp").get(m.group(0))
    if hit:
        return hit[0], hit[0], hit[1]
    return m.group(0), m.group(0), None


def _res_wii(base, cdir, rel):
    hi, lo = rel.parts[0].lower(), rel.parts[1].lower()
    if hi == "00000001":                    # system titles (IOS, sysmenu…)
        return "", "", None
    code = _hex_ascii(lo)
    key = code or f"{hi}/{lo}"              # code merges disc+channel+states
    stem = _wii_names().get(code) if code else None
    title = _clean_stem(stem) if stem else key
    return key, title, cover_for(stem or "", title)


def _res_dolphin_state(base, cdir, rel):
    m = re.match(r"([A-Z0-9]{4})[A-Z0-9]{2}\.", rel.name.upper())
    if not m:
        return rel.stem, rel.stem, None
    code = m.group(1)
    stem = _wii_names().get(code)
    title = _clean_stem(stem) if stem else code
    return code, title, cover_for(stem or "", title)


def _res_n3ds(base, cdir, rel):
    hi, lo = rel.parts[-2].lower(), rel.parts[-1].lower()
    if hi != "00040000":                    # not an application title
        return "", "", None
    key = f"{hi}/{lo}"
    stem = _3ds_names().get(key)
    title = _clean_stem(stem) if stem else key
    return key, title, cover_for(stem or "", title)


def _res_n3ds_state(base, cdir, rel):
    m = re.search(r"[0-9A-Fa-f]{16}", rel.name)
    if not m:
        return rel.stem, rel.stem, None
    tid = m.group(0).lower()
    key = f"{tid[:8]}/{tid[8:]}"
    stem = _3ds_names().get(key)
    title = _clean_stem(stem) if stem else key
    return key, title, cover_for(stem or "", title)


def _res_wiiu(base, cdir, rel):
    hi, lo = rel.parts[0].lower(), rel.parts[1].lower()
    if hi != "00050000":                    # updates/DLC/system saves
        return "", "", None
    d = cdir / rel
    title = _wiiu_longname(d / "meta/meta.xml") or f"{hi}/{lo}"
    icon = d / "meta/iconTex.tga"
    return f"{hi}/{lo}", title, icon if icon.is_file() else cover_for(title)


def _res_switch(base, cdir, rel):
    tid = rel.name.upper()
    if not re.fullmatch(r"[0-9A-F]{16}", tid):
        return "", "", None
    name = _switch_names().get(tid)
    return tid, name or tid, cover_for(name or "")


def _x360_header_name(d: Path) -> str:
    """Save display name from a Xenia content header (XCONTENT_DATA: u32
    device id, u32 content type, then a UTF-16 display name — guest structs
    are big-endian, but be tolerant and pick whichever decodes cleanly)."""
    hdr = d / "Headers/00000001"
    if not hdr.is_dir():
        return ""
    for h in sorted(hdr.iterdir()):
        if h.suffix != ".header" or not h.is_file():
            continue
        try:
            raw = h.read_bytes()[8:8 + 256]
        except OSError:
            continue
        for codec in ("utf-16-be", "utf-16-le"):
            name = raw.decode(codec, "ignore").split("\x00", 1)[0].strip()
            if len(name) >= 3 and all(c.isprintable() for c in name):
                return _collapse(name)
    return ""


def _res_x360(base, cdir, rel):
    """content/<XUID>/<TitleID>. The dashboard title (FFFE07D1) is the profile
    package and XUID 0 holds profile-less content (DLC) — both shared."""
    xuid, tid = rel.parts[0].upper(), rel.parts[1].upper()
    if tid == "FFFE07D1" or xuid == "0000000000000000":
        return "", "", None
    name = _x360_header_name(cdir / rel)
    return tid, name or tid, cover_for(name)


def _ps4_titles() -> dict:
    """TITLE_ID → (TITLE, icon) from the game dumps in emu/shadps4/<Game>/."""
    d = ROMS / "shadps4"

    def build():
        out = {}
        for g in sorted(p for p in d.iterdir() if p.is_dir()):
            meta = sfo.parse(g / "sce_sys/param.sfo")
            tid = str(meta.get("TITLE_ID", "")).upper()
            if not tid:
                continue
            icon = g / "sce_sys/icon0.png"
            out.setdefault(tid, (_collapse(str(meta.get("TITLE", ""))) or g.name,
                                 icon if icon.is_file() else None))
        return out
    return _cached("ps4", d, build)


def _res_ps4_save(base, cdir, rel):
    """savedata/<CUSA#####>/<savedir>. Title from the installed game's
    param.sfo; the save's own param.sfo MAINTITLE as a fallback."""
    cusa = rel.parts[0].upper()
    d = cdir / rel
    hit = _ps4_titles().get(cusa)
    title = hit[0] if hit else ""
    if not title:
        t = _collapse(str(sfo.parse(d / "sce_sys/param.sfo").get("MAINTITLE", "")))
        if t.lower() not in ("", "saved data"):     # skip the SDK placeholder
            title = t
    icon = d / "sce_sys/icon0.png"
    if not icon.is_file():
        icon = (hit[1] if hit and hit[1] else None) or cover_for(title)
    return cusa, title or cusa, icon


_RESOLVERS = {
    "rom": _res_rom, "n64": _res_n64,
    "ps_serial": _res_ps_serial, "card_or_serial": _res_card_or_serial,
    "gc_card": _res_gc_card, "shared": _res_shared,
    # C()'s default group — never used by the CATALOG today, mapped to
    # "shared" so a future collection that forgets `group=` can't crash scan()
    "none": _res_shared,
    "rpcs3_save": _res_rpcs3_save, "rpcs3_trophy": _res_rpcs3_trophy,
    "rpcs3_state": _res_rpcs3_state,
    "psp_save": _res_psp_save, "psp_state": _res_psp_state,
    "wii": _res_wii, "dolphin_state": _res_dolphin_state,
    "n3ds": _res_n3ds, "n3ds_state": _res_n3ds_state,
    "wiiu": _res_wiiu, "switch": _res_switch,
    "x360": _res_x360, "ps4_save": _res_ps4_save,
}


# ── scan ──────────────────────────────────────────────────────────────────────

_BAK_PART_RE = re.compile(r"\.bak-\d{8}-\d{6}")


def _skip(rel: PurePosixPath) -> bool:
    return any(p.startswith(".") or _BAK_PART_RE.search(p) for p in rel.parts)


def _candidates(cdir: Path, col: dict):
    """Yield (rel PurePosixPath, is_dir) for a collection."""
    mode = col["mode"]
    if mode == "dirs":
        found = [p for p in cdir.glob(col["glob"]) if p.is_dir()]
    elif mode == "cards":
        found = [p for p in cdir.rglob("*") if p.is_file()]
    elif mode == "any":
        found = list(cdir.iterdir())
    else:  # files
        found = [p for p in cdir.iterdir() if p.is_file()]
    for p in sorted(found, key=lambda x: str(x).lower()):
        rel = PurePosixPath(p.relative_to(cdir).as_posix())
        if _skip(rel):
            continue
        if not p.is_dir() and col["exts"] and p.suffix.lower() not in col["exts"]:
            continue
        yield rel, p.is_dir()


def scan(emu_id: str) -> tuple[Path | None, list[dict]]:
    """All entries of an emulator with their game identity resolved."""
    base = resolve_base(emu_id)
    if not base:
        return None, []
    out = []
    for ci, col in enumerate(CATALOG[emu_id]["collections"]):
        cdir = base / col["subpath"] if col["subpath"] else base
        if not cdir.is_dir():
            continue
        resolver = _RESOLVERS[col["group"]]
        for rel, is_dir in _candidates(cdir, col):
            key, title, icon = resolver(base, cdir, rel)
            out.append({
                "ci": ci, "rel": str(rel), "path": cdir / rel,
                "kind": col["kind"], "mode": col["mode"], "is_dir": is_dir,
                "key": key, "title": title, "icon": icon,
            })
    return base, out
