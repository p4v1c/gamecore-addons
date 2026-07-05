"""GameCore addon — Save Manager.

Browse, download (backup), upload (restore) and delete emulator saves and
save states from the couch — no desktop needed. Save data locations per
emulator are in catalog.py. Every write is preceded by a timestamped .bak
(files) or a copy aside (folders); nothing is deleted without a backup.

Save STATES are version-specific (see catalog.py) — the UI warns before a
restore. Native SAVES are portable and safe to move around.
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

from catalog import CATALOG, resolve_base

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
    """All save/state entries for one emulator, with a stable relative id."""
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
            size = dir_size(f) if is_dir else f.stat().st_size
            out.append({
                # rel id = "<collection index>/<name>" — unambiguous, path-safe
                "id": f"{ci}/{f.name}",
                "name": f.name,
                "kind": col["kind"],
                "shared_card": col["mode"] == "cards",
                "is_dir": is_dir,
                "size": size,
                "sizeHuman": fmt_size(size),
                "mtime": int(f.stat().st_mtime),
            })
    return out


def _resolve_entry(emu_id: str, entry_id: str) -> tuple[Path, dict]:
    """Map a rel id back to a real path, guarding against traversal."""
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
    target = cdir / Path(name).name  # strip any path components
    try:
        target.resolve().relative_to(cdir.resolve())
    except ValueError:
        raise HTTPException(403, "path outside the save directory")
    return target, col


def _backup(path: Path) -> None:
    """Timestamped copy aside before overwrite/delete (file or folder)."""
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
    """Emulators that have a save directory present on the box, with counts."""
    result = []
    for emu_id, meta in CATALOG.items():
        base = resolve_base(emu_id)
        entries = _entries(emu_id) if base else []
        result.append({
            "id": emu_id,
            "label": meta["label"],
            "available": base is not None,
            "saves": sum(1 for e in entries if e["kind"] == "save"),
            "states": sum(1 for e in entries if e["kind"] == "state"),
        })
    return result


_MODE_HINT = {
    "files": "single file (.sav, memory card, state…)",
    "dirs": "folder save — upload it as a .zip",
    "cards": "shared memory card file",
}


@app.get("/api/saves/{emu_id}")
def list_saves(emu_id: str):
    meta = _emu(emu_id)
    base = resolve_base(emu_id)
    collections = [{
        "index": i,
        "kind": c["kind"],
        "mode": c["mode"],
        "subpath": c["subpath"] or "(root)",
        "hint": _MODE_HINT.get(c["mode"], ""),
    } for i, c in enumerate(meta["collections"])]
    return {
        "base": str(base) if base else None,
        "available": base is not None,
        "collections": collections,
        "entries": _entries(emu_id),
    }


@app.get("/api/saves/{emu_id}/download")
def download(emu_id: str, id: str):
    target, _col = _resolve_entry(emu_id, id)
    if not target.exists():
        raise HTTPException(404, "not found")
    if target.is_dir():
        # zip the folder on the fly
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in target.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(target.parent))
        buf.seek(0)
        return StreamingResponse(
            buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{target.name}.zip"'})
    return FileResponse(str(target), filename=target.name)


@app.post("/api/saves/{emu_id}/upload")
async def upload(emu_id: str, collection: int, file: UploadFile = File(...)):
    """Restore a save. `collection` picks where it goes (files/dirs mode).
    A .zip is extracted (folder saves); anything else is written as a file.
    The existing entry is backed up first."""
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
        # folder save delivered as a zip — extract, guarding each member
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
        return {"ok": True, "restored": sorted(roots), "kind": col["kind"]}

    # single file (memory card, .sav, save state…)
    dest = cdir / name
    try:
        dest.resolve().relative_to(cdir.resolve())
    except ValueError:
        raise HTTPException(403, "unsafe path")
    _backup(dest)
    dest.write_bytes(data)
    return {"ok": True, "restored": [name], "kind": col["kind"]}


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
