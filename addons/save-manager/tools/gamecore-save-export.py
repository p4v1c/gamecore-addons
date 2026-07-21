#!/usr/bin/env python3
"""gamecore-save-export — pack a PC's emulator saves for GameCore's Save Manager.

Run this ON THE PC where you played. It scans the default save locations of
every emulator GameCore supports (Windows / Linux / macOS), packs one
upload-ready zip per emulator, and can push them straight to the box:

    python gamecore-save-export.py                          # what was found
    python gamecore-save-export.py --pack -o out/           # write the zips
    python gamecore-save-export.py --push https://BOX:8443/saves --password …
    python gamecore-save-export.py --emu pcsx2 dolphin --push https://BOX:8443/saves
    python gamecore-save-export.py --path xenia="D:\\xenia" --push https://BOX:8443/saves
    python gamecore-save-export.py --n64-rom Zelda.z64 --n64-save old.sra

The box sits behind an HTTPS proxy with a shared login: pass the web
password with --password (or env GC_PASSWORD — https pushes prompt if
absent). Its certificate authority is self-hosted: download it once from
https://BOX:8443/gc/ca.crt and pass --ca gamecore-ca.crt (or --insecure).
Direct loopback pushes (http://127.0.0.1:8772, on the box) need neither.

Zips use the exact layout the Save Manager restores ("full backup" zone /
POST /api/saves/<emu>/upload-full):
  * plain emulators — paths relative to the emulator's data dir
    (memcards/…, sstates/…, Wii/title/…, dev_hdd0/…),
  * id-dependent emulators — normalized, install-independent paths:
    switch-title/<titleid>/<type>/…, x360-title/<TitleID>/…,
    ps4-title/<CUSA…>/<savedir>/….

Python 3.8+, standard library only. Nothing on the PC is modified.
"""
import argparse
import getpass
import hashlib
import io
import json
import os
import re
import ssl
import struct
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOME = Path.home()
WIN = sys.platform == "win32"
APPDATA = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
DOCS = HOME / "Documents"


def _first(*cands):
    for c in cands:
        if c and Path(c).is_dir():
            return Path(c)
    return None


_BAK_PART_RE = re.compile(r"\.bak-\d{8}-\d{6}")


def _files(root: Path, arc_prefix: str = "") -> list:
    """(arcname, path) for every file under root, skipping addon backups."""
    out = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or _BAK_PART_RE.search(f.name):
            continue
        rel = f.relative_to(root).as_posix()
        out.append((f"{arc_prefix}/{rel}" if arc_prefix else rel, f))
    return out


# ── plain emulators: PC data dir → base-relative members ────────────────────────

def scan_duckstation(override):
    base = override or _first(
        DOCS / "DuckStation", LOCALAPPDATA / "DuckStation",
        HOME / ".local/share/duckstation",
        HOME / ".var/app/org.duckstation.DuckStation/data/duckstation")
    if not base:
        return None, []
    out = []
    for sub in ("memcards", "savestates"):
        if (base / sub).is_dir():
            out += _files(base / sub, sub)
    return base, out


def scan_pcsx2(override):
    base = override or _first(
        DOCS / "PCSX2", HOME / ".config/PCSX2",
        HOME / ".var/app/net.pcsx2.PCSX2/config/PCSX2")
    if not base:
        return None, []
    folder_cards = {d.name for d in (base / "memcards").iterdir()
                    if d.is_dir() and (d / "_pcsx2_superblock").exists()} \
        if (base / "memcards").is_dir() else set()
    for name in sorted(folder_cards):
        print(f"  ! PCSX2 folder memory card skipped (convert to a file card first): {name}")
    out = []
    for sub in ("memcards", "sstates"):
        if (base / sub).is_dir():
            out += [(a, p) for a, p in _files(base / sub, sub)
                    if not (sub == "memcards" and a.split("/")[1] in folder_cards)]
    return base, out


def scan_dolphin(override):
    base = override or _first(
        APPDATA / "Dolphin Emulator", DOCS / "Dolphin Emulator",
        HOME / ".local/share/dolphin-emu",
        HOME / ".var/app/org.DolphinEmu.dolphin-emu/data/dolphin-emu")
    if not base:
        return None, []
    out = []
    for sub in ("GC", "Wii/title", "StateSaves"):
        if (base / sub).is_dir():
            out += _files(base / sub, sub)
    return base, out


