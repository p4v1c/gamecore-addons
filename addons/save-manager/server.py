"""GameCore addon — Save Manager.

Game-centric view of emulator saves & save states: each game shows its icon,
its name, and (on click) every save file/folder that makes it up. Saves that
can't be tied to a game (shared memory cards, Switch hashed ids, system data)
are listed apart. Download (zip for folders), restore (backup first), delete
(backup first). Native saves are portable; save states are version-specific
and carry a restore warning.

Beyond single entries:
  * whole-game zip and full-emulator backup zip (paths relative to the
    emulator base, restorable in one drop via /upload-full),
  * per-save export/import/delete INSIDE shared PS1/PS2/GC memory cards,
  * a per-emulator "transfer from PC" guide (guide.py) + a standalone PC
    export tool served under /tools/ that packs a PC's saves for this API,
  * GameCore profiles (profiles.py): every route takes `profile=<id>` to act
    on that profile's saves, and a game's saves copy from one profile to
    another. Without it, or on a box without profiles, it is the primary's.
"""
import io
import os
import re
import shutil
import struct
import tempfile
import zipfile
import zlib
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import memcard
import profiles
from archive import NORM_TAGS, arc_items, restore_normalized, zip_entries
from backups import BAK_RE, dir_size, fmt_size
from backups import backup as _backup
from backups import listing as _backups
from catalog import CATALOG, resolve_base, scan, sony_game
from guide import GUIDE
from profiles import View

ADDON_DIR = Path(__file__).parent
PORT = int(os.environ.get("ADDON_PORT", 8772))
_PROFILE_ZIP = re.compile(r"profiles/[^/]*-([A-Za-z0-9]+)/(.+)")

app = FastAPI(title="GameCore addon — Save Manager", root_path=os.environ.get("ADDON_BASE", ""))


def _emu(emu_id: str) -> dict:
    if emu_id not in CATALOG:
        raise HTTPException(404, "unknown emulator")
    return CATALOG[emu_id]


def _person(profile: str | None) -> dict | None:
    """The profile a request names, None for the primary (or no profile)."""
    person = profiles.find(profile) if profile else {"primary": True}
    if person is None:
        raise HTTPException(404, "unknown profile")
    return None if person["primary"] else person


def _view(emu_id: str, profile: str | None = None, write: bool = False) -> View:
    """One profile's saves of an emulator. `write` refuses while a profile's
    game holds them: its swapped folders would take the write, or lose it."""
    meta = _emu(emu_id)
    base = resolve_base(emu_id)
    if not base:
        raise HTTPException(404, "no data directory for this emulator on the box")
    if write and meta.get("readonly"):
        raise HTTPException(403, f"{meta['label']} saves are read only.")
    if write and (busy := profiles.playing_message(emu_id, base)):
        raise HTTPException(409, busy)
    person = _person(profile)
    if person and emu_id not in profiles.LAYOUT:
        raise HTTPException(400, f"Every profile shares the {meta['label']} saves.")
    return View(emu_id, base, person)


