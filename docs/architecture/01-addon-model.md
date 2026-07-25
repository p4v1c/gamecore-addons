# 1 — The addon model

## Services, not plugins

An addon is **an independent HTTP service with a static web UI**. The core
never imports addon code; an addon never imports core code. They meet over
loopback HTTP and one JSON registry file.

That choice buys three things:

| Property | Because |
|---|---|
| An addon crash cannot take the TV down | separate process, separate systemd unit, `Restart=on-failure` |
| An addon can be written in anything | the contract is HTTP + a manifest, not a Python API |
| The checkout is what runs | no build step, so `git log` on the box tells you exactly what is deployed |

The cost is that everything shared has to be *copied* rather than imported —
see [2](02-lifecycle-and-registry.md#shared-components).

## The file contract

Every addon directory contains exactly:

| File | Role |
|---|---|
| `addon.json` | the manifest |
| `install.sh` | idempotent: venv, deps, shared copies, systemd unit, enable + start |
| `uninstall.sh` | stop, disable, remove the unit |
| `requirements.txt` | pinned Python deps |
| `server.py` | the FastAPI app |
| `web/index.html` | the whole UI, no build step |

Copy `addons/_template/` to start. Its `server.py` is the minimum that works:

```python
ADDON_DIR = Path(__file__).parent
PORT = int(os.environ.get("ADDON_PORT", 8799))

app = FastAPI(title="GameCore addon — template",
              root_path=os.environ.get("ADDON_BASE", ""))

@app.get("/api/health")
def health():
    return {"ok": True}

app.mount("/", StaticFiles(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
```

Four things in there are the contract, not decoration:

1. **`host="127.0.0.1"`** — an addon on `0.0.0.0` is an unauthenticated file
   manager on the LAN.
2. **`root_path=os.environ["ADDON_BASE"]`** — the app is served under `/roms`,
   `/saves`, `/rpcs3` by Caddy. Without it every generated URL is wrong.
3. **`ADDON_PORT` from the environment**, with the manifest's port as default.
4. **`web/` mounted last**, `html=True`, so unknown paths fall back to the SPA
   and `/api/*` still wins.

## `addon.json`

```jsonc
{
  "name": "save-manager",          // directory name, unit name, registry key
  "label": "Saves",                // shown in the nav bar and the TV screen
  "description": "Browse, back up, restore & delete every emulator's saves…",
  "version": "2.0.4",
  "type": "web",                   // web | service | tool
  "default": false,                // true → installed by the graphical installer
  "service": "user",               // systemd --user
  "port": 8772,
  "offline_assets": ["wheels"],    // extra payload for offline installs
  "path": "/saves"                 // the prefix Caddy proxies → ADDON_BASE
}
```

| `type` | What it is | In the nav bar |
|---|---|---|
| `web` | web UI on its own port + systemd service | yes |
| `service` | headless daemon (watcher, bridge…) | no |
| `tool` | one-shot script / system tweak, no service | no |

Every installed addon of any type lands in the registry
(`$GAMECORE_PATH/config/addons.json`) and shows on the TV's Addons screen.
Only `web` addons get a link.

## Ports

8770-8799 are reserved for addons.

| Addon | Port | `path` |
|---|---|---|
| rom-manager | 8770 | `/roms` |
| rpcs3-manager | 8771 | `/rpcs3` |
| save-manager | 8772 | `/saves` |
| _template | 8799 | — |

> The port appears **twice**: in `addon.json` and as `PORT=` in `install.sh`
> (which bakes it into the unit's `Environment=ADDON_PORT=`). Nothing checks
> that they agree. They must.

## What an addon may and may not do

**May:**

- read the box's filesystem — ROMs, emulator data, `config/systems.json` to
  learn where things live;
- call the core over loopback: `http://127.0.0.1:8765/api/…`;
- push an event to the TV with
  `POST /api/addons/notify {"event": "...", "data": {...}}`, which the core
  relays on its WebSocket;
- read `/gc/addons` to discover its siblings (same origin, no auth needed).

**May not:**

- import core Python modules — they are not on its path and the core's release
  cycle is independent;
- write auth code — Caddy owns that;
- bind anything but loopback;
- assume a build step exists.

## Runtime environment

The systemd unit written by `install.sh` provides:

| Variable | Meaning |
|---|---|
| `GAMECORE_PATH` | the core's root — how the addon finds `config/`, `emu/`, `assets/` |
| `ADDON_PORT` | the port to bind |
| `ADDON_BASE` | the path prefix → `root_path` |

with `WorkingDirectory=${ADDON_DIR}` and
`ExecStart=${ADDON_DIR}/.venv/bin/python server.py`. That working directory is
why `import sfo` resolves to the copy sitting next to `server.py`.

Logs: `journalctl --user -u gamecore-addon-<name> -f`.
