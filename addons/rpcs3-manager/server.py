"""GameCore addon — RPCS3 Manager.

Web UI to configure PS3 games from the couch: game list (games.yml +
dev_hdd0), per-game config (custom_configs/config_<SERIAL>.yml, same
options as the RPCS3 UI), and patch management (imported_patch.yml +
patch_config.yml). Every write is preceded by a timestamped .bak and all
YAML goes through the string-preserving ryaml module — RPCS3 files contain
scalars ("16:9", "01.10") that a naive round-trip would corrupt.
"""
import datetime
import logging
import os
import re
import shutil
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ryaml
import sfo
from schema import SCHEMA, FIELD_BY_ID

ADDON_DIR = Path(__file__).parent
PORT = int(os.environ.get("ADDON_PORT", 8771))
_SERIAL_RE = re.compile(r"^[A-Z0-9]{9}$")

log = logging.getLogger("rpcs3-manager")


def config_dir() -> Path:
    """RPCS3 config dir — env override, then native, then flatpak."""
    env = os.environ.get("RPCS3_CONFIG_DIR")
    if env:
        return Path(env)
    native = Path.home() / ".config" / "rpcs3"
    if native.exists():
        return native
    return Path.home() / ".var/app/net.rpcs3.RPCS3/config/rpcs3"


def backup(path: Path) -> None:
    """Timestamped .bak before touching an existing file (box convention)."""
    if path.exists():
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.name}.bak-{ts}"))


def yload(path: Path):
    try:
        return ryaml.load(path.read_text())
    except FileNotFoundError:
        return None
    except Exception as e:
        raise HTTPException(500, f"cannot parse {path.name}: {e}")


app = FastAPI(title="GameCore addon — RPCS3 Manager")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    cfg = config_dir()
    return {"ok": True, "config_dir": str(cfg), "exists": cfg.exists()}


# ── Games ─────────────────────────────────────────────────────────────────────

def _disc_sfo(game_path: Path) -> dict:
    return sfo.parse(game_path / "PS3_GAME" / "PARAM.SFO") or sfo.parse(game_path / "PARAM.SFO")


def _game_versions(cfg: Path, serial: str, base_ver: str) -> list[str]:
    """Effective app versions: disc APP_VER plus any installed update's
    (game data dirs in dev_hdd0/game starting with the serial)."""
    versions = {base_ver} if base_ver else set()
    game_dir = cfg / "dev_hdd0" / "game"
    if game_dir.is_dir():
        for d in game_dir.iterdir():
            if d.name.startswith(serial):
                v = sfo.parse(d / "PARAM.SFO").get("APP_VER", "")
                if v:
                    versions.add(v)
    return sorted(versions)


@app.get("/api/games")
def list_games():
    cfg = config_dir()
    games: dict[str, dict] = {}

    mapping = yload(cfg / "games.yml") or {}
    if isinstance(mapping, dict):
        for serial, path in mapping.items():
            p = Path(str(path))
            meta = _disc_sfo(p)
            games[serial] = {
                "serial": serial,
                "title": " ".join(meta.get("TITLE", p.name.strip("/") or serial).split()),
                "source": "disc",
                "versions": _game_versions(cfg, serial, meta.get("APP_VER", "")),
            }

    hdd = cfg / "dev_hdd0" / "game"
    if hdd.is_dir():
        for d in sorted(hdd.iterdir()):
            meta = sfo.parse(d / "PARAM.SFO")
            serial = meta.get("TITLE_ID", d.name)
            if meta.get("CATEGORY") != "HG" or serial in games:
                continue
            games[serial] = {
                "serial": serial,
                "title": " ".join(meta.get("TITLE", d.name).split()),
                "source": "hdd",
                "versions": _game_versions(cfg, serial, meta.get("APP_VER", "")),
            }

    for g in games.values():
        g["has_custom_config"] = (cfg / "custom_configs" / f"config_{g['serial']}.yml").exists()
    return sorted(games.values(), key=lambda g: g["title"].lower())


def _check_serial(serial: str) -> str:
    if not _SERIAL_RE.fullmatch(serial):
        raise HTTPException(400, "invalid serial")
    return serial


@app.get("/api/games/{serial}/icon")
def game_icon(serial: str):
    cfg = config_dir()
    _check_serial(serial)
    mapping = yload(cfg / "games.yml") or {}
    candidates = []
    if isinstance(mapping, dict) and serial in mapping:
        candidates.append(Path(str(mapping[serial])) / "PS3_GAME" / "ICON0.PNG")
    candidates.append(cfg / "dev_hdd0" / "game" / serial / "ICON0.PNG")
    for c in candidates:
        if c.is_file():
            return FileResponse(str(c), media_type="image/png")
    raise HTTPException(404)


# ── Per-game config ───────────────────────────────────────────────────────────

def _get_path(tree, segments):
    node = tree
    for s in segments:
        if not isinstance(node, dict) or s not in node:
            return None
        node = node[s]
    return node


