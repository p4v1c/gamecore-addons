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
registry (`$GAMECORE_PATH/config/addons.json`). Only `web` addons get a link.

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
For how the whole thing fits together — the registry, the install contract,
what each addon does internally and the traps — see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
