# gamecore-addons — Règles de sécurité

Le plan complet est dans
[GamecoreRenew/docs/SECURITY.md](https://github.com/p4v1c/GamecoreRenew/blob/main/docs/SECURITY.md).
Résumé : un seul port exposé au LAN (Caddy `:8443`, HTTPS + login partagé via
`forward_auth`), tout le reste en loopback. Les addons sont servis derrière des
préfixes de chemin (`/roms/`, `/saves/`, `/rpcs3/`) sur une origine unique.

## Règles pour tout addon

1. **Loopback uniquement** : `uvicorn.run(app, host="127.0.0.1", port=PORT)`.
   Jamais `0.0.0.0` — c'est Caddy qui expose au LAN.
2. **Pas de CORS** : derrière Caddy, tout est same-origin. Aucun
   `CORSMiddleware`.
3. **Aucune ligne d'auth** dans les addons : Caddy applique le login en amont
   (`forward_auth` vers le core). L'addon reçoit l'identité via l'en-tête
   `X-GC-User`, c'est tout.
4. **Aucun port ni hôte hardcodé côté client** (Phase 4) : URLs relatives dans le
   HTML/JS, nav partagée basée sur `location.origin` + `/gc/addons`. L'app FastAPI
   prend `root_path=os.environ.get("ADDON_BASE", "")` et l'`install.sh` passe
   `ADDON_BASE=/<prefix>` dans l'unit ; `addon.json` déclare ce préfixe dans son
   champ `path`.
5. Si le navigateur a besoin d'une ressource du cœur, il passe par les statiques
   proxifiés (`/assets/*`) ou par un endpoint passthrough de l'addon (l'API `/api/*`
   du cœur n'est jamais exposée au LAN).

## Phases côté addons

- **Phase 1 (cette PR)** : bind `127.0.0.1` + suppression des CORS wildcard dans
  rom-manager, rpcs3-manager, save-manager et `_template`.
- **Phase 4** : `root_path`/`ADDON_BASE`, champ `path` dans `addon.json`, nav
  partagée par chemins, audit complet des URLs absolues dans le HTML/JS.