def _set_path(tree, segments, value):
    node = tree
    for s in segments[:-1]:
        nxt = node.get(s)
        if not isinstance(nxt, dict):
            nxt = {}
            node[s] = nxt
        node = nxt
    node[segments[-1]] = value


def _schema_values(tree) -> dict:
    out = {}
    for fid, fld in FIELD_BY_ID.items():
        v = _get_path(tree or {}, [*fld["section"], fld["key"]])
        if v is not None and not isinstance(v, (dict, list)):
            out[fid] = str(v)
    return out


def _custom_path(serial: str) -> Path:
    return config_dir() / "custom_configs" / f"config_{serial}.yml"


def _fmt_value(value: str) -> str:
    """Plain scalar like RPCS3 writes them — except "Null", which RPCS3
    itself double-quotes (plain Null would parse as ~)."""
    return f'"{value}"' if value == "Null" else value


def _edit_config_text(text: str, section: list[str], key: str, value: str) -> str:
    """Surgical line edit: change (or insert) one key inside a section
    without rewriting the rest of the file — keeps diffs minimal and the
    exact RPCS3 formatting everywhere else."""
    lines = text.splitlines()
    stack: list[str] = []
    section_end = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "- ", "[", "{")):
            continue
        if ":" not in stripped:
            continue
        indent = (len(line) - len(line.lstrip(" "))) // 2
        name = stripped.split(":", 1)[0].strip().strip("\"'")
        stack = stack[:indent] + [name]
        if stack[: len(section)] == section:
            if stack == [*section, key] and indent == len(section):
                pad = "  " * indent
                lines[i] = f"{pad}{key}: {_fmt_value(value)}"
                return "\n".join(lines) + "\n"
            section_end = i
        elif section_end is not None and indent < len(section):
            break
    if section_end is None:
        raise HTTPException(500, f"section {'/'.join(section)} not found in config")
    pad = "  " * len(section)
    lines.insert(section_end + 1, f"{pad}{key}: {_fmt_value(value)}")
    return "\n".join(lines) + "\n"


@app.get("/api/schema")
def get_schema():
    globals_ = _schema_values(yload(config_dir() / "config.yml"))
    return {"schema": SCHEMA, "globals": globals_}


@app.get("/api/games/{serial}/config")
def get_game_config(serial: str):
    _check_serial(serial)
    custom = yload(_custom_path(serial))
    if custom is not None:
        return {"exists": True, "values": _schema_values(custom)}
    return {"exists": False, "values": _schema_values(yload(config_dir() / "config.yml"))}


class ConfigBody(BaseModel):
    values: dict[str, str]


@app.put("/api/games/{serial}/config")
async def put_game_config(serial: str, body: ConfigBody):
    _check_serial(serial)
    for fid, value in body.values.items():
        fld = FIELD_BY_ID.get(fid)
        if not fld:
            raise HTTPException(400, f"unknown option: {fid}")
        if fld["type"] == "enum" and value not in fld["options"]:
            raise HTTPException(400, f"invalid value for {fid}: {value}")
        if fld["type"] == "bool" and value not in ("true", "false"):
            raise HTTPException(400, f"invalid value for {fid}: {value}")
        if fld["type"] == "int":
            try:
                n = int(value)
            except ValueError:
                raise HTTPException(400, f"invalid value for {fid}: {value}")
            if not (fld["min"] <= n <= fld["max"]):
                raise HTTPException(400, f"{fid} out of range [{fld['min']}-{fld['max']}]")

    path = _custom_path(serial)
    created = not path.exists()
    if created:
        # RPCS3's "create custom configuration from global settings"
        global_path = config_dir() / "config.yml"
        if not global_path.exists():
            raise HTTPException(503, "global config.yml not found")
        text = global_path.read_text()
    else:
        text = path.read_text()

    for fid, value in body.values.items():
        fld = FIELD_BY_ID[fid]
        text = _edit_config_text(text, fld["section"], fld["key"], value)

    path.parent.mkdir(parents=True, exist_ok=True)
    backup(path)
    path.write_text(text)
    return {"ok": True, "created": created, "changed": len(body.values)}


@app.delete("/api/games/{serial}/config")
def reset_game_config(serial: str):
    _check_serial(serial)
    path = _custom_path(serial)
    if not path.exists():
        raise HTTPException(404, "no custom config")
    backup(path)
    path.unlink()
    return {"ok": True}


# ── Patches ───────────────────────────────────────────────────────────────────
# RPCS3 only reads these files in patches/ (Utilities/bin_patch.cpp):
#   patch.yml, imported_patch.yml, {TITLE_ID}_patch.yml

_META_KEYS = {"Version", "Anchors"}


def _patch_files(serial: str) -> list[Path]:
    pdir = config_dir() / "patches"
    return [p for p in (pdir / "patch.yml", pdir / "imported_patch.yml",
                        pdir / f"{serial}_patch.yml") if p.is_file()]