def scan_rpcs3(override):
    base = override or _first(
        HOME / ".config/rpcs3", HOME / ".var/app/net.rpcs3.RPCS3/config/rpcs3")
    if not base:
        return None, []
    out = []
    for sub in ("dev_hdd0/home/00000001/savedata", "dev_hdd0/home/00000001/trophy"):
        if (base / sub).is_dir():
            out += _files(base / sub, sub)
    return base, out


def scan_ppsspp(override):
    """PC memstick PSP/ dir → members relative to PSP/ (the box collections)."""
    for cand in ([override] if override else
                 [DOCS / "PPSSPP", HOME / ".config/ppsspp",
                  HOME / ".var/app/org.ppsspp.PPSSPP/config/ppsspp"]):
        if cand and (Path(cand) / "PSP").is_dir():
            psp = Path(cand) / "PSP"
            out = []
            for sub in ("SAVEDATA", "PPSSPP_STATE"):
                if (psp / sub).is_dir():
                    out += _files(psp / sub, sub)
            return psp, out
    return None, []


def scan_cemu(override):
    base = override or _first(
        APPDATA / "Cemu", HOME / ".local/share/Cemu",
        HOME / ".var/app/info.cemu.Cemu/data/Cemu")
    if not base:
        return None, []
    save = base / "mlc01/usr/save"
    return (base, _files(save, "mlc01/usr/save")) if save.is_dir() else (None, [])


def scan_azahar(override):
    base = override or _first(
        APPDATA / "Azahar", HOME / ".local/share/azahar-emu",
        HOME / ".var/app/org.azahar_emu.Azahar/data/azahar-emu",
        APPDATA / "Citra", HOME / ".local/share/citra-emu")
    if not base:
        return None, []
    out = []
    if (base / "sdmc/Nintendo 3DS").is_dir():
        out += _files(base / "sdmc/Nintendo 3DS", "sdmc/Nintendo 3DS")
    if (base / "states").is_dir():
        out += _files(base / "states", "states")
    return base, out


def scan_gopher64(override):
    base = override or _first(
        APPDATA / "gopher64", HOME / ".local/share/gopher64",
        HOME / ".var/app/io.github.gopher64.gopher64/data/gopher64")
    if not base:
        return None, []
    out = []
    for sub in ("saves", "states"):
        if (base / sub).is_dir():
            out += _files(base / sub, sub)
    return base, out


def _scan_romdir(override, save_exts, state_exts):
    """mGBA / melonDS keep saves next to the ROMs — needs --path <emu>=DIR."""
    if not override:
        return None, []
    out = []
    for f in sorted(Path(override).rglob("*")):
        if f.is_file() and f.suffix.lower() in save_exts + state_exts:
            out.append((f.name, f))
    return Path(override), out


def scan_mgba(override):
    return _scan_romdir(override, [".sav", ".srm"], [f".ss{i}" for i in range(10)])


def scan_melonds(override):
    return _scan_romdir(override, [".sav"], [".mln"] + [f".ml{i}" for i in range(1, 9)])


# ── id-dependent emulators: normalized members ──────────────────────────────────

def _ryujinx_attr(save_dir: Path):
    for name in ("ExtraData0", "ExtraData1"):
        try:
            raw = (save_dir / name).read_bytes()
        except OSError:
            continue
        if len(raw) >= 0x21:
            tid = struct.unpack_from("<Q", raw, 0)[0]
            if tid:
                return f"{tid:016X}", raw[0x20]
    return None, None


def _ryujinx_indexer(base: Path) -> dict:
    out = {}
    for commit in ("0", "1"):
        p = base / "bis/system/save/8000000000000000" / commit / "imkvdb.arc"
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if len(raw) < 12 or raw[:4] != b"IMKV":
            continue
        off = 12
        for _ in range(max(0, struct.unpack_from("<i", raw, 8)[0])):
            if off + 12 > len(raw) or raw[off:off + 4] != b"IMEN":
                break
            ksz, vsz = struct.unpack_from("<ii", raw, off + 4)
            key, val = raw[off + 12:off + 12 + ksz], raw[off + 12 + ksz:off + 12 + ksz + vsz]
            off += 12 + ksz + vsz
            if len(key) >= 0x21 and len(val) >= 8:
                tid = struct.unpack_from("<Q", key, 0)[0]
                sid = struct.unpack_from("<Q", val, 0)[0]
                if tid and sid:
                    out.setdefault(f"{sid:016x}", (f"{tid:016X}", key[0x20]))
        if out:
            break
    return out


