# gamecore-addons — security rules

The full plan lives in
[GamecoreRenew/docs/SECURITY.md](https://github.com/p4v1c/GamecoreRenew/blob/main/docs/SECURITY.md).
Summary: a single LAN-facing port (Caddy `:8443`, HTTPS + shared login via
`forward_auth`), everything else on loopback. Addons are served behind path
prefixes (`/roms/`, `/saves/`, `/rpcs3/`) on a single origin.

## Rules for every addon

1. **Loopback only**: `uvicorn.run(app, host="127.0.0.1", port=PORT)`.
   Never `0.0.0.0` — Caddy is what exposes to the LAN.
2. **No CORS**: behind Caddy everything is same-origin. No
   `CORSMiddleware`, ever.
3. **Not one line of auth** in the addons: Caddy enforces the login upstream
   (`forward_auth` to the core). The addon receives the identity in the
   `X-GC-User` header, and that is all.
4. **No hardcoded port or host on the client side** (phase 4): relative URLs
   in HTML/JS, shared nav based on `location.origin` + `/gc/addons`. The
   FastAPI app takes `root_path=os.environ.get("ADDON_BASE", "")` and
   `install.sh` sets `ADDON_BASE=/<prefix>` in the unit; `addon.json` declares
   the prefix in its `path` field.
5. When the browser needs a core resource, it goes through the proxied
   statics (`/assets/*`) or a passthrough endpoint of the addon — the core's
   `/api/*` is never exposed to the LAN.
6. **A passthrough relays; it does not reimplement.** rom-manager's bezels are
   the example: `assets/overlays/` belongs to the core, and `api: 1` says an
   addon writes inside its own data directory and nowhere else. The addon
   POSTs to the core over loopback; the core decides the filename, the
   destination and the validation. Writing the PNG from the addon would have
   been one line shorter and would have turned the rule into "an addon writes
   wherever it can reach", for every addon. The core's response **status and
   body** are returned verbatim: a refusal carries a sentence explaining why,
   and swallowing it into a generic failure would lose it.

## Phases on the addon side

- **Phase 1**: bind `127.0.0.1` + removal of wildcard CORS in rom-manager,
  rpcs3-manager, save-manager and `_template`.
- **Phase 4**: `root_path`/`ADDON_BASE`, `path` field in `addon.json`, shared
  path-based nav, full audit of absolute URLs in HTML/JS.
