"""GameCore profiles as the save manager sees them.

Since GameCore v1.3.7 each profile can have its own saves. The primary
profile keeps the emulators' normal locations; another profile's saves live
under `<DATA>/emu/profile-saves/<profile id>/<system>/`, laid out per
emulator by the `profileSaves` block of its pack.json in the core:

- `keys`: a config option points the emulator at the profile's folder for
  the length of a game. The owner's files never move.
- `dirs`: the emulator has no option, so while a profile plays, its save
  folder is a symlink into that profile's folder and the owner's folder waits
  beside it as `<name>.gamecore-primary`. After a crash the swap stays until
  the next launch.

`LAYOUT` mirrors those declarations for the emulators this addon knows.
tests/test_profiles.py checks it against the packs' own pack.json.
The addon never imports core code: profiles come from the core's
`GET /api/profiles`, and a core that does not answer means "no profiles".
"""
import json
import os
import re
import time
import urllib.request
from pathlib import Path, PurePosixPath

import catalog
from catalog import CATALOG, GC_DATA

ROOT = GC_DATA / "emu" / "profile-saves"
PRIMARY_SUFFIX = ".gamecore-primary"
STORE = ".primary.json"            # the core's record of the options it changed
CORE_PROFILES = f"http://127.0.0.1:{int(os.environ.get('GAMECORE_BACKEND_PORT', '8765'))}/api/profiles"
CORE_TIMEOUT_S = 2
CACHE_S = 5                         # one page load asks for many covers
_ID = re.compile(r"[A-Za-z0-9]{1,64}")
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_DEFAULT_COLOR = "#4b5563"


def _layout(system: str, keys: dict | None = None, dirs: dict | None = None) -> dict:
    """keys: {collection index: folder inside the profile's system folder};
    dirs: {swapped folder, relative to the emulator base: its name in the profile's folder}."""
    return {"system": system, "keys": keys or {}, "dirs": dirs or {}}


# gb, gbc and gba are one mGBA (`sharesEmulator`): one folder per profile.
_MGBA = _layout("gba", keys={0: "", 1: "states"})
LAYOUT = {
    "mgba": _MGBA, "gb": _MGBA, "gbc": _MGBA, "gba": _MGBA,
    # per-instance: player 1's saves and states; players 2-4 stay beside the ROM.
    "melonds": _layout("melonds", keys={0: "", 1: ""}),
    "gopher64": _layout("gopher64", keys={0: "sram", 1: "states"}),
    "duckstation": _layout("duckstation", keys={0: "memcards", 1: "savestates"}),
    "pcsx2": _layout("pcsx2", keys={0: "memcards", 1: "sstates"}),
    # Dolphin is the core's `gamecube` pack; its save states stay shared.
    "dolphin": _layout("gamecube", keys={0: "Wii/title", 1: "GC"}),
    "azahar": _layout("azahar", keys={0: "sdmc/Nintendo 3DS", 1: "sdmc/Nintendo 3DS"}),
    "ppsspp": _layout("ppsspp", dirs={"SAVEDATA": "SAVEDATA", "PPSSPP_STATE": "PPSSPP_STATE"}),
    "rpcs3": _layout("rpcs3", dirs={"dev_hdd0/home/00000001/savedata": "savedata",
                                    "dev_hdd0/home/00000001/trophy": "trophy"}),
    "cemu": _layout("cemu", dirs={"mlc01/usr/save/00050000": "save"}),
    "switch": _layout("switch", dirs={"bis/user/save": "user-save",
                                      "bis/system/save/8000000000000000": "save-index"}),
    # Not in the core any more: a profile's Eden saves stay where the Eden-era
    # pack put them, <profile>/switch/save (EDEN_ERA in the tests).
    "eden": _layout("switch", dirs={"nand/user/save": "save"}),
    "shadps4": _layout("shadps4", dirs={"home/1/savedata": "savedata", "savedata/1": "savedata-v015"}),
    # xenia: not separated by the core (title updates share the save folder).
}