def scan_switch(override):
    """yuzu-family (nand/…, ids literal — citron/citron-neo, yuzu, sudachi)
    AND Ryujinx (bis/…, ids resolved)."""
    bases = ([override] if override else
             [APPDATA / "citron", HOME / ".local/share/citron",
              APPDATA / "yuzu", APPDATA / "sudachi",
              HOME / ".local/share/yuzu", HOME / ".local/share/sudachi",
              APPDATA / "Ryujinx", HOME / ".config/Ryujinx",
              HOME / ".var/app/io.github.ryubing.Ryujinx/config/Ryujinx"])
    out, used = [], None
    for b in bases:
        b = Path(b)
        root = b / "bis/user/save"
        if root.is_dir():
            idx = None
            for d in sorted(p for p in root.iterdir() if p.is_dir()):
                tid, typ = _ryujinx_attr(d)
                if not tid:
                    if idx is None:
                        idx = _ryujinx_indexer(b)
                    tid, typ = idx.get(d.name.lower(), (None, None))
                if not tid:
                    continue
                src = next((d / c for c in ("0", "1") if (d / c).is_dir()), d)
                out += _files(src, f"switch-title/{tid}/{typ or 1}")
            used = used or b
        nand = b / "nand/user/save/0000000000000000"
        if nand.is_dir():
            for user in sorted(p for p in nand.iterdir() if p.is_dir()):
                for d in sorted(p for p in user.iterdir() if p.is_dir()):
                    if re.fullmatch(r"[0-9A-Fa-f]{16}", d.name):
                        out += _files(d, f"switch-title/{d.name.upper()}/1")
            used = used or b
        if out:
            break
    return used, out


def scan_xenia(override):
    """Xenia Canary is portable: content\\ sits next to the exe — pass
    --path xenia=<that folder> if the default guesses miss it."""
    for cand in ([override] if override else [DOCS / "Xenia", DOCS / "xenia"]):
        if not cand:
            continue
        content = Path(cand) / "content" if (Path(cand) / "content").is_dir() else Path(cand)
        if not content.is_dir() or content.name != "content":
            continue
        out = []
        for top in sorted(p for p in content.iterdir() if p.is_dir()):
            if re.fullmatch(r"[0-9A-F]{16}", top.name.upper()):
                if top.name.upper() == "0" * 16:
                    continue                     # profile-less DLC — not saves
                for tid in sorted(p for p in top.iterdir() if p.is_dir()):
                    if tid.name.upper() == "FFFE07D1":
                        continue                 # the profile package itself
                    out += _files(tid, f"x360-title/{tid.name.upper()}")
            elif re.fullmatch(r"[0-9A-F]{8}", top.name.upper()) \
                    and top.name.upper() != "FFFE07D1":
                out += _files(top, f"x360-title/{top.name.upper()}")  # old layout
        return content, out
    return None, []


def scan_shadps4(override):
    bases = ([override] if override else
             [APPDATA / "shadPS4", HOME / ".local/share/shadPS4",
              HOME / ".var/app/net.shadps4.shadPS4/data/shadPS4",
              Path.cwd() / "user"])
    for b in bases:
        b = Path(b)
        for sub in ("home/1/savedata", "savedata/1"):
            root = b / sub
            if root.is_dir():
                out = []
                for cusa in sorted(p for p in root.iterdir() if p.is_dir()):
                    out += _files(cusa, f"ps4-title/{cusa.name.upper()}")
                return b, out
    return None, []


