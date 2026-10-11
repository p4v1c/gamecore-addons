"""GameCore addon: BIOS Manager.

Drop BIOS dumps, firmware and keys (loose files, folders or zips) and each one
goes where the emulator GameCore runs for that system reads it. Recognition is
in identify.py, destinations in targets.py, the writes in place.py, the
per-system checklist in status.py. Nothing here ever downloads a BIOS or says
where to get one; it only files away what the owner already has.
"""
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles

import status
from intake import MAX_MEMBERS, clean_rel, recognise
from place import place_all
from targets import ADDON_DATA_DIR, ADDON_DIR, HOME, STAGING, core_rows, systems

PORT = int(os.environ.get("ADDON_PORT", "8773"))
MAX_UPLOAD = 2 << 30          # one Switch firmware + a DSi NAND + a PS3 PUP fit with room
WORK_ROOT = ADDON_DATA_DIR / "tmp"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bios-manager")

# Multipart parts above 1 MB spill to the temp dir, and /tmp is RAM on the box:
# a 400 MB firmware there would sit in memory twice.
tempfile.tempdir = str(WORK_ROOT)

app = FastAPI(title="GameCore addon: BIOS Manager", root_path=os.environ.get("ADDON_BASE", ""))
_busy = threading.Lock()


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/status")
def get_status():
    core = core_rows()
    return {"core": core is not None, "home": str(HOME), "systems": status.report(core)}


def _process(uploads: list[tuple[str, Path]], work: Path) -> list[dict]:
    core = core_rows()
    systems_by_id = {s["id"]: s for s in systems()}
    # Files known by name only: MAME sets, shadPS4 modules. RPCS3's .sprx are
    # the installed firmware's own pieces, put there by its installer, not by us.
    declared_zips = {f["file"]: sid for sid, row in (core or {}).items()
                     for f in row.get("files", [])
                     if f.get("file", "").endswith(".zip")
                     or (f.get("file", "").endswith(".sprx") and sid != "rpcs3")}
    try:
        found, ignored = recognise(uploads, work, declared_zips)
    except ValueError as e:
        raise HTTPException(413, f"Upload refused: {e}.")
    results = place_all(found, systems_by_id, core) + [{"outcome": "ignored", **i} for i in ignored]
    for r in results:
        log.info("%s: %s -> %s", r["outcome"], r.get("file"), r.get("dest") or r.get("reason", ""))
    return results


@app.post("/api/upload")
async def upload(request: Request):
    """Every part named `files`; its filename may carry a folder path."""
    length = int(request.headers.get("content-length") or 0)
    if not length:
        raise HTTPException(411, "The upload has no size.")
    if length > MAX_UPLOAD:
        raise HTTPException(413, "Upload refused: more than 2 GB at once. Send it in several goes.")
    if not _busy.acquire(blocking=False):
        raise HTTPException(409, "Another upload is being sorted. Try again when it is done.")
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=WORK_ROOT))
    form = None
    try:
        form = await request.form(max_files=MAX_MEMBERS, max_fields=MAX_MEMBERS)
        uploads = []
        for i, (key, part) in enumerate(form.multi_items()):
            if key != "files" or isinstance(part, str):
                continue
            dest = work / f"u{i}"
            with dest.open("wb") as out:
                while chunk := await part.read(1 << 20):
                    out.write(chunk)
            uploads.append((clean_rel(part.filename or "") or f"file{i}", dest))
        if not uploads:
            raise HTTPException(400, "No file in the upload.")
        return {"results": await run_in_threadpool(_process, uploads, work)}
    finally:
        if form is not None:
            await form.close()
        shutil.rmtree(work, ignore_errors=True)
        _busy.release()


@app.delete("/api/staged")
def delete_staged(path: str):
    """Drop a staged firmware once the emulator has installed it."""
    target = Path(path)
    try:
        rel = target.resolve().relative_to(STAGING.resolve())
    except ValueError:
        raise HTTPException(400, "Not a staged file.")
    if len(rel.parts) != 2 or not target.exists():
        raise HTTPException(404, "Not a staged file.")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True}


class _FreshStatic(StaticFiles):
    """The page, never from the browser cache: it changes with every update."""

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp


app.mount("/", _FreshStatic(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
