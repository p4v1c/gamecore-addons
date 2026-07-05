"""Where each emulator stores saves and save states.

Grounded in the real GameCore box layout + each emulator's docs/source.
Two natures of data (see README):
  - "save"  = native saves (memory card / battery / save data). Portable and
              stable — safe to back up, restore and transfer.
  - "state" = save states (RAM snapshot). Tied to the exact emulator version,
              NOT portable — restore only onto the same version.

A "collection" describes one directory to scan:
  subpath : path relative to the emulator's resolved base dir ("" = base)
  mode    : "files" → each matching file is an entry
            "dirs"  → each sub-directory is an entry (folder-per-game/save)
            "cards" → shared memory-card files (hold several games' saves)
  exts    : lowercase extensions for "files"/"cards" (empty = any file)
  kind    : "save" | "state"
"""
import os
from pathlib import Path

HOME = Path.home()
GC = Path(os.environ.get("GAMECORE_PATH", "/opt/GameCore"))


def C(subpath, mode, kind, exts=()):
    return {"subpath": subpath, "mode": mode, "kind": kind, "exts": [e.lower() for e in exts]}


# emulator id (matches config/systems.json) → definition.
# "bases": candidate roots, first existing wins (Flatpak vs native installs).
CATALOG = {
    "mgba": {
        "label": "Game Boy Advance",
        "bases": [GC / "emu/mgba"],  # mGBA writes next to the ROM
        "collections": [
            C("", "files", "save", [".sav", ".srm"]),
            C("", "files", "state", [".ss0", ".ss1", ".ss2", ".ss3", ".ss4",
                                     ".ss5", ".ss6", ".ss7", ".ss8", ".ss9"]),
        ],
    },
    "melonds": {
        "label": "Nintendo DS",
        "bases": [GC / "emu/melonds"],  # next to the ROM by default
        "collections": [
            C("", "files", "save", [".sav", ".nvm"]),
            C("", "files", "state", [".mln", ".ml0", ".ml1", ".ml2", ".ml3"]),
        ],
    },
    "gopher64": {
        "label": "Nintendo 64",
        "bases": [HOME / ".var/app/io.github.gopher64.gopher64/data/gopher64",
                  HOME / ".local/share/gopher64"],
        "collections": [
            C("saves", "files", "save", [".eep", ".mpk", ".sra", ".fla", ".srm"]),
            C("states", "files", "state"),
        ],
    },
    "ppsspp": {
        "label": "PSP",
        "bases": [HOME / ".var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP",
                  HOME / ".config/ppsspp/PSP"],
        "collections": [
            C("SAVEDATA", "dirs", "save"),
            C("PPSSPP_STATE", "files", "state"),
        ],
    },
    "duckstation": {
        "label": "PlayStation 1",
        "bases": [HOME / ".local/share/duckstation",
                  HOME / ".var/app/org.duckstation.DuckStation/data/duckstation"],
        "collections": [
            C("memcards", "cards", "save", [".mcd", ".mcr", ".ps"]),
            C("savestates", "files", "state"),
        ],
    },
    "pcsx2": {
        "label": "PlayStation 2",
        "bases": [HOME / ".config/PCSX2",
                  HOME / ".var/app/net.pcsx2.PCSX2/config/PCSX2"],
        "collections": [
            C("memcards", "cards", "save", [".ps2", ".mcd", ".mcr"]),
            C("sstates", "files", "state"),
        ],
    },
    "dolphin": {
        "label": "GameCube / Wii",
        "bases": [HOME / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu",
                  HOME / ".local/share/dolphin-emu"],
        "collections": [
            C("GC", "cards", "save", [".raw", ".gci"]),          # GameCube memory cards
            C("Wii/title", "dirs", "save"),                       # Wii NAND saves (by title id)
            C("StateSaves", "files", "state"),
        ],
    },
    "rpcs3": {
        "label": "PlayStation 3",
        "bases": [HOME / ".config/rpcs3",
                  HOME / ".var/app/net.rpcs3.RPCS3/config/rpcs3"],
        "collections": [
            C("dev_hdd0/home/00000001/savedata", "dirs", "save"),  # <SERIAL>_...
            C("savestates", "files", "state"),
        ],
    },
    "azahar": {
        "label": "Nintendo 3DS",
        "bases": [HOME / ".var/app/org.azahar_emu.Azahar/data/azahar-emu",
                  HOME / ".local/share/azahar-emu",
                  HOME / ".local/share/citra-emu"],
        "collections": [
            # game saves live deep under the emulated SD card
            C("sdmc/Nintendo 3DS", "dirs", "save"),
            C("states", "files", "state"),
        ],
    },
    "cemu": {
        "label": "Wii U",
        "bases": [HOME / ".var/app/info.cemu.Cemu/data/Cemu",
                  HOME / ".local/share/Cemu"],
        "collections": [
            C("mlc01/usr/save", "dirs", "save"),  # by title id
        ],
    },
    "citron": {
        "label": "Nintendo Switch",
        "bases": [HOME / ".local/share/citron",
                  HOME / ".var/app/io.github.ryubing.Ryujinx/config/Ryujinx"],
        "collections": [
            C("nand/user/save", "dirs", "save"),  # hashed save_data_id
            C("bis/user/save", "dirs", "save"),   # Ryujinx layout (fallback base)
        ],
    },
}


def resolve_base(emu_id: str) -> Path | None:
    """First candidate base dir that exists, else None."""
    for base in CATALOG[emu_id]["bases"]:
        if base.exists():
            return base
    return None