SCANNERS = {
    "duckstation": ("PlayStation 1 (DuckStation)", scan_duckstation),
    "pcsx2": ("PlayStation 2 (PCSX2)", scan_pcsx2),
    "rpcs3": ("PlayStation 3 (RPCS3)", scan_rpcs3),
    "shadps4": ("PlayStation 4 (shadPS4)", scan_shadps4),
    "ppsspp": ("PSP (PPSSPP)", scan_ppsspp),
    "dolphin": ("GameCube / Wii (Dolphin)", scan_dolphin),
    "cemu": ("Wii U (Cemu)", scan_cemu),
    "citron-neo": ("Switch (yuzu-family / Ryujinx)", scan_switch),
    "azahar": ("Nintendo 3DS (Azahar / Citra)", scan_azahar),
    "gopher64": ("Nintendo 64 (gopher64)", scan_gopher64),
    "mgba": ("Game Boy Advance (mGBA — needs --path mgba=ROMDIR)", scan_mgba),
    "melonds": ("Nintendo DS (melonDS — needs --path melonds=ROMDIR)", scan_melonds),
    "xenia": ("Xbox 360 (Xenia — content dir, try --path xenia=DIR)", scan_xenia),
}


# ── N64 conversion (Project64 / mupen64plus / simple64 → gopher64) ──────────────

def n64_convert(rom: Path, save: Path, outdir: Path) -> Path:
    """Rename (and byte-swap .sra/.fla) a foreign N64 save to gopher64's
    <InternalName>-<SHA256 of the big-endian ROM>.<ext> naming."""
    data = bytearray(rom.read_bytes())
    if data[:4] == b"\x37\x80\x40\x12":            # v64 → z64
        data[::2], data[1::2] = data[1::2], data[::2]
    elif data[:4] == b"\x40\x12\x37\x80":          # n64 → z64
        for i in range(0, len(data), 4):
            data[i:i + 4] = data[i:i + 4][::-1]
    if data[:4] != b"\x80\x37\x12\x40":
        raise SystemExit(f"{rom} doesn't look like an N64 ROM")
    name = data[0x20:0x34].decode("ascii", "ignore").strip()
    sha = hashlib.sha256(bytes(data)).hexdigest().upper()
    blob = save.read_bytes()
    ext = save.suffix.lower()
    if ext in (".sra", ".fla"):                    # little-endian → big-endian words
        b = bytearray(blob)
        for i in range(0, len(b) - 3, 4):
            b[i:i + 4] = b[i:i + 4][::-1]
        blob = bytes(b)
    dest = outdir / f"{name}-{sha}{ext}"
    dest.write_bytes(blob)
    print(f"→ {dest}")
    print("  drop it on the box's gopher64 save zone (or --push a zip with saves/<that name>)")
    return dest


# ── pack & push ──────────────────────────────────────────────────────────────────

def build_zip(items) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        seen = set()
        for arc, path in items:
            if arc not in seen:
                seen.add(arc)
                z.write(path, arc)
    return buf.getvalue()


_TLS_HINT = ("TLS verification failed — download the box CA once from "
             "https://BOX:8443/gc/ca.crt and pass --ca gamecore-ca.crt "
             "(or use --insecure)")


def make_ctx(args) -> ssl.SSLContext:
    if args.insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=args.ca)


def _is_tls_error(e: Exception) -> bool:
    return isinstance(getattr(e, "reason", None), ssl.SSLError) \
        or "CERTIFICATE_VERIFY" in str(e).upper()


