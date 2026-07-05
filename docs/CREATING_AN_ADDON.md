# Creating a GameCore addon

Copy `addons/_template/` to `addons/<your-name>/` and adapt. Directories whose
name starts with `_` are ignored by the manager.

## Layout

```
addons/<name>/
  addon.json      metadata — the only file the manager reads
  install.sh      idempotent setup (venv, systemd unit, …)
  uninstall.sh    removes everything install.sh created
  server.py       your service (python, node, anything)
  web/            static UI (web addons)
  requirements.txt
```

## addon.json

```json
{
  "name": "rom-manager",          // = directory name, [a-z0-9-]
  "label": "ROM Manager",         // shown in nav + Addons screen
  "description": "…",
  "version": "1.0.0",             // bump on every change; `update` re-runs install.sh
  "type": "web",                  // web | service | tool
  "default": true,                // pre-checked in the GameCore installer
  "service": "user",              // user | system | none
  "port": 8770,                   // web only — pick a free one in 8770-8799
  "offline_assets": []            // files the ISO payload must provide when OFFLINE=1
}
```

## install.sh / uninstall.sh contract

The manager runs them with these variables in the environment:

| var | meaning |
|-----|---------|
| `USER_NAME`     | user the addon runs as |
| `GAMECORE_PATH` | core install dir (default `/opt/GameCore`) |
| `GAMECORE_BACKEND_PORT` | core API port (default `8765`) — bake it into your unit if you call the core |
| `ADDON_DIR`     | this addon's directory in the checkout (= runtime dir) |
| `OFFLINE`       | `1` when installing from the GameCore OS ISO without network |
| `PAYLOAD_DIR`   | offline assets dir (when `OFFLINE=1`) |

Rules:

- **Idempotent** — `update` re-runs `install.sh` on the pulled checkout.
- **Never touch the registry** (`config/addons.json`) — the manager owns it.
- **No network when `OFFLINE=1`** — ship everything in the repo or declare it
  in `offline_assets` (provided under `PAYLOAD_DIR`).
- Service name convention: `gamecore-addon-<name>.service` (user unit unless
  the addon genuinely needs root, then `"service": "system"` and the CLI will
  require sudo).
- Buildless by design: plain static `web/`, no npm build step, so the checkout
  is exactly what runs.

## The shared nav (web addons)

`install.sh` copies `shared/nav/gamecore-nav.{js,css}` into your `web/` and your
page includes them (see the template's `index.html`). The bar fetches
`http://<host>:8765/api/addons` from the core and links every installed web
addon — that's what makes all addons feel like one site.

## Talking to the core

- Registry / nav data: `GET  http://<host>:8765/api/addons`
- Refresh the TV UI after a change: `POST http://<host>:8765/api/addons/notify`
  with `{"event": "rom_uploaded", "data": {…}}` — the core broadcasts it on its
  WebSocket to the frontend.

CORS is open on the core and should be open on your addon (see template).
