"""GameCore addon — Save Manager.

Game-centric view of emulator saves & save states: each game shows its icon,
its name, and (on click) every save file/folder that makes it up. Saves that
can't be tied to a game (shared memory cards, Switch hashed ids, system data)
are listed apart. Download (zip for folders), restore (backup first), delete
(backup first). Native saves are portable; save states are version-specific
and carry a restore warning.

Beyond single entries:
  * whole-game zip and full-emulator backup zip (paths relative to the
    emulator base, restorable in one drop via /upload-full),
  * per-save export/import/delete INSIDE shared PS1/PS2/GC memory cards,
  * a per-emulator "transfer from PC" guide (guide.py) + a standalone PC
    export tool served under /tools/ that packs a PC's saves for this API.
"""
import io
import logging
import os
import re
import shutil
import struct
import tempfile
import zipfile
import zlib
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import memcard
import ryujinx as ryu
from catalog import CATALOG, resolve_base, scan, sony_game
from guide import GUIDE

ADDON_DIR = Path(__file__).parent
PORT = int(os.environ.get("ADDON_PORT", 8772))
log = logging.getLogger("save-manager")

app = FastAPI(title="GameCore addon — Save Manager")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _emu(emu_id: str) -> dict:
    if emu_id not in CATALOG:
        raise HTTPException(404, "unknown emulator")
    return CATALOG[emu_id]


def _entries(emu_id: str, internal: bool = False) -> list[dict]:
    _emu(emu_id)
    _base, raw = scan(emu_id)
    out = []
    for e in raw:
        try:
            size = dir_size(e["path"]) if e["is_dir"] else e["path"].stat().st_size
        except OSError:
            size = 0
        # Empty folders/files are phantoms — Wii channels or title dirs that were
        # registered but never written (e.g. an empty title/<hi>/<lo>/data). They
        # carry no save to back up, so they must not show up as "games".
        if size == 0:
            continue
        card_id = f"{e['ci']}/{e['rel']}"

        # A shared PS1/PS2 card holds every game's save in a card filesystem, so
        # listing filenames alone shows none of them. Read the card open (see
        # memcard.py) and surface each save inside as its own game — each one
        # individually exportable and deletable.
        in_card = memcard.read_saves(e["path"]) if e["mode"] == "cards" else []
        for s in in_card:
            # prefer the real game name + cover (from the ROM) over the card's
            # own short title ("NFS MW V"); fall back to the card title/serial
            rom_title, rom_icon = sony_game(s["serial"])
            title = rom_title or s["title"]
            v = {
                "id": card_id,                 # actions act on the whole card
                "name": f"{title} · in {e['rel']}",
                "kind": "save",
                "card": False,
                "in_card": e["rel"],
                "save_key": s.get("name") or s["serial"],  # unique on-card save name
                "is_dir": False,
                "size": s["size"],
                "sizeHuman": fmt_size(s["size"]),
                "game_key": s["serial"],
                "game_title": title,
            }
            if internal:
                v["_icon"] = rom_icon
            out.append(v)

        # Attribute the card itself: a card whose filename carries a serial, or
        # one whose content is a single game's saves (DuckStation's default
        # PerGameTitle cards are named after the game, not the serial), belongs
        # to that game; a multi-game card stays in "Shared & system files".
        key, title = e["key"], e["title"]
        serials = {s["serial"] for s in in_card}
        if in_card and not key:
            if len(serials) == 1:
                key = in_card[0]["serial"]
                title = sony_game(key)[0] or in_card[0]["title"]
            else:
                key, title = "", ""
        d = {
            "id": card_id,
            "name": e["rel"],
            "kind": e["kind"],
            "card": e["mode"] == "cards" and (bool(in_card) or not key),
            "is_dir": e["is_dir"],
            "size": size,
            "sizeHuman": fmt_size(size),
            "game_key": key,
            "game_title": title,
        }
        if internal:
            d["_icon"] = e["icon"]
        out.append(d)
    return out