def login(box: str, password: str, ctx) -> str:
    """POST /api/auth/login at the box ORIGIN (the auth API lives at the
    root, not under the /saves prefix) — returns the session cookie."""
    parts = urlsplit(box if "//" in box else "//" + box)
    origin = f"{parts.scheme or 'http'}://{parts.netloc}"
    req = urllib.request.Request(
        origin + "/api/auth/login",
        data=json.dumps({"password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            for header, value in r.headers.items():
                if header.lower() == "set-cookie" and value.startswith("gc_session="):
                    return value.split(";", 1)[0]
    except urllib.error.HTTPError as e:
        raise SystemExit("login refused — wrong password?" if e.code == 401
                         else f"login failed — HTTP {e.code}")
    except OSError as e:
        raise SystemExit(_TLS_HINT if _is_tls_error(e) else f"login failed — {e}")
    raise SystemExit("login failed — no session cookie in the reply")


def push(box: str, emu: str, blob: bytes, ctx=None, cookie=None) -> str:
    url = f"{box.rstrip('/')}/api/saves/{emu}/upload-full"
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{emu}-saves.zip\"\r\nContent-Type: application/zip\r\n\r\n"
            ).encode() + blob + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=300, context=ctx) as r:
            restored = json.loads(r.read()).get("restored", [])
            return f"restored: {', '.join(map(str, restored)) or 'ok'}"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return "REFUSED — login required (pass --password / GC_PASSWORD)"
        try:
            return f"REFUSED — {json.loads(e.read()).get('detail', e)}"
        except Exception:
            return f"FAILED — HTTP {e.code}"
    except OSError as e:
        return f"FAILED — {_TLS_HINT if _is_tls_error(e) else e}"


def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emu", nargs="*", metavar="ID",
                    help=f"only these systems ({', '.join(SCANNERS)})")
    ap.add_argument("--path", action="append", default=[], metavar="EMU=DIR",
                    help="override/provide a location (repeatable)")
    ap.add_argument("--pack", action="store_true", help="write one zip per emulator")
    ap.add_argument("-o", "--out", default="gamecore-saves", help="output dir for --pack")
    ap.add_argument("--push", metavar="URL", help="upload to the box, e.g. https://192.168.1.50:8443/saves")
    ap.add_argument("--password", metavar="PW",
                    help="box web password (or env GC_PASSWORD); https pushes prompt if absent")
    ap.add_argument("--ca", metavar="FILE",
                    help="the box CA certificate (download it from https://BOX:8443/gc/ca.crt)")
    ap.add_argument("--insecure", action="store_true", help="skip TLS verification (not recommended)")
    ap.add_argument("--n64-rom", metavar="ROM", help="convert a foreign N64 save: the matching ROM")
    ap.add_argument("--n64-save", metavar="SAVE", help="…and the .eep/.sra/.fla/.mpk to convert")
    args = ap.parse_args()

    if args.n64_rom or args.n64_save:
        if not (args.n64_rom and args.n64_save):
            ap.error("--n64-rom and --n64-save go together")
        outdir = Path(args.out)
        outdir.mkdir(parents=True, exist_ok=True)
        n64_convert(Path(args.n64_rom), Path(args.n64_save), outdir)
        return

    overrides = {}
    for spec in args.path:
        emu, _, p = spec.partition("=")
        if emu not in SCANNERS or not p:
            ap.error(f"bad --path '{spec}' (expected EMU=DIR)")
        overrides[emu] = Path(p)

    wanted = args.emu or list(SCANNERS)
    for w in wanted:
        if w not in SCANNERS:
            ap.error(f"unknown system '{w}' ({', '.join(SCANNERS)})")

    found = {}
    print("Scanning this PC for emulator saves…\n")
    for emu in wanted:
        label, fn = SCANNERS[emu]
        base, items = fn(overrides.get(emu))
        if items:
            size = sum(p.stat().st_size for _a, p in items)
            print(f"  {emu:<12} {len(items):>4} files  {human(size):>8}   {base}")
            found[emu] = items
        else:
            hint = "" if base or emu not in ("mgba", "melonds", "xenia") else "  (give --path)"
            print(f"  {emu:<12}    — nothing found{hint}")
    if not found:
        print("\nNo saves found. Use --path EMU=DIR to point at custom locations.")
        return
    if not (args.pack or args.push):
        print("\nNext: add --pack to write zips, or --push https://<box-ip>:8443/saves to upload.")
        return

    ctx = cookie = None
    if args.push:
        ctx = make_ctx(args)
        password = args.password or os.environ.get("GC_PASSWORD", "")
        if not password and args.push.lower().startswith("https"):
            password = getpass.getpass("Box web password (empty = try without login): ")
        if password:
            cookie = login(args.push, password, ctx)

    outdir = Path(args.out)
    if args.pack:
        outdir.mkdir(parents=True, exist_ok=True)
    print()
    for emu, items in found.items():
        blob = build_zip(items)
        if args.pack:
            dest = outdir / f"{emu}-saves.zip"
            dest.write_bytes(blob)
            print(f"  {emu:<12} → {dest}  ({human(len(blob))})")
        if args.push:
            print(f"  {emu:<12} → {args.push} … {push(args.push, emu, blob, ctx, cookie)}")
    if args.pack and not args.push:
        print("\nUpload each zip on the box: Save Manager → the system → 'Restore full backup',")
        print("or run again with --push https://<box-ip>:8443/saves")


if __name__ == "__main__":
    main()
