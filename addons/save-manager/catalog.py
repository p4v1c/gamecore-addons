"""Where each emulator stores saves and save states, and how to group them
into games.

Two natures of data (see README):
  - "save"  = native saves (memory card / battery / save data). Portable.
  - "state" = save states (RAM snapshot). Version-specific, NOT portable.

A "collection" = one directory to scan:
  subpath : path relative to the emulator's base dir ("" = base)
  mode    : "files" | "dirs" | "cards"
  exts    : lowercase extensions for files/cards (empty = any)
  kind    : "save" | "state"
  group   : how an entry name maps to a game (see game_of):
            "rom"      → ROM base name (mGBA/DS/N64 files next to the ROM)
            "serial"   → PS3-style serial prefix (RPCS3)
            "prefix"   → id before the first separator (PPSSPP game id…)
            "card"     → shared memory card, not per-game
            "titledir" → the folder name is the title id (Wii/Wii U/3DS)
            "opaque"   → unmappable id (Switch hashed dirs)
"""
import os
import re
from pathlib import Path

HOME = Path.home()
GC = Path(os.environ.get("GAMECORE_PATH", "/opt/GameCore"))
COVERS = GC / "emu" / "covers"
_SERIAL_RE = re.compile(r"^([A-Za-z]{4}\d{5})")


def C(subpath, mode, kind, exts=(), group="prefix"):
    return {"subpath": subpath, "mode": mode, "kind": kind,
            "exts": [e.lower() for e in exts], "group": group}


CATALOG = {
    "mgba": {"label": "Game Boy Advance", "bases": [GC / "emu/mgba"], "collections": [
        C("", "files", "save", [".sav", ".srm"], "rom"),
        C("", "files", "state", [f".ss{i}" for i in range(10)], "rom"),
    ]},
    "melonds": {"label": "Nintendo DS", "bases": [GC / "emu/melonds"], "collections": [
        C("", "files", "save", [".sav", ".nvm"], "rom"),
        C("", "files", "state", [".mln", ".ml0", ".ml1", ".ml2", ".ml3"], "rom"),
    ]},
    "gopher64": {"label": "Nintendo 64", "bases": [
        HOME / ".var/app/io.github.gopher64.gopher64/data/gopher64", HOME / ".local/share/gopher64"], "collections": [
        C("saves", "files", "save", [".eep", ".mpk", ".sra", ".fla", ".srm"], "rom"),
        C("states", "files", "state", (), "rom"),
    ]},
    "ppsspp": {"label": "PSP", "bases": [
        HOME / ".var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP", HOME / ".config/ppsspp/PSP"], "collections": [
        C("SAVEDATA", "dirs", "save", (), "prefix"),
        C("PPSSPP_STATE", "files", "state", (), "prefix"),
    ]},
    "duckstation": {"label": "PlayStation 1", "bases": [
        HOME / ".local/share/duckstation", HOME / ".var/app/org.duckstation.DuckStation/data/duckstation"], "collections": [
        C("memcards", "cards", "save", [".mcd", ".mcr", ".ps"], "card"),
        C("savestates", "files", "state", (), "prefix"),
    ]},
    "pcsx2": {"label": "PlayStation 2", "bases": [
        HOME / ".config/PCSX2", HOME / ".var/app/net.pcsx2.PCSX2/config/PCSX2"], "collections": [
        C("memcards", "cards", "save", [".ps2", ".mcd", ".mcr"], "card"),
        C("sstates", "files", "state", (), "prefix"),
    ]},
    "dolphin": {"label": "GameCube / Wii", "bases": [
        HOME / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu", HOME / ".local/share/dolphin-emu"], "collections": [
        C("GC", "cards", "save", [".raw", ".gci"], "card"),
        C("Wii/title", "dirs", "save", (), "titledir"),
        C("StateSaves", "files", "state", (), "prefix"),
    ]},
    "rpcs3": {"label": "PlayStation 3", "bases": [
        HOME / ".config/rpcs3", HOME / ".var/app/net.rpcs3.RPCS3/config/rpcs3"], "collections": [
        C("dev_hdd0/home/00000001/savedata", "dirs", "save", (), "serial"),
        C("savestates", "files", "state", (), "serial"),
    ]},
    "azahar": {"label": "Nintendo 3DS", "bases": [
        HOME / ".var/app/org.azahar_emu.Azahar/data/azahar-emu",
        HOME / ".local/share/azahar-emu", HOME / ".local/share/citra-emu"], "collections": [
        C("sdmc/Nintendo 3DS", "dirs", "save", (), "titledir"),
        C("states", "files", "state", (), "prefix"),
    ]},
    "cemu": {"label": "Wii U", "bases": [
        HOME / ".var/app/info.cemu.Cemu/data/Cemu", HOME / ".local/share/Cemu"], "collections": [
        C("mlc01/usr/save", "dirs", "save", (), "titledir"),
    ]},
    "citron": {"label": "Nintendo Switch", "bases": [
        HOME / ".local/share/citron", HOME / ".var/app/io.github.ryubing.Ryujinx/config/Ryujinx"], "collections": [
        C("nand/user/save", "dirs", "save", (), "opaque"),
        C("bis/user/save", "dirs", "save", (), "opaque"),
    ]},
}


def resolve_base(emu_id: str) -> Path | None:
    for base in CATALOG[emu_id]["bases"]:
        if base.exists():
            return base
    return None


# ── game grouping ─────────────────────────────────────────────────────────────

def _prettify(stem: str) -> str:
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip()


def _rpcs3_titles(base: Path) -> dict:
    """serial → folder-name title, from RPCS3's games.yml (best effort)."""
    out = {}
    try:
        import ryaml  # optional; falls back to a tiny parser
        data = ryaml.load((base / "games.yml").read_text())
    except Exception:
        data = None
        try:
            for line in (base / "games.yml").read_text().splitlines():
                m = re.match(r"^([A-Za-z0-9]+):\s*(.+?)\s*$", line)
                if m:
                    out[m.group(1)] = Path(m.group(2).strip()).name.strip("/")
        except OSError:
            pass
        return out
    if isinstance(data, dict):
        for serial, path in data.items():
            out[serial] = Path(str(path)).name.strip("/")
    return out


def game_of(emu_id: str, entry_name: str, group: str, base: Path) -> tuple[str, str]:
    """Return (game_key, game_title) for an entry. Key '' means 'not mappable'."""
    if group == "rom":
        stem = re.sub(r"\.[^.]+$", "", entry_name)          # strip extension
        stem = re.sub(r"-[0-9A-Fa-f]{16,}$", "", stem)      # gopher64 hash suffix
        return stem, _prettify(stem)
    if group == "serial":
        m = _SERIAL_RE.match(entry_name)
        serial = m.group(1) if m else re.split(r"[_\-.]", entry_name)[0]
        title = _rpcs3_titles(base).get(serial, "") or serial
        return serial, title
    if group == "prefix":
        return re.split(r"[_\-.]", entry_name)[0], re.split(r"[_\-.]", entry_name)[0]
    if group == "titledir":
        return entry_name, entry_name
    if group == "card":
        return "", ""      # shared card — not a single game
    if group == "opaque":
        return "", ""      # hashed id — can't tie to a game
    return entry_name, entry_name


def cover_for(*candidates: str) -> Path | None:
    """Match a game to a GameCore cover (used as the icon). Covers are named
    after the ROM display name, so try the raw key (underscores) and the
    prettified title."""
    for c in candidates:
        if not c:
            continue
        p = COVERS / f"{c}.png"
        if p.is_file():
            return p
    return None