def _collection_dir(emu_id: str, ci: int) -> tuple[Path, dict]:
    base = resolve_base(emu_id)
    if not base:
        raise HTTPException(404, "no data directory for this emulator on the box")
    cols = _emu(emu_id)["collections"]
    if ci >= len(cols):
        raise HTTPException(400, "bad collection")
    col = cols[ci]
    return (base / col["subpath"]) if col["subpath"] else base, col


def _resolve_entry(emu_id: str, entry_id: str) -> tuple[Path, Path, dict]:
    """entry id = '<collection>/<relative path>' (games can nest several
    levels deep — Wii title trees, Switch user dirs…)."""
    m = re.fullmatch(r"(\d+)/(.+)", entry_id)
    if not m:
        raise HTTPException(400, "bad entry id")
    cdir, col = _collection_dir(emu_id, int(m.group(1)))
    rel = PurePosixPath(m.group(2))
    if rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(403, "path outside the save directory")
    target = cdir.joinpath(*rel.parts)
    try:
        target.resolve().relative_to(cdir.resolve())
    except ValueError:
        raise HTTPException(403, "path outside the save directory")
    return target, cdir, col


_KEEP_BACKUPS = 3


def _backup(path: Path, prune: bool = True) -> None:
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-{ts}")
    if path.is_dir():
        shutil.copytree(path, dest)
    else:
        shutil.copy2(path, dest)
    if not prune:        # restoring FROM a backup must never delete that backup
        return
    # keep the disk sane: only the _KEEP_BACKUPS most recent backups per target
    # (prefix match, not glob — ROM names may contain [brackets] etc.)
    prefix = f"{path.name}.bak-"
    baks = sorted(p for p in path.parent.iterdir() if p.name.startswith(prefix))
    for old in baks[:-_KEEP_BACKUPS]:
        try:
            shutil.rmtree(old) if old.is_dir() else old.unlink()
        except OSError:
            pass


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/emulators")
def list_emulators():
    result = []
    for emu_id, meta in CATALOG.items():
        base = resolve_base(emu_id)
        entries = _entries(emu_id) if base else []
        games = {e["game_key"] for e in entries if e["game_key"]}
        result.append({
            "id": emu_id, "label": meta["label"], "available": base is not None,
            "games": len(games),
            "entries": len(entries),
        })
    return result


@app.get("/api/games/{emu_id}")
def list_games(emu_id: str):
    """Saves grouped by game (icon + name + its files), plus an 'other' bucket
    for saves not tied to a game (shared cards, system, cache)."""
    _emu(emu_id)
    base = resolve_base(emu_id)
    entries = _entries(emu_id, internal=True)
    games: dict[str, dict] = {}
    other: list[dict] = []
    for e in entries:
        icon = e.pop("_icon")
        if not e["game_key"]:
            other.append(e)
            continue
        g = games.setdefault(e["game_key"], {
            "key": e["game_key"],
            "title": e["game_title"] or e["game_key"],
            "entries": [], "saves": 0, "states": 0, "size": 0, "_icon": None,
        })
        g["entries"].append(e)
        g["size"] += e["size"]
        g["saves" if e["kind"] == "save" else "states"] += 1
        if icon and not g["_icon"]:
            g["_icon"] = icon
    games_list = sorted(games.values(), key=lambda g: g["title"].lower())
    for g in games_list:
        g["sizeHuman"] = fmt_size(g["size"])
        has_icon = g.pop("_icon") is not None
        g["icon"] = (f"/api/games/{emu_id}/icon?key={quote(g['key'])}"
                     if has_icon else None)
    return {
        "available": base is not None,
        "base": str(base) if base else None,
        "collections": [{"index": i, "kind": c["kind"], "mode": c["mode"],
                         "hint": _MODE_HINT.get(c["mode"], "")}
                        for i, c in enumerate(_emu(emu_id)["collections"])],
        "games": games_list,
        "other": other,
        "backups": _backups(emu_id),
        "guide": GUIDE.get(emu_id),
    }


_MODE_HINT = {
    "files": "single file (.sav, state…)",
    "dirs": "folder save — upload it as a .zip",
    "cards": "shared memory-card file",
    "any": "save-state file or folder (.zip)",
}