def _enabled_key(tree: dict) -> str:
    """The box's RPCS3 writes 'Enabled'; newer sources say 'enabled' —
    reuse whatever the existing file uses."""
    def scan(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("Enabled", "enabled"):
                    return k
                found = scan(v)
                if found:
                    return found
        return None
    return scan(tree) or "Enabled"


@app.get("/api/games/{serial}/patches")
def game_patches(serial: str):
    _check_serial(serial)
    cfg = config_dir()
    game = next((g for g in list_games() if g["serial"] == serial), None)
    game_versions = set(game["versions"]) if game else set()

    pconf = yload(cfg / "patch_config.yml") or {}
    enabled_key = _enabled_key(pconf)
    result = []
    for pfile in _patch_files(serial):
        tree = yload(pfile)
        if not isinstance(tree, dict):
            continue
        for hash_, patches in tree.items():
            if hash_ in _META_KEYS or not isinstance(patches, dict):
                continue
            for name, entry in patches.items():
                if not isinstance(entry, dict):
                    continue
                games_map = entry.get("Games") or {}
                for title, serials in games_map.items():
                    if not isinstance(serials, dict):
                        continue
                    for skey, versions in serials.items():
                        if skey != serial and skey != "All":
                            continue
                        notes = entry.get("Notes")
                        if isinstance(notes, list):
                            notes = " ".join(str(n) for n in notes)
                        for ver in (versions or []):
                            ver = str(ver)
                            enabled = str(_get_path(pconf,
                                [hash_, name, title, skey, ver, enabled_key]) or "").lower() == "true"
                            result.append({
                                "file": pfile.name,
                                "hash": hash_,
                                "name": name,
                                "title": title,
                                "serial_key": skey,
                                "version": ver,
                                "version_match": ver == "All" or ver in game_versions,
                                "author": str(entry.get("Author") or ""),
                                "notes": str(notes or ""),
                                "patch_version": str(entry.get("Patch Version") or ""),
                                "enabled": enabled,
                            })
    result.sort(key=lambda p: (not p["version_match"], p["name"].lower()))
    return result


class ToggleBody(BaseModel):
    hash: str
    name: str
    title: str
    serial_key: str
    version: str
    enabled: bool


@app.post("/api/games/{serial}/patches/toggle")
async def toggle_patch(serial: str, body: ToggleBody):
    _check_serial(serial)
    path = config_dir() / "patch_config.yml"
    tree = yload(path) or {}
    key = _enabled_key(tree)
    _set_path(tree, [body.hash, body.name, body.title, body.serial_key,
                     body.version, key], "true" if body.enabled else "false")
    backup(path)
    path.write_text(ryaml.dump(tree))
    return {"ok": True, "enabled": body.enabled}


def _validate_patch_yaml(tree) -> int:
    """Return the number of patch entries; raise on structural problems."""
    if not isinstance(tree, dict):
        raise HTTPException(400, "not a YAML mapping")
    if "Version" not in tree:
        raise HTTPException(400, "missing 'Version' header")
    if str(tree["Version"]) != "1.2":
        raise HTTPException(400, f"unsupported patch engine version: {tree['Version']}")
    count = 0
    for hash_, patches in tree.items():
        if hash_ in _META_KEYS:
            continue
        if not isinstance(patches, dict):
            raise HTTPException(400, f"'{hash_}' must contain patches")
        for name, entry in patches.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("Patch"), list):
                raise HTTPException(400, f"patch '{name}' has no 'Patch' list")
            if not isinstance(entry.get("Games"), dict):
                raise HTTPException(400, f"patch '{name}' has no 'Games' map")
            count += 1
    if count == 0:
        raise HTTPException(400, "no patch found in file")
    return count


@app.post("/api/patches/upload")
async def upload_patch(file: UploadFile = File(...)):
    """Validate then merge into imported_patch.yml — the file RPCS3 actually
    reads (it does NOT scan arbitrary .yml files in patches/)."""
    raw = (await file.read()).decode("utf-8", "replace")
    try:
        tree = ryaml.load(raw)
    except Exception as e:
        raise HTTPException(400, f"invalid YAML: {e}")
    count = _validate_patch_yaml(tree)

    path = config_dir() / "patches" / "imported_patch.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = yload(path)
    if existing is None:
        existing = {"Version": "1.2"}
    merged = 0
    for hash_, patches in tree.items():
        if hash_ in _META_KEYS:
            continue
        group = existing.setdefault(hash_, {})
        if not isinstance(group, dict):
            raise HTTPException(409, f"imported_patch.yml has a conflicting '{hash_}' entry")
        group.update(patches)
        merged += len(patches)
    backup(path)
    path.write_text(ryaml.dump(existing))
    return {"ok": True, "patches": count, "merged": merged, "file": path.name}


app.mount("/", StaticFiles(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