def _entries(view: View, internal: bool = False) -> list[dict]:
    _base, raw = scan(view.emu_id, view)
    out = []
    for e in raw:
        try:
            size = dir_size(e["path"]) if e["is_dir"] else e["path"].stat().st_size
        except OSError:
            size = 0
        # Empty folders/files are phantoms — Wii channels or title dirs that were
        # registered but never written (e.g. an empty title/<hi>/<lo>/data). They
        # carry no save to back up, so they must not show up as "games".
        if size == 0:
            continue
        card_id = f"{e['ci']}/{e['rel']}"

        # A shared PS1/PS2 card holds every game's save in a card filesystem, so
        # listing filenames alone shows none of them. Read the card open (see
        # memcard.py) and surface each save inside as its own game — each one
        # individually exportable and deletable.
        in_card = memcard.read_saves(e["path"]) if e["mode"] == "cards" else []
        for s in in_card:
            # prefer the real game name + cover (from the ROM) over the card's
            # own short title ("NFS MW V"); fall back to the card title/serial
            rom_title, rom_icon = sony_game(s["serial"])
            title = rom_title or s["title"]
            v = {
                "id": card_id,                 # actions act on the whole card
                "name": f"{title} · in {e['rel']}",
                "kind": "save",
                "card": False,
                "in_card": e["rel"],
                "save_key": s.get("name") or s["serial"],  # unique on-card save name
                "is_dir": False,
                "size": s["size"],
                "sizeHuman": fmt_size(s["size"]),
                "game_key": s["serial"],
                "game_title": title,
            }
            if internal:
                v["_icon"] = rom_icon
            out.append(v)

        # Attribute the card itself: a card whose filename carries a serial, or
        # one whose content is a single game's saves (DuckStation's default
        # PerGameTitle cards are named after the game, not the serial), belongs
        # to that game; a multi-game card stays in "Shared & system files".
        key, title = e["key"], e["title"]
        serials = {s["serial"] for s in in_card}
        if in_card and not key:
            if len(serials) == 1:
                key = in_card[0]["serial"]
                title = sony_game(key)[0] or in_card[0]["title"]
            else:
                key, title = "", ""
        d = {
            "id": card_id,
            "name": e["rel"],
            "kind": e["kind"],
            "card": e["mode"] == "cards" and (bool(in_card) or not key),
            "is_dir": e["is_dir"],
            "size": size,
            "sizeHuman": fmt_size(size),
            "game_key": key,
            "game_title": title,
        }
        if internal:
            d["_icon"] = e["icon"]
        out.append(d)
    return out


def _collection(view: View, ci: int) -> dict:
    cols = CATALOG[view.emu_id]["collections"]
    if not 0 <= ci < len(cols):
        raise HTTPException(400, "bad collection")
    return cols[ci]


def _locate(view: View, ci: int, rel: PurePosixPath) -> tuple[Path, Path]:
    """(path on disk, its folder) of a collection-relative path, after the
    containment checks every write relies on."""
    _collection(view, ci)
    if rel.is_absolute() or ".." in rel.parts:
        raise HTTPException(403, "path outside the save directory")
    found = view.path(ci, rel)
    if found is None:
        raise HTTPException(400, "Every profile shares these saves: use the main profile's page.")
    target, cdir = found
    try:
        target.resolve().relative_to(cdir.resolve())
    except ValueError:
        raise HTTPException(403, "path outside the save directory")
    # The owner's side never reaches into a profile's folder, whatever links lie on disk.
    if view.profile is None and profiles.in_root(target) is not None:
        raise HTTPException(403, "path outside the save directory")
    return target, cdir


def _resolve_entry(view: View, entry_id: str) -> tuple[Path, Path]:
    """entry id = '<collection>/<relative path>' (games can nest several
    levels deep — Wii title trees, Switch user dirs…)."""
    m = re.fullmatch(r"(\d+)/(.+)", entry_id)
    if not m:
        raise HTTPException(400, "bad entry id")
    _collection(view, int(m.group(1)))
    rel = PurePosixPath(m.group(2))
    # `not rel.parts` is the one that matters — same guard upload() already has.
    # PurePosixPath(".").parts is the empty tuple, so a "." entry id sailed past
    # both of the other checks (nothing is absolute, no ".." to find),
    # joinpath(*()) handed back cdir itself, and relative_to(cdir) trivially
    # succeeded. DELETE ?id=0/. therefore backed up and rmtree'd the entire
    # collection. For mgba and melonDS that is worse than it sounds: catalog.py
    # gives them collections with an empty subpath, so the "collection" is the
    # ROM directory.
    if not rel.parts:
        raise HTTPException(400, "bad entry id")
    target, cdir = _locate(view, int(m.group(1)), rel)
    # Defence in depth: whatever the id looked like, an entry is something
    # *inside* a collection, never the collection itself.
    if target.resolve() == cdir.resolve():
        raise HTTPException(400, "bad entry id")
    return target, cdir


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/profiles")
def list_profiles():
    """The box's profiles ([] = no profiles: the page looks as it always did)."""
    return profiles.listing()


