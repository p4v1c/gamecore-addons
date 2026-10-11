"""What an uploaded file is, and for which system. Pure: no filesystem writes.

Order matters. A known md5 is proof, so it wins. Files that have no fixed hash
(console-unique keys, per-console firmware, regional dumps the table lacks)
are recognised by content first, then by name and size, never by name alone
when a wrong guess would put the file in front of the wrong emulator.

`identify()` returns a dict or None:
  system  GameCore system id (systems.json / catalog pack id)
  names   target names inside that system's BIOS directory
  how     "md5" | "content" | "name and size"
  label   what the file is, for the report
An empty `names` means the emulator scans its directory (DuckStation, PCSX2):
the file keeps its own name.
Some results carry `kind`: "firmware" (installed by the emulator, staged
here), "nca" (one piece of a Switch firmware set) or "ignore" (recognised,
but GameCore has no use for it; `reason` says why).
"""
import hashlib
import json
import re
from pathlib import Path, PurePosixPath

KNOWN = json.loads((Path(__file__).parent / "known_bios.json").read_text(encoding="utf-8-sig"))["files"]

KIB = 1024
TEXT_MAX = 1 << 20          # key files are a few KB; anything bigger is not one
PS1_SIZE = 512 * KIB
PS2_SIZE = 4096 * KIB
IPL_SIZE = 2048 * KIB
DSI_NAND_MIN = 200 * KIB * KIB

JUNK_EXT = {".txt", ".nfo", ".diz", ".md", ".url", ".htm", ".html", ".pdf", ".jpg", ".jpeg",
            ".png", ".gif", ".bmp", ".webp", ".sfv", ".md5", ".sha1", ".log", ".xml",
            ".json", ".ini", ".lnk", ".db", ".torrent"}
JUNK_NAMES = {"thumbs.db", ".ds_store", "desktop.ini"}
UNSUPPORTED_ARCHIVES = {b"7z\xbc\xaf\x27\x1c": "7z", b"Rar!\x1a\x07": "rar"}

_PROD_KEYS = re.compile(r"^\s*(header_key|master_key_[0-9a-f]{2}|aes_kek_generation_source)\s*=\s*[0-9a-f]{32,64}\s*$",
                        re.IGNORECASE | re.MULTILINE)
_TITLE_KEY = re.compile(r"^[0-9a-f]{32}\s*=\s*[0-9a-f]{32}$", re.IGNORECASE)
_WIIU_KEY = re.compile(r"^[0-9a-f]{32}\s*(#.*)?$", re.IGNORECASE)
_3DS_KEYS = re.compile(r"^\s*(slot0x[0-9a-f]{2}key[xyn]|generatorconstant)\s*=", re.IGNORECASE | re.MULTILINE)
_REGIONS = {"eur": "EUR", "pal": "EUR", "usa": "USA", "us": "USA", "ntsc-u": "USA",
            "jap": "JAP", "jpn": "JAP", "jp": "JAP", "ntsc-j": "JAP"}


def md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def junk_reason(rel: str) -> str:
    """Why a file is noise (readme, picture, OS litter), or "" if it is not."""
    p = PurePosixPath(rel)
    if p.name.lower() in JUNK_NAMES or "__MACOSX" in p.parts or p.name.startswith("._"):
        return "system litter"
    if p.suffix.lower() in JUNK_EXT:
        return "readme, picture or text"
    return ""


def unsupported_archive(head: bytes) -> str:
    return next((kind for magic, kind in UNSUPPORTED_ARCHIVES.items() if head.startswith(magic)), "")


def _hit(system: str, names: list[str], how: str, label: str, **extra) -> dict:
    return {"system": system, "names": names, "how": how, "label": label, **extra}


def _by_text(text: str) -> dict | None:
    if _PROD_KEYS.search(text):
        return _hit("switch", ["prod.keys"], "content", "Switch console keys (prod.keys)")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and all(_TITLE_KEY.match(ln) for ln in lines):
        return _hit("switch", ["title.keys"], "content", "Switch title keys (title.keys)")
    if _3DS_KEYS.search(text):
        return _hit("azahar", [], "content", "3DS AES keys (aes_keys.txt)", kind="ignore",
                    reason="Azahar no longer reads aes_keys.txt; it plays decrypted dumps.")
    keys = [ln for ln in lines if not ln.startswith("#")]
    if keys and all(_WIIU_KEY.match(ln) for ln in keys):
        return _hit("cemu", ["keys.txt"], "content", "Wii U title keys (keys.txt)")
    return None


