"""GameCore addon — ROM Manager.

Browser drag & drop ROM uploads, per system. Extracted from the core's
backend/routers/roms.py: same endpoints, same UX, own port. Reads the
systems catalogue from $GAMECORE_PATH/config/systems.json and writes ROMs
under $GAMECORE_PATH; after an upload it notifies the core
(POST /api/addons/notify → WebSocket rom_uploaded) so the TV UI refreshes.
"""
import fnmatch
import json
import logging
import os
import shutil
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

ADDON_DIR = Path(__file__).parent
GAMECORE_PATH = Path(os.environ.get("GAMECORE_PATH", "/opt/GameCore"))
SYSTEMS_FILE = GAMECORE_PATH / "config" / "systems.json"
PORT = int(os.environ.get("ADDON_PORT", 8770))
CORE_PORT = int(os.environ.get("GAMECORE_BACKEND_PORT", 8765))
CORE_NOTIFY = f"http://127.0.0.1:{CORE_PORT}/api/addons/notify"

log = logging.getLogger("rom-manager")

app = FastAPI(title="GameCore addon — ROM Manager")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── helpers (mirrored from the core so the addon stays self-contained) ────────

def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def matches_ext(filename: str, extensions: list[str]) -> bool:
    name = filename.lower()
    return any(fnmatch.fnmatch(name, p.lower()) for p in extensions)


def iter_rom_files(roms_path: Path, extensions: list[str], scan_dirs: bool = False):
    """Yield ROM entries. Most systems = files matching `extensions`; systems
    with scanDirs (PS3 disc games…) = folders. Mirrors the core rom_scanner."""
    if not roms_path.exists():
        return
    for f in sorted(roms_path.iterdir(), key=lambda x: x.name.lower()):
        if f.name.startswith(".") or "example" in f.name.lower():
            continue
        if scan_dirs:
            if f.is_dir():
                yield f
        else:
            if not f.is_file():
                continue
            if extensions and not matches_ext(f.name, extensions):
                continue
            yield f


def entry_size(p: Path) -> int:
    """File size, or the recursive size of a game folder."""
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return p.stat().st_size


def safe_filename(filename: str) -> str:
    """Remove only truly dangerous characters (/, \\0) to preserve exact ROM
    names for save-file matching on Linux."""
    filename = Path(filename).name
    filename = filename.replace("\x00", "").replace("/", "_")
    filename = filename.strip(". ")
    return filename or "unknown"


def systems() -> list[dict]:
    try:
        return json.loads(SYSTEMS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as e:
        raise HTTPException(503, f"systems.json unreadable: {e}")


def get_system(system_id: str) -> dict:
    s = next((x for x in systems() if x["id"].lower() == system_id.lower()), None)
    if not s:
        raise HTTPException(404, "System not found")
    return s


def roms_path_of(system: dict) -> Path | None:
    raw = system.get("romsPath", "")
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_absolute() else GAMECORE_PATH / p


async def notify_core(event: str, data: dict) -> None:
    """Best effort — an unreachable core must never fail an upload."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(CORE_NOTIFY, json={"event": event, "data": data})
    except Exception as e:
        log.warning("core notify failed: %s", e)


# ── API (same shape as the former core endpoints) ─────────────────────────────

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/emulators")
def list_emulators():
    result = []
    for s in systems():
        if s.get("type") != "emulator":
            continue
        roms_path = roms_path_of(s)
        extensions = s.get("extensions", [])
        scan_dirs = s.get("scanDirs", False)
        rom_count = 0
        total_size = 0
        if roms_path and roms_path.exists():
            for f in iter_rom_files(roms_path, extensions, scan_dirs):
                rom_count += 1
                total_size += entry_size(f)
        result.append({
            "id":         s["id"],
            "platform":   s.get("label", s["id"]),
            "iconPath":   s.get("iconPath", ""),
            "color":      s.get("color", "#5c7cfa"),
            "type":       "emulator",
            "extensions": extensions,
            "romCount":   rom_count,
            "totalSize":  fmt_size(total_size) if total_size else None,
        })
    return result


@app.get("/api/roms/{system_id}")
def list_roms(system_id: str):
    system = get_system(system_id)
    roms_path = roms_path_of(system)
    if not roms_path or not roms_path.exists():
        return []
    files = []
    for f in iter_rom_files(roms_path, system.get("extensions", []), system.get("scanDirs", False)):
        size = entry_size(f)
        files.append({
            "name":      f.name,
            "size":      size,
            "sizeHuman": fmt_size(size),
            "ext":       "DISC" if f.is_dir() else f.suffix.lstrip(".").upper(),
        })
    return files


@app.post("/api/roms/{system_id}/upload")
async def upload_rom(system_id: str, file: UploadFile = File(...)):
    system = get_system(system_id)
    if system.get("scanDirs"):
        raise HTTPException(400, "This system stores games as folders (disc games). "
                                 "Copy them via SSH/USB, or install a game .pkg from the "
                                 "RPCS3 manager — single-file upload does not apply here.")
    roms_path = roms_path_of(system)
    if not roms_path:
        raise HTTPException(400, "No ROM path configured")

    filename = safe_filename(file.filename or "")
    if not filename:
        raise HTTPException(400, "Invalid filename")

    exts = system.get("extensions", [])
    if exts and not matches_ext(filename, exts):
        raise HTTPException(415, f"Extension not allowed. Accepted: {', '.join(exts)}")

    roms_path.mkdir(parents=True, exist_ok=True)
    dest = roms_path / filename

    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1 << 20)  # 1 MB chunks — avoids loading large ROMs into RAM
            if not chunk:
                break
            out.write(chunk)
            size += len(chunk)

    await notify_core("rom_uploaded", {"system_id": system_id, "filename": filename})
    return {"name": filename, "size": size, "sizeHuman": fmt_size(size)}


@app.delete("/api/roms/{system_id}/{filename}")
def delete_rom(system_id: str, filename: str):
    system = get_system(system_id)
    roms_path = roms_path_of(system)
    if not roms_path:
        raise HTTPException(404)

    safe = safe_filename(filename)
    target = roms_path / safe
    try:
        target.resolve().relative_to(roms_path.resolve())
    except ValueError:
        raise HTTPException(403)

    if target.is_dir() and system.get("scanDirs"):
        shutil.rmtree(target)          # disc-game folder
    elif target.is_file():
        target.unlink()
    else:
        raise HTTPException(404)
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