@app.get("/api/emulators")
def list_emulators(profile: str | None = None):
    person = _person(profile)
    result = []
    for emu_id, meta in CATALOG.items():
        base = resolve_base(emu_id)
        shared = person is not None and emu_id not in profiles.LAYOUT
        entries = _entries(View(emu_id, base, person)) if base and not shared else []
        games = {e["game_key"] for e in entries if e["game_key"]}
        result.append({
            "id": emu_id, "label": meta["label"], "available": base is not None,
            "games": len(games),
            "entries": len(entries),
            **({"shared": True} if shared else {}),
        })
    return result


@app.get("/api/games/{emu_id}")
def list_games(emu_id: str, profile: str | None = None):
    """Saves grouped by game (icon + name + its files), plus an 'other' bucket
    for saves not tied to a game (shared cards, system, cache)."""
    _emu(emu_id)
    base = resolve_base(emu_id)
    person = _person(profile)
    shared = person is not None and emu_id not in profiles.LAYOUT
    view = View(emu_id, base, person) if base and not shared else None
    entries = _entries(view, internal=True) if view else []
    games: dict[str, dict] = {}
    other: list[dict] = []
    for e in entries:
        icon = e.pop("_icon")
        if not e["game_key"]:
            other.append(e)
            continue
        g = games.setdefault(e["game_key"], {
            "key": e["game_key"],
            "title": e["game_title"] or e["game_key"],
            "entries": [], "saves": 0, "states": 0, "size": 0, "_icon": None,
        })
        g["entries"].append(e)
        g["size"] += e["size"]
        g["saves" if e["kind"] == "save" else "states"] += 1
        if icon and not g["_icon"]:
            g["_icon"] = icon
    games_list = sorted(games.values(), key=lambda g: g["title"].lower())
    for g in games_list:
        g["sizeHuman"] = fmt_size(g["size"])
        has_icon = g.pop("_icon") is not None
        # Relative: behind Caddy the page lives under the addon's path (/saves/).
        g["icon"] = (f"api/games/{emu_id}/icon?key={quote(g['key'])}"
                     + (f"&profile={person['id']}" if person else "") if has_icon else None)
    cols = _emu(emu_id)["collections"]
    return {
        "available": base is not None,
        "base": str(base) if base else None,
        "folder": str(view.folder) if view and view.folder else None,
        "shared": shared,
        "playing": profiles.playing_message(emu_id, base),
        "readonly": bool(CATALOG[emu_id].get("readonly")),
        "collections": [{"index": i, "kind": c["kind"], "mode": c["mode"],
                         "hint": _MODE_HINT.get(c["mode"], "")}
                        for i, c in enumerate(cols) if not shared and (view is None or view.sources(i))],
        "games": games_list,
        "other": other,
        "backups": _backups(view) if view else [],
        "guide": None if shared else GUIDE.get(emu_id),
    }


_MODE_HINT = {
    "files": "single file (.sav, state…)",
    "dirs": "folder save — upload it as a .zip",
    "cards": "shared memory-card file",
    "any": "save-state file or folder (.zip)",
}


def _tga_to_png(data: bytes) -> bytes | None:
    """Wii U iconTex.tga → PNG (type-2 uncompressed 24/32-bit only)."""
    if len(data) < 18 or data[2] != 2:
        return None
    w, h = int.from_bytes(data[12:14], "little"), int.from_bytes(data[14:16], "little")
    bpp, desc = data[16], data[17]
    n = bpp // 8
    if n not in (3, 4) or len(data) < 18 + data[0] + w * h * n:
        return None
    off = 18 + data[0]
    rows = []
    for y in range(h):
        src = data[off + y * w * n:off + (y + 1) * w * n]
        px = bytearray(w * 4)
        for x in range(w):
            b, g, r = src[x * n], src[x * n + 1], src[x * n + 2]
            a = src[x * n + 3] if n == 4 else 255
            px[x * 4:x * 4 + 4] = (r, g, b, a)
        rows.append(bytes(px))
    if not desc & 0x20:          # bottom-up origin
        rows.reverse()
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, body):
        c = tag + body
        return len(body).to_bytes(4, "big") + c + zlib.crc32(c).to_bytes(4, "big")

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


