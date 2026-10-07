"""Backup zips: what goes in them under which name, and how the
id-dependent members come back out.

Most members are named by their path relative to the emulator base. Switch,
Xbox 360 and PS4 game saves get a NORMALIZED prefix instead, because their
folders carry install-specific ids; see "The normalized archive format" in
docs/architecture/05-save-manager.md.
"""
import re
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

import ryujinx as ryu
from backups import backup as _backup

# Normalized zip prefix → the systems that restore it. Ryujinx and Eden share
# one format so a save moves between them.
NORM_TAGS = {"switch-title": ("ryujinx", "switch"), "x360-title": ("xenia",),
             "ps4-title": ("shadps4",)}
# yuzu layout: device saves sit under the all-zero account. A Ryujinx Bcat
# container (type 2) holding game data goes there too: Eden has no Bcat saves,
# and ACNH's island reached one through an older import.
_DEVICE_USER, _DEVICE_TYPE, _DEVICE_TYPES = "0" * 32, "3", ("2", "3")


def _clear_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for c in d.iterdir():
        shutil.rmtree(c) if c.is_dir() else c.unlink()


def _yuzu_user_for(user_root: Path, tid: str) -> str:
    """The yuzu-family account dir to restore a title into. Saves are keyed by
    account and a box can have several, so target the profile that already holds
    this title, else the one with the most saves — not just the first sorted
    (which is often the empty all-zero account)."""
    if not user_root.is_dir():
        return "0" * 32
    # The all-zero account holds device saves, never an account's.
    users = [p for p in user_root.iterdir() if p.is_dir() and p.name != _DEVICE_USER]
    if not users:
        return "0" * 32
    for u in users:
        if (u / tid).is_dir():
            return u.name
    return max(users, key=lambda u: sum(
        1 for c in u.iterdir() if c.is_dir() and ".bak-" not in c.name)).name


def restore_normalized(view, zf: zipfile.ZipFile, norm: list) -> list[str]:
    """Write switch-title/… x360-title/… ps4-title/… members onto this
    install's own layout (see arc_items), in one profile's folders
    (profiles.View). `norm` = [(ZipInfo, rel parts)]."""
    emu_id, base, restored = view.emu_id, view.base, []
    if emu_id in NORM_TAGS["switch-title"]:
        # group by (title id, save type); target the local save container
        groups: dict = {}
        for m, parts in norm:
            if len(parts) < 4 or not re.fullmatch(r"[0-9A-Fa-f]{16}", parts[1]):
                raise HTTPException(400, f"malformed switch save path '{m.filename}'")
            groups.setdefault((parts[1].upper(), parts[2]), []).append((m, parts[3:]))
        ryujinx_saves = view.at("bis/user/save")
        ryujinx_layout = ryujinx_saves is not None and ryujinx_saves.is_dir()
        if ryujinx_layout and view.profile:
            # title_map reads the owner's index; a profile has its own.
            raise HTTPException(400, "Ryujinx saves can't be restored into a profile. "
                                     "Restore them to the main profile instead.")
        tmap = ryu.title_map(base) if ryujinx_layout else {}
        for (tid, typ), files in sorted(groups.items()):
            if ryujinx_layout:
                try:
                    want = int(typ)
                except ValueError:
                    want = 1
                d = (tmap.get((tid, want)) or tmap.get((tid, 1))
                     or next((v for (t, _y), v in sorted(tmap.items()) if t == tid), None))
                if d is None:
                    raise HTTPException(400,
                        f"no save container for title {tid} on this box — launch the "
                        "game once (or open its save directory in Ryujinx), then retry")
                _backup(d)
                for c in ("0", "1"):     # 0 = committed, 1 = working: write both
                    _clear_dir(d / c)
                for m, rest in files:
                    for c in ("0", "1"):
                        dest = d / c / Path(*rest)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(m))
                restored.append(f"{tid} → {d.name}")
            else:                        # yuzu-family layout: dir name IS the title id
                user_root = view.at("nand/user/save/0000000000000000")
                user = _DEVICE_USER if typ in _DEVICE_TYPES else _yuzu_user_for(user_root, tid)
                d = user_root / user / tid
                _backup(d)
                _clear_dir(d)
                for m, rest in files:
                    dest = d / Path(*rest)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(m))
                restored.append(tid)
        return restored

    if emu_id == "xenia":
        content = base / "content"
        profiles = [p.name for p in sorted(content.iterdir())
                    if p.is_dir() and re.fullmatch(r"[0-9A-F]{16}", p.name)
                    and p.name != "0" * 16] if content.is_dir() else []
        profiles.sort(key=lambda x: not (content / x / "FFFE07D1").is_dir())
        if not profiles:
            raise HTTPException(400, "no Xenia profile on this box — launch Xenia "
                                     "once to create one, then retry")
        done = set()
        for m, parts in norm:
            if len(parts) < 3 or not re.fullmatch(r"[0-9A-Fa-f]{8}", parts[1]):
                raise HTTPException(400, f"malformed X360 save path '{m.filename}'")
            tid = parts[1].upper()
            root = content / profiles[0] / tid
            if tid not in done:
                done.add(tid)
                _backup(root)
            dest = root / Path(*parts[2:])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(m))
        restored += sorted(done)
        return restored

    if emu_id == "shadps4":
        root = next((view.at(s) for s in ("home/1/savedata", "savedata/1")
                     if view.at(s).is_dir()), view.at("home/1/savedata"))
        done = set()
        for m, parts in norm:
            if len(parts) < 4:
                raise HTTPException(400, f"malformed PS4 save path '{m.filename}'")
            cusa = parts[1].upper()
            if cusa not in done:
                done.add(cusa)
                _backup(root / cusa)
            dest = root / cusa / Path(*parts[2:])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(m))
        restored += sorted(done)
        return restored

    raise HTTPException(400, "normalized save paths aren't supported for this emulator")


