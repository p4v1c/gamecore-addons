# GameCore Addons

Optional, individually installable modules for [GameCore](https://github.com/p4v1c/GamecoreRenew).

An addon is a versioned directory under `addons/`. This repo checkout **is** the
runtime: `gamecore-addon install <name>` clones/pulls it to `/opt/gamecore-addons`
and services run straight from there — `git log` always tells you exactly what
is running.

## Install

```bash
gamecore-addon install rom-manager     # one command, nothing else
```

The `gamecore-addon` CLI ships with the GameCore core (`/usr/local/bin`); the
graphical installer also offers the addons as checkboxes at install time, and
the core backend exposes install/remove endpoints (`/api/addons/...`) for
future UIs.

```bash
gamecore-addon list            # available vs installed
gamecore-addon remove <name>
gamecore-addon update          # git pull + re-run install.sh for installed addons
```

## Addon types

| type      | what it is                              | in the shared nav |
|-----------|-----------------------------------------|-------------------|
| `web`     | web UI on its own port + systemd service | yes               |
| `service` | headless daemon (watcher, bridge…)       | no                |
| `tool`    | one-shot script / system tweak, no service | no              |

Every installed addon (any type) appears in GameCore's Addons screen and in the
registry (`$GAMECORE_DATA/config/addons.json`). Only `web` addons get a link.

## Code and data are separate — write to `GAMECORE_DATA`, never to `GAMECORE_PATH`

GameCore has two roots, and an addon must know which is which:

| var | what it holds | may an addon write? |
|-----|---------------|---------------------|
| `GAMECORE_PATH` | the installation — backend, venv, bundled binaries, anything under `lib/` | **no** |
| `GAMECORE_DATA` | what the player owns — `config/systems.json`, ROMs under `emu/`, covers, saves | yes |
| `ADDON_DATA_DIR` | your addon's own corner, `$GAMECORE_DATA/addons/<name>` — caches, indexes, state | yes |

**The reason is not tidiness: `GAMECORE_PATH` is becoming a read-only mount.**
An addon that writes there installs fine today and fails on the release that
flips the mount — on a box with nobody in front of it. Resolving a *data* path
under `GAMECORE_PATH` is the quieter version of the same bug: once the data
moves, reads land in a directory nothing writes to, and the addon reports
"nothing found" instead of failing.

The question to ask for each path, one at a time:

> **Can the player modify this file, or does it arrive with the release?**

Data (`config/systems.json`, ROMs, saves, uploads) → `GAMECORE_DATA`.
Code (libraries, bundled binaries, shipped templates) → `GAMECORE_PATH`.
The core's `backend/services/paths.py` has already drawn this line — consult it
rather than reinventing it. Genuine exceptions exist (see the `xenia` entry in
`save-manager/catalog.py`, a portable emulator that keeps saves beside its own
exe); document them where they are, and do not generalise from them.

Read both roots the same way in every addon, so the fallback stays uniform:

```python
GAMECORE_PATH = Path(os.environ.get("GAMECORE_PATH", "/opt/GameCore"))
# Temporary: keeps a box that has not taken the P3 OTA resolving as before.
GAMECORE_DATA = Path(os.environ.get("GAMECORE_DATA") or GAMECORE_PATH)
```

`GAMECORE_DATA` currently *defaults* to `GAMECORE_PATH`, so on today's box both
spellings work and a wrong root is invisible. **Test with the two pointing at
different directories** — that is the only configuration in which a test of the
split proves anything. Each addon has a `tests/test_paths.py` doing exactly
that; copy the pattern.

An addon must declare `"api": 1` in its `addon.json` to say it obeys the above.
`gamecore-addon` refuses to install or update one that doesn't, by name —
deliberately, rather than letting it write into a directory that will not
accept it. Add the field **after** the paths are right: it is a statement of
conformance, not a switch.

Web addons include the shared nav bar (`shared/nav/`) so all UIs feel like one
site with sections — users never see ports.

Anything under `shared/` is copied into the addon by its `install.sh`, never
imported across the tree (each addon runs from its own directory with its own
venv): `shared/nav/` for the nav bar, `shared/py/` for Python modules such as
the PARAM.SFO reader. The copies are gitignored — `shared/` is the only
version to edit.

## Available addons

| addon | type | port | description |
|-------|------|------|-------------|
| [rom-manager](addons/rom-manager) | web | 8770 | Upload ROMs from the browser (drag & drop per system) |
| [rpcs3-manager](addons/rpcs3-manager) | web | 8771 | Configure PS3 games remotely — per-game config & patches, RPCS3-style |
| [save-manager](addons/save-manager) | web | 8772 | Back up / restore / delete emulator saves & save states (all systems), incl. inside PS1/PS2 memory cards |

Ports 8770–8799 are reserved for addons; each addon declares its own in `addon.json`.

## Creating an addon

Copy `addons/_template/` and read [docs/CREATING_AN_ADDON.md](docs/CREATING_AN_ADDON.md).
For how the whole thing fits together — the model, the registry, the install
contract, what each addon does internally and the traps — see
[docs/architecture/](docs/architecture/).