_tga_cache: dict = {}


@app.get("/api/games/{emu_id}/icon")
def game_icon(emu_id: str, key: str, profile: str | None = None):
    """The icon the resolver found for this game: savedata ICON0.PNG (PS3/PSP),
    Wii U iconTex.tga (converted), or a GameCore cover."""
    view = _view(emu_id, profile)
    _base, raw = scan(emu_id, view)
    icon = next((e["icon"] for e in raw if e["key"] == key and e["icon"]), None)
    if not icon and emu_id in ("pcsx2", "duckstation") and re.fullmatch(r"[A-Z]{4}-\d{5}", key):
        # a game that lives inside a shared card isn't in scan()'s entries
        # (attributed at the server layer) — resolve its cover by serial
        icon = sony_game(key)[1]
    if not icon or not icon.is_file():
        raise HTTPException(404)
    if icon.suffix.lower() == ".tga":
        stamp = (str(icon), icon.stat().st_mtime_ns)
        png = _tga_cache.get(stamp)
        if png is None:
            png = _tga_to_png(icon.read_bytes())
            if png is None:
                raise HTTPException(404)
            if len(_tga_cache) > 64:
                _tga_cache.clear()
            _tga_cache[stamp] = png
        return Response(png, media_type="image/png")
    media = {".webp": "image/webp", ".jpg": "image/jpeg"}.get(icon.suffix.lower(), "image/png")
    return FileResponse(str(icon), media_type=media)


