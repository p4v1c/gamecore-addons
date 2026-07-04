"""Template addon — minimal FastAPI service serving a static web UI.

Runs from the repo checkout; the systemd unit provides ADDON_PORT and
GAMECORE_PATH. Keep addons buildless (plain static web/) so the checkout
is exactly what runs.
"""
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

ADDON_DIR = Path(__file__).parent
PORT = int(os.environ.get("ADDON_PORT", 8799))

app = FastAPI(title="GameCore addon — template")

# The core UI and sibling addons live on other ports of the same host.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
