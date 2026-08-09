"""Template addon — minimal FastAPI service serving a static web UI.

Runs from the repo checkout; the systemd unit provides ADDON_PORT and the two
GameCore roots. Keep addons buildless (plain static web/) so the checkout is
exactly what runs.

── Where you may write (api 1) ───────────────────────────────────────────────

GameCore separates code from data, and an addon must know which is which:

  GAMECORE_PATH   the installation. Code shipped by the release — binaries,
                  bundled libraries, anything under lib/. READ ONLY: it is
                  becoming a read-only mount. Writing here fails at install
                  time on a box with nobody in front of it.
  GAMECORE_DATA   everything the player owns — config/systems.json, the ROMs
                  under emu/, covers, saves. Read and write freely.
  ADDON_DATA_DIR  your own writable corner ($GAMECORE_DATA/addons/<name>),
                  created for you before install.sh runs. Put your caches,
                  indexes and state here — nothing of yours belongs anywhere
                  else, and never under GAMECORE_PATH.

The question to ask for each path you touch, one at a time:

    Can the player modify this file, or does it arrive with the release?

Data → GAMECORE_DATA. Code → GAMECORE_PATH. If you are unsure, the core's
backend/services/paths.py has already decided; do not reinvent the boundary.

Declare `"api": 1` in addon.json only once this is true of your addon — the
manager refuses an addon without it precisely so a pre-split addon cannot
install and fail silently later. It is a statement of conformance, not a
switch.
"""
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

ADDON_DIR = Path(__file__).parent
GAMECORE_PATH = Path(os.environ.get("GAMECORE_PATH", "/opt/GameCore"))
# Falls back to GAMECORE_PATH so a box that has not yet taken the P3 OTA — whose
# systemd unit passes only GAMECORE_PATH — resolves every path exactly where it
# did before. TEMPORARY: drop the fallback once P12 has moved the data.
GAMECORE_DATA = Path(os.environ.get("GAMECORE_DATA") or GAMECORE_PATH)
# Your own state goes here, never under GAMECORE_PATH.
ADDON_DATA_DIR = Path(os.environ.get("ADDON_DATA_DIR")
                      or GAMECORE_DATA / "addons" / ADDON_DIR.name)
PORT = int(os.environ.get("ADDON_PORT", 8799))

app = FastAPI(title="GameCore addon — template", root_path=os.environ.get("ADDON_BASE", ""))

# The core UI and sibling addons live on other ports of the same host.


@app.get("/api/health")
def health():
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
