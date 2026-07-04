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

The `gamecore-addon` CLI ships with the GameCore core (`/usr/local/bin`), and the
GameCore Settings → Addons screen drives it for one-click install/remove.

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

## Available addons

| addon | type | port | description |
|-------|------|------|-------------|
| [rom-manager](addons/rom-manager) | web | 8770 | Upload ROMs from the browser (drag & drop per system) |
| *(coming)* rpcs3-manager | web | 8771 | Configure RPCS3 games remotely (per-game config, patches) |

Ports 8770–8799 are reserved for addons; each addon declares its own in `addon.json`.

## Creating an addon

Copy `addons/_template/` and read [docs/CREATING_AN_ADDON.md](docs/CREATING_AN_ADDON.md).
