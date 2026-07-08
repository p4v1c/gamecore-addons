"""Read — and now export/import — the per-game saves *inside* a shared
PlayStation memory card.

PS1 and PS2 store every game's save in one card file, in a card-specific
filesystem, so listing filenames never surfaces them and a whole library looks
like one opaque blob.

  read_saves(path)                 → [{"serial","title","size","name"}, ...]
  export_save(card_bytes, key)     → (filename, blob)   single save, .mcs/.psu/.gci
  import_save(card_bytes, blob, n) → new_card_bytes      inject one save
  delete_save(card_bytes, key)     → new_card_bytes      remove one save

"serial" groups a card's saves by game; "name" is the unique on-card save name
used to export exactly one save (a GameCube game can have several).

Everything is defensive:
  * read_saves never raises — an unrecognised/corrupt card yields [].
  * export_save raises KeyError if the serial isn't on the card.
  * import_save and delete_save build a *copy*, then re-parse it and refuse
    (raise ValueError) unless the result reads back as expected — so a botched
    write can never damage the caller's original card. Both refuse ECC PS2
    cards, where writing without recomputing ECC would corrupt the card.
  * delete_save mirrors what the consoles do: PS1 frames flip to the deleted
    state (0xA1-0xA3, data recoverable), PS2 root entries lose their EXISTS
    bit and their clusters are freed, GC directory slots are wiped (0xFF) and
    their BAT chain freed.

Formats:
  PS1 — 128 KiB, 16 blocks of 8 KiB. Block 0 is a 16-frame directory; each
        in-use frame names a save (region+serial+title) and links its blocks.
        Single save = .mcs (128-byte dir frame + its 8 KiB blocks).
  PS2 — 8 MiB FAT filesystem ("Sony PS2 Memory Card Format "). Each top-level
        directory in the root is one game's save folder, named by serial.
        Single save = .psu (folder + "." + ".." + per-file {entry, padded data}).
"""
import re
import struct
import unicodedata

_SONY_ID_RE = re.compile(r"([A-Za-z]{4})[-_]?(\d{3})\.?(\d{2})")


def _serial(name: str) -> str | None:
    m = _SONY_ID_RE.search(name)
    return f"{m.group(1).upper()}-{m.group(2)}{m.group(3)}" if m else None


def _read(path) -> bytes:
    if hasattr(path, "read_bytes"):
        return path.read_bytes()
    if isinstance(path, (bytes, bytearray)):
        return bytes(path)
    with open(path, "rb") as f:
        return f.read()


def _is_ps2(data: bytes) -> bool:
    return data[:28] == b"Sony PS2 Memory Card Format "


# ══ read ═══════════════════════════════════════════════════════════════════════

_scan_cache: dict = {}


def _jis(raw: bytes) -> str:
    """Shift-JIS bytes → clean text (PS titles are often full-width chars)."""
    txt = raw.split(b"\x00", 1)[0].decode("shift_jis", "ignore")
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", txt)).strip()


def read_saves(path) -> list[dict]:
    """Cards are re-listed on every API call; parsing an 8 MiB PS2 card each
    time is wasteful, so path-based reads are cached on (path, mtime, size)."""
    try:
        if hasattr(path, "stat"):
            st = path.stat()
            key = (str(path), st.st_mtime_ns, st.st_size)
            hit = _scan_cache.get(key)
            if hit is not None:
                return hit
            out = _parse_saves(path.read_bytes())
            if len(_scan_cache) > 64:
                _scan_cache.clear()
            _scan_cache[key] = out
            return out
        return _parse_saves(_read(path))
    except Exception:
        return []


def _parse_saves(data: bytes) -> list[dict]:
    try:
        if _is_ps2(data):
            return _ps2_saves(data)
        if _ps1_offset(data) is not None:
            return _ps1_saves(data)
        if _is_gc(data):
            return _gc_saves(data)
        return []
    except Exception:
        return []


# ══ PS1 ════════════════════════════════════════════════════════════════════════
# 131072 bytes = 16 blocks × 8192. Block 0 = directory: frame 0 header ("MC"),
# frames 1..15 one 128-byte entry each. State byte 0x51 = in-use first block,
# 0x52/0x53 = middle/last link, 0xA0 = free. Each frame's byte 127 is the XOR
# checksum of bytes 0..126. Directory "next block" field (u16 @8) is the next
# data block minus one, or 0xFFFF at the end of the chain.

_PS1_LEN = 131072
_PS1_FRAME = 128
_PS1_BLOCK = 8192


