"""Where each system's files go on this box, and what each system still lacks.

Two sources, one per kind of system:
  * the core's `GET /api/bios` for every system whose catalogue pack declares a
    `bios` block (Switch, PS1, PS2, PS3, the RetroArch systems, Wii U). It has
    already expanded the directory for the Flatpak the box has installed, and
    it is what the launch gate checks, so a file put there is a file the core
    sees. Never re-derived here.
  * this module's own table for the emulators the core declares nothing for
    (melonDS, Dolphin, mGBA): their paths are facts about those emulators.
"""
import json
import logging
import os
import tomllib
import urllib.request
from pathlib import Path

log = logging.getLogger("bios-manager")

ADDON_DIR = Path(__file__).parent
GAMECORE_PATH = Path(os.environ.get("GAMECORE_PATH", "/opt/GameCore"))
# Temporary: keeps a box that has not taken the P3 OTA resolving as before.
GAMECORE_DATA = Path(os.environ.get("GAMECORE_DATA") or GAMECORE_PATH)
ADDON_DATA_DIR = Path(os.environ.get("ADDON_DATA_DIR") or GAMECORE_DATA / "addons" / ADDON_DIR.name)
# GAMECORE_HOME lets tests point every emulator path into a sandbox.
HOME = Path(os.environ.get("GAMECORE_HOME") or Path.home())
SYSTEMS_FILE = GAMECORE_DATA / "config" / "systems.json"
STAGING = ADDON_DATA_DIR / "firmware"
CORE_BIOS = f"http://127.0.0.1:{int(os.environ.get('GAMECORE_BACKEND_PORT', '8765'))}/api/bios"
CORE_TIMEOUT_S = 5

_FLATPAK = HOME / ".var/app"
# (flatpak dir, native dir) per emulator the core has no `bios` block for.
OWN_DIRS = {
    "gamecube": (_FLATPAK / "org.DolphinEmu.dolphin-emu/data/dolphin-emu", HOME / ".local/share/dolphin-emu"),
    "gb": (_FLATPAK / "io.mgba.mGBA/config/mgba", HOME / ".config/mgba"),
    "gbc": (_FLATPAK / "io.mgba.mGBA/config/mgba", HOME / ".config/mgba"),
    "gba": (_FLATPAK / "io.mgba.mGBA/config/mgba", HOME / ".config/mgba"),
}
_NOT_NEEDED = "Plays without them; the emulator uses its own replacement."
# name, note. None of them is required: each emulator has a built-in fallback.
OWN_FILES = {
    "gamecube": [("GC/EUR/IPL.bin", "European IPL: the boot animation and fonts. " + _NOT_NEEDED),
                 ("GC/USA/IPL.bin", "American IPL. " + _NOT_NEEDED),
                 ("GC/JAP/IPL.bin", "Japanese IPL. " + _NOT_NEEDED)],
    "gb": [("gb_bios.bin", "Game Boy boot ROM (mGBA reads it from its config folder). " + _NOT_NEEDED),
           ("sgb_bios.bin", "Super Game Boy boot ROM. " + _NOT_NEEDED)],
    "gbc": [("gbc_bios.bin", "Game Boy Color boot ROM. " + _NOT_NEEDED)],
    "gba": [("gba_bios.bin", "Game Boy Advance BIOS. " + _NOT_NEEDED)],
}
MELONDS_TOML = (_FLATPAK / "net.kuribo64.melonDS/config/melonDS/melonDS.toml", HOME / ".config/melonDS/melonDS.toml")
# Target name -> (toml section, key, the field's label in melonDS's Emu settings).
MELONDS_KEYS = {
    "bios9.bin": ("DS", "BIOS9Path", "DS-mode: DS ARM9 BIOS"),
    "bios7.bin": ("DS", "BIOS7Path", "DS-mode: DS ARM7 BIOS"),
    "firmware.bin": ("DS", "FirmwarePath", "DS-mode: DS firmware"),
    "dsi_bios9.bin": ("DSi", "BIOS9Path", "DSi-mode: DSi ARM9 BIOS"),
    "dsi_bios7.bin": ("DSi", "BIOS7Path", "DSi-mode: DSi ARM7 BIOS"),
    "dsi_firmware.bin": ("DSi", "FirmwarePath", "DSi-mode: DSi firmware"),
    "dsi_nand.bin": ("DSi", "NANDPath", "DSi-mode: DSi NAND"),
}
NOTHING_NEEDED = {
    "wii": "Dolphin needs no system file for Wii games.",
    "ppsspp": "PPSSPP needs no BIOS.",
    "gopher64": "gopher64 needs no BIOS.",
    "azahar": "Azahar needs no BIOS. Console system files come from its Artic Setup Tool, not from a file.",
}
SKIP_SYSTEMS = {"lutris", "steam"}


def systems() -> list[dict]:
    """The systems installed on this box, in the order systems.json lists them."""
    try:
        data = json.loads(SYSTEMS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        log.warning("cannot read %s", SYSTEMS_FILE, exc_info=True)
        return []
    return [s for s in data if isinstance(s, dict) and s.get("id") and s["id"] not in SKIP_SYSTEMS]


def core_rows() -> dict[str, dict] | None:
    """The core's BIOS report by system id, or None when the core does not answer."""
    try:
        with urllib.request.urlopen(CORE_BIOS, timeout=CORE_TIMEOUT_S) as resp:
            return {row["id"]: row for row in json.load(resp)}
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("core %s unreachable", CORE_BIOS, exc_info=True)
        return None


def _is_flatpak(system: dict) -> bool:
    return system.get("path") == "flatpak"


def own_dir(system: dict) -> Path:
    flatpak, native = OWN_DIRS[system["id"]]
    return flatpak if _is_flatpak(system) else native


def melonds_paths(system: dict) -> dict[str, Path | None]:
    """Each melonDS file's configured path; None where melonDS has none set."""
    toml = MELONDS_TOML[0] if _is_flatpak(system) else MELONDS_TOML[1]
    try:
        conf = tomllib.loads(toml.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        conf = {}
    out = {}
    for name, (section, key, _label) in MELONDS_KEYS.items():
        value = conf.get(section, {}).get(key, "")
        out[name] = Path(value) if isinstance(value, str) and value.startswith("/") else None
    return out


def melonds_action(name: str, staged: Path) -> dict:
    label = MELONDS_KEYS[name][2]
    return {"action": f"melonDS has no path set for this file. In melonDS, open Config, Emu settings, "
                      f"{label}, and pick {staged}. Tick \"Use external BIOS/firmware files\" too."}


def destination(system: dict, name: str, core: dict | None) -> tuple[Path | None, Path | None, str]:
    """(destination, the directory it must stay inside, reason when there is none).

    melonDS without a configured path gets (None, None, ""): the caller stages it.
    Everything else without a destination comes with the reason.
    """
    sid = system["id"]
    if sid == "melonds":
        path = melonds_paths(system).get(name)
        if path is None:
            return None, None, ""
        # The path comes from a config file: refuse one pointing outside the owner's data.
        if not any(r.resolve() in path.resolve().parents for r in (HOME, GAMECORE_DATA)):
            return None, None, f"melonDS points this file at {path}, outside the home and data folders; left alone."
        return path, path.parent, ""
    if sid in OWN_DIRS:
        root = own_dir(system)
        return root / name, root, ""
    if core is None:
        return None, None, "GameCore is not answering, so where this file goes is unknown. Try again in a minute."
    row = core.get(sid)
    if row is None:
        return None, None, f"GameCore keeps no BIOS list for {system.get('label', sid)}."
    root = Path(row["dir"])
    return root / name, root, ""
