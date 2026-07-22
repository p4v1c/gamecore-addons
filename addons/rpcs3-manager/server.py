"""GameCore addon — RPCS3 Manager.

Web UI to configure PS3 games from the couch: game list (games.yml +
dev_hdd0), per-game config (custom_configs/config_<SERIAL>.yml, same
options as the RPCS3 UI), and patch management (imported_patch.yml +
patch_config.yml). Every write is preceded by a timestamped .bak and all
YAML goes through the string-preserving ryaml module — RPCS3 files contain
scalars ("16:9", "01.10") that a naive round-trip would corrupt.
"""
import asyncio
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ryaml
import sfo
from schema import SCHEMA, FIELD_BY_ID

ADDON_DIR = Path(__file__).parent
GAMECORE_PATH = Path(os.environ.get("GAMECORE_PATH", "/opt/GameCore"))
PORT = int(os.environ.get("ADDON_PORT", 8771))
_SERIAL_RE = re.compile(r"^[A-Z0-9]{9}$")
RPCS3_FLATPAK = "net.rpcs3.RPCS3"


def _declared_path() -> str:
    """The `path` of the rpcs3 entry in the box's systems.json ("flatpak" or a
    native launcher path). "" when unreadable/absent — the addon then falls
    back to what exists on disk, so it still works off-box."""
    try:
        systems = json.loads((GAMECORE_PATH / "config" / "systems.json").read_text())
        return next((s.get("path", "") for s in systems if s.get("id") == "rpcs3"), "")
    except (OSError, ValueError):
        return ""


def config_dir() -> Path:
    """RPCS3 config dir — env override, then the install systems.json declares
    (flatpak vs native), then whichever exists. A native dir kept around as a
    post-migration backup must not shadow the flatpak the box actually runs."""
    env = os.environ.get("RPCS3_CONFIG_DIR")
    if env:
        return Path(env)
    native = Path.home() / ".config" / "rpcs3"
    flatpak = Path.home() / ".var/app" / RPCS3_FLATPAK / "config" / "rpcs3"
    declared = _declared_path()
    if declared == "flatpak":
        return flatpak
    if declared:
        return native
    return native if native.exists() else flatpak