# ── who plays ────────────────────────────────────────────────────────────────

_cache: tuple[float, list] = (-CACHE_S, [])


def listing() -> list[dict]:
    """The box's profiles, or [] when it has none (an unnamed primary) or the core doesn't answer."""
    global _cache
    if time.monotonic() - _cache[0] < CACHE_S:
        return _cache[1]
    try:
        with urllib.request.urlopen(CORE_PROFILES, timeout=CORE_TIMEOUT_S) as r:
            raw = json.load(r)["profiles"]
        people = [{"id": p["id"], "name": str(p.get("name") or ""),
                   "color": p["color"] if _COLOR.fullmatch(str(p.get("color"))) else _DEFAULT_COLOR,
                   "avatar": p.get("avatar"), "primary": bool(p.get("primary"))}
                  for p in raw if _ID.fullmatch(str(p.get("id", "")))]
    except (OSError, ValueError, LookupError, TypeError):
        people = []
    if not any(p["name"] for p in people):
        people = []
    _cache = (time.monotonic(), people)
    return people


def find(profile_id: str) -> dict | None:
    return next((p for p in listing() if p["id"] == profile_id), None)


def label(person: dict) -> str:
    """`<name>-<id>`: the folder a profile's saves get in a full backup zip."""
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", person["name"]).strip(" .") or "profile"
    return f"{name}-{person['id']}"


# ── whose game holds an emulator's saves ─────────────────────────────────────

def in_root(path: Path) -> PurePosixPath | None:
    """`path` relative to the profile saves root once resolved, or None outside it."""
    try:
        return PurePosixPath(path.resolve().relative_to(ROOT.resolve()).as_posix())
    except (OSError, ValueError):
        return None


def is_profile_link(path: Path) -> bool:
    return path.is_symlink() and in_root(path) is not None


def _ini_value(file: Path, section: str | None, key: str) -> str | None:
    """Raw value of `key` in `[section]` (None = no section), as the core reads it."""
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    inside = section is None
    pat = re.compile(rf"\s*{re.escape(key)}\s*=\s*(.*)$")
    for line in lines:
        if line.lstrip().startswith("["):
            inside = line.strip() == f"[{section}]"
        elif inside and (m := pat.match(line)):
            return m.group(1)
    return None