def _tga_to_png(data: bytes) -> bytes | None:
    """Wii U iconTex.tga → PNG (type-2 uncompressed 24/32-bit only)."""
    if len(data) < 18 or data[2] != 2:
        return None
    w, h = int.from_bytes(data[12:14], "little"), int.from_bytes(data[14:16], "little")
    bpp, desc = data[16], data[17]
    n = bpp // 8
    if n not in (3, 4) or len(data) < 18 + data[0] + w * h * n:
        return None
    off = 18 + data[0]
    rows = []
    for y in range(h):
        src = data[off + y * w * n:off + (y + 1) * w * n]
        px = bytearray(w * 4)
        for x in range(w):
            b, g, r = src[x * n], src[x * n + 1], src[x * n + 2]
            a = src[x * n + 3] if n == 4 else 255
            px[x * 4:x * 4 + 4] = (r, g, b, a)
        rows.append(bytes(px))
    if not desc & 0x20:          # bottom-up origin
        rows.reverse()
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, body):
        c = tag + body
        return len(body).to_bytes(4, "big") + c + zlib.crc32(c).to_bytes(4, "big")

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


_tga_cache: dict = {}


@app.get("/api/games/{emu_id}/icon")
def game_icon(emu_id: str, key: str):
    """The icon the resolver found for this game: savedata ICON0.PNG (PS3/PSP),
    Wii U iconTex.tga (converted), or a GameCore cover."""
    _emu(emu_id)
    _base, raw = scan(emu_id)
    icon = next((e["icon"] for e in raw if e["key"] == key and e["icon"]), None)
    if not icon and emu_id in ("pcsx2", "duckstation") and re.fullmatch(r"[A-Z]{4}-\d{5}", key):
        # a game that lives inside a shared card isn't in scan()'s entries
        # (attributed at the server layer) — resolve its cover by serial
        icon = sony_game(key)[1]
    if not icon or not icon.is_file():
        raise HTTPException(404)
    if icon.suffix.lower() == ".tga":
        stamp = (str(icon), icon.stat().st_mtime_ns)
        png = _tga_cache.get(stamp)
        if png is None:
            png = _tga_to_png(icon.read_bytes())
            if png is None:
                raise HTTPException(404)
            if len(_tga_cache) > 64:
                _tga_cache.clear()
            _tga_cache[stamp] = png
        return Response(png, media_type="image/png")
    return FileResponse(str(icon), media_type="image/png")


@app.get("/api/saves/{emu_id}/download")
def download(emu_id: str, id: str, save: str | None = None):
    target, cdir, _col = _resolve_entry(emu_id, id)
    if not target.exists():
        raise HTTPException(404, "not found")
    if save:
        # Export one game's save out of a shared card (.mcs for PS1, .psu PS2).
        try:
            fname, blob = memcard.export_save(target.read_bytes(), save)
        except KeyError:
            raise HTTPException(404, "that save is no longer on the card")
        except Exception:
            raise HTTPException(400, "could not read that save from the card")
        return Response(blob, media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    if target.is_dir():
        # zip paths are relative to the collection dir, so re-uploading the
        # zip restores nested games (Wii <hi>/<lo>, Switch <user>/<tid>…)
        # at their exact place
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in target.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(cdir))
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{target.name}.zip"'})
    return FileResponse(str(target), filename=target.name)


