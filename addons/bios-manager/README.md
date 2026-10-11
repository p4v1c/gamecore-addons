# BIOS Manager

Drop BIOS dumps, firmware and keys from a phone or a PC. Each file is
recognised and filed where the emulator GameCore runs for that system reads
it, and every system gets a checklist of what it still lacks.

`https://<box>:8443/bios/` once installed (`gamecore-addon install bios-manager`).

It never downloads a BIOS and never says where to find one. It only files
away what the owner already has.

## What it accepts

- Loose files, several at once.
- A folder, subfolders included (folder picker, or drag and drop).
- Zip archives, zips inside zips (3 levels). 7z and rar are refused with a
  message: Python has no 7z reader in its standard library, and adding a
  dependency for it was not worth it. Repack as zip.

Readme files, pictures, `.nfo`, OS litter (`__MACOSX`, `Thumbs.db`) and
anything not recognised are listed as ignored, with the reason.

## How a file is recognised

1. **md5** against `known_bios.json`, a table built from libretro's
   `System.dat` by `tools/build_known.py` and renamed to the file each
   GameCore emulator loads. A match is proof.
2. **Content**: Switch `prod.keys` / `title.keys` and Wii U `keys.txt` by their
   text format, whatever the file is called; a PS3 update by its `SCEUF`
   magic; a PS1 or PS2 BIOS by its size and the strings Sony put in it.
3. **Name and size**, for what has no fixed hash: per-console DS/DSi firmware
   and NAND, DS BIOS dumps the table lacks, a GameCube IPL (its region comes
   from the folder it sits in: `EUR`, `USA`, `JAP`), Game Boy boot ROMs.
4. **Switch firmware**: every `.nca` of an upload (loose, in a folder, in a
   zip, or as `<id>.nca/00`) forms one set; fewer than 30 is game content, not
   a firmware, and is ignored.

## Where it goes

| System | Emulator | Destination | Source of the path |
|---|---|---|---|
| Switch | Ryujinx | `prod.keys`, `title.keys` in Ryujinx's `system/` | core `GET /api/bios` |
| Switch firmware | Ryujinx | staged, **Ryujinx installs it** | addon |
| PS1 | DuckStation | its `bios/` folder, file keeps its name | core |
| PS2 | PCSX2 | its `bios/` folder | core |
| PS3 firmware | RPCS3 | staged, **RPCS3 installs it** | addon |
| RetroArch systems, Wii U | RetroArch, Cemu | the directory the pack declares | core |
| DS / DSi | melonDS | the exact path set in `melonDS.toml`; staged when none is set | addon |
| GameCube | Dolphin | `GC/<region>/IPL.bin`, `GC/dsp_*.bin` | addon |
| Game Boy, GBC, GBA | mGBA | `gb_bios.bin`, `gbc_bios.bin`, `sgb_bios.bin`, `gba_bios.bin` in mGBA's config folder (read when no BIOS path is set) | addon |
| 3DS, Wii, PSP, N64 | Azahar, Dolphin, PPSSPP, gopher64 | nothing needed | addon |

The core's `/api/bios` is used for every system whose catalogue pack declares
a `bios` block: the core has already resolved the Flatpak directory, and it is
what the launch gate checks. When the core does not answer, those files are
reported as not placed rather than guessed.

A file goes only to a system installed on the box (`config/systems.json`).

## What needs the emulator

A copy is not an install for these, so the addon stages the file under
`$ADDON_DATA_DIR/firmware/<system>/` (visible to the Flatpaks through
`/userdata`) and shows the step:

- **Switch firmware**: Ryujinx, *Actions, Install Firmware, Install a firmware
  from a directory*, or with Ryujinx closed
  `flatpak run io.github.ryubing.Ryujinx --install-firmware "<folder>"`.
- **PS3 firmware**: RPCS3, *File, Install Firmware*, or
  `flatpak run net.rpcs3.RPCS3 --installfw "<PUP>"` (needs RPCS3's window).
- **melonDS** without a configured path: set it in *Config, Emu settings*.

The addon does not run either emulator itself: both open a window, and doing
that on the TV during a game is worse than one manual step. Remove the staged
copy from the system's page once installed.

## Safety

- Destinations are fixed names from the table and rules, never a name taken
  from the upload (except DuckStation/PCSX2 BIOS files, reduced to a bare file
  name). Each one is re-checked to stay inside its directory; a link or a
  folder in the way is left alone.
- Zip members are extracted to numbered work files, never to their own path.
  Absolute or `..` members are refused and reported. 5000 members and 8 GB
  expanded per upload at most; 2 GB per upload.
- An existing different file is copied to
  `$ADDON_DATA_DIR/backups/<timestamp>/<system>/` before being replaced. A file
  whose md5 is a known dump is never replaced by one that is not.
- Writes are atomic (temp file, then rename).
- Saves are never touched: no destination is a save folder, and an existing
  DSi NAND (it holds the DSiWare saves) is never replaced.
- A melonDS path pointing outside the home and data folders is refused.
- Work files go to `$ADDON_DATA_DIR/tmp`, not `/tmp` (RAM on the box).

## API

| Route | What |
|---|---|
| `GET /api/status` | per installed system: files present or missing, staged items |
| `POST /api/upload` | multipart, parts named `files`; a part's filename may carry its folder path |
| `DELETE /api/staged?path=` | drop a staged firmware once installed |

## Tests

```bash
python -m pytest addons/bios-manager/tests    # fastapi, httpx, python-multipart, pytest
```

Everything runs in a temp sandbox (`GAMECORE_HOME`, two distinct roots, a fake
core report); fake BIOS bytes get their md5 added to the table for the test.

Rebuild the table after a libretro update:

```bash
python tools/build_known.py System.dat > known_bios.json
```
