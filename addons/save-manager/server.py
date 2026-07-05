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
import zipfile
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from catalog import CATALOG, resolve_base, game_of, cover_for

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


def _entries(emu_id: str) -> list[dict]:
    base = resolve_base(emu_id)
    if not base:
        return []
    out = []
    for ci, col in enumerate(_emu(emu_id)["collections"]):
        cdir = base / col["subpath"] if col["subpath"] else base
        if not cdir.is_dir():
            continue
        for f in sorted(cdir.iterdir(), key=lambda x: x.name.lower()):
            if f.name.startswith("."):
                continue
            is_dir = f.is_dir()
            if col["mode"] == "dirs" and not is_dir:
                continue
            if col["mode"] in ("files", "cards"):
                if is_dir:
                    continue
                if col["exts"] and f.suffix.lower() not in col["exts"]:
                    continue
            key, title = game_of(emu_id, f.name, col["group"], base)
            size = dir_size(f) if is_dir else f.stat().st_size
            out.append({
                "id": f"{ci}/{f.name}",
                "name": f.name,
                "kind": col["kind"],
                "shared_card": col["mode"] == "cards",
                "is_dir": is_dir,
                "size": size,
                "sizeHuman": fmt_size(size),
                "game_key": key,
                "game_title": title,
            })
    return out


def _resolve_entry(emu_id: str, entry_id: str) -> tuple[Path, dict]:
    base = resolve_base(emu_id)
    if not base:
        raise HTTPException(404, "no data directory for this emulator on the box")
    m = re.fullmatch(r"(\d+)/(.+)", entry_id)
    if not m:
        raise HTTPException(400, "bad entry id")
    ci, name = int(m.group(1)), m.group(2)
    cols = _emu(emu_id)["collections"]
    if ci >= len(cols):
        raise HTTPException(400, "bad collection")
    col = cols[ci]
    cdir = (base / col["subpath"]) if col["subpath"] else base
    target = cdir / Path(name).name
    try:
        target.resolve().relative_to(cdir.resolve())
    except ValueError:
        raise HTTPException(403, "path outside the save directory")
    return target, col


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


def _icon_url(emu_id: str, key: str, title: str) -> str | None:
    """Icon available? RPCS3 ICON0, else a GameCore cover matched by title."""
    base = resolve_base(emu_id)
    if emu_id == "rpcs3" and base and re.fullmatch(r"[A-Za-z]{4}\d{5}", key):
        if (base / "dev_hdd0/game" / key / "ICON0.PNG").is_file():
            return f"/api/games/{emu_id}/icon?key={key}"
    if cover_for(key, title):
        return f"/api/games/{emu_id}/icon?key={key}"
    return None


@app.get("/api/games/{emu_id}")
def list_games(emu_id: str):
    """Saves grouped by game (icon + name + its files), plus an 'other' bucket
    for saves not tied to a game (shared cards, Switch, system)."""
    _emu(emu_id)
    base = resolve_base(emu_id)
    entries = _entries(emu_id)
    games: dict[str, dict] = {}
    other: list[dict] = []
    for e in entries:
        if not e["game_key"]:
            other.append(e)
            continue
        g = games.setdefault(e["game_key"], {
            "key": e["game_key"],
            "title": e["game_title"] or e["game_key"],
            "entries": [], "saves": 0, "states": 0, "size": 0,
        })
        g["entries"].append(e)
        g["size"] += e["size"]
        g["saves" if e["kind"] == "save" else "states"] += 1
    games_list = sorted(games.values(), key=lambda g: g["title"].lower())
    for g in games_list:
        g["sizeHuman"] = fmt_size(g["size"])
        g["icon"] = _icon_url(emu_id, g["key"], g["title"])
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
}


@app.get("/api/games/{emu_id}/icon")
def game_icon(emu_id: str, key: str):
    base = resolve_base(emu_id)
    if base and emu_id == "rpcs3" and re.fullmatch(r"[A-Za-z]{4}\d{5}", key):
        icon = base / "dev_hdd0/game" / key / "ICON0.PNG"
        if icon.is_file():
            return FileResponse(str(icon), media_type="image/png")
    # else: cover matched by the game's title — recompute the title for this key
    for e in _entries(emu_id):
        if e["game_key"] == key:
            cov = cover_for(e["game_key"], e["game_title"])
            if cov:
                return FileResponse(str(cov), media_type="image/png")
            break
    raise HTTPException(404)


@app.get("/api/saves/{emu_id}/download")
def download(emu_id: str, id: str):
    target, _col = _resolve_entry(emu_id, id)
    if not target.exists():
        raise HTTPException(404, "not found")
    if target.is_dir():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in target.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(target.parent))
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{target.name}.zip"'})
    return FileResponse(str(target), filename=target.name)


@app.post("/api/saves/{emu_id}/upload")
async def upload(emu_id: str, collection: int, file: UploadFile = File(...)):
    base = resolve_base(emu_id)
    if not base:
        raise HTTPException(503, "no data directory for this emulator on the box")
    cols = _emu(emu_id)["collections"]
    if collection >= len(cols):
        raise HTTPException(400, "bad collection")
    col = cols[collection]
    cdir = (base / col["subpath"]) if col["subpath"] else base
    cdir.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "").name
    if not name:
        raise HTTPException(400, "no filename")
    data = await file.read()

    if name.lower().endswith(".zip") and col["mode"] == "dirs":
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
    target, _col = _resolve_entry(emu_id, id)
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
