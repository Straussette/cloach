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
  - `garmin.py` : sessions Garmin par utilisateur et routes `/garmin/*` appelées par l'app (connexion, code, état, déconnexion, `importer` pour l'historique, un an au plus par appel).
  - `strava.py` : pour ceux qui n'ont pas Garmin, branchement par l'API officielle de Strava (OAuth : `/strava/lien`, retour sur `/strava/retour`, jetons chiffrés dans `strava_comptes`, renouvelés automatiquement) et `/strava/importer` vers la table `activites` (ids `strava-…`, `source` = `strava`, « effort relatif » à la place de la charge). Variables `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`.
- `supabase/schema.sql` : schéma du projet Supabase `cloach` (tables `donnees`, `activites`, `garmin_comptes` et `strava_comptes`).

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

- L'état d'un compte est un seul document JSON (`donnees.data`) : `{ maj, profil, plan: { cree, resume, conseils, semaines: [{ debut, theme, seances: [{ id, jour, sport, titre, duree, intensite, details, etat, activite, ressenti, commentaire, alternatives: [{ pour, sport, titre, duree, intensite, details }] }] }] }, journal: [{ id, date, sport, duree, distance, ressenti, notes }], commentaires: { [idActivite]: { ressenti, notes, date } }, garmin: { connecte, nom, dernierImport }, strava: { connecte, nom, dernierImport }, avisDuJour: { date, verdict, explication, id }, analyse: { date, texte } }`.
- L'app et le serveur l'écrivent tous les deux ; `maj` (millisecondes) départage, la version la plus récente l'emporte. L'app relit avant chaque modification (`modifier()`) et au retour au premier plan. Si la forme du document change, mettre à jour l'app **et** le serveur.
- `analyse` (`{ date, texte }`) dans le même document : le commentaire de Claude sur l'historique, écrit par l'outil `enregistrer_analyse` et affiché dans l'onglet Stats.
- Historique des activités : table `activites`, une ligne par activité (`id` Garmin, `jour`, `sport`, `type`, `distance_km`, `duree_min`, `denivele_m`, `charge`…). Le serveur l'écrit (import depuis Garmin, mapping dans `vers_activite()`), l'app la lit (onglet Stats), Claude la lit avec `historique` et `statistiques`. L'app importe année par année (`importerPeriode`, `importerTout`) puis ajoute les nouvelles activités à chaque ouverture (`mettreAJourHistorique`).
- Source des activités : `sourceActive()` (Garmin en priorité, sinon Strava ; jamais les deux, pour éviter les doublons). Import, mise à jour automatique et « Séance faite » passent par `appelerServeur(`${source}/importer`)`. La forme du jour et les meilleurs efforts restent réservés à Garmin. Strava est en pause : `STRAVA_ACTIF = false` masque sa carte dans Profil (l'application Strava n'est pas encore créée) ; le code serveur reste en place.
- Onglets : Calendrier (vue `jour` dans le code : une semaine à la fois, passé et avenir ; le passé montre les activités Garmin de la table `activites` et le journal, l'avenir le plan), Plan, Stats, Profil. Le journal n'a plus d'onglet : « + Ajouter une séance sans montre » sur un jour du Calendrier ouvre `dlgJournal`.
- Graphiques : un toucher (ou le survol à la souris) affiche le détail sous le graphique (`.detail-graph`) ; l'info-bulle flottante n'apparaît qu'à la souris. Pas d'appui long, pas de sélection de texte sur les graphiques.
- Records (onglet Stats, `afficherRecords()`) : de tous les temps (seul le filtre de sport s'applique) ; une sortie enregistrée en plusieurs morceaux (même jour, même famille, relancée moins de 30 min après) est recollée (`sorties()` dans l'app, `_sorties()` côté serveur) ; calculés à partir de la table `activites` (plus longue sortie, dénivelé, durée, allure ou vitesse, meilleure semaine, meilleur mois, plus longue série) ; toucher un record ouvre ce jour dans le Calendrier. En tête, les « meilleurs efforts » calculés par Garmin (route `/garmin/records`, outil `records_garmin`, correspondance des `typeId` dans `RECORDS_GARMIN` de `garmin.py`).
- Jour J : le Calendrier affiche la forme du jour (route `/garmin/forme` : sommeil, VFC, Body Battery, FC de repos, disponibilité), classée ok / attention / alerte par `signauxForme()` (icône + mot, jamais la couleur seule), et l'avis de Claude (`avisDuJour`, écrit par l'outil `avis_du_jour`, qui peut remplacer la séance en gardant l'originale dans ses alternatives sous « Prévu initialement »).
- Séance faite : le bouton d'état d'une séance ouvre la fenêtre « Séance faite » (`ouvrirSeance()`) : import Garmin du jour (route `importer` sur une journée), choix de l'activité, ressenti et commentaire. La séance garde `activite` (id Garmin) ; le commentaire va dans `commentaires[idActivite] = { ressenti, notes, date }` (ou `ressenti` / `commentaire` sur la séance s'il n'y a pas d'activité). Le bouton 💬 d'une activité du Calendrier commente une activité hors plan. Le serveur ajoute ces commentaires aux activités dans `contexte_du_jour` et `historique`.
- Allure et vitesse (`vitessesActivite()`) : allure moyenne (durée / distance, comme Garmin) pour la course et le trail ; en trail, VAP = allure ajustée à la pente, estimée au kilomètre-effort (100 m D+ = 1 km), car Garmin ne fournit pas d'allure ajustée dans ses résumés ; vitesse moyenne à vélo. Le serveur ajoute `allure_min_km` et `vap_min_km` aux activités envoyées à Claude.
- Alternatives : chaque séance peut en avoir 1 à 3 (`pour` : « Si fatigue », « En intérieur »…). « Choisir » échange la séance et l'alternative.
- Doublons journal / Garmin : une séance du journal qui correspond à une activité Garmin (même jour, même famille de sport, durée à 20 % ou distance à 10 % près, voir `correspond()`) est rattachée à l'activité dans le Calendrier (ressenti et notes affichés avec elle). Les consignes du serveur demandent à Claude de ne pas recopier les activités Garmin dans le journal.
- Onglet Stats : le filtre « Course + trail » (`GROUPES.pied`) réunit les deux sports, comme Strava. Avec « Tous les sports », totaux et barres sont répartis par famille (`FAMILLES` : course + trail, vélo, autres), en barres empilées.
- Couleurs des graphiques : `--serie-1` (course + trail, forme), `--serie-2` (vélo, fatigue) et `--serie-autre` (gris neutre, autres sports), validées (contraste, écart pour les daltoniens) en clair et en sombre.
- Valeurs de `sport` : `course`, `trail`, `velo`, `renfo`, `natation`, `autre`. Valeurs d'`intensite` : `repos`, `facile`, `modere`, `soutenu`, `tres-soutenu`. Valeurs d'`etat` : vide (à faire), `fait`, `saute`.
