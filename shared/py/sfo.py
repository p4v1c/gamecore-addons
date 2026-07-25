"""Minimal PARAM.SFO reader (title, serial, version, category)."""
import struct
from pathlib import Path


def parse(path: Path) -> dict:
    """Return the PARAM.SFO key/value table ({} on any error)."""
    try:
        d = path.read_bytes()
        magic, _ver, key_tbl, data_tbl, count = struct.unpack_from("<4sIIII", d, 0)
        if magic != b"\x00PSF":
            return {}
        out = {}
        for i in range(count):
            key_off, fmt, length, _max, data_off = struct.unpack_from("<HHIII", d, 20 + i * 16)
            key = d[key_tbl + key_off : d.index(b"\x00", key_tbl + key_off)].decode()
            raw = d[data_tbl + data_off : data_tbl + data_off + length]
            if fmt in (0x0204, 0x0004):  # utf8 / non-terminated utf8
                out[key] = raw.rstrip(b"\x00").decode("utf-8", "replace")
            else:  # 0x0404 — uint32
                out[key] = struct.unpack("<I", raw[:4])[0]
        return out
    except Exception:
        return {}