def _option_holder(system: str) -> str | None:
    """Profile id whose folder a `keys` option points at right now, per the core's record."""
    try:
        store = json.loads((ROOT / STORE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in store if isinstance(store, dict) else ():
        parts = entry[4:].rsplit(":", 2) if entry.startswith("key:") else ()
        if len(parts) != 3:
            continue
        file, section, key = parts
        value = _ini_value(Path(file), section or None, key)
        rel = in_root(Path(value.strip().strip('"'))) if value else None
        if rel and len(rel.parts) >= 2 and rel.parts[1] == system:
            return rel.parts[0]
    return None


def holder(emu_id: str, base: Path | None) -> str | None:
    """Id of the profile whose game holds this emulator's saves now (or whose crash left them), else None."""
    lay = LAYOUT.get(emu_id)
    if lay is None or base is None:
        return None
    for rel in lay["dirs"]:
        inside = in_root(base / rel) if (base / rel).is_symlink() else None
        if inside is not None:
            return inside.parts[0] if inside.parts else "?"
    return _option_holder(lay["system"]) if lay["keys"] else None


def playing_message(emu_id: str, base: Path | None) -> str | None:
    who = holder(emu_id, base)
    if who is None:
        return None
    name = (find(who) or {}).get("name") or "Another profile"
    return f"{name} is playing {CATALOG[emu_id]['label']}: close the game first."


# ── where a profile's saves are ──────────────────────────────────────────────

def _rom_stems(folder: str) -> set:
    d = catalog.ROMS / folder
    return catalog._cached(f"stems:{folder}", d, lambda: {p.stem for p in d.iterdir()})


class View:
    """The saves of one emulator as one profile owns them.

    `profile` None is the primary: today's locations, read from
    `<name>.gamecore-primary` while another profile's game has them swapped.
    """

    def __init__(self, emu_id: str, base: Path, profile: dict | None = None):
        self.emu_id, self.base, self.profile = emu_id, base, profile
        self.layout = LAYOUT.get(emu_id) or _layout("")
        self.folder = ROOT / profile["id"] / self.layout["system"] if profile else None

    def _moved(self) -> list[tuple[str, Path]]:
        """(swapped folder relative to the base, where this view's copy of it is)."""
        out = []
        for rel, name in self.layout["dirs"].items():
            if self.folder is not None:
                out.append((rel, self.folder / name))
            elif is_profile_link(self.base / rel):
                out.append((rel, self.base / (rel + PRIMARY_SUFFIX)))
        return out

    def sources(self, ci: int) -> list[tuple[Path, str, str]]:
        """(folder, glob, logical prefix) to scan for collection `ci`; [] when the
        profile shares it. Entry ids carry the prefix, so an entry has the same
        id in every view (Cemu's swapped folder is one level below its collection)."""
        col = CATALOG[self.emu_id]["collections"][ci]
        sub, glob, out = PurePosixPath(col["subpath"]), col["glob"], []
        for rel, where in self._moved():
            if sub.is_relative_to(rel):
                return [(where / sub.relative_to(rel), glob, "")]
            if PurePosixPath(rel).is_relative_to(sub):
                prefix = PurePosixPath(rel).relative_to(sub)
                rest = "/".join(glob.split("/")[len(prefix.parts):]) or "*"
                out.append((where, rest, prefix.as_posix()))
        if self.folder is None:
            return [(self.base / col["subpath"] if col["subpath"] else self.base, glob, "")] + out
        if ci in self.layout["keys"]:
            sub_dir = self.layout["keys"][ci]
            return [(self.folder / sub_dir if sub_dir else self.folder, glob, "")]
        return out

    def keeps(self, cdir: Path, rel: PurePosixPath) -> bool:
        """Whether a scanned path is this view's own save."""
        if self.folder is None:
            # Never the profile's saves behind a swapped folder, nor the owner's
            # parked folder twice (it has its own source).
            if in_root(cdir / rel) is not None:
                return False
            return not any(part.endswith(PRIMARY_SUFFIX)
                           and is_profile_link(cdir.joinpath(*rel.parts[:i], part[:-len(PRIMARY_SUFFIX)]))
                           for i, part in enumerate(rel.parts))
        if catalog._PLAYER_SAVE.match(rel.name):
            return False
        if self.emu_id in ("gb", "gbc", "gba"):
            stem = PurePosixPath(rel.name).stem          # "Super Mario Bros. Deluxe" keeps its dot
            if stem in _rom_stems(self.emu_id):
                return True
            # A save with no ROM on the box shows under Advance, mGBA's own system.
            return self.emu_id == "gba" and not (stem in _rom_stems("gb") or stem in _rom_stems("gbc"))
        return True

    def path(self, ci: int, rel: PurePosixPath) -> tuple[Path, Path] | None:
        """(where a logical entry is on disk, the folder it must stay inside)."""
        for cdir, _glob, prefix in sorted(self.sources(ci), key=lambda s: -len(s[2])):
            head = PurePosixPath(prefix).parts if prefix else ()
            if rel.parts[:len(head)] == head:
                return cdir.joinpath(*rel.parts[len(head):]), cdir
        return None

    def at(self, rel: str) -> Path | None:
        """Where a folder given relative to the emulator base is in this view,
        None when the profile has no such folder."""
        for moved, where in self._moved():
            if PurePosixPath(rel).is_relative_to(moved):
                return where / PurePosixPath(rel).relative_to(moved)
        if self.folder is None:
            return self.base / rel
        col = next((ci for ci, c in enumerate(CATALOG[self.emu_id]["collections"])
                    if c["subpath"] == rel and ci in self.layout["keys"]), None)
        return None if col is None else self.folder / self.layout["keys"][col]