@app.post("/api/saves/{emu_id}/upload")
async def upload(emu_id: str, collection: int, file: UploadFile = File(...),
                 card: str | None = None):
    cdir, col = _collection_dir(emu_id, collection)
    cdir.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "").name
    if not name:
        raise HTTPException(400, "no filename")
    data = await file.read()

    if card is not None:
        # Inject one game's save (.mcs/.psu) into a specific shared card. The
        # whole card is backed up first; memcard.import_save builds a copy and
        # verifies the save reads back before we ever overwrite the original.
        card_path = cdir / Path(card).name
        if not card_path.is_file():
            raise HTTPException(404, "card not found")
        try:
            new_card = memcard.import_save(card_path.read_bytes(), data, name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception:
            raise HTTPException(400, "could not add that save to the card")
        _backup(card_path)
        card_path.write_bytes(new_card)
        return {"ok": True, "restored": [f"{name} → {card}"]}

    if name.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise HTTPException(400, "invalid zip")
        members = [m for m in zf.infolist() if not m.is_dir()]
        if not members:
            raise HTTPException(400, "empty zip")
        # Flat-file collections (mgba .sav dir, memcards…): a zip from a PC
        # usually wraps everything in one folder — strip that root so the
        # files land directly where the emulator looks for them.
        strip = 0
        if col["mode"] in ("files", "cards"):
            roots = {Path(m.filename).parts[0] for m in members}
            if len(roots) == 1 and all(len(Path(m.filename).parts) > 1 for m in members):
                strip = 1
        arcs = [(m, PurePosixPath(*PurePosixPath(m.filename).parts[strip:]))
                for m in members]
        for m, rel in arcs:
            if PurePosixPath(m.filename).is_absolute() or ".." in PurePosixPath(m.filename).parts:
                raise HTTPException(400, "zip contains an unsafe path")
        for root in sorted({rel.parts[0] for _m, rel in arcs}):
            _backup(cdir / root)
        for m, rel in arcs:
            dest = (cdir.joinpath(*rel.parts)).resolve()
            try:
                dest.relative_to(cdir.resolve())
            except ValueError:
                raise HTTPException(400, "zip contains an unsafe path")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(m))
        return {"ok": True, "restored": sorted({rel.parts[0] for _m, rel in arcs})}

    dest = cdir / name
    try:
        dest.resolve().relative_to(cdir.resolve())
    except ValueError:
        raise HTTPException(403, "unsafe path")
    _backup(dest)
    dest.write_bytes(data)
    return {"ok": True, "restored": [name]}


@app.delete("/api/saves/{emu_id}")
def delete(emu_id: str, id: str, save: str | None = None):
    target, _cdir, _col = _resolve_entry(emu_id, id)
    if not target.exists():
        raise HTTPException(404, "not found")
    if save:
        # Remove one game's save from inside a shared card. The whole card is
        # backed up first; memcard.delete_save builds a copy and verifies the
        # save is gone before we ever overwrite the original.
        try:
            new_card = memcard.delete_save(target.read_bytes(), save)
        except KeyError:
            raise HTTPException(404, "that save is no longer on the card")
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception:
            raise HTTPException(400, "could not remove that save from the card")
        _backup(target)
        target.write_bytes(new_card)
        return {"ok": True}
    _backup(target)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True}


def _zip_entries(items: list[tuple[Path, str]]):
    """Zip (path, arcname base) pairs. Backups are never bundled. Spools to a
    temp file past 64 MiB so a full RPCS3 tree can't eat the box's RAM."""
    buf = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    seen = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, arc in items:
            pairs = ([(f, f"{arc}/{f.relative_to(root).as_posix()}")
                      for f in sorted(root.rglob("*")) if f.is_file()]
                     if root.is_dir() else [(root, arc)])
            for f, name in pairs:
                if name in seen or ".bak-" in name:
                    continue
                seen.add(name)
                z.write(f, name)
    buf.seek(0)
    return buf


