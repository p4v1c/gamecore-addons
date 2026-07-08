"""Ryujinx save identity — map bis/user/save/<id> dirs to Switch title ids.

Ryujinx names save directories by an install-specific counter (0000000000000001,
…02, …), so the folder name says nothing about the game. Two identity sources,
cheapest first:

  * ExtraData0 / ExtraData1 in the save dir — a 0x200-byte SaveDataExtraData
    whose first 0x40 bytes are the SaveDataAttribute:
    ProgramId u64 LE @0x00, UserId @0x08, Type byte @0x20
    (1=Account, 2=BCAT, 3=Device). Rebuilt by Ryujinx at every start.
  * bis/system/save/8000000000000000/{0,1}/imkvdb.arc — the save indexer, an
    IMKV/IMEN key-value archive: key = SaveDataAttribute, value =
    SaveDataIndexerValue (SaveDataId u64 LE @0x00).

Layout facts (LibHac DirectorySaveDataFileSystem): inside a save dir, 0/ is the
committed data and 1/ the working copy; on mount 0/ wins. So the portable
content of a save is the contents of 0/ — that's what gets exported, and a
restore writes both 0/ and 1/.
"""
import struct
from pathlib import Path

# SaveDataType byte → label suffix shown next to the entry
TYPE_LABEL = {1: "", 2: " · BCAT", 3: " · device"}


def save_attr(save_dir: Path) -> tuple[str, int] | tuple[None, None]:
    """(title id 16-hex upper, type) from the dir's ExtraData, or (None, None)."""
    for name in ("ExtraData0", "ExtraData1"):
        try:
            raw = (save_dir / name).read_bytes()
        except OSError:
            continue
        if len(raw) >= 0x21:
            tid = struct.unpack_from("<Q", raw, 0)[0]
            if tid:
                return f"{tid:016X}", raw[0x20]
    return None, None


def indexer(base: Path) -> dict:
    """save dir name (16-hex lower) → (title id 16-hex upper, type), from the
    save indexer archive. {} when the archive is missing or unreadable."""
    out: dict = {}
    for commit in ("0", "1"):
        p = base / "bis/system/save/8000000000000000" / commit / "imkvdb.arc"
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if len(raw) < 12 or raw[:4] != b"IMKV":
            continue
        count = struct.unpack_from("<i", raw, 8)[0]
        off = 12
        for _ in range(max(0, count)):
            if off + 12 > len(raw) or raw[off:off + 4] != b"IMEN":
                break
            ksz, vsz = struct.unpack_from("<ii", raw, off + 4)
            key = raw[off + 12:off + 12 + ksz]
            val = raw[off + 12 + ksz:off + 12 + ksz + vsz]
            off += 12 + ksz + vsz
            if len(key) >= 0x21 and len(val) >= 8:
                tid = struct.unpack_from("<Q", key, 0)[0]
                sid = struct.unpack_from("<Q", val, 0)[0]
                if tid and sid:
                    out.setdefault(f"{sid:016x}", (f"{tid:016X}", key[0x20]))
        if out:
            break
    return out


def identify(base: Path, save_dir: Path) -> tuple[str, int] | tuple[None, None]:
    """Best-effort (title id, type) for one save dir."""
    tid, typ = save_attr(save_dir)
    if tid:
        return tid, typ
    return indexer(base).get(save_dir.name.lower(), (None, None))


def title_map(base: Path) -> dict:
    """(title id, type) → save dir Path, for every save on this install —
    used to remap a normalized 'switch-title/…' restore to local dirs."""
    out = {}
    root = base / "bis/user/save"
    if not root.is_dir():
        return out
    idx = None
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        tid, typ = save_attr(d)
        if not tid:
            if idx is None:
                idx = indexer(base)
            tid, typ = idx.get(d.name.lower(), (None, None))
        if tid:
            out.setdefault((tid, typ), d)
    return out