def rpcs3_cmd(extra_env: dict | None = None):
    """argv launching the configured RPCS3, or None when none is installed.
    RPCS3_BIN env wins; otherwise systems.json decides flatpak vs the native
    binary. extra_env is injected into the flatpak sandbox (--env=), which
    plain process env can't reach."""
    env_bin = os.environ.get("RPCS3_BIN")
    if env_bin:
        return [env_bin] if Path(env_bin).exists() else None
    if _declared_path() == "flatpak":
        have = subprocess.run(["flatpak", "info", RPCS3_FLATPAK],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
        if not have:
            return None
        return (["flatpak", "run"]
                + [f"--env={k}={v}" for k, v in (extra_env or {}).items()]
                + [RPCS3_FLATPAK])
    native = GAMECORE_PATH / "lib" / "rpcs3"
    return [str(native)] if native.exists() else None


_KEEP_BACKUPS = 3


def backup(path: Path) -> None:
    """Timestamped .bak before touching an existing file (box convention).
    Only the _KEEP_BACKUPS most recent per target are kept — patch.yml is
    several MB and re-downloaded regularly; without pruning the .bak files
    would grow without bound."""
    if not path.exists():
        return
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(path, path.with_name(f"{path.name}.bak-{ts}"))
    prefix = f"{path.name}.bak-"
    baks = sorted(p for p in path.parent.iterdir() if p.name.startswith(prefix))
    for old in baks[:-_KEEP_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass


def yload(path: Path):
    try:
        return ryaml.load(path.read_text())
    except FileNotFoundError:
        return None
    except Exception as e:
        raise HTTPException(500, f"cannot parse {path.name}: {e}")


app = FastAPI(title="GameCore addon — RPCS3 Manager", root_path=os.environ.get("ADDON_BASE", ""))


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
                # folder deleted but games.yml entry left behind — RPCS3 (and
                # we) keep listing it until the entry is removed
                "missing": not p.exists(),
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
                "missing": False,
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


@app.delete("/api/games/{serial}")
def remove_game(serial: str, data: bool = False):
    """Remove a game from RPCS3's list: drop its games.yml line (surgical,
    backup first). With data=true, also delete its dev_hdd0/game/<serial>*
    dirs (installed updates / HDD game data — never the save files, which
    live under dev_hdd0/home)."""
    _check_serial(serial)
    cfg = config_dir()
    removed_entry = False
    games_yml = cfg / "games.yml"
    if games_yml.exists():
        lines = games_yml.read_text().splitlines()
        kept = [l for l in lines if not re.match(rf"^{serial}:\s", l)]
        if len(kept) != len(lines):
            backup(games_yml)
            games_yml.write_text("\n".join(kept) + ("\n" if kept else ""))
            removed_entry = True

    removed_data = []
    if data:
        game_dir = cfg / "dev_hdd0" / "game"
        if game_dir.is_dir():
            for d in game_dir.iterdir():
                if d.is_dir() and d.name.startswith(serial):
                    shutil.rmtree(d)
                    removed_data.append(d.name)

    if not removed_entry and not removed_data:
        raise HTTPException(404, "nothing to remove for this serial")
    return {"ok": True, "removed_entry": removed_entry, "removed_data": removed_data}


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
    exact RPCS3 formatting everywhere else. A missing (sub)section is
    created in place instead of failing: an old custom config may predate
    e.g. the Video/Vulkan subsection."""
    lines = text.splitlines()
    stack: list[str] = []
    # prefix_end[d] = index of the last line still inside section[:d]
    prefix_end: dict[int, int] = {}
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "- ", "[", "{")):
            continue
        if ":" not in stripped:
            continue
        indent = (len(line) - len(line.lstrip(" "))) // 2
        name = stripped.split(":", 1)[0].strip().strip("\"'")
        stack = stack[:indent] + [name]
        if stack == [*section, key] and indent == len(section):
            pad = "  " * indent
            lines[i] = f"{pad}{key}: {_fmt_value(value)}"
            return "\n".join(lines) + "\n"
        for d in range(1, len(section) + 1):
            if stack[:d] == section[:d]:
                prefix_end[d] = i
    if len(section) in prefix_end:                    # section exists — insert
        lines.insert(prefix_end[len(section)] + 1,
                     f"{'  ' * len(section)}{key}: {_fmt_value(value)}")
        return "\n".join(lines) + "\n"
    # deepest existing parent (0 = none: append the whole block at the end,
    # which is safe — that top-level mapping key doesn't exist anywhere yet)
    depth = max((d for d in prefix_end), default=0)
    block = [f"{'  ' * d}{section[d]}:" for d in range(depth, len(section))]
    block.append(f"{'  ' * len(section)}{key}: {_fmt_value(value)}")
    at = prefix_end[depth] + 1 if depth else len(lines)
    lines[at:at] = block
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
    current_version = max(game_versions) if game_versions else ""

    pconf = yload(cfg / "patch_config.yml") or {}
    enabled_key = _enabled_key(pconf)
    # The imported file usually duplicates the official DB entries, and the
    # activation state is keyed by hash+name+title+serial+version — NOT by
    # file. So identical entries across files are one logical patch.
    entries: dict[tuple, dict] = {}
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
                            key = (hash_, name, title, skey, ver)
                            if key in entries:
                                if pfile.name not in entries[key]["files"]:
                                    entries[key]["files"].append(pfile.name)
                                continue
                            enabled = str(_get_path(pconf,
                                [hash_, name, title, skey, ver, enabled_key]) or "").lower() == "true"
                            entries[key] = {
                                "files": [pfile.name],
                                "hash": hash_,
                                "name": name,
                                "title": title,
                                "serial_key": skey,
                                "version": ver,
                                "version_match": ver == "All" or ver in game_versions,
                                # All/All entries are matched by RPCS3 against
                                # module hashes — we can't filter them per game
                                "generic": skey == "All",
                                "author": str(entry.get("Author") or ""),
                                "notes": str(notes or ""),
                                "patch_version": str(entry.get("Patch Version") or ""),
                                "enabled": enabled,
                            }
    result = sorted(entries.values(),
                    key=lambda p: (p["generic"], not p["version_match"],
                                   p["name"].lower(), p["version"]))
    return {"current_version": current_version, "patches": result}


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
    """Count real patch entries (a 'Patch' list + a 'Games' map). Tolerant on
    purpose: the official database also carries helper entries (Config Values
    definitions…) that are not patches — only a file with ZERO patches or a
    wrong structure is rejected."""
    if not isinstance(tree, dict):
        raise HTTPException(400, "not a YAML mapping")
    if "Version" not in tree:
        raise HTTPException(400, "missing 'Version' header")
    if str(tree["Version"]) != "1.2":
        raise HTTPException(400, f"unsupported patch engine version: {tree['Version']}")
    count = 0
    for hash_, patches in tree.items():
        if hash_ in _META_KEYS or not isinstance(patches, dict):
            continue
        for entry in patches.values():
            if isinstance(entry, dict) and isinstance(entry.get("Patch"), list) \
                    and isinstance(entry.get("Games"), dict):
                count += 1
    if count == 0:
        raise HTTPException(400, "no patch found in file")
    return count


# Same source as RPCS3's own "Download latest patches" button
# (rpcs3qt/patch_manager_dialog.cpp) — full official database, saved verbatim.
_PATCH_DB_URL = "https://rpcs3.net/compatibility?patch&api=v1&v=1.2"


@app.post("/api/patches/download-official")
def download_official_patches():
    import json as _json
    import urllib.request
    try:
        req = urllib.request.Request(_PATCH_DB_URL, headers={"User-Agent": "GameCore rpcs3-manager"})
        body = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
        # JSON envelope: {"return_code": 0, "version": "1.2", "sha256": …, "patch": "<yaml>"}
        envelope = _json.loads(body)
        if envelope.get("return_code") != 0 or not envelope.get("patch"):
            raise ValueError(f"return_code={envelope.get('return_code')}")
        raw = envelope["patch"]
    except Exception as e:
        raise HTTPException(502, f"rpcs3.net patch API failed: {e}")
    try:
        count = _validate_patch_yaml(ryaml.load(raw))
    except HTTPException as e:
        raise HTTPException(502, f"unexpected server response: {e.detail}")
    path = config_dir() / "patches" / "patch.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    backup(path)
    path.write_text(raw)  # verbatim — no re-emit needed for a full replacement
    return {"ok": True, "patches": count, "file": path.name}


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


# ── .pkg install (game updates / DLC) ─────────────────────────────────────────
# RPCS3 refuses to install a pkg in --no-gui mode ("Cannot perform installation
# in no-gui mode!") and this build ships no offscreen Qt plugin, so a real X
# display is required. On a GameCore box that's the TV — we discover it exactly
# like gamecore-ui.service does. A tiny progress dialog shows on the screen.

def _discover_display() -> dict | None:
    """Return env (DISPLAY + XAUTHORITY) for the box's active X session, or None."""
    if os.environ.get("DISPLAY"):
        env = {"DISPLAY": os.environ["DISPLAY"]}
        if os.environ.get("XAUTHORITY"):
            env["XAUTHORITY"] = os.environ["XAUTHORITY"]
        return env
    xauths = glob.glob(f"/run/user/{os.getuid()}/xauth_*")
    xauth = xauths[0] if xauths else None
    for disp in (":1", ":0", ":2"):
        env = {"DISPLAY": disp}
        if xauth:
            env["XAUTHORITY"] = xauth
        try:
            r = subprocess.run(["xdpyinfo", "-display", disp], env={**os.environ, **env},
                               capture_output=True, timeout=5)
            if r.returncode == 0:
                return env
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # no xdpyinfo — trust a probable display so install can still be tried
            return env
    return None


def _hdd_snapshot() -> dict:
    game = config_dir() / "dev_hdd0" / "game"
    if not game.is_dir():
        return {}
    return {d.name: d.stat().st_mtime for d in game.iterdir() if d.is_dir()}


# One install at a time; the frontend polls this for the outcome.
_pkg_job = {"state": "idle", "file": "", "installed": [], "error": ""}


async def _watch_install(proc, dest: Path, before: dict) -> None:
    """Detect the install landing in dev_hdd0/game, close RPCS3 (it stays open
    after --installpkg), clean the staged file. Runs detached from the request."""
    installed: list[str] = []
    deadline = time.monotonic() + 600  # 10 min cap for large updates
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        after = _hdd_snapshot()
        installed = [n for n, m in after.items() if n not in before or m > before.get(n, 0)]
        if installed:
            await asyncio.sleep(3)  # let RPCS3 finish flushing files
            after = _hdd_snapshot()
            installed = [n for n, m in after.items() if n not in before or m > before.get(n, 0)]
            break
        if proc.poll() is not None:  # RPCS3 closed (e.g. user dismissed an error)
            break
    if proc.poll() is None:
        proc.terminate()
        try:
            await asyncio.get_event_loop().run_in_executor(None, lambda: proc.wait(10))
        except Exception:
            proc.kill()
    dest.unlink(missing_ok=True)
    if installed:
        _pkg_job.update(state="done", installed=sorted(installed), error="")
    else:
        _pkg_job.update(state="error", installed=[],
                        error="no game data changed — the .pkg may be invalid, encrypted "
                              "(needs a .rap license) or already installed")


@app.get("/api/pkg/status")
def pkg_status():
    return _pkg_job


@app.post("/api/pkg/install")
async def install_pkg(file: UploadFile = File(...)):
    name = Path(file.filename or "").name
    if not name.lower().endswith(".pkg"):
        raise HTTPException(415, "not a .pkg file")
    if rpcs3_cmd() is None:
        raise HTTPException(503, "RPCS3 not found (native binary or flatpak)")
    if _pkg_job["state"] == "running":
        raise HTTPException(409, "a .pkg install is already running")
    # Claim the job BEFORE the first await — otherwise two concurrent uploads
    # both pass the check above and spawn two RPCS3 instances.
    _pkg_job.update(state="running", file=name, installed=[], error="")

    try:
        disp = _discover_display()
        if disp is None:
            raise HTTPException(503, "no active screen on the box — a .pkg install needs "
                                     "RPCS3's window; turn on the TV/box screen first")

        staging = config_dir() / "pkg_staging"
        staging.mkdir(parents=True, exist_ok=True)
        dest = staging / re.sub(r"[^\w.\-]", "_", name)
        with dest.open("wb") as out:
            while chunk := await file.read(1 << 20):
                out.write(chunk)

        before = _hdd_snapshot()
        try:
            cmd = rpcs3_cmd(extra_env=disp)
            if cmd is None:
                raise HTTPException(503, "RPCS3 not found (native binary or flatpak)")
            proc = subprocess.Popen(
                [*cmd, "--installpkg", str(dest)],
                env={**os.environ, **disp},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(500, f"could not launch RPCS3: {e}")
    except Exception:
        _pkg_job.update(state="idle", file="", installed=[], error="")
        raise

    asyncio.create_task(_finish_job(proc, dest, before))
    # Return immediately — the progress dialog shows on the box screen, the
    # frontend polls /api/pkg/status for the outcome.
    return {"ok": True, "state": "running", "file": name}


async def _finish_job(proc, dest, before):
    try:
        await _watch_install(proc, dest, before)
    except Exception as e:
        _pkg_job.update(state="error", error=str(e))


app.mount("/", StaticFiles(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
