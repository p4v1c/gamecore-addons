# Save Manager

Web addon (port **8772**) to browse, download, restore and delete the saves of
every emulator on the box — grouped **by game**, with box art. It also solves
the "I played on my PC, how do I get my saves onto the box?" problem: a
per-emulator transfer guide in the UI and a standalone PC export tool.

## What it does

- **Per game**: every save file/folder/state a game is made of, one click to
  download it all as a zip, restore it, or delete it (a timestamped backup is
  always taken first; the 3 most recent backups are kept per file).
- **Backups section**: everything a destructive action set aside is listed in
  the UI with an ↩ Restore button — restoring first backs up the current
  version (without pruning), so every operation stays reversible.
- **Inside PS1 / PS2 / GameCube memory cards** (`memcard.py`): the games on a
  card are listed individually — export one save as `.mcs` / `.psu` / `.gci`,
  inject one into a card, or delete one from a card. Cards are never modified
  in place: a patched copy is re-parsed and verified before the original is
  overwritten (ECC PS2 cards are read-only). DuckStation's default
  per-game-title cards are attributed to their game automatically.
- **Switch save identity**: citron-neo (yuzu-family) names each save dir after
  its title id — the addon maps it straight to the game (the PC export tool
  still resolves Ryujinx's install-numbered dirs when importing from a PC).
- **Full backup / full restore**: one zip per emulator (or per game) that can
  be restored on *any* install — see "normalized formats" below.
- **Transfer from PC**: each system shows where its saves live on a Windows /
  Linux / macOS PC, what to copy, and serves
  [`tools/gamecore-save-export.py`](tools/gamecore-save-export.py) which does
  it automatically.

## Where each emulator keeps its saves

On the box (as configured by the GameCore installer — first existing path wins):

| System | Emulator | Native saves | Save states |
|---|---|---|---|
| GBA | mGBA | `emu/mgba/<rom>.sav` (next to the ROM) | `<rom>.ss0`–`.ss9` |
| DS | melonDS | `emu/melonds/<rom>.sav` | `<rom>.ml1`–`.ml8`, `.mln` |
| N64 | gopher64 | `…/gopher64/saves/<name>-<sha256>.{eep,sra,fla,mpk}` | `…/gopher64/states/` |
| PS1 | DuckStation | `~/.local/share/duckstation/memcards/*.mcd` (1 card per game by default) | `savestates/<serial>_<slot>.sav` |
| PS2 | PCSX2 | `~/.config/PCSX2/memcards/Mcd001.ps2` (shared card) | `sstates/*.p2s` |
| GC/Wii | Dolphin | `…/dolphin-emu/GC/**.gci` + `.raw` cards; `Wii/title/<hi>/<lo>/data/` | `StateSaves/<gameid>.sNN` |
| PS3 | RPCS3 | `~/.config/rpcs3/dev_hdd0/home/00000001/savedata/<SERIAL-…>/` + `trophy/` | `savestates/` |
| PSP | PPSSPP | `…/ppsspp/PSP/SAVEDATA/<GAMEID…>/` | `PSP/PPSSPP_STATE/` |
| Wii U | Cemu | `…/Cemu/mlc01/usr/save/00050000/<tid-lo>/` | — (none in Cemu) |
| 3DS | Azahar | `…/azahar-emu/sdmc/Nintendo 3DS/<id0>/<id1>/title/00040000/<tid-lo>/data/00000001/` (+ `extdata`) | `states/<tid>.<slot>.cst` |
| Switch | citron-neo | `~/.local/share/citron/nand/user/save/0000000000000000/<user>/<titleid>/` | — |
| X360 | Xenia Canary | `lib/xenia/content/<XUID>/<TitleID>/00000001/` (+ `Headers/`) | — |
| PS4 | shadPS4 | `…/shadPS4/home/1/savedata/<CUSA…>/` (≤0.15: `savedata/1/<CUSA…>/`) | — |

The in-UI guide ("Transfer saves from a PC") documents the same locations for
desktop installs (Windows `Documents\…`/`%AppData%\…`, portable modes, Linux),
including version changes (DuckStation 2026-01, Cemu 2.1, shadPS4 0.16…).

## Normalized zip formats

Native saves are portable, but three systems hide them behind
**install-specific ids** — copying the raw folders to another machine breaks:

| Prefix in the zip | System | Why raw paths don't transfer |
|---|---|---|
| `switch-title/<titleid>/<type>/…` | Switch | yuzu-family uses per-install user dirs; Ryujinx numbers save dirs per install (`bis/user/save/…05`) |
| `x360-title/<TitleID>/…` | Xbox 360 | Xenia saves live under the profile's XUID |
| `ps4-title/<CUSA…>/<savedir>/…` | PS4 | shadPS4 moved its savedata dir in v0.16 |

Downloads from this addon ("⬇ all", "Full backup") already use these prefixes;
`POST /api/saves/<emu>/upload-full` maps them back onto the local install.
Everything else uses plain base-relative paths (`memcards/…`, `dev_hdd0/…`)
that restore as-is.

## The PC export tool

Served at `http://<box>:8772/tools/gamecore-save-export.py` (also in
[`tools/`](tools/)). Runs on the player's PC, Python 3.8+, stdlib only:

```
python gamecore-save-export.py                        # scan: what's on this PC?
python gamecore-save-export.py --pack -o out/         # one upload-ready zip per emulator
python gamecore-save-export.py --push http://BOX:8772 # pack + upload in one go
python gamecore-save-export.py --path mgba="D:\roms\gba" --push http://BOX:8772
python gamecore-save-export.py --n64-rom Zelda.z64 --n64-save old.sra
```

It knows the default save locations of all 13 systems (including portable
modes), reads Ryujinx's save index on the PC to emit `switch-title/…` paths,
and converts foreign N64 saves (Project64/mupen64plus word order + gopher64's
`<name>-<sha256>` naming) with `--n64-rom`.

## API

| Endpoint | |
|---|---|
| `GET /api/emulators` | systems + availability + game counts |
| `GET /api/games/{emu}` | games (entries, icons), shared files, collections, PC guide |
| `GET /api/games/{emu}/icon?key=` | game icon (savedata ICON0, Wii U TGA, cover) |
| `GET /api/games/{emu}/download?key=` | one game, one zip |
| `GET /api/saves/{emu}/download?id=` (`&save=`) | one entry / one in-card save |
| `GET /api/saves/{emu}/download-all` | full backup zip |
| `POST /api/saves/{emu}/upload?collection=` (`&card=`) | restore one file/zip / inject into a card |
| `POST /api/saves/{emu}/upload-full` | restore a full-backup / normalized zip |
| `DELETE /api/saves/{emu}?id=` (`&save=`) | delete an entry / one in-card save |
| `GET /api/backups/{emu}` | list the automatic backups |
| `POST /api/backups/{emu}/restore?id=` | put a backup back in place |
| `DELETE /api/backups/{emu}?id=` | delete a backup |

## Tests

No fixtures needed — synthetic memory cards and a synthetic save tree are
built in a temp dir:

```
python tests/test_memcard.py     # PS1/PS2/GC card engine (stdlib only)
python tests/test_api.py         # full API round-trips (needs fastapi + httpx)
```