_BAK_PART_RE = re.compile(r"\.bak-\d{8}-\d{6}")


def zip_entries(items: list[tuple[Path, str]]):
    """Zip (path, arcname base) pairs. Backups are never bundled (matched on
    the full `.bak-<timestamp>` suffix, not a raw substring, so a game file
    that merely contains '.bak-' in its name is kept). Spools to a temp file
    past 64 MiB so a full RPCS3 tree can't eat the box's RAM."""
    buf = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)
    seen = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, arc in items:
            pairs = ([(f, f"{arc}/{f.relative_to(root).as_posix()}")
                      for f in sorted(root.rglob("*")) if f.is_file()]
                     if root.is_dir() else [(root, arc)])
            for f, name in pairs:
                if name in seen or _BAK_PART_RE.search(name):
                    continue
                seen.add(name)
                z.write(f, name)
    buf.seek(0)
    return buf


def arc_items(emu_id: str, base: Path, cols: list, entries: list) -> list[tuple[Path, str]]:
    """(source path, zip name) per scan entry. Most entries are archived under
    their base-relative path. Game saves of the id-dependent emulators get a
    NORMALIZED prefix instead, so the zip restores on any install:
      Switch  switch-title/<title id>/<save type>/…   (Ryujinx ids and yuzu
              user dirs are install-specific)
      X360    x360-title/<TitleID>/…                  (Xenia profile XUIDs differ)
      PS4     ps4-title/<CUSA…>/<savedir>/…           (shadPS4 moved dirs in v0.16)
    /upload-full maps those prefixes back onto the local install. Plain names
    come from the entry's collection, never from where it sits on disk: a
    profile's saves are named as the owner's are."""
    items = []
    for e in entries:
        col, p = cols[e["ci"]], e["path"]
        if e["key"] and emu_id in NORM_TAGS["switch-title"]:
            if col["subpath"] == "bis/user/save":
                tid, typ = ryu.identify(base, p)
                if tid:
                    src = next((p / c for c in ("0", "1") if (p / c).is_dir()), p)
                    items.append((src, f"switch-title/{tid}/{typ or 1}"))
                    continue
            elif col["subpath"] == "nand/user/save":
                typ = _DEVICE_TYPE if p.parent.name == _DEVICE_USER else "1"
                items.append((p, f"switch-title/{e['key']}/{typ}"))
                continue
        elif e["key"] and emu_id == "xenia":
            items.append((p, f"x360-title/{p.name.upper()}"))
            continue
        elif e["key"] and emu_id == "shadps4":
            items.append((p, f"ps4-title/{e['rel']}"))
            continue
        items.append((p, PurePosixPath(col["subpath"], e["rel"]).as_posix()))
    return items
