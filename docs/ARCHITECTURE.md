# gamecore-addons — how it actually works

Reference for anyone (or anything) modifying this repo. `README.md` says what
the addons do; `CREATING_AN_ADDON.md` is the tutorial for writing one. This
document explains the machinery, names the files and functions, and lists the
traps.

Companion repo: [p4v1c/GamecoreRenew](https://github.com/p4v1c/GamecoreRenew)
(the "core"). Read its `docs/ARCHITECTURE.md` first if you have not.

---

## 1. The model

An addon is **an independent HTTP service with a static web UI**. Not a
plugin: the core never imports addon code, and an addon never imports core
code. They meet over loopback HTTP and one JSON registry file.

```
core :8765 ──────────────┐
   config/addons.json    │  registry, written by the `gamecore-addon` CLI
   GET /api/addons ──────┤  read by the TV settings screen
   GET /gc/addons  ──────┘  read by the addons' shared nav bar (no auth)
        ▲
        │ POST /api/addons/notify   ← addon tells the TV something changed
        │
addon :877x  ── systemd --user unit ── own venv ── own directory
```

Consequences worth internalising:

- **An addon crash cannot take the TV down.** Different process, different
  unit, `Restart=on-failure`.
- **Addons are buildless.** Plain static `web/`, no bundler. The checkout is
  exactly what runs — you can edit an addon's HTML on the box and reload.
- **Addons contain no authentication code.** Caddy enforces it upstream (§6).
- Each addon owns a port in the **8770-8799** range, declared in `addon.json`
  and mirrored in its `install.sh`.

| Addon | Port | Path | Default |
|---|---|---|---|
| `rom-manager` | 8770 | `/roms` | installed by default |
| `rpcs3-manager` | 8771 | `/rpcs3` | opt-in |
| `save-manager` | 8772 | `/saves` | opt-in |
| `_template` | 8799 | — | scaffold, never installed |

---

## 2. Repository layout

```
addons/
  _template/       minimal working addon — copy this to start
  rom-manager/     upload ROMs and overlays
  rpcs3-manager/   per-game RPCS3 config & patches
  save-manager/    browse / back up / restore saves, incl. inside memory cards
shared/
  nav/             gamecore-nav.js + .css — the cross-addon nav bar
  py/              sfo.py — Python shared by more than one addon
docs/
  CREATING_AN_ADDON.md   the contract, tutorial form
  SECURITY.md            threat model and rules
  ARCHITECTURE.md        this file
```

### The per-addon contract

Every addon directory contains exactly:

| File | Role |
|---|---|
| `addon.json` | manifest — name, label, description, version, port, `path`, `type`, `default`, `service`, `offline_assets` |
| `install.sh` | idempotent: venv, deps, shared copies, systemd unit, enable+start |
| `uninstall.sh` | stop, disable, remove unit |
| `requirements.txt` | pinned Python deps |
| `server.py` | the FastAPI app |
| `web/index.html` | the whole UI, no build step |

`install.sh` receives from the CLI: `ADDON_DIR`, `GAMECORE_PATH`,
`PAYLOAD_DIR` and `OFFLINE` (1 = install wheels from `PAYLOAD_DIR/wheels`
instead of PyPI, for boxes with no internet).

### The shared directory

Addons run from their own directory with their own venv and **never import
across the tree**. So sharing is done by copy at install time, not by import
path:

```bash
echo "[${ADDON_NAME}] Shared components"
cp "${ADDON_DIR}/../../shared/py/sfo.py"           "${ADDON_DIR}/"
cp "${ADDON_DIR}/../../shared/nav/gamecore-nav.js" "${ADDON_DIR}/web/"
cp "${ADDON_DIR}/../../shared/nav/gamecore-nav.css" "${ADDON_DIR}/web/"
```

The copies are gitignored (`addons/*/sfo.py`, `addons/*/web/gamecore-nav.*`).
**`shared/` is the only version to edit.** An addon run straight from a
checkout without `install.sh` will not find them.

`shared/nav/gamecore-nav.js` fetches `/gc/addons` (same origin, the one core
payload Caddy proxies without auth) and renders a link per installed web
addon from its `path` — that is what makes three separate services feel like
one site.

---

## 3. Lifecycle — the `gamecore-addon` CLI

The CLI lives in the **core** repo (`install/gamecore-addon`), not here.

```
gamecore-addon install <name>   clone/refresh, run install.sh, add to registry
gamecore-addon remove  <name>   run uninstall.sh, drop from registry
gamecore-addon update  [name]   git pull + re-run install.sh (idempotent)
gamecore-addon list             registry contents
gamecore-addon auth-reset       regenerate the core's shared password
```

The registry is `$GAMECORE_PATH/config/addons.json`, written by the CLI from
each `addon.json`. `config/` is excluded from the core's OTA rsync, so the
registry survives updates. The core's `routers/addons.py` shells out to this
CLI for install/update/remove — it never manipulates addon files itself.

Because `install.sh` is idempotent and `update` re-runs it, **shared files and
units are refreshed on every update**.

---

## 4. rom-manager (:8770)

Upload ROMs from any browser on the LAN. Also the overlay uploader.

| Route | Purpose |
|---|---|
| `GET /api/health` | liveness |
| `GET /api/emulators` | systems from the core's `config/systems.json` |
| `GET /api/roms/{system_id}` | list ROMs |
| `POST /api/roms/{system_id}/upload` | file upload |
| `POST /api/roms/{system_id}/upload-entry` | one entry of a folder-based game (PS3/PS4) |
| `DELETE /api/roms/{system_id}/{filename}` | delete |
| `POST/DELETE /api/overlays/{system_id}` | bezel PNG, forwarded to the core |

It reads the core's system list to know where each system's ROMs live, and
mirrors the core's `rom_scanner` logic. `CORE_NOTIFY`
(`http://127.0.0.1:8765/api/addons/notify`) is how it tells the TV to refresh
after an upload.

Folder-based games (`scanDirs`) are why `upload-entry` exists: a PS3 game is a
directory tree, uploaded entry by entry with its relative path preserved.

---

## 5. rpcs3-manager (:8771)

Configure PS3 games remotely, in RPCS3's own vocabulary.

| Route | Purpose |
|---|---|
| `GET /api/games` | scan `emu/rpcs3/`, read titles/serials from `PARAM.SFO` |
| `GET /api/games/{serial}/icon` | `PS3_GAME/ICON0.PNG` |
| `GET /api/schema` | the config schema the UI renders |
| `GET/PUT/DELETE /api/games/{serial}/config` | per-game YAML in RPCS3's custom-config dir |
| `GET /api/games/{serial}/patches` | patch list for the serial |
| `POST /api/games/{serial}/patches/toggle` | enable/disable a patch |
| `POST /api/patches/download-official` | fetch the upstream patch DB |
| `POST /api/patches/upload` | user-supplied patch YAML |
| `GET /api/pkg/status`, `POST /api/pkg/install` | install `.pkg` updates/DLC |

Three modules carry the weight:

- **`ryaml.py`** — string-preserving YAML. RPCS3 configs are full of scalars
  that YAML 1.1 would silently mangle (`On`/`Off` → booleans, version-like
  strings → floats). Round-tripping through PyYAML corrupts configs, so this
  reads and writes with the original spelling intact. **Do not replace it with
  PyYAML.**
- **`schema.py`** — curated per-game schema mirroring the RPCS3 UI tabs. The
  enum strings are the *exact* serializations RPCS3 expects; a typo produces a
  config the emulator silently ignores.
- **`sfo.py`** (from `shared/py/`) — PARAM.SFO reader for titles and serials.

---

## 6. save-manager (:8772)

The largest addon (~2 500 lines). Browse, back up, restore and delete saves
for every emulator on the box — including the individual game saves *inside* a
shared PlayStation memory card — plus a PC transfer tool.

### The catalog

`catalog.py` is the map of where every emulator hides its saves. Each entry:

```python
"gopher64": {"label": "Nintendo 64", "bases": [
    HOME / ".var/app/io.github.gopher64.gopher64/data/gopher64",
    HOME / ".local/share/gopher64"], "collections": [
    C("saves",  "files", "save",  [".eep", ".mpk", ".sra", ".fla", ".srm"], "n64"),
    C("states", "files", "state", (), "n64"),
]},
```

- **`bases`** is a *list* because the same emulator may be Flatpak or native —
  `resolve_base()` picks whichever exists on this box.
- **`collections`** describe each save kind: subpath, layout, semantic type,
  extensions, and the id scheme used to match a save to a game.

Adding an emulator means adding an entry here, nothing else.

### Memory cards

`memcard.py` (842 l.) reads *and writes* the per-game saves inside a shared
PS1/PS2 card file. PS1 and PS2 pack every game's save into one card in a
card-specific directory format; extracting one game means parsing that
format, and re-importing means rebuilding the FAT and directory entries.
This is the most delicate code in the repo — it edits a binary structure that
an emulator will happily corrupt a save library over. Its tests
(`tests/test_memcard.py`) are not optional.

### Ryujinx save identity

`ryujinx.py` — Ryujinx names save directories by an install-specific counter
(`0000000000000001`), not by title id, so the same game has a different
directory on every box. `title_map(base)` builds the
`(title_id, save_type) → directory` mapping needed to restore a save onto
*this* install.

### Normalized archives

Downloads produce zips whose members are either **plain** (paths relative to
the emulator base) or **normalized** (`switch-title/…`, `x360-title/…`,
`ps4-title/…`). Normalized members are the portable form: they carry the title
id rather than a local path, so `_restore_normalized()` can remap them onto
whatever this install's layout is. That is what lets you move a save between
two different boxes.

### Restore safety

`upload-full` is the entry point that accepts a whole-game archive. Its
validation is the security-critical path:

1. Reject any member whose path is absolute or contains `..`
   (**before** anything else touches it).
2. A plain member must fall under a known collection subpath for that
   emulator, or it is rejected with a message naming the valid roots.
3. On write, `dest.resolve().relative_to(base.resolve())` re-checks
   containment.
4. `_backup()` snapshots the affected unit before overwriting — the backup
   unit is the entry inside its collection, not the path's first component
   (backing up all of `dev_hdd0` for one RPCS3 save would copy gigabytes).

Uploads spool to a `SpooledTemporaryFile` past 64 MiB: a full RPCS3 backup
must never be held in the box's RAM.

`guide.py` holds the per-emulator "transfer your saves from a PC" instructions
shown in the UI, verified against each emulator's own source/docs.
`tools/gamecore-save-export.py` is the standalone PC-side counterpart.

---

## 7. Security

Full model in `docs/SECURITY.md`. What matters when writing an addon:

- **Bind `127.0.0.1`. Always.** Every `uvicorn.run` in this repo does.
  An addon on `0.0.0.0` is an unauthenticated file manager on the LAN.
- **Write no auth code.** Caddy gates every proxied path via the core's
  `forward_auth`. The addon receives `X-GC-User` and can trust it.
- **`root_path` comes from `ADDON_BASE`** so the app works under `/roms`,
  `/saves`, `/rpcs3`. Never hardcode a port or an absolute URL in the web UI —
  use relative fetches, or `/gc/addons` to discover siblings.
- **Validate every path from a request**: absolute and `..` rejected first,
  then `resolve().relative_to(root)` before any write. The pattern is applied
  consistently in `save-manager/server.py` — copy it rather than reinventing.
- **Never `shell=True`, never `eval`.** Nothing in this repo does.

---

## 8. Traps

- **`shared/` files are copies on disk.** Editing `addons/save-manager/sfo.py`
  changes nothing upstream and gets overwritten on the next update. Edit
  `shared/py/sfo.py`.
- **Ports live in two places** — `addon.json` and the `PORT=` line in
  `install.sh`. They must agree; nothing checks it for you.
- **RPCS3 YAML is not YAML.** Use `ryaml.py`.
- **Ryujinx save directories are not title ids.** Use `ryujinx.title_map()`.
- **Memory-card writes are irreversible for the user.** Run the tests.
- **A zip member is attacker-controlled input**, even from a friendly LAN.
  Validate before touching the filesystem.
- **The addon is what is on disk** — no build step. A syntax error in
  `web/index.html` ships instantly.

---

## 9. Development

```bash
# run an addon directly (no systemd)
cd addons/rom-manager
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp ../../shared/py/sfo.py .                     # what install.sh would do
cp ../../shared/nav/gamecore-nav.* web/
GAMECORE_PATH=/opt/GameCore ADDON_PORT=8770 .venv/bin/python server.py
```

Tests (save-manager): `pytest addons/save-manager/tests`.

Logs on a box: `journalctl --user -u gamecore-addon-<name> -f`.