def _ps1_offset(data: bytes) -> int | None:
    """Locate the 128 KiB raw card inside the file (raw, or GME/other header)."""
    for cand in (0, len(data) - _PS1_LEN, 3904, 64):
        if cand >= 0 and len(data) >= cand + _PS1_LEN and data[cand:cand + 2] == b"MC":
            return cand
    return None


def _ps1_entry(card: bytes, slot: int) -> bytes:
    return card[slot * _PS1_FRAME:(slot + 1) * _PS1_FRAME]


def _ps1_chain(card: bytes, slot: int) -> list[int]:
    """Data blocks (1..15) making up the save that starts at `slot`."""
    blocks, cur, seen = [], slot, set()
    while 1 <= cur <= 15 and cur not in seen:
        seen.add(cur)
        blocks.append(cur)
        nxt = struct.unpack_from("<H", card, cur * _PS1_FRAME + 8)[0]
        if nxt == 0xFFFF:
            break
        cur = nxt + 1
    return blocks


def _ps1_name(entry: bytes) -> str:
    return entry[0x0A:0x0A + 20].split(b"\x00", 1)[0].decode("ascii", "ignore")


def _ps1_saves(data: bytes) -> list[dict]:
    off = _ps1_offset(data)
    if off is None:
        return []
    card = data[off:off + _PS1_LEN]
    saves = []
    for slot in range(1, 16):
        e = _ps1_entry(card, slot)
        if e[0] != 0x51:                       # only in-use "first block" links
            continue
        serial = _serial(_ps1_name(e))
        if not serial:
            continue
        size = struct.unpack_from("<I", e, 4)[0] or (len(_ps1_chain(card, slot)) * _PS1_BLOCK)
        block = card[slot * _PS1_BLOCK:slot * _PS1_BLOCK + 0x44]
        title = ""
        if block[:2] == b"SC":
            title = _jis(block[4:0x44])
        saves.append({"serial": serial, "title": title or serial, "size": size,
                      "name": _ps1_name(e)})
    return saves


def _ps1_cksum(frame: bytearray) -> None:
    x = 0
    for b in frame[:127]:
        x ^= b
    frame[127] = x


def _ps1_export(data: bytes, key: str) -> tuple[str, bytes]:
    off = _ps1_offset(data)
    if off is None:
        raise KeyError(key)
    card = data[off:off + _PS1_LEN]
    for slot in range(1, 16):
        e = _ps1_entry(card, slot)
        nm = _ps1_name(e)
        if e[0] == 0x51 and (nm == key or _serial(nm) == key):
            blocks = _ps1_chain(card, slot)
            body = b"".join(card[b * _PS1_BLOCK:(b + 1) * _PS1_BLOCK] for b in blocks)
            return f"{re.sub(r'[^A-Za-z0-9._-]', '_', nm or key)}.mcs", bytes(e) + body
    raise KeyError(key)


def _ps1_import(data: bytes, blob: bytes) -> bytes:
    off = _ps1_offset(data)
    if off is None:
        raise ValueError("Target card isn't a recognisable PS1 memory card.")
    if len(blob) < _PS1_FRAME + _PS1_BLOCK or (len(blob) - _PS1_FRAME) % _PS1_BLOCK:
        raise ValueError("That .mcs file is malformed.")
    header = blob[:_PS1_FRAME]
    body = blob[_PS1_FRAME:]
    nblocks = len(body) // _PS1_BLOCK
    name = _ps1_name(header)
    serial = _serial(name)

    out = bytearray(data)
    card = memoryview(out)[off:off + _PS1_LEN]
    for slot in range(1, 16):                  # refuse a duplicate
        e = _ps1_entry(bytes(card), slot)
        if e[0] == 0x51 and _ps1_name(e) == name:
            raise ValueError(f"“{name}” is already on this card — delete it first.")
    # any 0xAx state is free (0xA0 formatted, 0xA1-0xA3 deleted first/mid/last)
    free = [s for s in range(1, 16) if card[s * _PS1_FRAME] & 0xF0 == 0xA0]
    if len(free) < nblocks:
        raise ValueError(f"Not enough free blocks on this card ({len(free)} free, need {nblocks}).")
    chosen = free[:nblocks]

    for k, slot in enumerate(chosen):
        card[slot * _PS1_BLOCK:(slot + 1) * _PS1_BLOCK] = body[k * _PS1_BLOCK:(k + 1) * _PS1_BLOCK]
        frame = bytearray(_PS1_FRAME)
        frame[0] = 0x51 if k == 0 else (0x53 if k == nblocks - 1 else 0x52)
        if k == 0:
            struct.pack_into("<I", frame, 4, nblocks * _PS1_BLOCK)
            frame[0x0A:0x0A + len(name[:20].encode("ascii", "ignore"))] = name[:20].encode("ascii", "ignore")
        nxt = 0xFFFF if k == nblocks - 1 else chosen[k + 1] - 1
        struct.pack_into("<H", frame, 8, nxt)
        _ps1_cksum(frame)
        card[slot * _PS1_FRAME:(slot + 1) * _PS1_FRAME] = frame
    del card
    if not any(s["serial"] == serial for s in read_saves(bytes(out))):
        raise ValueError("Could not place the save on the card (nothing was written).")
    return bytes(out)