@app.get("/api/saves/{emu_id}/download")
def download(emu_id: str, id: str, save: str | None = None, profile: str | None = None):
    target, _cdir = _resolve_entry(_view(emu_id, profile), id)
    if not target.exists():
        raise HTTPException(404, "not found")
    if save:
        # Export one game's save out of a shared card (.mcs for PS1, .psu PS2).
        try:
            fname, blob = memcard.export_save(target.read_bytes(), save)
        except KeyError:
            raise HTTPException(404, "that save is no longer on the card")
        except Exception:
            raise HTTPException(400, "could not read that save from the card")
        return Response(blob, media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'})
    if target.is_dir():
        # zip paths are relative to the collection dir, so re-uploading the
        # zip restores nested games (Wii <hi>/<lo>, Switch <user>/<tid>…)
        # at their exact place. zip_entries spools to disk past 64 MiB so a
        # huge save-state folder can't eat the box's RAM.
        buf = zip_entries([(target, PurePosixPath(id.split("/", 1)[1]).as_posix())])
        return StreamingResponse(buf, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{target.name}.zip"'})
    return FileResponse(str(target), filename=target.name)


@app.post("/api/saves/{emu_id}/upload")
async def upload(emu_id: str, collection: int, file: UploadFile = File(...),
                 card: str | None = None, profile: str | None = None):
    view = _view(emu_id, profile, write=True)
    col = _collection(view, collection)
    name = Path(file.filename or "").name
    if not name:
        raise HTTPException(400, "no filename")
    data = await file.read()

    def dest(rel: PurePosixPath) -> Path:
        target, cdir = _locate(view, collection, rel)
        cdir.mkdir(parents=True, exist_ok=True)
        return target

    if card is not None:
        # Inject one game's save (.mcs/.psu) into a specific shared card. The
        # whole card is backed up first; memcard.import_save builds a copy and
        # verifies the save reads back before we ever overwrite the original.
        # `card` is the collection-relative path (cards mode scans recursively,
        # so a card may live in a subfolder) — validated like every entry path.
        rel = PurePosixPath(card)
        if not rel.parts:
            raise HTTPException(403, "path outside the save directory")
        card_path = dest(rel)
        if not card_path.is_file():
            raise HTTPException(404, "card not found")
        try:
            new_card = memcard.import_save(card_path.read_bytes(), data, name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception:
            raise HTTPException(400, "could not add that save to the card")
        _backup(card_path)
        card_path.write_bytes(new_card)
        return {"ok": True, "restored": [f"{name} → {card}"]}

    if name.lower().endswith(".zip"):
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise HTTPException(400, "invalid zip")
        members = [m for m in zf.infolist() if not m.is_dir()]
        if not members:
            raise HTTPException(400, "empty zip")
        # Flat-file collections (mgba .sav dir, memcards…): a zip from a PC
        # usually wraps everything in one folder — strip that root so the
        # files land directly where the emulator looks for them.
        strip = 0
        if col["mode"] in ("files", "cards"):
            roots = {Path(m.filename).parts[0] for m in members}
            if len(roots) == 1 and all(len(Path(m.filename).parts) > 1 for m in members):
                strip = 1
        arcs = [(m, PurePosixPath(*PurePosixPath(m.filename).parts[strip:]))
                for m in members]
        for m, rel in arcs:
            if PurePosixPath(m.filename).is_absolute() or ".." in PurePosixPath(m.filename).parts:
                raise HTTPException(400, "zip contains an unsafe path")
        dests = [(m, dest(rel)) for m, rel in arcs]       # every path checked before any write
        for root in sorted({rel.parts[0] for _m, rel in arcs}):
            _backup(dest(PurePosixPath(root)))
        for m, target in dests:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(m))
        return {"ok": True, "restored": sorted({rel.parts[0] for _m, rel in arcs})}

    target = dest(PurePosixPath(name))
    _backup(target)
    target.write_bytes(data)
    return {"ok": True, "restored": [name]}


@app.delete("/api/saves/{emu_id}")
def delete(emu_id: str, id: str, save: str | None = None, profile: str | None = None):
    target, _cdir = _resolve_entry(_view(emu_id, profile, write=True), id)
    if not target.exists():
        raise HTTPException(404, "not found")
    if save:
        # Remove one game's save from inside a shared card. The whole card is
        # backed up first; memcard.delete_save builds a copy and verifies the
        # save is gone before we ever overwrite the original.
        try:
            new_card = memcard.delete_save(target.read_bytes(), save)
        except KeyError:
            raise HTTPException(404, "that save is no longer on the card")
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception:
            raise HTTPException(400, "could not remove that save from the card")
        _backup(target)
        target.write_bytes(new_card)
        return {"ok": True}
    _backup(target)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True}


def _game_items(view: View, key: str) -> list[tuple[Path, str]]:
    """(path, zip name) of everything one game is made of in this view."""
    cols = CATALOG[view.emu_id]["collections"]
    _base, raw = scan(view.emu_id, view)
    picks = [e for e in raw if e["key"] == key]
    items = arc_items(view.emu_id, view.base, cols, picks)
    # A game may (also) live inside a shared memory card — that attribution
    # happens at the server layer (_entries), not in scan, so scan-level picks
    # alone would miss the card (or, for card-only games, find nothing at all).
    # Bundle every card holding this game, under its base-relative path so
    # /upload-full accepts the zip. zip_entries dedups repeated arc names.
    seen = set()
    for e in _entries(view):
        if e["game_key"] != key or e["id"] in seen or not (e["card"] or e.get("in_card")):
            continue                     # plain entries are covered by `picks`
        seen.add(e["id"])
        target, _cdir = _resolve_entry(view, e["id"])
        ci, rel = e["id"].split("/", 1)
        if target.exists():
            items.append((target, PurePosixPath(cols[int(ci)]["subpath"], rel).as_posix()))
    return items


@app.get("/api/games/{emu_id}/download")
def download_game(emu_id: str, key: str, profile: str | None = None):
    """Everything one game is made of (saves + states, every collection) as a
    single zip, restorable via the 'full backup' drop zone."""
    items = _game_items(_view(emu_id, profile), key)
    if not items:
        raise HTTPException(404, "unknown game")
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", key).strip() or "game"
    return StreamingResponse(zip_entries(items), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{emu_id}-{stem}.zip"'})


@app.get("/api/saves/{emu_id}/download-all")
def download_all(emu_id: str, profile: str | None = None):
    """Full backup of an emulator: every save, state, card and system file this
    addon knows about, in one zip /upload-full can restore anywhere. The
    primary's backup also carries every other profile's saves, each under
    `profiles/<name>-<id>/` with the same names inside."""
    view = _view(emu_id, profile)
    cols = CATALOG[emu_id]["collections"]
    items = arc_items(emu_id, view.base, cols, scan(emu_id, view)[1])
    others = [p for p in profiles.listing() if not p["primary"]] if view.profile is None else []
    for person in others if emu_id in profiles.LAYOUT else ():
        theirs = View(emu_id, view.base, person)
        items += [(p, f"profiles/{profiles.label(person)}/{name}")
                  for p, name in arc_items(emu_id, view.base, cols, scan(emu_id, theirs)[1])]
    if not items:
        raise HTTPException(404, "nothing to back up")
    ts = datetime.now().strftime("%Y%m%d")
    who = f"-{profiles.label(view.profile)}" if view.profile else ""
    return StreamingResponse(zip_entries(items), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{emu_id}-saves{who}-{ts}.zip"'})


# ── backups ───────────────────────────────────────────────────────────────────
# Every destructive operation leaves a sibling <name>.bak-<YYYYMMDD-HHMMSS>
# (the 3 most recent per target are kept, see backups.py). This section makes
# them browsable and restorable from the UI.

@app.get("/api/backups/{emu_id}")
def list_backups(emu_id: str, profile: str | None = None):
    _emu(emu_id)
    if not resolve_base(emu_id):
        return []                    # not on the box: nothing set aside
    return _backups(_view(emu_id, profile))


@app.post("/api/backups/{emu_id}/restore")
def restore_backup(emu_id: str, id: str, profile: str | None = None):
    """Put a backup back in place of the original. The current version (if
    any) is backed up first — without pruning, so the backup being restored
    can never be deleted mid-operation — making a restore itself reversible."""
    target, _cdir = _resolve_entry(_view(emu_id, profile, write=True), id)
    m = BAK_RE.fullmatch(target.name)
    if not m or not target.exists():
        raise HTTPException(404, "backup not found")
    orig = target.with_name(m.group(1))
    _backup(orig, prune=False)
    _replace(target, orig)
    return {"ok": True, "restored": m.group(1)}


@app.delete("/api/backups/{emu_id}")
def delete_backup(emu_id: str, id: str, profile: str | None = None):
    target, _cdir = _resolve_entry(_view(emu_id, profile, write=True), id)
    if not BAK_RE.fullmatch(target.name) or not target.exists():
        raise HTTPException(404, "backup not found")
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    return {"ok": True}


def _replace(src: Path, dest: Path) -> None:
    """`dest` becomes a copy of `src`. Back `dest` up first."""
    if dest.exists():
        shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)


# ── copy between profiles ─────────────────────────────────────────────────────

def _card_patch(src_card: Path, dest_card: Path, save_keys: list[str]) -> bytes:
    """`dest_card` with these saves of `src_card` in it, each replacing its old copy."""
    src, card = src_card.read_bytes(), dest_card.read_bytes()
    for key in save_keys:
        try:
            fname, blob = memcard.export_save(src, key)
            try:
                card = memcard.delete_save(card, key)
            except KeyError:
                pass                        # not on that card yet
            card = memcard.import_save(card, blob, fname)
        except ValueError as e:
            raise HTTPException(400, f"Couldn't copy {key} into {dest_card.name}: {e}")
        except Exception:
            raise HTTPException(400, f"Couldn't copy {key} into {dest_card.name}.")
    return card


def _copy_plan(src: View, dest: View, key: str) -> tuple[list, list]:
    """(whole files or folders to copy, cards to patch) for one game, every
    destination checked before anything is written."""
    rows = [e for e in _entries(src) if e["game_key"] == key]
    in_card: dict[str, list[str]] = {}
    for e in rows:
        if e.get("in_card"):
            in_card.setdefault(e["id"], []).append(e["save_key"])
    copies, patches = [], []
    for e in rows:
        if e.get("in_card"):
            continue
        ci, rel = e["id"].split("/", 1)
        found = dest.path(int(ci), PurePosixPath(rel))
        # The other side shares this collection (Dolphin's states), or would
        # not list it as its own (players 2-4 of a DS game): leave it.
        if found is None or not dest.keeps(found[1], PurePosixPath(found[0].relative_to(found[1]).as_posix())):
            continue
        source, _cdir = _resolve_entry(src, e["id"])
        target, _cdir = _locate(dest, int(ci), PurePosixPath(rel))
        if e["id"] in in_card and target.is_file():
            patches.append((target, _card_patch(source, target, in_card.pop(e["id"]))))
        else:
            in_card.pop(e["id"], None)
            copies.append((source, target))
    for card_id, keys in in_card.items():
        ci, rel = card_id.split("/", 1)
        source, _cdir = _resolve_entry(src, card_id)
        target, _cdir = _locate(dest, int(ci), PurePosixPath(rel))
        if not target.is_file():
            who = (dest.profile or {}).get("name") or "The main profile"
            raise HTTPException(409, f"{who} has no memory card {rel} yet: play a "
                                     f"{CATALOG[src.emu_id]['label']} game as {who} once, then retry.")
        patches.append((target, _card_patch(source, target, keys)))
    return copies, patches


@app.post("/api/games/{emu_id}/copy")
def copy_game(emu_id: str, key: str, source: str, target: str):
    """Copy one game's saves from one profile to another (ids from
    /api/profiles, the primary's included). What it overwrites is backed up
    first and listed under the destination's backups; the source is only read."""
    if emu_id == "switch":      # folders numbered per profile index: a copy could hit another game
        raise HTTPException(400, "Ryujinx saves can't be copied between profiles.")
    src, dest = _view(emu_id, source, write=True), _view(emu_id, target, write=True)
    if (src.profile or {}).get("id") == (dest.profile or {}).get("id"):
        raise HTTPException(400, "Pick two different profiles.")
    copies, patches = _copy_plan(src, dest, key)
    if not copies and not patches:
        raise HTTPException(404, "This game has no save to copy.")
    for source_path, target_path in copies:
        _backup(target_path)
        _replace(source_path, target_path)
    for target_path, card in patches:
        _backup(target_path)
        target_path.write_bytes(card)
    return {"ok": True, "copied": sorted({t.name for _s, t in copies} | {t.name for t, _c in patches})}


# ── full restore ──────────────────────────────────────────────────────────────

def _collection_of(cols: list, rel: PurePosixPath) -> tuple[int, PurePosixPath] | None:
    """(collection, path inside it) a base-relative zip member belongs to:
    the deepest folder that holds it, then the one whose extensions match
    (mGBA's saves and states share its ROM folder)."""
    path = rel.as_posix()
    hits = [(ci, c) for ci, c in enumerate(cols)
            if c["subpath"] == "" or path.startswith(c["subpath"] + "/")]
    if not hits:
        return None
    deepest = max(len(c["subpath"]) for _ci, c in hits)
    hits = [(ci, c) for ci, c in hits if len(c["subpath"]) == deepest]
    ci, col = next(((ci, c) for ci, c in hits
                    if not c["exts"] or rel.name.lower().endswith(tuple(c["exts"]))), hits[0])
    return ci, PurePosixPath(path[len(col["subpath"]):].lstrip("/"))


def _restore_members(view: View, zf: zipfile.ZipFile, members: list) -> list[str]:
    """Write [(ZipInfo, base-relative name)] into one profile's view."""
    cols = CATALOG[view.emu_id]["collections"]
    norm, plain = [], []
    for m, name in members:
        rel = PurePosixPath(name)
        tag_emus = NORM_TAGS.get(rel.parts[0])
        if tag_emus:
            if view.emu_id not in tag_emus:
                raise HTTPException(400,
                    f"'{m.filename}' is a {CATALOG[tag_emus[0]]['label']} save — "
                    f"upload it to that system instead")
            norm.append((m, rel.parts))
            continue
        found = _collection_of(cols, rel)
        if found is None:
            subpaths = [c["subpath"] for c in cols]
            raise HTTPException(400,
                f"'{m.filename}' doesn't belong to any save folder of this emulator "
                f"(expected paths under: {', '.join(s or '<root>' for s in subpaths)})")
        ci, inner = found
        plain.append((m, ci, inner, _locate(view, ci, inner)[0]))

    restored: list[str] = []
    if plain:
        # backup unit = the entry inside its collection, not the path's first
        # component (backing up all of dev_hdd0 for one RPCS3 save would copy
        # gigabytes of game data)
        units = sorted({(ci, inner.parts[0]) for _m, ci, inner, _d in plain})
        for ci, first in units:
            _backup(_locate(view, ci, PurePosixPath(first))[0])
        for m, _ci, _inner, dest in plain:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(m))
        restored += sorted({first for _ci, first in units})
    if norm:
        restored += restore_normalized(view, zf, norm)
    return restored


@app.post("/api/saves/{emu_id}/upload-full")
async def upload_full(emu_id: str, file: UploadFile = File(...), profile: str | None = None):
    """Restore a whole-game / full-backup zip — what /download-all, the
    per-game download and the PC export tool produce. Plain members (paths
    relative to the emulator base) must land inside a known save collection;
    normalized switch-title/… x360-title/… ps4-title/… members are remapped
    onto this install's own ids. Members under `profiles/<name>-<id>/` go to
    that profile; the rest to the profile the page shows."""
    view = _view(emu_id, profile, write=True)
    # Spool the upload to disk past 64 MiB — a full RPCS3 backup can be huge
    # and must not be held in RAM on the box.
    buf = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    while chunk := await file.read(1 << 20):
        buf.write(chunk)
    buf.seek(0)
    try:
        zf = zipfile.ZipFile(buf)
    except zipfile.BadZipFile:
        raise HTTPException(400, "invalid zip")
    members = [m for m in zf.infolist() if not m.is_dir()]
    if not members:
        raise HTTPException(400, "empty zip")

    groups: dict[str | None, list] = {}
    for m in members:
        rel = PurePosixPath(m.filename)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise HTTPException(400, "zip contains an unsafe path")
        tagged = _PROFILE_ZIP.fullmatch(rel.as_posix())
        who, name = (tagged.group(1), tagged.group(2)) if tagged else (None, rel.as_posix())
        groups.setdefault(who, []).append((m, name))
    views = {None: view}
    for who in groups.keys() - {None}:
        person = profiles.find(who)
        if person is None or person["primary"]:
            raise HTTPException(400, f"This zip holds the saves of a profile this box doesn't have ({who}).")
        views[who] = _view(emu_id, who)
    restored: list[str] = []
    for who, group in groups.items():
        restored += _restore_members(views[who], zf, group)
    return {"ok": True, "restored": restored}


app.mount("/tools", StaticFiles(directory=str(ADDON_DIR / "tools")), name="tools")

class _FreshStatic(StaticFiles):
    """The addon's page, never served from the browser's cache.

    `web/index.html` is a single file that changes with every addon update,
    and it was served with no Cache-Control at all — so the browser applied
    its heuristic freshness and kept the OLD page for a while after
    `gamecore-addon update`. On the reference box that meant a ROM Manager
    that still showed one overlay slot per system the evening the core had
    grown one per console, and "No overlay set" for a file that was there:
    the new page was on disk and on the wire, the browser just never asked.
    Same rule the core applies to its theme files (`_NoCacheStatic`): small,
    local, and a stale copy costs more than a fetch.
    """

    def is_not_modified(self, response_headers, request_headers) -> bool:
        return False

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp


app.mount("/", _FreshStatic(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