def _arc_items(emu_id: str, base: Path, cols: list, entries: list) -> list[tuple[Path, str]]:
    """(source path, zip name) per scan entry. Most entries are archived under
    their base-relative path. Game saves of the id-dependent emulators get a
    NORMALIZED prefix instead, so the zip restores on any install:
      Switch  switch-title/<title id>/<save type>/…   (Ryujinx ids and yuzu
              user dirs are install-specific)
      X360    x360-title/<TitleID>/…                  (Xenia profile XUIDs differ)
      PS4     ps4-title/<CUSA…>/<savedir>/…           (shadPS4 moved dirs in v0.16)
    /upload-full maps those prefixes back onto the local install."""
    items = []
    for e in entries:
        col, p = cols[e["ci"]], e["path"]
        if e["key"] and emu_id == "ryujinx":
            if col["subpath"] == "bis/user/save":
                tid, typ = ryu.identify(base, p)
                if tid:
                    src = next((p / c for c in ("0", "1") if (p / c).is_dir()), p)
                    items.append((src, f"switch-title/{tid}/{typ or 1}"))
                    continue
            elif col["subpath"] == "nand/user/save":
                items.append((p, f"switch-title/{e['key']}/1"))
                continue
        elif e["key"] and emu_id == "xenia":
            items.append((p, f"x360-title/{p.name.upper()}"))
            continue
        elif e["key"] and emu_id == "shadps4":
            rel = p.relative_to(base / col["subpath"])
            items.append((p, f"ps4-title/{rel.as_posix()}"))
            continue
        items.append((p, p.relative_to(base).as_posix()))
    return items


@app.get("/api/games/{emu_id}/download")
def download_game(emu_id: str, key: str):
    """Everything one game is made of (saves + states, every collection) as a
    single zip, restorable via the 'full backup' drop zone."""
    base, raw = scan(emu_id)
    if not base:
        raise HTTPException(404, "no data directory for this emulator on the box")
    picks = [e for e in raw if e["key"] == key]
    if picks:
        items = _arc_items(emu_id, base, _emu(emu_id)["collections"], picks)
    else:
        # A game that lives entirely inside a shared card is attributed at the
        # server layer (not by scan), so bundle the card(s) that hold it.
        seen, items = set(), []
        for e in _entries(emu_id):
            if e["game_key"] != key or e["id"] in seen:
                continue
            seen.add(e["id"])
            target, _cdir, _col = _resolve_entry(emu_id, e["id"])
            if target.exists():
                items.append((target, target.name))
        if not items:
            raise HTTPException(404, "unknown game")
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", key).strip() or "game"
    return StreamingResponse(_zip_entries(items), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{emu_id}-{stem}.zip"'})


@app.get("/api/saves/{emu_id}/download-all")
def download_all(emu_id: str):
    """Full backup of an emulator: every save, state, card and system file this
    addon knows about, in one zip /upload-full can restore anywhere."""
    base, raw = scan(emu_id)
    if not base:
        raise HTTPException(404, "no data directory for this emulator on the box")
    if not raw:
        raise HTTPException(404, "nothing to back up")
    items = _arc_items(emu_id, base, _emu(emu_id)["collections"], raw)
    ts = datetime.now().strftime("%Y%m%d")
    return StreamingResponse(_zip_entries(items), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{emu_id}-saves-{ts}.zip"'})


# ── backups ───────────────────────────────────────────────────────────────────
# Every destructive operation leaves a sibling <name>.bak-<YYYYMMDD-HHMMSS>
# (the 3 most recent per target are kept). This section makes them browsable
# and restorable from the UI.

_BAK_RE = re.compile(r"^(.+)\.bak-(\d{8}-\d{6})$")


def _backups(emu_id: str) -> list[dict]:
    base = resolve_base(emu_id)
    if not base:
        return []
    out, seen = [], set()
    for ci, col in enumerate(_emu(emu_id)["collections"]):
        cdir = base / col["subpath"] if col["subpath"] else base
        if not cdir.is_dir():
            continue
        for p in cdir.rglob("*"):
            m = _BAK_RE.fullmatch(p.name)
            if not m or p in seen:
                continue
            rel = p.relative_to(cdir)
            # a backup of a folder may contain older backups — list only the top one
            if any(".bak-" in part for part in rel.parts[:-1]):
                continue
            seen.add(p)
            try:
                size = dir_size(p) if p.is_dir() else p.stat().st_size
            except OSError:
                size = 0
            ts = m.group(2)
            out.append({
                "id": f"{ci}/{rel.as_posix()}",
                "name": rel.as_posix()[:-20],          # strip ".bak-<timestamp>"
                "when": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:]}",
                "is_dir": p.is_dir(),
                "size": size, "sizeHuman": fmt_size(size),
                "orig_exists": p.with_name(m.group(1)).exists(),
            })
    out.sort(key=lambda b: (b["when"], b["name"]), reverse=True)
    return out


