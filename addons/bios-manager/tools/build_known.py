"""Rebuild known_bios.json from libretro's System.dat.

Usage: python tools/build_known.py System.dat > known_bios.json

System.dat (https://github.com/libretro/libretro-database, dat/System.dat)
names files the way libretro cores want them. GameCore runs some systems on
standalone emulators with their own names, so every kept entry is renamed to
the file the emulator GameCore uses actually loads; anything GameCore cannot
use is dropped. Only hashes and names are kept, never content.
"""
import json
import re
import sys

# System.dat section -> GameCore system id.
SECTIONS = {
    "Nintendo - Famicom Disk System": "fds",
    "NEC - PC Engine - TurboGrafx 16 - SuperGrafx": "pcenginecd",
    "Nintendo - Gameboy": "gb",
    "Nintendo - Super Game Boy": "gb",
    "Nintendo - Gameboy Color": "gbc",
    "Nintendo - Game Boy Advance": "gba",
    "Nintendo - GameCube": "gamecube",
    "Nintendo - Nintendo DS": "melonds",
    "Nintendo - Nintendo DSi": "melonds",
    "Sega - Dreamcast": "dreamcast",
    "Sega - Dreamcast-based Arcade": "naomi",
    "Sega - Mega CD - Sega CD": "megacd",
    "Sega - Saturn": "saturn",
    "Sony - PlayStation": "duckstation",
    "Sony - PlayStation 2": "pcsx2",
}

# dat name -> GameCore target name (None drops it). Names not listed keep
# their dat name, minus a "pcsx2/bios/" prefix.
RENAME = {
    "gamegenie.nes": None,
    "dmg_boot.bin": "gb_bios.bin", "dmg_rom.bin": "gb_bios.bin",
    "cgb_boot.bin": "gbc_bios.bin", "cgb_bios.bin": "gbc_bios.bin",
    "sgb2_bios.bin": "sgb_bios.bin", "sgb_boot.bin": "sgb_bios.bin",
    "sgb.boot.rom": "sgb_bios.bin", "sgb2_boot.bin": "sgb_bios.bin",
    "SGB1.sfc/sgb1.boot.rom": "sgb_bios.bin", "SGB2.sfc/sgb2.boot.rom": "sgb_bios.bin",
    "dolphin-emu/Sys/GC/EUR/IPL.bin": "GC/EUR/IPL.bin",
    "dolphin-emu/Sys/GC/JAP/IPL.bin": "GC/JAP/IPL.bin",
    "dolphin-emu/Sys/GC/USA/IPL.bin": "GC/USA/IPL.bin",
    "nds7.bin": "bios7.bin", "nds9.bin": "bios9.bin",
    "dc/boot.bin": "dc/dc_boot.bin", "dc/flash.bin": "dc/dc_flash.bin",
    "dc/naomi2.zip": None,
    # Kronos loads one file; the other Saturn BIOSes are for other cores.
    "saturn_bios.bin": "kronos/saturn_bios.bin",
    "kronos/stvbios.zip": None,
    "pcsx2/resources/GameIndex.yaml": None,
}
# Whole families GameCore has no use for: SNES-side Super Game Boy programs,
# GameCube DVD-drive firmware and the Dolphin code handler, other Saturn cores.
DROP = re.compile(r"^(SGB[12]\.sfc|sgb[12]\.program\.rom|SGB[12]\.sfc/program\.rom"
                  r"|gc-dvd-|gc-(ntsc|pal)-|dolphin-emu/Sys/codehandler"
                  r"|hisaturn|vsaturn|mpr-|sega_10|sega1003)")
ARCADE = {"dc/awbios.zip": "atomiswave"}
ROM = re.compile(r'rom \( name "?(.+?)"? size (\d+) crc \w+ md5 (\w+)')


def build(text: str) -> dict:
    out: dict = {}
    system = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('comment "'):
            system = SECTIONS.get(line[9:-1])
            continue
        m = ROM.match(line)
        if not m or system is None or DROP.match(m.group(1)):
            continue
        name, size, md5 = m.group(1), int(m.group(2)), m.group(3).lower()
        target = RENAME.get(name, name.removeprefix("pcsx2/bios/"))
        if target is None:
            continue
        entry = out.setdefault(md5, {"system": ARCADE.get(name, system), "size": size, "names": []})
        if target not in entry["names"]:
            entry["names"].append(target)
    return dict(sorted(out.items(), key=lambda kv: (kv[1]["system"], kv[1]["names"][0])))


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as fh:
        table = build(fh.read())
    json.dump({"source": "libretro-database dat/System.dat", "files": table}, sys.stdout, indent=1)
    sys.stdout.write("\n")
