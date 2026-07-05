"""GameCore addon — Save Manager.

Game-centric view of emulator saves & save states: each game shows its icon,
its name, and (on click) every save file/folder that makes it up. Saves that
can't be tied to a game (shared memory cards, Switch hashed ids, system data)
are listed apart. Download (zip for folders), restore (backup first), delete
(backup first). Native saves are portable; save states are version-specific
and carry a restore warning.
"""
import io
import logging
import os
import re
import shutil
import struct
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

from catalog import CATALOG, resolve_base, scan

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
        d = {
            "id": f"{e['ci']}/{e['rel']}",
            "name": e["rel"],
            "kind": e["kind"],
            "shared_card": e["mode"] == "cards",
            "is_dir": e["is_dir"],
            "size": size,
            "sizeHuman": fmt_size(size),
            "game_key": e["key"],
            "game_title": e["title"],
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


def _backup(path: Path) -> None:
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-{ts}")
    if path.is_dir():
        shutil.copytree(path, dest)
    else:
        shutil.copy2(path, dest)


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
def download(emu_id: str, id: str):
    target, cdir, _col = _resolve_entry(emu_id, id)
    if not target.exists():
        raise HTTPException(404, "not found")
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
async def upload(emu_id: str, collection: int, file: UploadFile = File(...)):
    cdir, col = _collection_dir(emu_id, collection)
    cdir.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "").name
    if not name:
        raise HTTPException(400, "no filename")
    data = await file.read()

    if name.lower().endswith(".zip") and col["mode"] in ("dirs", "any"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise HTTPException(400, "invalid zip")
        roots = {Path(n).parts[0] for n in zf.namelist() if n.strip("/")}
        for root in roots:
            _backup(cdir / root)
        for member in zf.infolist():
            if member.is_dir():
                continue
            dest = (cdir / member.filename).resolve()
            try:
                dest.relative_to(cdir.resolve())
            except ValueError:
                raise HTTPException(400, "zip contains an unsafe path")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(member))
        return {"ok": True, "restored": sorted(roots)}

    dest = cdir / name
    try:
        dest.resolve().relative_to(cdir.resolve())
    except ValueError:
        raise HTTPException(403, "unsafe path")
    _backup(dest)
    dest.write_bytes(data)
    return {"ok": True, "restored": [name]}


@app.delete("/api/saves/{emu_id}")
def delete(emu_id: str, id: str):
    target, _cdir, _col = _resolve_entry(emu_id, id)
    if not target.exists():
        raise HTTPException(404, "not found")
    _backup(target)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
