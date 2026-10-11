"""Turn an upload (loose files, folders, zips inside zips) into recognised files.

Zip members are never written to their own path: each one is extracted to a
numbered file in the work directory, and where it finally goes is decided by
what it is, not by what the archive says. A crafted member name can therefore
not reach anything; members with absolute or `..` paths are still refused and
reported, because an archive carrying them is not one to trust.
"""
import zipfile
from pathlib import Path, PurePosixPath

from identify import identify, junk_reason, md5_of, unsupported_archive

MAX_DEPTH = 3                  # a zip in a zip in a zip; deeper is not a BIOS pack
MAX_MEMBERS = 5000             # a Switch firmware is ~250 files
MAX_EXPANDED = 8 << 30         # bytes extracted from one upload, all archives together


class Budget:
    """Bytes and members still allowed out of the archives of one upload."""

    def __init__(self) -> None:
        self.bytes = MAX_EXPANDED
        self.members = MAX_MEMBERS


def clean_rel(name: str) -> str:
    """A client-supplied relative path, reduced to safe components (may be "")."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(p.replace("\0", "") for p in parts)


def _unsafe(member: str) -> bool:
    p = PurePosixPath(member.replace("\\", "/"))
    return p.is_absolute() or ".." in p.parts


def _extract(zf: zipfile.ZipFile, info: zipfile.ZipInfo, dest: Path, budget: Budget) -> None:
    """Copy one member out, counting real bytes: a header can lie about its size."""
    with zf.open(info) as src, dest.open("wb") as out:
        while chunk := src.read(1 << 20):
            budget.bytes -= len(chunk)
            if budget.bytes < 0:
                raise ValueError("the archives expand to more than 8 GB")
            out.write(chunk)


def _expand(rel: str, path: Path, work: Path, budget: Budget) -> tuple[list, list]:
    """Members of one zip as (rel, path) pairs, plus the refused ones."""
    members, refused = [], []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if _unsafe(info.filename):
                refused.append({"file": f"{rel}/{info.filename}", "reason": "Unsafe path inside the archive."})
                continue
            budget.members -= 1
            if budget.members < 0:
                raise ValueError("the archives hold more than 5000 files")
            dest = work / f"m{MAX_MEMBERS - budget.members}"
            _extract(zf, info, dest, budget)
            members.append((f"{rel}/{clean_rel(info.filename)}", dest))
    return members, refused


def recognise(uploads: list[tuple[str, Path]], work: Path, declared_zips: dict[str, str]) -> tuple[list, list]:
    """(recognised, ignored). Recognised items: {"file", "path", "md5", "hit"}.

    An archive is opened only when it is not itself a known file: dc/naomi.zip
    is a BIOS, and unpacking it would scatter the BIOS into unrecognised pieces.
    Raises ValueError when the archives blow the size or member budget.
    """
    found, ignored = [], []
    budget = Budget()
    queue = [(rel, path, 0) for rel, path in uploads]
    while queue:
        rel, path, depth = queue.pop(0)
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(8)
        if kind := unsupported_archive(head):
            ignored.append({"file": rel, "reason": f"{kind} archives are not supported. Repack it as a zip."})
            continue
        md5 = md5_of(path)
        hit = identify(rel, path, md5, size, declared_zips)
        if hit is None and zipfile.is_zipfile(path):
            if depth >= MAX_DEPTH:
                ignored.append({"file": rel, "reason": "Archive nested too deep."})
                continue
            try:
                members, refused = _expand(rel, path, work, budget)
            except (zipfile.BadZipFile, OSError) as e:
                ignored.append({"file": rel, "reason": f"Unreadable zip: {e}"})
                continue
            ignored += refused
            queue += [(r, p, depth + 1) for r, p in members]
            continue
        if hit is None:
            junk = junk_reason(rel)
            reason = f"{junk.capitalize()}." if junk else "Not recognised as a BIOS, firmware or key file."
            ignored.append({"file": rel, "reason": reason})
            continue
        found.append({"file": rel, "path": path, "md5": md5, "hit": hit})
    return found, ignored
