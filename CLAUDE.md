# CLAUDE.md

Ce fichier guide Claude Code lorsqu'il travaille sur ce dépôt.

## Projet

**Cloach**, « le coaching by Claude » : coach sportif personnel, en français. Le texte de l'interface, les noms dans le code, les commentaires et les messages de commit sont en français : il faut garder cette règle.

Le coaching se passe dans le Claude de chaque utilisateur, sur son propre abonnement, via un connecteur MCP. **Ni l'app ni le serveur n'appellent l'API Claude** : ne pas en ajouter sans que ce soit demandé, car le coût retomberait sur le propriétaire.

- `docs/index.html` : l'app (HTML + CSS + JavaScript sans framework, sans dépendance, sans build), publiée par GitHub Pages sur https://straussette.github.io/cloach/.
- `serveur/` : le serveur MCP (Python, SDK `mcp`, hébergé sur Render, voir `render.yaml`).
  - `serveur.py` : consignes de coach (`CONSIGNES`), outils (profil, plan, journal, Garmin), prompts, routes HTTP ;
  - `acces.py` : OAuth sans stockage ; la page de connexion vérifie le compte Cloach auprès de Supabase, et les jetons portent l'identifiant de l'utilisateur (`subject`) ;
  - `donnees.py` : accès à Supabase avec la clé secrète (filtrer toujours sur l'identifiant de l'utilisateur) ;
  - `garmin.py` : sessions Garmin par utilisateur et routes `/garmin/*` appelées par l'app.
- `supabase/schema.sql` : schéma du projet Supabase `cloach` (tables `donnees` et `garmin_comptes`).

## Commandes

- Syntaxe de l'app : `awk '/<script>/{f=1;next}/<\/script>/{f=0}f' docs/index.html > /tmp/app.js && node --check /tmp/app.js`
- Serveur en local : voir README.md (`python serveur.py` dans `serveur/`, avec les variables d'environnement).
- Déployer l'app : pousser sur `main`. Le serveur se redéploie aussi depuis `main` sur Render.

## Conventions

- **À chaque modification de `docs/index.html`, augmenter `<meta name="app-version" content="AAAA-MM-JJ.N">`** : `verifierMiseAJour()` force le rechargement quand la version en ligne diffère.
- Code découpé en sections `/* ---------- Titre ---------- */` (CSS et JS) ou `# ---------- Titre ----------` (Python).
- Couleurs uniquement via les variables CSS de `:root`, avec leur valeur en mode sombre (`prefers-color-scheme` et `[data-theme]`).
- Échapper avec `esc()` toute valeur insérée dans du HTML généré.

## Données

- L'état d'un compte est un seul document JSON (`donnees.data`) : `{ maj, profil, plan: { cree, resume, conseils, semaines: [{ debut, theme, seances: [{ id, jour, sport, titre, duree, intensite, details, etat }] }] }, journal: [{ id, date, sport, duree, distance, ressenti, notes }], garmin: { connecte, nom } }`.
- L'app et le serveur l'écrivent tous les deux ; `maj` (millisecondes) départage, la version la plus récente l'emporte. L'app relit avant chaque modification (`modifier()`) et au retour au premier plan. Si la forme du document change, mettre à jour l'app **et** le serveur.
- Valeurs de `sport` : `course`, `trail`, `velo`, `renfo`, `natation`, `autre`. Valeurs d'`intensite` : `repos`, `facile`, `modere`, `soutenu`, `tres-soutenu`. Valeurs d'`etat` : vide (à faire), `fait`, `saute`.
