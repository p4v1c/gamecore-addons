# 6 — Security & traps

Full threat model in [`../SECURITY.md`](../SECURITY.md). This is what matters
while writing code.

## The four rules

**1. Bind `127.0.0.1`. Always.**
Every `uvicorn.run` in this repo does. An addon on `0.0.0.0` is an
unauthenticated file manager exposed to the LAN — and these addons delete
saves and write into ROM directories.

**2. Write no authentication code.**
Caddy gates every proxied path through the core's `forward_auth`. By the time
a request reaches an addon it is authenticated, and the user is in the
`X-GC-User` header. An addon that adds its own login is adding a second,
weaker, unsynchronised one.

**3. `root_path` comes from `ADDON_BASE`.**
Never hardcode a port or an absolute URL in the web UI. Use relative fetches,
or `/gc/addons` to discover siblings. Hardcoded ports break the moment Caddy
moves an addon behind a prefix — which is the normal deployment.

**4. Never `shell=True`, never `eval`.**
Nothing in this repo does either. Keep it that way.

## The path-validation pattern

Every path that arrives in a request is attacker-controlled, LAN or not. The
sequence, in this order:

```python
rel = PurePosixPath(member.filename)
# 1. reject the obvious, before anything touches the filesystem
if rel.is_absolute() or ".." in rel.parts or not rel.parts:
    raise HTTPException(400, "zip contains an unsafe path")

# 2. it must belong to a known collection of this emulator
if not any(s == "" or rel.as_posix().startswith(s + "/") for s in subpaths):
    raise HTTPException(400, f"'{member.filename}' doesn't belong to any save folder…")

# 3. re-check containment on the resolved destination, before writing
dest = (base / member.filename).resolve()
try:
    dest.relative_to(base.resolve())
except ValueError:
    raise HTTPException(400, "zip contains an unsafe path")

# 4. back up what you are about to overwrite
_backup(base / unit)
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_bytes(zf.read(member))
```

Step 1 is not redundant with step 3. Step 3 alone would still let a member
escape through a symlink already on disk, and step 1 alone misses paths that
resolve outside without containing `..`. `upload_full()` in
`save-manager/server.py` is the reference implementation — copy it rather than
reinventing.

The same shape guards `rom-manager`'s folder uploads (`safe_relpath`) and its
single-file uploads (`safe_filename`).

> `safe_filename` deliberately strips **only** `/` and NUL. Game names contain
> spaces, brackets, apostrophes and unicode, and mangling them breaks cover
> lookup on the TV. Sanitize for safety, not for tidiness.

## Backup before you destroy

Every destructive operation snapshots first:

- `save-manager/_backup(path, prune)` before restore, upload-over and delete;
- `rpcs3-manager/backup(path)` before editing a config or patch file.

The backup unit is chosen carefully: **the entry inside its collection**, not
the first path component. Backing up all of `dev_hdd0` for one RPCS3 save
would copy gigabytes.

Restoring a backup backs up the current state first, so the operation is
reversible.

## Memory of a real bug

The `.gitignore` still lists `addons/local-coop/state/` for an addon that was
never committed. Harmless, but a reminder: the README once advertised
`local-coop` with a link that 404'd and an `install` command that could not
work. If you add a row to the addon table, the directory has to exist.

---

## Traps

**`shared/` files are copies on disk.**
Editing `addons/save-manager/sfo.py` changes nothing upstream and is
overwritten on the next update. Edit `shared/py/sfo.py`.

**Ports live in two places.**
`addon.json` and the `PORT=` line in `install.sh`. Nothing checks that they
agree.

**`install.sh` must be idempotent.**
`gamecore-addon update` re-runs it on every update. Anything that only works
on a clean box is a bug.

**The checkout is the deployment.**
No build step. A syntax error in `web/index.html` ships instantly, and local
edits on the box are lost at the next `git pull`.

**RPCS3 YAML is not YAML.**
`On`/`Off` become booleans and `1.10` becomes a float under stock PyYAML,
which silently corrupts every custom config. Use `ryaml.py`.

**RPCS3 key names drift between versions.**
`_enabled_key(tree)` exists because the box's RPCS3 writes `Enabled` while
newer sources say `enabled`. Read what the file uses; do not assume.

**Ryujinx save directories are not title ids.**
They are an install-local counter. Use `ryujinx.title_map()`; a path copied
from another box will not match.

**The GameCube card keeps two directory copies.**
`_gc_active()` picks the live one by checksum and counter. Writing the other
one loses saves silently.

**A zip member is attacker-controlled input**, even from a friendly LAN.

**Memory-card writes are irreversible for the user.**
Run `pytest addons/save-manager/tests` before and after touching
`memcard.py`.

**An unreachable core must never fail an upload.**
`notify_core()` is best effort — the file already landed on disk; failing the
request would tell the user a lie.
