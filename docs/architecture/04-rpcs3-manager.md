# 4 — rpcs3-manager (:8771, `/rpcs3`)

Configurer les jeux PS3 à distance, dans le vocabulaire de RPCS3 : config par
jeu, patches, et installation de `.pkg`. `server.py` 726 l., plus `ryaml.py`
(50) et `schema.py` (96).

## Trouver RPCS3

Le boîtier peut faire tourner RPCS3 en Flatpak ou en build natif, et l'addon
doit éditer la config de **l'installation que le boîtier lance réellement**.

| Fonction | Rôle |
|---|---|
| `_declared_path()` | le `path` de l'entrée `rpcs3` dans le `systems.json` du boîtier |
| `config_dir()` | surcharge par variable d'environnement, puis le dossier impliqué par cette installation |
| `rpcs3_cmd(extra_env)` | l'argv qui lance le RPCS3 configuré, ou `None` |
| `backup(path)` | `.bak` horodaté avant de toucher un fichier existant |
| `yload(path)` | lit un fichier YAML via `ryaml` |

## `ryaml.py`

**Le module le plus important de cet addon.** Les fichiers de config et de
patches de RPCS3 sont remplis de scalaires que YAML 1.1 réinterprète
silencieusement :

| Dans le fichier | Ce qu'en fait PyYAML | Ce dont RPCS3 a besoin |
|---|---|---|
| `On` / `Off` | `True` / `False` | les chaînes `On` / `Off` |
| `1.10` | flottant `1.1` | la chaîne `1.10` |
| `No` | `False` | `No` |

Faire un aller-retour d'une config à travers PyYAML standard la **corrompt**
donc, et RPCS3 répond en ignorant le fichier sans un mot. `_StrLoader`
surcharge `compose_node` pour que chaque scalaire reste une chaîne ;
`_StrDumper` les réécrit sans guillemets comme le fait RPCS3. `load(text)` /
`dump(data)` sont les seuls points d'entrée.

> Ne « simplifiez » pas ceci en `yaml.safe_load`. Ça a été tenté ; ça casse
> toutes les configs personnalisées du boîtier.

Ceinture et bretelles par-dessus : `_edit_config_text(text, section, key, value)`
fait une **édition de ligne chirurgicale** — change ou insère une clé dans une
section, sans toucher au reste. `_fmt_value(value)` formate un scalaire comme
RPCS3 l'écrit (avec `"Null"` comme exception documentée).

## `schema.py` — la surface de configuration retenue

`f(section, key, type_, **kw)` construit un champ ; le module est une liste de
ces champs, calquée sur les onglets de l'interface RPCS3. Les chaînes d'énumération
sont les sérialisations **exactes** attendues par RPCS3 — une faute de frappe
produit une config que l'émulateur ignore sans erreur. `GET /api/schema` la
transmet à l'interface, qui rend le formulaire de façon générique.

## Routes

### Jeux

| Route | Fonction | Notes |
|---|---|---|
| `GET /api/health` | `health()` | |
| `GET /api/games` | `list_games()` | balaye `emu/rpcs3/`, lit titre et série depuis `PARAM.SFO` via `shared/py/sfo.py` |
| `GET /api/games/{serial}/icon` | `game_icon(serial)` | `PS3_GAME/ICON0.PNG` |
| `DELETE /api/games/{serial}` | `remove_game(serial, data)` | retire la ligne du jeu du `games.yml` de RPCS3 |

Utilitaires : `_disc_sfo(game_path)`, `_check_serial(serial)` (valide le segment
de chemin) et `_game_versions(cfg, serial, base_ver)` — les versions
applicatives effectives, `APP_VER` du disque plus toute mise à jour installée.

### Config par jeu

| Route | Fonction |
|---|---|
| `GET /api/games/{serial}/config` | `get_game_config(serial)` |
| `PUT /api/games/{serial}/config` | `put_game_config(serial, body)` — `ConfigBody` |
| `DELETE /api/games/{serial}/config` | `reset_game_config(serial)` |

`_custom_path(serial)` localise le YAML par jeu dans le dossier de configs
personnalisées de RPCS3. `_get_path(tree, segments)` /
`_set_path(tree, segments, value)` parcourent l'arbre imbriqué ;
`_schema_values(tree)` le projette sur le schéma pour l'interface.

> Les configs par jeu vivent dans le dossier de configuration propre à RPCS3 —
> celui que l'émulateur lit au lancement. Lequel c'est dépend du couple
> Flatpak/natif, d'où `config_dir()`.

### Patches

| Route | Fonction |
|---|---|
| `GET /api/games/{serial}/patches` | `game_patches(serial)` |
| `POST /api/games/{serial}/patches/toggle` | `toggle_patch(serial, body)` — `ToggleBody` |
| `POST /api/patches/download-official` | `download_official_patches()` |
| `POST /api/patches/upload` | `upload_patch(file)` |

- `_patch_files(serial)` — quels fichiers de patch s'appliquent à une série.
- `_enabled_key(tree)` — **dérive de version** : le RPCS3 du boîtier écrit
  `Enabled`, les sources plus récentes disent `enabled`. Ceci choisit ce que le
  fichier utilise au lieu de deviner.
- `_validate_patch_yaml(tree)` — compte les vraies entrées de patch (une liste
  `Patch` plus une table `Games`) avant d'accepter un envoi, pour qu'un fichier
  YAML égaré ne soit pas fusionné comme un patch.
- `upload_patch()` valide puis fusionne dans `imported_patch.yml`.

### Installation de `.pkg`

Les mises à jour et DLC arrivent en fichiers `.pkg`, et RPCS3 les installe via
son interface graphique. L'addon pilote cela sans interface :

| Fonction | Rôle |
|---|---|
| `_discover_display()` | l'environnement (`DISPLAY` + `XAUTHORITY`) de la session X active du boîtier |
| `_hdd_snapshot()` | listing de `dev_hdd0/game` avant l'installation |
| `_watch_install(proc, dest, before)` | détecte l'arrivée de l'installation, puis ferme RPCS3 |
| `_finish_job(proc, dest, before)` | comptabilité de fin de tâche |
| `GET /api/pkg/status` → `pkg_status()` | interrogation depuis l'interface |
| `POST /api/pkg/install` → `install_pkg(file)` | démarre la tâche |

L'approche par différence d'instantanés existe parce que RPCS3 ne donne aucun
signal exploitable par une machine indiquant qu'un `.pkg` est terminé :
l'addon surveille le dossier à la place, puis termine l'émulateur lui-même.