@app.get("/api/backups/{emu_id}")
def list_backups(emu_id: str):
    return _backups(emu_id)


@app.post("/api/backups/{emu_id}/restore")
def restore_backup(emu_id: str, id: str):
    """Put a backup back in place of the original. The current version (if
    any) is backed up first — without pruning, so the backup being restored
    can never be deleted mid-operation — making a restore itself reversible."""
    target, _cdir, _col = _resolve_entry(emu_id, id)
    m = _BAK_RE.fullmatch(target.name)
    if not m or not target.exists():
        raise HTTPException(404, "backup not found")
    orig = target.with_name(m.group(1))
    _backup(orig, prune=False)
    if orig.exists():
        shutil.rmtree(orig) if orig.is_dir() else orig.unlink()
    if target.is_dir():
        shutil.copytree(target, orig)
    else:
        shutil.copy2(target, orig)
    return {"ok": True, "restored": m.group(1)}


@app.delete("/api/backups/{emu_id}")
def delete_backup(emu_id: str, id: str):
    target, _cdir, _col = _resolve_entry(emu_id, id)
    if not _BAK_RE.fullmatch(target.name) or not target.exists():
        raise HTTPException(404, "backup not found")
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    return {"ok": True}


_NORM_TAGS = {"switch-title": "ryujinx", "x360-title": "xenia", "ps4-title": "shadps4"}


def _clear_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for c in d.iterdir():
        shutil.rmtree(c) if c.is_dir() else c.unlink()


def _restore_normalized(emu_id: str, base: Path, zf: zipfile.ZipFile,
                        norm: list) -> list[str]:
    """Write switch-title/… x360-title/… ps4-title/… members onto this
    install's own layout (see _arc_items). `norm` = [(ZipInfo, rel parts)]."""
    restored = []
    if emu_id == "ryujinx":
        # group by (title id, save type); target the local save container
        groups: dict = {}
        for m, parts in norm:
            if len(parts) < 4 or not re.fullmatch(r"[0-9A-Fa-f]{16}", parts[1]):
                raise HTTPException(400, f"malformed switch save path '{m.filename}'")
            groups.setdefault((parts[1].upper(), parts[2]), []).append((m, parts[3:]))
        ryujinx_layout = (base / "bis/user/save").is_dir()
        tmap = ryu.title_map(base) if ryujinx_layout else {}
        for (tid, typ), files in sorted(groups.items()):
            if ryujinx_layout:
                try:
                    want = int(typ)
                except ValueError:
                    want = 1
                d = (tmap.get((tid, want)) or tmap.get((tid, 1))
                     or next((v for (t, _y), v in sorted(tmap.items()) if t == tid), None))
                if d is None:
                    raise HTTPException(400,
                        f"no save container for title {tid} on this box — launch the "
                        "game once (or open its save directory in Ryujinx), then retry")
                _backup(d)
                for c in ("0", "1"):     # 0 = committed, 1 = working: write both
                    _clear_dir(d / c)
                for m, rest in files:
                    for c in ("0", "1"):
                        dest = d / c / Path(*rest)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(m))
                restored.append(f"{tid} → {d.name}")
            else:                        # yuzu-family layout: dir name IS the title id
                user_root = base / "nand/user/save/0000000000000000"
                user = next((p.name for p in sorted(user_root.iterdir()) if p.is_dir()),
                            "0" * 32) if user_root.is_dir() else "0" * 32
                d = user_root / user / tid
                _backup(d)
                _clear_dir(d)
                for m, rest in files:
                    dest = d / Path(*rest)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(m))
                restored.append(tid)
        return restored

    if emu_id == "xenia":
        content = base / "content"
        profiles = [p.name for p in sorted(content.iterdir())
                    if p.is_dir() and re.fullmatch(r"[0-9A-F]{16}", p.name)
                    and p.name != "0" * 16] if content.is_dir() else []
        profiles.sort(key=lambda x: not (content / x / "FFFE07D1").is_dir())
        if not profiles:
            raise HTTPException(400, "no Xenia profile on this box — launch Xenia "
                                     "once to create one, then retry")
        done = set()
        for m, parts in norm:
            if len(parts) < 3 or not re.fullmatch(r"[0-9A-Fa-f]{8}", parts[1]):
                raise HTTPException(400, f"malformed X360 save path '{m.filename}'")
            tid = parts[1].upper()
            root = content / profiles[0] / tid
            if tid not in done:
                done.add(tid)
                _backup(root)
            dest = root / Path(*parts[2:])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(m))
        restored += sorted(done)
        return restored

    if emu_id == "shadps4":
        root = next((base / s for s in ("home/1/savedata", "savedata/1")
                     if (base / s).is_dir()), base / "home/1/savedata")
        done = set()
        for m, parts in norm:
            if len(parts) < 4:
                raise HTTPException(400, f"malformed PS4 save path '{m.filename}'")
            cusa = parts[1].upper()
            if cusa not in done:
                done.add(cusa)
                _backup(root / cusa)
            dest = root / cusa / Path(*parts[2:])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(m))
        restored += sorted(done)
        return restored

    raise HTTPException(400, "normalized save paths aren't supported for this emulator")


