# 6 — Sécurité & pièges

Modèle de menace complet dans [`../SECURITY.md`](../SECURITY.md). Voici ce qui
compte pendant qu'on écrit du code.

## Les quatre règles

**1. Écouter sur `127.0.0.1`. Toujours.**
Chaque `uvicorn.run` de ce dépôt le fait. Un addon sur `0.0.0.0` est un
gestionnaire de fichiers sans authentification exposé au LAN — et ces addons
suppriment des sauvegardes et écrivent dans les dossiers de ROMs.

**2. N'écrire aucun code d'authentification.**
Caddy filtre chaque chemin proxifié via le `forward_auth` du cœur. Quand une
requête atteint un addon, elle est authentifiée, et l'utilisateur est dans
l'en-tête `X-GC-User`. Un addon qui ajoute son propre login en ajoute un
second, plus faible et non synchronisé.

**3. `root_path` vient de `ADDON_BASE`.**
Ne jamais coder en dur un port ou une URL absolue dans l'interface web.
Utiliser des `fetch` relatifs, ou `/gc/addons` pour découvrir les voisins. Un
port en dur casse dès que Caddy déplace un addon derrière un préfixe — ce qui
est le déploiement normal.

**4. Jamais `shell=True`, jamais `eval`.**
Rien dans ce dépôt ne fait ni l'un ni l'autre. Que ça reste ainsi.

## Le motif de validation des chemins

Tout chemin qui arrive dans une requête est contrôlé par un tiers, LAN ou pas.
La séquence, dans cet ordre :

```python
rel = PurePosixPath(member.filename)
# 1. rejeter l'évident, avant que quoi que ce soit ne touche le disque
if rel.is_absolute() or ".." in rel.parts or not rel.parts:
    raise HTTPException(400, "zip contains an unsafe path")

# 2. le membre doit appartenir à une collection connue de cet émulateur
if not any(s == "" or rel.as_posix().startswith(s + "/") for s in subpaths):
    raise HTTPException(400, f"'{member.filename}' doesn't belong to any save folder…")

# 3. revérifier le confinement sur la destination résolue, avant d'écrire
dest = (base / member.filename).resolve()
try:
    dest.relative_to(base.resolve())
except ValueError:
    raise HTTPException(400, "zip contains an unsafe path")

# 4. sauvegarder ce qu'on s'apprête à écraser
_backup(base / unit)
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_bytes(zf.read(member))
```

L'étape 1 n'est pas redondante avec l'étape 3. L'étape 3 seule laisserait
encore un membre s'échapper par un lien symbolique déjà présent sur le disque,
et l'étape 1 seule raterait des chemins qui se résolvent en dehors sans
contenir `..`. `upload_full()` dans `save-manager/server.py` est
l'implémentation de référence — à copier plutôt qu'à réinventer.

La même forme protège les envois de dossier de `rom-manager`
(`safe_relpath`) et ses envois de fichier unique (`safe_filename`).

> `safe_filename` ne retire délibérément **que** `/` et NUL. Les noms de jeux
> contiennent espaces, crochets, apostrophes et unicode, et les massacrer casse
> la recherche de jaquette sur la TV. Assainir pour la sécurité, pas pour le
> rangement.

## Sauvegarder avant de détruire

Chaque opération destructive fait d'abord un instantané :

- `save-manager/_backup(path, prune)` avant restauration, écrasement et
  suppression ;
- `rpcs3-manager/backup(path)` avant d'éditer une config ou un fichier de
  patch.

L'unité de sauvegarde est choisie avec soin : **l'entrée dans sa collection**,
pas le premier composant du chemin. Sauvegarder tout `dev_hdd0` pour une seule
sauvegarde RPCS3 copierait des gigaoctets.

Restaurer un backup sauvegarde d'abord l'état courant : l'opération est donc
réversible.

## Souvenir d'un vrai bug

Le `.gitignore` liste encore `addons/local-coop/state/` pour un addon qui n'a
jamais été commité. Inoffensif, mais c'est un rappel : le README a longtemps
annoncé `local-coop` avec un lien qui renvoyait 404 et une commande
d'installation qui ne pouvait pas fonctionner. Si vous ajoutez une ligne au
tableau des addons, le dossier doit exister.

---

## Pièges

**Les fichiers de `shared/` sont des copies sur disque.**
Éditer `addons/save-manager/sfo.py` ne change rien en amont et sera écrasé à la
prochaine mise à jour. Éditez `shared/py/sfo.py`.

**Les ports vivent à deux endroits.**
`addon.json` et la ligne `PORT=` d'`install.sh`. Rien ne vérifie qu'ils
concordent.

**`install.sh` doit être idempotent.**
`gamecore-addon update` le réexécute à chaque mise à jour. Tout ce qui ne
fonctionne que sur un boîtier vierge est un bug.

**Le clone est le déploiement.**
Aucune étape de build. Une erreur de syntaxe dans `web/index.html` part
immédiatement en production, et les modifications locales sur le boîtier sont
perdues au prochain `git pull`.

**Le YAML de RPCS3 n'est pas du YAML.**
`On`/`Off` deviennent des booléens et `1.10` un flottant sous PyYAML standard,
ce qui corrompt silencieusement toutes les configs personnalisées. Utilisez
`ryaml.py`.

**Les noms de clés RPCS3 dérivent entre versions.**
`_enabled_key(tree)` existe parce que le RPCS3 du boîtier écrit `Enabled` alors
que les sources récentes disent `enabled`. Lisez ce que le fichier utilise ; ne
supposez pas.

**Les dossiers de sauvegarde Ryujinx ne sont pas des identifiants de titre.**
C'est un compteur local à l'installation. Utilisez `ryujinx.title_map()` ; un
chemin copié depuis un autre boîtier ne correspondra pas.

**La carte GameCube conserve deux copies du répertoire.**
`_gc_active()` choisit la vivante par somme de contrôle et compteur. Écrire
l'autre perd des sauvegardes en silence.

**Un membre de zip est une entrée contrôlée par un tiers**, même depuis un LAN
amical.

**Les écritures de carte mémoire sont irréversibles pour l'utilisateur.**
Lancez `pytest addons/save-manager/tests` avant et après avoir touché à
`memcard.py`.

**Un cœur injoignable ne doit jamais faire échouer un envoi.**
`notify_core()` est au mieux — le fichier est déjà sur le disque ; faire
échouer la requête reviendrait à mentir à l'utilisateur.