def _ps1_delete(data: bytes, key: str) -> bytes:
    """Flip every frame of the save's chain to its deleted state (0x51→0xA1,
    0x52→0xA2, 0x53→0xA3) — exactly what the console does; data blocks stay."""
    off = _ps1_offset(data)
    if off is None:
        raise KeyError(key)
    out = bytearray(data)
    card = memoryview(out)[off:off + _PS1_LEN]
    start = name = None
    for slot in range(1, 16):
        e = _ps1_entry(bytes(card), slot)
        nm = _ps1_name(e)
        if e[0] == 0x51 and (nm == key or _serial(nm) == key):
            start, name = slot, nm
            break
    if start is None:
        raise KeyError(key)
    for slot in _ps1_chain(bytes(card), start):
        frame = bytearray(card[slot * _PS1_FRAME:(slot + 1) * _PS1_FRAME])
        frame[0] = 0xA0 | (frame[0] & 0x0F)
        _ps1_cksum(frame)
        card[slot * _PS1_FRAME:(slot + 1) * _PS1_FRAME] = frame
    del card
    if any(s["name"] == name for s in read_saves(bytes(out))):
        raise ValueError("Could not remove the save from the card (nothing was written).")
    return bytes(out)


# ══ PS2 ════════════════════════════════════════════════════════════════════════
# 8 MiB FAT filesystem. The superblock gives geometry; the FAT is doubly
# indirect (ifc_list → indirect-FAT clusters → FAT clusters). Directory entries
# are 512 bytes. Each root entry that is a directory (not "." / "..") is a game.

_DF_EXISTS = 0x8000
_DF_DIRECTORY = 0x0020
_DF_FILE = 0x0010
_MODE_DIR = 0x8427
_MODE_FILE = 0x8497
_FAT_END = 0x7FFFFFFF
_FAT_USED = 0x80000000


class _Ps2:
    """Read view over a PS2 card. `mutable=True` (no-ECC only) enables writes."""

    def __init__(self, data, mutable: bool = False):
        self.data = bytearray(data) if mutable else data
        (self.page_len, self.ppc, self.ppb, _u,
         self.clusters, self.alloc_off, self.alloc_end,
         self.root_cluster) = struct.unpack_from("<HHHHIIII", self.data, 0x28)
        self.ifc = struct.unpack_from("<32I", self.data, 0x50)
        self.cl = self.page_len * self.ppc
        total_pages = self.clusters * self.ppc
        exact = total_pages * self.page_len
        self.ecc = len(self.data) != exact and len(self.data) >= total_pages * (self.page_len + 16)
        self.page_raw = self.page_len + (16 if self.ecc else 0)
        self.epc = self.cl // 4                 # FAT entries per cluster
        self.dpc = self.cl // 512               # dir entries per cluster
        if mutable and self.ecc:
            raise ValueError("This PS2 card uses ECC pages; per-game restore "
                             "isn't supported for it yet — restore the whole card.")

    # -- raw cluster access -----------------------------------------------------
    def read_cluster(self, n: int) -> bytes:
        bp = n * self.ppc
        return b"".join(self.data[(bp + j) * self.page_raw:(bp + j) * self.page_raw + self.page_len]
                        for j in range(self.ppc))

    def write_cluster(self, n: int, buf: bytes) -> None:   # no-ECC only (contiguous)
        self.data[n * self.cl:n * self.cl + len(buf)] = buf

    # -- FAT --------------------------------------------------------------------
    def _fat_loc(self, rel: int) -> tuple[int, int]:
        dbl_i, dbl_o = divmod(rel, self.epc)
        ind_i, ind_o = divmod(dbl_i, self.epc)
        fat_cluster = struct.unpack_from("<I", self.read_cluster(self.ifc[ind_i]), ind_o * 4)[0]
        return fat_cluster, dbl_o

    def fat(self, rel: int) -> int:
        c, o = self._fat_loc(rel)
        return struct.unpack_from("<I", self.read_cluster(c), o * 4)[0]

    def set_fat(self, rel: int, val: int) -> None:
        c, o = self._fat_loc(rel)
        struct.pack_into("<I", self.data, c * self.cl + o * 4, val)

    def chain(self, rel: int):
        seen = set()
        while rel != _FAT_END and rel not in seen and len(seen) < self.clusters:
            seen.add(rel)
            yield rel
            e = self.fat(rel)
            if not e & _FAT_USED:
                break
            rel = e & _FAT_END

    def chain_data(self, rel: int) -> bytes:
        return b"".join(self.read_cluster(self.alloc_off + r) for r in self.chain(rel))

    def entries(self, rel: int, count: int):
        buf = self.chain_data(rel)
        for i in range(count):
            e = buf[i * 512:(i + 1) * 512]
            if len(e) < 512:
                break
            mode, length = struct.unpack_from("<HxxI", e, 0)
            first = struct.unpack_from("<I", e, 0x10)[0]
            name = e[0x40:0x60].split(b"\x00", 1)[0].decode("ascii", "ignore")
            yield mode, length, first, name, e

    def root_len(self) -> int:
        return next(self.entries(self.root_cluster, 1))[1]


