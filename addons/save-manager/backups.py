"""The automatic backups every destructive operation leaves behind.

Each one is a sibling `<name>.bak-<YYYYMMDD-HHMMSS>` of what it saved; the 3
most recent per target are kept. The server lists them and puts them back.
"""
import re
import shutil
from datetime import datetime
from pathlib import Path

from catalog import CATALOG, resolve_base

def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


KEEP = 3


def backup(path: Path, prune: bool = True) -> None:
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.bak-{ts}")
    if path.is_dir():
        shutil.copytree(path, dest)
    else:
        shutil.copy2(path, dest)
    if not prune:        # restoring FROM a backup must never delete that backup
        return
    # keep the disk sane: only the KEEP most recent backups per target
    # (prefix match, not glob — ROM names may contain [brackets] etc.)
    prefix = f"{path.name}.bak-"
    baks = sorted(p for p in path.parent.iterdir() if p.name.startswith(prefix))
    for old in baks[:-KEEP]:
        try:
            shutil.rmtree(old) if old.is_dir() else old.unlink()
        except OSError:
            pass


BAK_RE = re.compile(r"^(.+)\.bak-(\d{8}-\d{6})$")


def listing(emu_id: str) -> list[dict]:
    base = resolve_base(emu_id)
    if not base:
        return []
    out, seen = [], set()
    for ci, col in enumerate(CATALOG[emu_id]["collections"]):
        cdir = base / col["subpath"] if col["subpath"] else base
        if not cdir.is_dir():
            continue
        for p in cdir.rglob("*"):
            m = BAK_RE.fullmatch(p.name)
            if not m or p in seen:
                continue
            rel = p.relative_to(cdir)
            # a backup of a folder may contain older backups — list only the top one
            if any(".bak-" in part for part in rel.parts[:-1]):
                continue
            seen.add(p)
            try:
                size = dir_size(p) if p.is_dir() else p.stat().st_size
            except OSError:
                size = 0
            ts = m.group(2)
            out.append({
                "id": f"{ci}/{rel.as_posix()}",
                "name": rel.as_posix()[:-20],          # strip ".bak-<timestamp>"
                "when": f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:]}",
                "is_dir": p.is_dir(),
                "size": size, "sizeHuman": fmt_size(size),
                "orig_exists": p.with_name(m.group(1)).exists(),
            })
    out.sort(key=lambda b: (b["when"], b["name"]), reverse=True)
    return out
