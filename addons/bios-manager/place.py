"""Put recognised files where their emulator reads them, or stage what only the
emulator itself can install. Every result is one dict with an `outcome`:

  placed    written where the emulator reads it
  replaced  written over a different file, which was backed up first
  same      the identical file was already there
  kept      a verified dump was already there and the upload is not one
  waiting   staged under the addon's folder; `action` says what to do next
  ignored   not written; `reason` says why
"""
import os
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath

from identify import KNOWN, md5_of
from targets import ADDON_DATA_DIR, STAGING, destination, melonds_action

# The DSi NAND holds the DSiWare saves: an existing one is never replaced.
NEVER_REPLACE = {"dsi_nand.bin"}

MIN_FIRMWARE_NCAS = 30        # a Switch firmware has 200+; a handful of .nca is game content
BACKUPS = ADDON_DATA_DIR / "backups"
RYUJINX_APP = "io.github.ryubing.Ryujinx"
RPCS3_APP = "net.rpcs3.RPCS3"


def firmware_action(system_id: str, staged: Path) -> dict:
    """The emulator step that finishes an install the addon can only prepare:
    {"action": what to do in the emulator, "command": the same from a shell}."""
    if system_id == "switch":
        return {"action": "Install it from Ryujinx: Actions, Install Firmware, Install a firmware from a "
                          f"directory, then pick {staged}. With Ryujinx closed, this command does the same:",
                "command": f'flatpak run {RYUJINX_APP} --install-firmware "{staged}"'}
    if system_id == "rpcs3":
        return {"action": f"Install it from RPCS3: File, Install Firmware, then pick {staged}. "
                          "Or run this, which opens RPCS3's window:",
                "command": f'flatpak run {RPCS3_APP} --installfw "{staged}"'}
    return {"action": ""}


def _is_verified(md5: str, system_id: str) -> bool:
    return KNOWN.get(md5, {}).get("system") == system_id


def _copy(src: Path, dest: Path) -> None:
    """Atomic: a power cut leaves the old file or the new one, never half of it."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.part")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)


def _write(item: dict, dest: Path, root: Path, system_id: str, stamp: str) -> dict:
    if not dest.exists():
        _copy(item["path"], dest)
        return {"outcome": "placed"}
    old = md5_of(dest)
    if old == item["md5"]:
        return {"outcome": "same"}
    if item["name"] in NEVER_REPLACE:
        return {"outcome": "kept", "reason": "The DSi NAND already there holds DSi saves. Replace it by hand if you mean to."}
    if _is_verified(old, system_id) and not _is_verified(item["md5"], system_id):
        return {"outcome": "kept", "reason": "The file already there is a verified dump; the upload is not."}
    backup = BACKUPS / stamp / system_id / dest.relative_to(root)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(dest, backup)
    _copy(item["path"], dest)
    return {"outcome": "replaced", "backup": str(backup)}


def _pinned_note(system: dict, name: str, core: dict | None) -> str:
    """Says so when the emulator is set to a different file name than this one."""
    row = (core or {}).get(system["id"]) or {}
    declared = [f["file"] for f in row.get("files", []) if f.get("file") and not f.get("any_file")]
    if not declared or name in declared:
        return ""
    return f"{system.get('label', system['id'])} is set to use {', '.join(declared)}; this file is stored beside it."


def _safe_name(rel: str) -> str:
    """The upload's own file name, for emulators that scan their folder."""
    return PurePosixPath(rel).name.lstrip(".") or "bios.bin"


def place_one(item: dict, system: dict, name: str, core: dict | None, ctx: dict) -> dict:
    """Write one recognised file under one target name. `ctx` is the upload's
    shared state: the timestamp and what this upload already wrote."""
    sid = system["id"]
    dest, root, reason = destination(system, name, core)
    if dest is None and sid == "melonds" and not reason:
        staged = STAGING / sid / name
        _copy(item["path"], staged)
        return {"outcome": "waiting", "dest": str(staged), **melonds_action(name, staged)}
    if dest is None:
        return {"outcome": "ignored", "reason": reason}
    try:
        dest.parent.resolve().relative_to(root.resolve())
    except ValueError:
        return {"outcome": "ignored", "reason": f"{dest} leads outside {root}; left alone."}
    if dest.is_symlink() or (dest.exists() and not dest.is_file()):
        return {"outcome": "ignored", "reason": f"{dest} is a link or a folder; left alone."}
    if ctx["written"].get(dest, item["md5"]) != item["md5"]:
        return {"outcome": "ignored", "reason": f"Another file in this upload already went to {dest}."}
    ctx["written"][dest] = item["md5"]
    result = _write({**item, "name": name}, dest, root, sid, ctx["stamp"])
    result["dest"] = str(dest)
    if note := _pinned_note(system, name, core):
        result["note"] = note
    return result


def stage_pup(item: dict) -> dict:
    staged = STAGING / "rpcs3" / "PS3UPDAT.PUP"
    _copy(item["path"], staged)
    return {"outcome": "waiting", "dest": str(staged), **firmware_action("rpcs3", staged)}


def stage_switch_firmware(ncas: list[dict], stamp: str) -> dict:
    """One staged firmware folder out of every .nca of the upload."""
    names = {}
    for item in ncas:
        p = PurePosixPath(item["file"])
        # A firmware dumped as a directory keeps each NCA as <id>.nca/00.
        names[p.parent.name if p.name == "00" else p.name] = item
    if len(names) < MIN_FIRMWARE_NCAS:
        return {"outcome": "ignored", "file": f"{len(names)} .nca files",
                "reason": f"Too few for a Switch firmware (it has more than {MIN_FIRMWARE_NCAS}). Game files are not installed here."}
    folder = STAGING / "switch" / stamp
    for name, item in names.items():
        _copy(item["path"], folder / name)
    return {"outcome": "waiting", "file": f"{len(names)} .nca files", "label": "Switch firmware",
            "dest": str(folder), **firmware_action("switch", folder)}


def place_all(found: list[dict], systems_by_id: dict[str, dict], core: dict | None) -> list[dict]:
    ctx = {"stamp": datetime.now().strftime("%Y%m%d-%H%M%S"), "written": {}}
    results, ncas = [], []
    for item in found:
        hit = item["hit"]
        base = {"file": item["file"], "label": hit["label"], "how": hit["how"]}
        system = systems_by_id.get(hit["system"])
        if hit.get("kind") == "nca":
            ncas.append(item)
        elif hit.get("kind") == "ignore":
            results.append({**base, "outcome": "ignored", "reason": hit["reason"]})
        elif system is None:
            results.append({**base, "outcome": "ignored",
                            "reason": "The emulator for this file is not installed on this box."})
        elif hit.get("kind") == "firmware":
            results.append({**base, "system": system.get("label"), **stage_pup(item)})
        else:
            for name in hit["names"] or [_safe_name(item["file"])]:
                results.append({**base, "system": system.get("label"), **place_one(item, system, name, core, ctx)})
    if ncas and "switch" in systems_by_id:
        results.append({"how": "name", "system": systems_by_id["switch"].get("label"),
                        **stage_switch_firmware(ncas, ctx["stamp"])})
    elif ncas:
        results.append({"file": f"{len(ncas)} .nca files", "outcome": "ignored",
                        "reason": "The Switch emulator is not installed on this box."})
    return results