def _ds_name(stem: str, size: int) -> str:
    """melonDS's role for a DS/DSi file, from its name and size."""
    dsi = "dsi" in stem
    if "nand" in stem and size >= DSI_NAND_MIN:
        return "dsi_nand.bin"
    if "firm" in stem and size in (128 * KIB, 256 * KIB, 512 * KIB):
        return "dsi_firmware.bin" if dsi else "firmware.bin"
    arm = "7" if "7" in stem else "9" if "9" in stem else ""
    if not arm or ("bios" not in stem and "nds" not in stem):
        return ""
    if dsi and size == 64 * KIB:
        return f"dsi_bios{arm}.bin"
    if not dsi and size == (16 * KIB if arm == "7" else 4 * KIB):
        return f"bios{arm}.bin"
    return ""


def _gc_region(rel: str) -> str:
    return next((_REGIONS[p.lower()] for p in PurePosixPath(rel).parts[:-1] if p.lower() in _REGIONS), "")


def _by_name(rel: str, size: int, data_has) -> dict | None:
    name = PurePosixPath(rel).name
    stem = name.lower().rsplit(".", 1)[0]
    if "gba" in stem and size == 16 * KIB:
        return _hit("gba", ["gba_bios.bin"], "name and size", "Game Boy Advance BIOS")
    if size == 256 and ("sgb" in stem or "dmg" in stem or stem.startswith("gb")):
        target = "sgb_bios.bin" if "sgb" in stem else "gb_bios.bin"
        return _hit("gb", [target], "name and size", "Game Boy boot ROM")
    if size == 2304 and ("cgb" in stem or "gbc" in stem):
        return _hit("gbc", ["gbc_bios.bin"], "name and size", "Game Boy Color boot ROM")
    if ds := _ds_name(stem, size):
        return _hit("melonds", [ds], "name and size", f"Nintendo DS file ({ds})")
    if stem == "ipl" and size == IPL_SIZE:
        region = _gc_region(rel)
        if not region:
            return _hit("gamecube", [], "name and size", "GameCube IPL", kind="ignore",
                        reason="Region unknown. Put it in a EUR, USA or JAP folder and upload it again.")
        return _hit("gamecube", [f"GC/{region}/IPL.bin"], "name and size", f"GameCube IPL ({region})")
    if name.lower() in ("dsp_rom.bin", "dsp_coef.bin"):
        return _hit("gamecube", [f"GC/{name.lower()}"], "name and size", "GameCube DSP ROM")
    if size == PS1_SIZE and data_has(b"Sony Computer Entertainment Inc"):
        return _hit("duckstation", [], "content", "PlayStation BIOS")
    if size == PS2_SIZE and data_has(b"ROMVER") and data_has(b"RESET"):
        return _hit("pcsx2", [], "content", "PlayStation 2 BIOS")
    return None


def identify(rel: str, path: Path, md5: str, size: int, declared_zips: dict[str, str]) -> dict | None:
    """Recognise one file. `rel` is its path inside the upload, `declared_zips`
    maps BIOS archive names the core declares (dc/naomi.zip) to their system."""
    if entry := KNOWN.get(md5):
        return _hit(entry["system"], entry["names"], "md5", entry["names"][0])
    with path.open("rb") as fh:
        head = fh.read(4096)
    name = PurePosixPath(rel).name
    if head.startswith(b"SCEUF"):
        return _hit("rpcs3", ["PS3UPDAT.PUP"], "content", "PS3 system update (PUP)", kind="firmware")
    if name.lower().endswith(".nca") or PurePosixPath(rel).parent.name.lower().endswith(".nca"):
        return _hit("switch", [], "name", "Switch firmware piece (.nca)", kind="nca")
    for zip_name, system in declared_zips.items():
        if name.lower() == PurePosixPath(zip_name).name.lower():
            return _hit(system, [zip_name], "name", f"Declared file {zip_name}")
    if size <= TEXT_MAX and b"\0" not in head:
        try:
            hit = _by_text(path.read_text(encoding="utf-8-sig"))
        except UnicodeDecodeError:
            hit = None
        if hit:
            return hit

    def data_has(needle: bytes) -> bool:
        return size <= PS2_SIZE and needle in path.read_bytes()

    return _by_name(rel, size, data_has)
