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
  "api": 1,                       // REQUIRED — "this addon writes to GAMECORE_DATA,
                                  // never to GAMECORE_PATH". install/update refuse
                                  // an addon without it. Add it last (see Rules).
  "name": "rom-manager",          // = directory name, [a-z0-9-]
  "label": "ROM Manager",         // shown in nav + Addons screen
  "description": "…",
  "version": "1.0.0",             // bump on every change; `update` re-runs install.sh
  "type": "web",                  // web | service | tool
  "default": true,                // pre-checked in the GameCore installer
  "service": "user",              // user | system | none
  "port": 8770,                   // web only — pick a free one in 8770-8799 (loopback only)
  "path": "/roms",                // web only — URL prefix behind the Caddy proxy;
                                  // needs a matching route in the core's install/Caddyfile
  "offline_assets": []            // files the ISO payload must provide when OFFLINE=1
}
```

## install.sh / uninstall.sh contract

The manager runs them with these variables in the environment:

| var | meaning |
|-----|---------|
| `USER_NAME`     | user the addon runs as |
| `GAMECORE_PATH` | core install dir (default `/opt/GameCore`) — **read only** |
| `GAMECORE_DATA` | player data dir (default `$GAMECORE_PATH`) — writable |
| `ADDON_DATA_DIR` | your writable corner, `$GAMECORE_DATA/addons/<name>`, created before this script runs |
| `GAMECORE_ADDON_API` | api version the manager speaks (`1`) |
| `GAMECORE_BACKEND_PORT` | core API port (default `8765`) — bake it into your unit if you call the core |
| `ADDON_DIR`     | this addon's directory in the checkout (= runtime dir) |
| `OFFLINE`       | `1` when installing from the GameCore OS ISO without network |
| `PAYLOAD_DIR`   | offline assets dir (when `OFFLINE=1`) |

Rules:

- **Write to `$GAMECORE_DATA` / `$ADDON_DATA_DIR`, never to `$GAMECORE_PATH`.**
  The install root is becoming a read-only mount and is handed to you for
  reading only. See the code/data section in the repo README for how to tell a
  data path from a code path — and note that your `install.sh` must pass **both**
  roots to your systemd unit, or the service will never see them.
- **Declare `"api": 1` in `addon.json`** once the above is true of your addon.
  Without it, `gamecore-addon install` and `update` refuse it by name. Add it
  last, after the paths are right: it states conformance, it does not grant it.
- **Idempotent** — `update` re-runs `install.sh` on the pulled checkout.
- **Never touch the registry** (`$GAMECORE_DATA/config/addons.json`) — the manager owns it.
- **No network when `OFFLINE=1`** — ship everything in the repo or declare it
  in `offline_assets` (provided under `PAYLOAD_DIR`).
- Service name convention: `gamecore-addon-<name>.service` (user unit unless
  the addon genuinely needs root, then `"service": "system"` and the CLI will
  require sudo).
- Buildless by design: plain static `web/`, no npm build step, so the checkout
  is exactly what runs.

## Security model (docs/SECURITY.md)

The LAN reaches everything through the Caddy reverse-proxy on ONE origin
(`https://box:8443`), which enforces the shared login. Consequences for
addons:

- **Bind loopback only**: `uvicorn.run(app, host="127.0.0.1", port=PORT)`.
- **No CORS middleware** — everything is same-origin behind the proxy.
- **No auth code** — Caddy logs the user in before your addon sees the
  request; you only receive the `X-GC-User` header.
- **Path prefix**: your unit gets `ADDON_BASE=/yourprefix` (see the template's
  `install.sh`) and `server.py` passes it as FastAPI's `root_path`. Declare
  the same prefix in `addon.json` `"path"` and add a `handle_path` route to
  the core's `install/Caddyfile`.
- **Relative client URLs only**: the page is served at `/yourprefix/`, so
  `fetch('api/…')`, `src="api/…"` — never `/api/…`, never a host or port.
  Root-absolute is allowed only for core statics that Caddy routes for you
  (`/assets/…`, `/covers/…`).

## The shared nav (web addons)

`install.sh` copies `shared/nav/gamecore-nav.{js,css}` into your `web/` and your
page includes them (see the template's `index.html`). The bar fetches
`/gc/addons` (same origin — the only core payload proxied without a session)
and links every installed web addon by its `path` — that's what makes all
addons feel like one site.

## Shared Python (`shared/py/`)

Same contract, server side. An addon runs from its own directory with its own
venv, so it never imports across the tree: `install.sh` copies what it needs
next to `server.py` and the addon just does `import sfo`. Copies are
gitignored, `shared/py/` is the only version to edit.

Currently there: `sfo.py`, a minimal PARAM.SFO reader (title, serial, version,
category — same binary format on PS3, PS4 and PSP). Adding a module means
dropping it in `shared/py/`, adding the `cp` line to the addons that want it,
and gitignoring the copy.

## Talking to the core

- Server-side only, over loopback: `http://127.0.0.1:8765/api/…`
- Refresh the TV UI after a change: `POST http://127.0.0.1:8765/api/addons/notify`
  with `{"event": "rom_uploaded", "data": {…}}` — the core broadcasts it on its
  WebSocket to the frontend.
- The core API is NEVER reachable from the LAN: if your browser UI needs a
  core endpoint, relay it through your own server (see rom-manager's
  `/api/overlays` passthrough).
