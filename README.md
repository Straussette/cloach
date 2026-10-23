# Cloach — le coaching by Claude

Coach sportif personnel : course à pied, trail, vélo, renforcement, natation. Le coaching se passe dans **le Claude de chaque utilisateur** (claude.ai, appli mobile ou de bureau), sur son propre abonnement, grâce à un connecteur MCP.

- **App** : https://straussette.github.io/cloach/ (`docs/index.html`, publié par GitHub Pages). Compte, profil sportif, plan d'entraînement, journal des séances, import de l'historique Garmin, statistiques (distance, durée, dénivelé, charge, forme et fatigue), branchement de Garmin et de Claude.
- **Serveur MCP** (`serveur/`, Python, hébergé sur Render) : le connecteur que chacun ajoute dans Claude. Il donne à Claude le profil, le plan et le journal de l'utilisateur, ses données Garmin, des consignes de coach et son historique d'activités (détail et statistiques), et quatre prompts (« Que faire aujourd'hui ? », « Créer ou réajuster mon plan », « Analyser mon historique », « Bilan de la semaine »).
- **Supabase** (projet `cloach`, schéma dans `supabase/schema.sql`) : comptes, données de chaque compte (table `donnees`), historique des activités (table `activites`), jetons Garmin chiffrés (table `garmin_comptes`).

Personne ne paie l'IA pour les autres : l'app et le serveur n'appellent jamais l'API Claude.

## Comment ça marche

1. L'utilisateur crée son compte dans l'app, remplit son profil et, s'il le veut, branche Garmin (e-mail, mot de passe, code de double authentification). Le mot de passe Garmin n'est stocké nulle part : seuls les jetons d'accès le sont, chiffrés.
2. Dans claude.ai → Paramètres → Connecteurs, il ajoute un connecteur personnalisé avec l'URL `https://cloach.onrender.com/mcp` et se connecte avec son compte Cloach (OAuth, voir `serveur/acces.py`).
3. Il parle à son coach dans Claude. Claude lit son contexte, ses données Garmin, et enregistre le plan et les séances : l'app les affiche aussitôt (table `donnees`, partagée).

## Mise en route

### Supabase
1. Exécuter `supabase/schema.sql` (déjà fait sur le projet `cloach`).
2. Authentication → URL Configuration : Site URL `https://straussette.github.io/cloach/`, et l'ajouter aux Redirect URLs (liens de confirmation et de mot de passe oublié).
3. Récupérer la clé secrète (Project Settings → API Keys → `sb_secret_…`) pour Render.

### Render
1. « New » → « Blueprint » → ce dépôt (`render.yaml` est fourni). `CLE_SIGNATURE` est générée automatiquement.
2. Renseigner `SUPABASE_CLE_SECRETE`.
3. Une fois « Live », `https://cloach.onrender.com/sante` affiche `ok` et l'adresse du connecteur. Si le service a un autre nom, mettre son adresse dans `SERVEUR` (`docs/index.html`).

| Variable | Rôle |
|---|---|
| `CLE_SIGNATURE` | **obligatoire**, 32 caractères minimum : signe les jetons donnés à Claude et chiffre les jetons Garmin. La changer déconnecte tout le monde et oblige chacun à rebrancher Garmin. |
| `SUPABASE_URL`, `SUPABASE_CLE` | projet Supabase et sa clé publique (`sb_publishable_…`) |
| `SUPABASE_CLE_SECRETE` | clé secrète (`sb_secret_…`) : le serveur lit et écrit les données de chaque compte |
| `ORIGINE_APP` | origine de l'app, par défaut `https://straussette.github.io` |
| `URL_PUBLIQUE` | adresse publique du serveur ; inutile sur Render |
| `FUSEAU` | fuseau horaire des dates, par défaut `Europe/Paris` |

Sur l'offre gratuite de Render, le service s'endort après 15 min d'inactivité : le premier appel prend environ 30 s (l'app le réveille dès l'ouverture).

### GitHub Pages
Settings → Pages → « Deploy from a branch » → `main`, dossier `/docs`.

## En local

```bash
cd serveur
pip install -r requirements.txt
CLE_SIGNATURE='une-cle-locale-de-32-caracteres-minimum' SUPABASE_URL=… SUPABASE_CLE=… SUPABASE_CLE_SECRETE=… python serveur.py
```

L'app se lance avec `python3 -m http.server 8000` dans `docs/` (les appels au serveur échouent en local, car il n'accepte que l'origine de l'app).

Garmin passe par la bibliothèque non officielle [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) : Garmin ne propose pas d'API pour les particuliers, ça peut cesser de marcher si Garmin change son site.