def _ps2_title(icon_sys: bytes) -> str:
    """Game title from a save's icon.sys (magic PS2D; Shift-JIS title @0xC0,
    68 bytes; u16 @0x06 = byte offset where the second line starts)."""
    if icon_sys[:4] != b"PS2D" or len(icon_sys) < 0xC0 + 68:
        return ""
    brk = struct.unpack_from("<H", icon_sys, 6)[0]
    raw = icon_sys[0xC0:0xC0 + 68]
    if 0 < brk < 68:
        return f"{_jis(raw[:brk])} {_jis(raw[brk:])}".strip()
    return _jis(raw)


def _ps2_saves(data: bytes) -> list[dict]:
    v = _Ps2(data)
    saves = []
    for mode, dlen, first, name, _e in v.entries(v.root_cluster, v.root_len()):
        if not (mode & _DF_EXISTS) or not (mode & _DF_DIRECTORY) or name in (".", ".."):
            continue
        serial = _serial(name)
        if not serial:
            continue
        size, title = 0, ""
        # entry count comes from the PARENT entry — PCSX2 writes 0 in "." length
        for m, l, f, n, _e2 in v.entries(first, dlen):
            if not (m & _DF_EXISTS) or not (m & _DF_FILE):
                continue
            size += l
            if not title and n.lower() == "icon.sys":
                title = _ps2_title(v.chain_data(f)[:l])
        saves.append({"serial": serial, "title": title or serial, "size": size, "name": name})
    return saves


def _ps2_folder_at(v: _Ps2, key: str):
    for i, (mode, _l, first, name, e) in enumerate(v.entries(v.root_cluster, v.root_len())):
        if (mode & _DF_EXISTS) and (mode & _DF_DIRECTORY) and name not in (".", "..") \
                and (name == key or _serial(name) == key):
            return i, name, first, e
    raise KeyError(key)


def _ps2_folder(v: _Ps2, key: str):
    _i, name, first, e = _ps2_folder_at(v, key)
    return name, first, e


def _ps2_export(data: bytes, key: str) -> tuple[str, bytes]:
    v = _Ps2(data)
    name, first, folder_e = _ps2_folder(v, key)
    dot = dotdot = None
    files = []
    flen = struct.unpack_from("<I", folder_e, 4)[0]     # count is in the parent
    for mode, length, f, n, e in v.entries(first, flen):
        if n == ".":
            dot = e
        elif n == "..":
            dotdot = e
        elif mode & _DF_FILE:
            files.append((e, v.chain_data(f)[:length]))
    out = bytearray()
    folder = bytearray(folder_e)
    struct.pack_into("<I", folder, 4, len(files) + 2)      # folder length = #files + . + ..
    out += folder
    out += (dot or _mk_entry(_MODE_DIR, len(files) + 2, 0, "."))
    out += (dotdot or _mk_entry(_MODE_DIR, len(files) + 2, 0, ".."))
    for e, content in files:
        out += e
        out += content + b"\x00" * (-len(content) % 1024)
    return f"{re.sub(r'[^A-Za-z0-9._-]', '_', name)}.psu", bytes(out)


def _mk_entry(mode: int, length: int, cluster: int, name: str) -> bytes:
    e = bytearray(512)
    struct.pack_into("<H", e, 0, mode)
    struct.pack_into("<I", e, 4, length)
    struct.pack_into("<I", e, 0x10, cluster & 0xFFFFFFFF)
    nb = name.encode("ascii", "ignore")[:32]
    e[0x40:0x40 + len(nb)] = nb
    return bytes(e)