@app.post("/api/saves/{emu_id}/upload-full")
async def upload_full(emu_id: str, file: UploadFile = File(...)):
    """Restore a whole-game / full-backup zip — what /download-all, the
    per-game download and the PC export tool produce. Plain members (paths
    relative to the emulator base) must land inside a known save collection;
    normalized switch-title/… x360-title/… ps4-title/… members are remapped
    onto this install's own ids."""
    meta = _emu(emu_id)
    base = resolve_base(emu_id)
    if not base:
        raise HTTPException(404, "no data directory for this emulator on the box")
    data = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "invalid zip")
    members = [m for m in zf.infolist() if not m.is_dir()]
    if not members:
        raise HTTPException(400, "empty zip")

    norm, plain = [], []
    subpaths = [c["subpath"] for c in meta["collections"]]
    for m in members:
        rel = PurePosixPath(m.filename)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise HTTPException(400, "zip contains an unsafe path")
        tag_emu = _NORM_TAGS.get(rel.parts[0])
        if tag_emu:
            if tag_emu != emu_id:
                raise HTTPException(400,
                    f"'{m.filename}' is a {CATALOG[tag_emu]['label']} save — "
                    f"upload it to that system instead")
            norm.append((m, rel.parts))
        else:
            if not any(s == "" or rel.as_posix().startswith(s + "/") for s in subpaths):
                raise HTTPException(400,
                    f"'{m.filename}' doesn't belong to any save folder of this emulator "
                    f"(expected paths under: {', '.join(s or '<root>' for s in subpaths)})")
            plain.append(m)

    restored: list[str] = []
    if plain:
        # backup unit = the entry inside its collection, not the path's first
        # component (backing up all of dev_hdd0 for one RPCS3 save would copy
        # gigabytes of game data)
        units = set()
        for m in plain:
            rel = PurePosixPath(m.filename).as_posix()
            s = max((s for s in subpaths if s == "" or rel.startswith(s + "/")), key=len)
            rest = rel[len(s):].lstrip("/")
            units.add(f"{s}/{rest.split('/', 1)[0]}" if s else rest.split("/", 1)[0])
        for u in sorted(units):
            _backup(base / u)
        for m in plain:
            dest = (base / m.filename).resolve()
            try:
                dest.relative_to(base.resolve())
            except ValueError:
                raise HTTPException(400, "zip contains an unsafe path")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(m))
        restored += sorted({u.rsplit("/", 1)[-1] for u in units})
    if norm:
        restored += _restore_normalized(emu_id, base, zf, norm)
    return {"ok": True, "restored": restored}


app.mount("/tools", StaticFiles(directory=str(ADDON_DIR / "tools")), name="tools")
app.mount("/", StaticFiles(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
