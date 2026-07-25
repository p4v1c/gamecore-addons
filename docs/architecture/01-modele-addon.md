# 1 — Le modèle d'addon

## Des services, pas des greffons

Un addon est **un service HTTP indépendant doté d'une interface web statique**.
Le cœur n'importe jamais de code d'addon ; un addon n'importe jamais de code du
cœur. Ils se rencontrent en HTTP loopback et via un fichier de registre JSON.

Ce choix apporte trois choses :

| Propriété | Parce que |
|---|---|
| Le plantage d'un addon ne peut pas faire tomber la TV | processus distinct, unit systemd distincte, `Restart=on-failure` |
| Un addon peut être écrit dans n'importe quoi | le contrat est HTTP + un manifeste, pas une API Python |
| Le clone est ce qui tourne | aucune étape de build, donc `git log` sur le boîtier dit exactement ce qui est déployé |

Le coût : tout ce qui est partagé doit être *copié* plutôt qu'importé — voir
[2](02-cycle-de-vie-et-registre.md#composants-partagés).

## Le contrat de fichiers

Chaque dossier d'addon contient exactement :

| Fichier | Rôle |
|---|---|
| `addon.json` | le manifeste |
| `install.sh` | idempotent : venv, dépendances, copies partagées, unit systemd, activation + démarrage |
| `uninstall.sh` | arrêt, désactivation, suppression de l'unit |
| `requirements.txt` | dépendances Python épinglées |
| `server.py` | l'application FastAPI |
| `web/index.html` | toute l'interface, sans étape de build |

Copiez `addons/_template/` pour démarrer. Son `server.py` est le minimum qui
fonctionne :

```python
ADDON_DIR = Path(__file__).parent
PORT = int(os.environ.get("ADDON_PORT", 8799))

app = FastAPI(title="GameCore addon — template",
              root_path=os.environ.get("ADDON_BASE", ""))

@app.get("/api/health")
def health():
    return {"ok": True}

app.mount("/", StaticFiles(directory=str(ADDON_DIR / "web"), html=True), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
```

Quatre éléments là-dedans sont le contrat, pas de la décoration :

1. **`host="127.0.0.1"`** — un addon sur `0.0.0.0` est un gestionnaire de
   fichiers sans authentification exposé au LAN.
2. **`root_path=os.environ["ADDON_BASE"]`** — l'application est servie sous
   `/roms`, `/saves`, `/rpcs3` par Caddy. Sans cela, chaque URL générée est
   fausse.
3. **`ADDON_PORT` depuis l'environnement**, avec le port du manifeste par
   défaut.
4. **`web/` monté en dernier**, `html=True`, pour que les chemins inconnus
   retombent sur la SPA et que `/api/*` garde la priorité.

## `addon.json`

```jsonc
{
  "name": "save-manager",          // nom du dossier, de l'unit, clé du registre
  "label": "Saves",                // affiché dans la barre de nav et sur la TV
  "description": "Browse, back up, restore & delete every emulator's saves…",
  "version": "2.0.4",
  "type": "web",                   // web | service | tool
  "default": false,                // true → installé par l'installeur graphique
  "service": "user",               // systemd --user
  "port": 8772,
  "offline_assets": ["wheels"],    // charge utile pour les installations hors ligne
  "path": "/saves"                 // le préfixe proxifié par Caddy → ADDON_BASE
}
```

| `type` | Ce que c'est | Dans la barre de nav |
|---|---|---|
| `web` | interface web sur son port + service systemd | oui |
| `service` | démon sans interface (veilleur, passerelle…) | non |
| `tool` | script ponctuel / réglage système, pas de service | non |

Tout addon installé, quel que soit son type, atterrit dans le registre
(`$GAMECORE_PATH/config/addons.json`) et apparaît sur l'écran Addons de la TV.
Seuls les addons `web` obtiennent un lien.

## Ports

8770-8799 sont réservés aux addons.

| Addon | Port | `path` |
|---|---|---|
| rom-manager | 8770 | `/roms` |
| rpcs3-manager | 8771 | `/rpcs3` |
| save-manager | 8772 | `/saves` |
| _template | 8799 | — |

> Le port apparaît **deux fois** : dans `addon.json` et en `PORT=` dans
> `install.sh` (qui l'inscrit dans le `Environment=ADDON_PORT=` de l'unit).
> Rien ne vérifie qu'ils concordent. Ils le doivent.

## Ce qu'un addon peut et ne peut pas faire

**Peut :**

- lire le système de fichiers du boîtier — ROMs, données d'émulateurs,
  `config/systems.json` pour savoir où se trouvent les choses ;
- appeler le cœur en loopback : `http://127.0.0.1:8765/api/…` ;
- pousser un événement vers la TV avec
  `POST /api/addons/notify {"event": "...", "data": {...}}`, que le cœur relaie
  sur son WebSocket ;
- lire `/gc/addons` pour découvrir ses voisins (même origine, sans auth).

**Ne peut pas :**

- importer des modules Python du cœur — ils ne sont pas sur son chemin et le
  cycle de release du cœur est indépendant ;
- écrire du code d'authentification — c'est le domaine de Caddy ;
- écouter ailleurs qu'en loopback ;
- supposer qu'une étape de build existe.

## Environnement d'exécution

L'unit systemd écrite par `install.sh` fournit :

| Variable | Signification |
|---|---|
| `GAMECORE_PATH` | la racine du cœur — comment l'addon trouve `config/`, `emu/`, `assets/` |
| `ADDON_PORT` | le port d'écoute |
| `ADDON_BASE` | le préfixe de chemin → `root_path` |

avec `WorkingDirectory=${ADDON_DIR}` et
`ExecStart=${ADDON_DIR}/.venv/bin/python server.py`. Ce répertoire de travail
est la raison pour laquelle `import sfo` trouve la copie posée à côté de
`server.py`.

Journaux : `journalctl --user -u gamecore-addon-<nom> -f`.
