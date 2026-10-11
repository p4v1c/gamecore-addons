"""What each installed system has, lacks, and has waiting for its emulator."""
from pathlib import Path

from place import firmware_action
from targets import (
    MELONDS_KEYS,
    NOTHING_NEEDED,
    OWN_FILES,
    STAGING,
    melonds_action,
    melonds_paths,
    own_dir,
    systems,
)

SWITCH_FIRMWARE_NOTE = ("Ryujinx's system firmware. Some games and features need it (fonts, Mii, applets). "
                        "Upload the firmware zip or folder; Ryujinx installs it.")


def _row(file: str, path: Path | str, ok: bool, required: bool, note: str) -> dict:
    return {"file": file, "path": str(path), "status": "ok" if ok else "absent",
            "required": required, "note": note}


def _core_rows(row: dict) -> list[dict]:
    out = []
    for f in row["files"]:
        out.append({"file": f["file"] or "Any BIOS image", "path": f["path"], "status": f["status"],
                    "required": f["required"], "note": f["note"]})
    return out


def _switch_firmware(row: dict) -> dict:
    registered = Path(row["dir"]).parent / "bis/system/Contents/registered"
    try:
        has = any(registered.iterdir())
    except OSError:
        has = False
    return _row("Firmware", registered, has, False, SWITCH_FIRMWARE_NOTE)


def _melonds_rows(system: dict) -> list[dict]:
    rows = []
    for name, path in melonds_paths(system).items():
        label = MELONDS_KEYS[name][2]
        note = "Not set in melonDS." if path is None else ""
        rows.append(_row(label, path or "", bool(path and path.is_file()), False, note))
    return rows


def staged(system_id: str) -> list[dict]:
    """Uploads parked under the addon's folder until the emulator takes them."""
    folder = STAGING / system_id
    if not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.iterdir()):
        step = melonds_action(p.name, p) if system_id == "melonds" else firmware_action(system_id, p)
        out.append({"name": p.name, "path": str(p), **step})
    return out


def _files_and_note(system: dict, core: dict | None) -> tuple[list[dict], str]:
    sid = system["id"]
    if sid in NOTHING_NEEDED:
        return [], NOTHING_NEEDED[sid]
    if sid == "melonds":
        return _melonds_rows(system), "melonDS plays DS games without these (FreeBIOS); DSi games need the DSi files."
    if sid in OWN_FILES:
        root = own_dir(system)
        return [_row(name, root / name, (root / name).is_file(), False, note) for name, note in OWN_FILES[sid]], ""
    if core is None:
        return [], "GameCore is not answering. Reload in a minute."
    if sid not in core:
        return [], "GameCore keeps no BIOS list for this system."
    rows = _core_rows(core[sid])
    if sid == "switch":
        rows.append(_switch_firmware(core[sid]))
    return rows, ""


def report(core: dict | None) -> list[dict]:
    out = []
    for system in systems():
        files, note = _files_and_note(system, core)
        missing = [f for f in files if f["status"] != "ok"]
        out.append({
            "id": system["id"], "label": system.get("label", system["id"]), "note": note,
            "files": files, "staged": staged(system["id"]),
            "missing_required": sum(f["required"] for f in missing),
            "missing": len(missing),
        })
    return out