def _parse_psu(blob: bytes):
    if len(blob) < 1536:
        raise ValueError("That .psu file is malformed.")
    folder = blob[0:512]
    dot, dotdot = blob[512:1024], blob[1024:1536]
    nfiles = struct.unpack_from("<I", folder, 4)[0] - 2
    off, files = 1536, []
    for _ in range(max(0, nfiles)):
        if off + 512 > len(blob):
            break
        ent = blob[off:off + 512]
        off += 512
        size = struct.unpack_from("<I", ent, 4)[0]
        files.append((ent, blob[off:off + size]))
        off += (size + 1023) // 1024 * 1024
    return folder, dot, dotdot, files


def _ps2_import(data: bytes, blob: bytes) -> bytes:
    v = _Ps2(data, mutable=True)                # raises on ECC cards
    folder, dot, dotdot, files = _parse_psu(blob)
    fname = folder[0x40:0x60].split(b"\x00", 1)[0].decode("ascii", "ignore")
    serial = _serial(fname)

    for mode, _l, _f, name, _e in v.entries(v.root_cluster, v.root_len()):
        if not mode & _DF_EXISTS:              # deleted entries don't block
            continue
        if name == fname or (serial and _serial(name) == serial):
            raise ValueError(f"A save for {serial or fname} is already on this card — "
                             "delete it first (or restore the whole card).")

    # Occupied data clusters = everything reachable from the root.
    used = set()

    def walk(rel, cnt):
        used.update(v.chain(rel))
        for mode, sublen, first, name, _e in v.entries(rel, cnt):
            if name in (".", "..") or not mode & _DF_EXISTS:
                continue                    # deleted entries' clusters are free
            if mode & _DF_DIRECTORY:
                walk(first, sublen)         # count lives in the parent entry
            elif mode & _DF_FILE:
                used.update(v.chain(first))

    walk(v.root_cluster, v.root_len())
    free = iter(r for r in range(v.alloc_end) if r not in used)

    def take(n):
        got = []
        for _ in range(n):
            nxt = next(free, None)
            if nxt is None:
                raise ValueError("Not enough free space on this memory card for that save.")
            got.append(nxt)
        return got

    def link(cs):                               # chain a run of clusters in the FAT
        for i, rel in enumerate(cs):
            v.set_fat(rel, _FAT_USED | (cs[i + 1] if i + 1 < len(cs) else _FAT_END))

    # write each file's data, remember its first cluster
    file_first = []
    for _e, content in files:
        if not content:
            file_first.append(_FAT_END)
            continue
        nc = (len(content) + v.cl - 1) // v.cl
        cs = take(nc)
        padded = content + b"\x00" * (nc * v.cl - len(content))
        for i, rel in enumerate(cs):
            v.write_cluster(v.alloc_off + rel, padded[i * v.cl:(i + 1) * v.cl])
        link(cs)
        file_first.append(cs[0])

    # build the save folder's own directory (".", "..", files)
    nentries = len(files) + 2
    sd = take((nentries * 512 + v.cl - 1) // v.cl)
    link(sd)
    sdbuf = bytearray(len(sd) * v.cl)
    dotE = bytearray(dot if len(dot) == 512 else _mk_entry(_MODE_DIR, nentries, 0, "."))
    struct.pack_into("<I", dotE, 4, nentries)
    struct.pack_into("<I", dotE, 0x10, sd[0])
    ddE = bytearray(dotdot if len(dotdot) == 512 else _mk_entry(_MODE_DIR, nentries, 0, ".."))
    struct.pack_into("<I", ddE, 0x10, v.root_cluster)
    sdbuf[0:512], sdbuf[512:1024] = dotE, ddE
    for i, ((ent, _c), ff) in enumerate(zip(files, file_first)):
        fe = bytearray(ent)
        struct.pack_into("<I", fe, 0x10, ff & 0xFFFFFFFF)
        struct.pack_into("<I", fe, 0x14, i + 2)
        sdbuf[(i + 2) * 512:(i + 3) * 512] = fe
    for i, rel in enumerate(sd):
        v.write_cluster(v.alloc_off + rel, sdbuf[i * v.cl:(i + 1) * v.cl])

    # add the folder to the root directory (growing it a cluster if full)
    rlen = v.root_len()
    root_chain = list(v.chain(v.root_cluster))
    ci, oi = divmod(rlen, v.dpc)
    if ci >= len(root_chain):
        newc = take(1)[0]
        v.set_fat(root_chain[-1], _FAT_USED | newc)
        v.set_fat(newc, _FAT_USED | _FAT_END)
        v.write_cluster(v.alloc_off + newc, b"\x00" * v.cl)
        root_chain.append(newc)
    folderE = bytearray(folder)
    struct.pack_into("<I", folderE, 4, nentries)
    struct.pack_into("<I", folderE, 0x10, sd[0])
    struct.pack_into("<I", folderE, 0x14, rlen)
    base = (v.alloc_off + root_chain[ci]) * v.cl + oi * 512
    v.data[base:base + 512] = folderE
    struct.pack_into("<I", v.data, (v.alloc_off + root_chain[0]) * v.cl + 4, rlen + 1)

    out = bytes(v.data)
    if not any(s["serial"] == serial for s in read_saves(out)):
        raise ValueError("Could not place the save on the card (nothing was written).")
    return out


def _ps2_delete(data: bytes, key: str) -> bytes:
    v = _Ps2(data, mutable=True)                # raises on ECC cards
    idx, name, first, folder_e = _ps2_folder_at(v, key)
    flen = struct.unpack_from("<I", folder_e, 4)[0]

    # free every file's cluster chain, then the folder's own directory chain
    for mode, _l, f, n, _e in list(v.entries(first, flen)):
        if n in (".", "..") or not mode & _DF_EXISTS:
            continue
        if mode & _DF_FILE:
            for rel in list(v.chain(f)):
                v.set_fat(rel, _FAT_END)        # no USED bit = free
    for rel in list(v.chain(first)):
        v.set_fat(rel, _FAT_END)

    # clear the EXISTS bit on the root entry — how the console marks deletion
    ci, oi = divmod(idx, v.dpc)
    root_chain = list(v.chain(v.root_cluster))
    base = (v.alloc_off + root_chain[ci]) * v.cl + oi * 512
    mode = struct.unpack_from("<H", v.data, base)[0]
    struct.pack_into("<H", v.data, base, mode & ~_DF_EXISTS)

    out = bytes(v.data)
    if any(s["name"] == name for s in read_saves(out)):
        raise ValueError("Could not remove the save from the card (nothing was written).")
    return out


# ══ GameCube ═══════════════════════════════════════════════════════════════════
# Flat array of 0x2000-byte blocks: 0 header, 1&2 directory (two copies), 3&4
# block-allocation table (BAT, two copies), 5.. data. The copy with the higher
# update counter and a valid big-endian checksum is the live one. A save = a
# directory entry (game code + internal name + first block + block count) whose
# data blocks are chained through the BAT. Single save = .gci (64-byte entry +
# its blocks) — the format Dolphin imports/exports natively.

_GC_BLOCK = 0x2000
_GC_ENT = 0x40
_GC_ENTRIES = 127


def _be16(b, o):
    return (b[o] << 8) | b[o + 1]


def _gc_csum(block, start, length):
    """Dolphin's big-endian checksum + inverse over `length` bytes of u16 words."""
    s = inv = 0
    for i in range(start, start + length, 2):
        w = (block[i] << 8) | block[i + 1]
        s = (s + w) & 0xFFFF
        inv = (inv + (w ^ 0xFFFF)) & 0xFFFF
    return (0 if s == 0xFFFF else s), (0 if inv == 0xFFFF else inv)


def _gc_active(data, blocks, cs_off, ctr_off):
    """Pick the live copy among `blocks`: valid checksum, highest counter."""
    best = None
    for blk in blocks:
        b = data[blk * _GC_BLOCK:(blk + 1) * _GC_BLOCK]
        if len(b) < _GC_BLOCK:
            continue
        rng = (0, 0x1FFC) if cs_off == 0x1FFC else (0x0004, 0x1FFC)
        cs, inv = _gc_csum(b, rng[0], rng[1])
        if cs == _be16(b, cs_off) and inv == _be16(b, cs_off + 2):
            ctr = _be16(b, ctr_off)
            if best is None or ctr > best[0]:
                best = (ctr, blk, b)
    return best


def _gc_dir(data):
    return _gc_active(data, (1, 2), 0x1FFC, 0x1FFA)


def _gc_bat(data):
    return _gc_active(data, (3, 4), 0x0000, 0x0004)


def _is_gc(data):
    return (len(data) % _GC_BLOCK == 0 and len(data) >= 6 * _GC_BLOCK
            and _gc_dir(data) is not None and _gc_bat(data) is not None)


def _gc_entries(dir_block):
    for i in range(_GC_ENTRIES):
        e = dir_block[i * _GC_ENT:(i + 1) * _GC_ENT]
        if e[0] == 0xFF:                       # empty slot
            continue
        code = e[0:4].decode("ascii", "ignore")
        name = e[0x08:0x08 + 0x20].split(b"\x00", 1)[0].decode("ascii", "ignore")
        first, count = _be16(e, 0x36), _be16(e, 0x38)
        yield i, code, name, first, count, e


def _gc_saves(data):
    _c, _b, d = _gc_dir(data)
    out = []
    for _i, code, name, _f, count, _e in _gc_entries(d):
        if not code.strip():
            continue
        out.append({"serial": code.upper(), "title": name or code,
                    "size": count * _GC_BLOCK, "name": name or code})
    return out


def _gc_chain(data, bat_block, first, count):
    """Physical data block numbers of a save, following the BAT."""
    blocks, cur = [], first
    while cur >= 5 and cur != 0xFFFF and len(blocks) < count:
        blocks.append(cur)
        cur = _be16(bat_block, 0x0A + (cur - 5) * 2)
    return blocks


def _gc_export(data, key):
    _c, _bd, d = _gc_dir(data)
    _cb, _bb, bat = _gc_bat(data)
    for _i, code, name, first, count, e in _gc_entries(d):
        if name == key or code.upper() == key:
            blocks = _gc_chain(data, bat, first, count)
            body = b"".join(data[b * _GC_BLOCK:(b + 1) * _GC_BLOCK] for b in blocks)
            fn = re.sub(r"[^A-Za-z0-9._-]", "_", f"{code}_{name}" or key)
            return f"{fn}.gci", bytes(e) + body
    raise KeyError(key)


def _gc_import(data, blob):
    if len(blob) < _GC_ENT or (len(blob) - _GC_ENT) % _GC_BLOCK:
        raise ValueError("That .gci file is malformed.")
    ent = bytearray(blob[:_GC_ENT])
    body = blob[_GC_ENT:]
    count = len(body) // _GC_BLOCK
    up_code = ent[0:4].decode("ascii", "ignore")
    up_name = ent[0x08:0x08 + 0x20].split(b"\x00", 1)[0].decode("ascii", "ignore")

    out = bytearray(data)
    total = len(out) // _GC_BLOCK
    dctr, dblk, dcur = _gc_dir(out)
    bctr, bblk, bcur = _gc_bat(out)
    d = bytearray(dcur)
    bat = bytearray(bcur)

    slot = None
    for i in range(_GC_ENTRIES):
        e = d[i * _GC_ENT:(i + 1) * _GC_ENT]
        if e[0] == 0xFF:
            if slot is None:
                slot = i
            continue
        if e[0:4].decode("ascii", "ignore") == up_code and \
                e[0x08:0x08 + 0x20].split(b"\x00", 1)[0].decode("ascii", "ignore") == up_name:
            raise ValueError(f"“{up_name}” is already on this card — delete it first.")
    if slot is None:
        raise ValueError("This card's directory is full (127 saves).")

    free = [blk for blk in range(5, total) if _be16(bat, 0x0A + (blk - 5) * 2) == 0]
    if len(free) < count:
        raise ValueError(f"Not enough free blocks on this card ({len(free)} free, need {count}).")
    chosen = free[:count]

    for k, blk in enumerate(chosen):
        out[blk * _GC_BLOCK:(blk + 1) * _GC_BLOCK] = body[k * _GC_BLOCK:(k + 1) * _GC_BLOCK]
        nxt = 0xFFFF if k == count - 1 else chosen[k + 1]
        struct.pack_into(">H", bat, 0x0A + (blk - 5) * 2, nxt)
    struct.pack_into(">H", bat, 0x0006, _be16(bat, 0x0006) - count)   # free blocks
    struct.pack_into(">H", bat, 0x0008, chosen[-1])                   # last allocated

    struct.pack_into(">H", ent, 0x36, chosen[0])                      # first block
    struct.pack_into(">H", ent, 0x38, count)   # keep count honest even if the
    d[slot * _GC_ENT:(slot + 1) * _GC_ENT] = ent                      # .gci lied

    dnew = max(dctr, _be16(out[2 * _GC_BLOCK:3 * _GC_BLOCK] or d, 0x1FFA)) + 1 & 0xFFFF
    bnew = max(bctr, _be16(out[4 * _GC_BLOCK:5 * _GC_BLOCK] or bat, 0x0004)) + 1 & 0xFFFF
    _gc_write_dir(out, d, dnew)
    _gc_write_bat(out, bat, bnew)

    if not any(s["name"] == up_name for s in read_saves(bytes(out))):
        raise ValueError("Could not place the save on the card (nothing was written).")
    return bytes(out)


def _gc_write_dir(out, d, counter):
    struct.pack_into(">H", d, 0x1FFA, counter & 0xFFFF)
    cs, inv = _gc_csum(d, 0, 0x1FFC)
    struct.pack_into(">H", d, 0x1FFC, cs)
    struct.pack_into(">H", d, 0x1FFE, inv)
    out[1 * _GC_BLOCK:2 * _GC_BLOCK] = d       # write both copies, both current
    out[2 * _GC_BLOCK:3 * _GC_BLOCK] = d


def _gc_write_bat(out, bat, counter):
    struct.pack_into(">H", bat, 0x0004, counter & 0xFFFF)
    cs, inv = _gc_csum(bat, 0x0004, 0x1FFC)
    struct.pack_into(">H", bat, 0x0000, cs)
    struct.pack_into(">H", bat, 0x0002, inv)
    out[3 * _GC_BLOCK:4 * _GC_BLOCK] = bat
    out[4 * _GC_BLOCK:5 * _GC_BLOCK] = bat


def _gc_delete(data: bytes, key: str) -> bytes:
    out = bytearray(data)
    dctr, _dblk, dcur = _gc_dir(out)
    bctr, _bblk, bcur = _gc_bat(out)
    d = bytearray(dcur)
    bat = bytearray(bcur)
    hit = None
    for i, code, name, first, count, _e in _gc_entries(d):
        if name == key or code.upper() == key:
            hit = (i, name, first, count)
            break
    if hit is None:
        raise KeyError(key)
    i, name, first, count = hit
    blocks = _gc_chain(out, bat, first, count)
    for blk in blocks:                          # free the BAT chain
        struct.pack_into(">H", bat, 0x0A + (blk - 5) * 2, 0)
    struct.pack_into(">H", bat, 0x0006, (_be16(bat, 0x0006) + len(blocks)) & 0xFFFF)
    d[i * _GC_ENT:(i + 1) * _GC_ENT] = b"\xFF" * _GC_ENT   # empty directory slot
    _gc_write_dir(out, d, (dctr + 1) & 0xFFFF)
    _gc_write_bat(out, bat, (bctr + 1) & 0xFFFF)
    if any(s["name"] == name for s in read_saves(bytes(out))):
        raise ValueError("Could not remove the save from the card (nothing was written).")
    return bytes(out)


def gci_info(path) -> dict | None:
    """Header of a STANDALONE .gci file (Dolphin GCI-folder mode) →
    {"code": 4-char game id, "name": internal save name}, or None."""
    try:
        if hasattr(path, "open"):        # only the header — scans hit many GCIs
            with path.open("rb") as f:
                h = f.read(_GC_ENT)
        else:
            h = _read(path)[:_GC_ENT]
    except Exception:
        return None
    if len(h) < _GC_ENT or h[0] == 0xFF:
        return None
    code = h[0:4].decode("ascii", "ignore").strip()
    if len(code) != 4 or not code.isalnum():
        return None
    name = h[0x08:0x08 + 0x20].split(b"\x00", 1)[0].decode("ascii", "ignore")
    return {"code": code.upper(), "name": name}


# ══ public export/import/delete ════════════════════════════════════════════════

def export_save(card_bytes: bytes, key: str) -> tuple[str, bytes]:
    """One game's save as a standalone file (.mcs PS1, .psu PS2, .gci GameCube)."""
    if _is_ps2(card_bytes):
        return _ps2_export(card_bytes, key)
    if _ps1_offset(card_bytes) is not None:
        return _ps1_export(card_bytes, key)
    if _is_gc(card_bytes):
        return _gc_export(card_bytes, key)
    raise KeyError(key)


def delete_save(card_bytes: bytes, key: str) -> bytes:
    """Remove one game's save from a card; returns the new card bytes.
    Raises KeyError if the save isn't on the card, ValueError (user-facing
    message) on anything else — the caller's original card is never touched."""
    if _is_ps2(card_bytes):
        return _ps2_delete(card_bytes, key)
    if _ps1_offset(card_bytes) is not None:
        return _ps1_delete(card_bytes, key)
    if _is_gc(card_bytes):
        return _gc_delete(card_bytes, key)
    raise KeyError(key)


def import_save(card_bytes: bytes, blob: bytes, blob_name: str) -> bytes:
    """Inject one save (.mcs/.psu/.gci) into a card; returns the new card bytes.
    Raises ValueError with a user-facing message on any problem — the caller's
    original card is never touched unless this returns successfully."""
    name = blob_name.lower()
    if name.endswith(".psu"):
        if not _is_ps2(card_bytes):
            raise ValueError("A .psu is a PS2 save — it can't go on this card.")
        return _ps2_import(card_bytes, blob)
    if name.endswith(".gci"):
        if not _is_gc(card_bytes):
            raise ValueError("A .gci is a GameCube save — it can't go on this card.")
        return _gc_import(card_bytes, blob)
    if name.endswith((".mcs", ".mcr", ".psx")):
        if _ps1_offset(card_bytes) is None:
            raise ValueError("A .mcs is a PS1 save — it can't go on this card.")
        return _ps1_import(card_bytes, blob)
    raise ValueError("Unsupported save — use .mcs (PS1), .psu (PS2) or .gci (GameCube).")
