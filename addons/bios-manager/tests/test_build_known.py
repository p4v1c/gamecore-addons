"""known_bios.json renames libretro names to what GameCore's emulators load."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
import build_known

DAT = '''
	comment "Nintendo - Nintendo DS"
	rom ( name nds7.bin size 16384 crc 1 md5 AA sha1 1 )
	comment "Nintendo - GameCube"
	rom ( name "dolphin-emu/Sys/GC/JAP/IPL.bin" size 2097152 crc 1 md5 BB sha1 1 )
	rom ( name "dolphin-emu/Sys/GC/USA/IPL.bin" size 2097152 crc 1 md5 BB sha1 1 )
	rom ( name gc-dvd-20010608.bin size 131072 crc 1 md5 CC sha1 1 )
	comment "Sony - PlayStation 2"
	rom ( name "pcsx2/bios/ps2-0150e-20001228.bin" size 4194304 crc 1 md5 DD sha1 1 )
	comment "Atari - Lynx"
	rom ( name lynxboot.img size 512 crc 1 md5 EE sha1 1 )
'''


def test_renames_merges_and_drops():
    table = build_known.build(DAT)
    assert table["aa"] == {"system": "melonds", "size": 16384, "names": ["bios7.bin"]}
    assert table["bb"]["names"] == ["GC/JAP/IPL.bin", "GC/USA/IPL.bin"]
    assert table["dd"]["names"] == ["ps2-0150e-20001228.bin"]
    assert "cc" not in table and "ee" not in table


def test_shipped_table_matches_the_core_declared_names():
    files = json.loads((Path(__file__).parent.parent / "known_bios.json").read_text())["files"]
    assert files["838544f12de9b0abc90811279ee223c8"]["names"] == ["ps2-0150e-20001228.bin"]
    assert files["af5828fdff51384f99b3c4926be27762"]["names"] == ["kronos/saturn_bios.bin"]
    assert files["e10c53c2f8b90bab96ead2d368858623"]["names"] == ["dc/dc_boot.bin"]
