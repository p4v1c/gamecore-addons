"""A sandbox box: fake HOME, two distinct GameCore roots, a fake core report.

The env vars are set before any addon module is imported, because the paths
are module constants. Nothing here can reach the real box: HOME, both roots
and the core port all point into a temp dir or at a closed port.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="bios-manager-test-"))
HOME = _TMP / "home"
CODE = _TMP / "GameCore"
DATA = _TMP / "userdata"
os.environ.update({
    "GAMECORE_HOME": str(HOME),
    "GAMECORE_PATH": str(CODE),
    "GAMECORE_DATA": str(DATA),
    "GAMECORE_BACKEND_PORT": "9",       # discard port: never the box's core
})
os.environ.pop("ADDON_DATA_DIR", None)
sys.path.insert(0, str(Path(__file__).parent.parent))

RA = HOME / ".config/gamecore-retroarch/system"
RYU = HOME / ".var/app/io.github.ryubing.Ryujinx/config/Ryujinx"
PCSX2_BIOS = HOME / ".var/app/net.pcsx2.PCSX2/config/PCSX2/bios"
DUCK_BIOS = HOME / ".local/share/duckstation/bios"
MELON_TOML = HOME / ".var/app/net.kuribo64.melonDS/config/melonDS/melonDS.toml"
DOLPHIN = HOME / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu"

SYSTEMS = [
    {"id": "switch", "label": "Nintendo Switch", "path": "flatpak"},
    {"id": "duckstation", "label": "PlayStation", "path": "lib/duck"},
    {"id": "pcsx2", "label": "PlayStation 2", "path": "flatpak"},
    {"id": "rpcs3", "label": "PlayStation 3", "path": "flatpak"},
    {"id": "melonds", "label": "Nintendo DS", "path": "flatpak"},
    {"id": "gamecube", "label": "GameCube", "path": "flatpak"},
    {"id": "wii", "label": "Wii", "path": "flatpak"},
    {"id": "gba", "label": "Game Boy Advance", "path": "/usr/bin/mgba-qt"},
    {"id": "naomi", "label": "Naomi", "path": "/usr/bin/retroarch"},
    {"id": "shadps4", "label": "PlayStation 4", "path": "flatpak"},
    {"id": "lutris", "label": "PC", "path": "/usr/bin/python3"},
]


def _row(sid, directory, files, any_file=False):
    rows = [{"file": f, "path": str(directory / f), "required": req, "note": "n",
             "status": "ok" if (directory / f).is_file() else "absent"} for f, req in files]
    if any_file:
        rows.append({"file": "", "path": str(directory), "required": True, "note": "n",
                     "any_file": True, "status": "absent"})
    return {"id": sid, "label": sid, "dir": str(directory), "files": rows, "installed": True}


def core_report() -> dict:
    """What the core's GET /api/bios answers for this sandbox."""
    rows = [
        _row("switch", RYU / "system", [("prod.keys", True), ("title.keys", False)]),
        _row("duckstation", DUCK_BIOS, [], any_file=True),
        _row("pcsx2", PCSX2_BIOS, [("ps2-0150e-20001228.bin", True)]),
        _row("rpcs3", HOME / ".var/app/net.rpcs3.RPCS3/config/rpcs3/dev_flash/sys/external", [("liblv2.sprx", True)]),
        _row("naomi", RA, [("dc/naomi.zip", False)]),
        _row("shadps4", HOME / ".var/app/net.shadps4.shadPS4/data/shadPS4", [("sys_modules/libSceNgs2.sprx", False)]),
        _row("cemu", HOME / ".var/app/info.cemu.Cemu/data/Cemu", [("keys.txt", False)]),
    ]
    return {r["id"]: r for r in rows}


@pytest.fixture
def box(monkeypatch):
    """A clean sandbox per test; the core answers unless the test says otherwise."""
    for d in (HOME, DATA, CODE):
        shutil.rmtree(d, ignore_errors=True)
    (DATA / "config").mkdir(parents=True)
    (DATA / "config/systems.json").write_text("\ufeff" + json.dumps(SYSTEMS), encoding="utf-8")
    CODE.mkdir(parents=True)
    import server
    monkeypatch.setattr(server, "core_rows", core_report)
    return server


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
