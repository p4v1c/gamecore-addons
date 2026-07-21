"""Per-emulator "transfer your saves from a PC" guide, shown in the web UI.

Facts verified against each emulator's source/docs (July 2026). Every entry:
  pc      — where the DEFAULT install keeps saves on a desktop PC
  grab    — what to copy, with exact naming patterns
  restore — how to get it into GameCore (this addon)
  notes   — pitfalls: version changes, portable modes, format conversions

The PC export tool (served at /tools/gamecore-save-export.py) automates the
whole thing: it scans these locations on the PC, packs upload-ready zips and
can push them straight to this addon.
"""

GUIDE = {
    "mgba": {
        "pc": [
            {"os": "All", "path": "next to the ROM file",
             "note": "default — mGBA writes <rom name>.sav beside the ROM"},
            {"os": "Windows", "path": "%LocalAppData%\\VirtualStore\\…",
             "note": "only if the ROMs live under Program Files"},
        ],
        "grab": ["<rom name>.sav — battery save, raw & portable",
                 "<rom name>.ss1 … .ss9 — save states (same-build only)"],
        "restore": "Name each .sav exactly like the ROM on the box, then drop the "
                   "file (or a .zip of several) on the save zone.",
        "notes": ["RetroArch stores the identical raw save as <rom>.srm — just rename it to .sav.",
                  "If a save doesn't load, the flash size differs (64K vs 128K) — fix it in the source emulator."],
    },
    "melonds": {
        "pc": [
            {"os": "All", "path": "next to the ROM file",
             "note": "default (SaveFilePath empty in melonDS.toml; the Windows zip is portable)"},
        ],
        "grab": ["<rom name>.sav — raw save, portable",
                 "<rom name>.ml1 … .ml8 — save states (same-build only)"],
        "restore": "Name each .sav exactly like the ROM on the box and drop it on the save zone.",
        "notes": ["DeSmuME .dsv files are NOT the same format — use melonDS's "
                  "'Import savefile' on the PC first, then copy the resulting .sav."],
    },
    "gopher64": {
        "pc": [
            {"os": "Windows", "path": "%APPDATA%\\gopher64\\saves", "note": ""},
            {"os": "Linux", "path": "~/.local/share/gopher64/saves", "note": ""},
            {"os": "portable", "path": "<exe dir>\\portable_data\\data\\saves",
             "note": "when a portable.txt sits next to the exe"},
        ],
        "grab": ["<InternalRomName>-<SHA256>.eep / .sra / .fla / .mpk — the hash is "
                 "computed from the ROM, so the same ROM gives the same file name"],
        "restore": "Drop the files as-is on the save zone — same ROM on the box → same name → it just works.",
        "notes": ["Coming from Project64 / mupen64plus / simple64: .eep and .mpk transfer as-is "
                  "(rename to gopher64's pattern); .sra and .fla need a 4-byte word swap — "
                  "the PC export tool does both automatically with --n64-rom."],
    },
    "duckstation": {
        "pc": [
            {"os": "Windows", "path": "Documents\\DuckStation",
             "note": "new installs since 2026-01 use %LocalAppData%\\DuckStation instead"},
            {"os": "Linux", "path": "~/.local/share/duckstation", "note": ""},
        ],
        "grab": ["memcards\\*.mcd — one card per game by default (named after the game, or "
                 "<serial>_1.mcd, or shared_card_1.mcd)",
                 "savestates\\<serial>_<slot>.sav — save states (same-build only)"],
        "restore": "Drop the .mcd files on the memory-card zone. The addon reads every card and "
                   "lists the games inside; single saves can also be injected as .mcs.",
        "notes": ["Any raw 128 KiB PS1 card works (.mcd/.mcr/.ps), and DexDrive .gme files are readable too.",
                  "ePSXe/RetroArch cards are the same raw format under another extension."],
    },
    "pcsx2": {
        "pc": [
            {"os": "Windows", "path": "Documents\\PCSX2",
             "note": "portable if a portable.txt sits next to pcsx2.exe"},
            {"os": "Linux", "path": "~/.config/PCSX2", "note": ""},
        ],
        "grab": ["memcards\\Mcd001.ps2 (and Mcd002.ps2) — each card holds many games' saves",
                 "sstates\\<serial> (<crc>).<slot>.p2s — save states (same-build only)"],
        "restore": "Drop the whole .ps2 card on the memory-card zone — every game inside is listed "
                   "individually and can be exported/deleted one by one. Single .psu saves can be "
                   "injected into the box's card.",
        "notes": ["'Folder' memory cards (a directory with _pcsx2_superblock inside) aren't supported — "
                  "convert to a file card in PCSX2 first (Settings → Memory Cards)."],
    },
    "dolphin": {
        "pc": [
            {"os": "Windows", "path": "%AppData%\\Dolphin Emulator",
             "note": "older installs: Documents\\Dolphin Emulator; portable: User\\ next to the exe"},
            {"os": "Linux", "path": "~/.local/share/dolphin-emu", "note": ""},
        ],
        "grab": ["GC\\<REGION>\\Card A\\*.gci — one file per GameCube save (default mode)",
                 "GC\\MemoryCardA.<REGION>.raw — raw card, if you use raw mode",
                 "Wii\\title\\<hi>\\<lo>\\data\\… — Wii saves (whole folder tree)",
                 "StateSaves\\<GameID>.sNN — save states (same-build only)"],
        "restore": "Drop .gci files on the memory-card zone (each is one game's save, auto-attributed). "
                   "Raw .raw cards work too. For Wii: zip your title\\… tree and drop it on the folder zone.",
        "notes": ["Keep the Wii zip paths starting at <hi>/<lo>/data so everything lands back in place."],
    },
    "rpcs3": {
        "pc": [
            {"os": "Windows", "path": "next to rpcs3.exe (RPCS3 is portable)", "note": ""},
            {"os": "Linux", "path": "~/.config/rpcs3", "note": ""},
        ],
        "grab": ["dev_hdd0\\home\\00000001\\savedata\\<SERIAL-…>\\ — one folder per save "
                 "(PARAM.SFO + ICON0.PNG + data)",
                 "dev_hdd0\\home\\00000001\\trophy\\<NPWR…>\\ — trophies",
                 "savestates\\ — save states (same-build only, often huge)"],
        "restore": "Zip the savedata folder(s) and drop the zip on the folder zone — folders restore at "
                   "their exact place. Trophy folders too.",
        "notes": ["PARAM.PFD stays valid when copied whole — don't edit files inside a save folder."],
    },
    "ppsspp": {
        "pc": [
            {"os": "Windows", "path": "Documents\\PPSSPP\\PSP",
             "note": "zip/portable builds: memstick\\PSP next to the exe"},
            {"os": "Linux", "path": "~/.config/ppsspp/PSP", "note": ""},
        ],
        "grab": ["SAVEDATA\\<GAMEID…>\\ — one folder per save (e.g. ULUS10041SAVE00)",
                 "PPSSPP_STATE\\<GAMEID>_<ver>_<slot>.ppst — save states (same-build only)"],
        "restore": "Zip the SAVEDATA folder(s) and drop the zip on the folder zone.",
        "notes": [],
    },
    "cemu": {
        "pc": [
            {"os": "Windows", "path": "next to Cemu.exe (mlc01\\…)",
             "note": "Cemu 2.1+ fresh installs: %AppData%\\Cemu instead"},
            {"os": "Linux", "path": "~/.local/share/Cemu", "note": ""},
        ],
        "grab": ["mlc01\\usr\\save\\00050000\\<titleid-lo>\\ — the whole per-game folder "
                 "(includes meta\\ and user\\<account>\\)"],
        "restore": "Zip the 00050000\\<titleid-lo> folder keeping that path structure and drop it on "
                   "the folder zone.",
        "notes": ["Cemu has no save states — the mlc save is everything."],
    },
    "azahar": {
        "pc": [
            {"os": "Windows", "path": "%AppData%\\Azahar",
             "note": "migrated from %AppData%\\Citra / Lime3DS on first run"},
            {"os": "Linux", "path": "~/.local/share/azahar-emu",
             "note": "Citra legacy: ~/.local/share/citra-emu"},
        ],
        "grab": ["sdmc\\Nintendo 3DS\\<id0>\\<id1>\\title\\00040000\\<titleid-lo>\\data\\00000001\\ — "
                 "the game's save", "states\\<titleid>.<slot>.cst — save states (same-build only)"],
        "restore": "Zip the tree starting at <id0>\\<id1>\\title\\… and drop it on the folder zone. "
                   "On Azahar id0/id1 are 32 zeros; the addon matches games by title id either way.",
        "notes": ["Some games keep progress in extdata (sdmc\\…\\extdata\\…) — bring it too; it shows "
                  "under Shared & system files."],
    },
    "citron-neo": {
        "pc": [
            {"os": "Windows", "path": "%AppData%\\citron\\nand\\user\\save\\0000000000000000\\<user>\\<titleid>",
             "note": "citron-neo still uses the citron data dir; yuzu/sudachi are the same layout"},
            {"os": "Linux", "path": "~/.local/share/citron/nand/user/save/0000000000000000/<user>/<titleid>",
             "note": ""},
            {"os": "Ryujinx", "path": "%AppData%\\Ryujinx\\bis\\user\\save",
             "note": "install-numbered dirs — use the PC export tool, it resolves them to title ids"},
        ],
        "grab": ["yuzu-family (citron/yuzu/sudachi): the <titleid> folder itself",
                 "Ryujinx: right-click the game → 'Open User Save Directory' → copy the contents of 0\\ "
                 "(the folder number is install-specific — don't rely on it)"],
        "restore": "Use the PC export tool — it packs an id-independent zip "
                   "(switch-title/<titleid>/…) that the full-restore zone maps onto this box. "
                   "Manual zips must use those switch-title/… paths.",
        "notes": ["Downloads from this addon are already in the id-independent format."],
    },
    "xenia": {
        "pc": [
            {"os": "Windows", "path": "content\\ next to xenia_canary.exe",
             "note": "Xenia Canary is portable by default on Windows"},
        ],
        "grab": ["content\\<XUID>\\<TitleID>\\ — the whole per-game folder (00000001\\ = the saves, "
                 "Headers\\ = their metadata — keep both)",
                 "content\\0000000000000000\\<TitleID>\\00000002\\ — DLC, if wanted"],
        "restore": "Use the PC export tool (it packs id-independent x360-title/<TitleID>/… zips), or "
                   "zip manually with those paths. The full-restore zone maps them onto the box's "
                   "own profile (XUID).",
        "notes": ["A profile must exist on the box (launch Xenia once).",
                  "Very old Xenia builds have no <XUID> level — the PC tool handles both layouts."],
    },
    "shadps4": {
        "pc": [
            {"os": "Windows", "path": "%AppData%\\shadPS4",
             "note": "portable: user\\ folder next to the exe"},
            {"os": "Linux", "path": "~/.local/share/shadPS4", "note": ""},
        ],
        "grab": ["home\\1\\savedata\\<CUSA…>\\ — shadPS4 ≥ 0.16 (June 2026)",
                 "savedata\\1\\<CUSA…>\\ — older shadPS4",
                 "copy the whole <CUSA…> folder including sce_sys\\param.sfo"],
        "restore": "Use the PC export tool, or zip with ps4-title/<CUSA…>/<savedir>/… paths and drop "
                   "it on the full-restore zone — the addon targets whichever layout the box runs.",
        "notes": ["No account dependency: only the user number (1) matters, and the addon handles it."],
    },
}
