# Profil avancé — dossier de conception

Statut : à instruire (aucun code écrit). Point de départ : l'analyse du trail de Montmartre du 29 octobre 2026 (Fenix 8 AMOLED), faite à la main dans Claude, qu'on veut rendre automatique et suivie dans le temps.

## Objectif

Donner à l'athlète une photo de son moteur et de sa foulée **par terrain** (plat, montée, descente), suivie sur plusieurs semaines, et à Claude de quoi **diagnostiquer les écarts** et proposer des séances ciblées pour chaque terrain.

Ce n'est pas un outil de dosage : les séances restent écrites en zones FC, allures ou puissance au choix de Claude. Le profil avancé sert à comprendre et à progresser.

## Ce qu'on affiche

### 1. Les trois terrains côte à côte

Une carte par terrain, calculée sur une fenêtre glissante (par défaut 8 semaines, course + trail) :

| Mesure | Plat (pente entre −2 % et +2 %) | Montée (> +2 %) | Descente (< −2 %) |
|---|---|---|---|
| Temps passé, distance | ✓ | ✓ | ✓ |
| Puissance moyenne, W/kg | ✓ | ✓ | ✓ |
| Efficacité : vitesse (m/s) par W/kg | ✓ (indicateur clé) | — | — |
| VAP moyenne (min/km) | ✓ | ✓ | ✓ |
| VAM (m/h) | — | ✓ | — |
| FC moyenne, ratio puissance / FC | ✓ | ✓ | ✓ |
| Cadence, temps de contact, foulée, ratio vertical | ✓ | ✓ | ✓ |

La montée se découpe aussi en tranches de pente (2-6 %, 6-12 %, > 12 %), parce que la VAM et la foulée en dépendent beaucoup.

### 2. La foulée selon la pente

Un graphique cadence et longueur de foulée en fonction de la pente (tranches de 2 %). Il montre d'un coup d'œil le schéma vu le 29 octobre : cadence presque constante (160-170), foulée qui s'allonge en descente et raccourcit en montée sans que la cadence monte.

### 3. Les montées

Liste des montées détectées (D+ ≥ 20 m), sortie par sortie : pente, durée, D+, puissance moyenne, puissance par tiers (début / milieu / fin), FC moyenne et max, VAM. Repère le **fléchissement** (dernier tiers < premier tiers de plus de 5 %) et la **dérive** d'une montée à l'autre dans la même sortie.

### 4. Courbe puissance-durée

Meilleure puissance sur 1, 3, 5, 10, 20, 30 et 60 min, en deux courbes : en montée et à plat. Le point 20-30 min donne une estimation du seuil de puissance en course (à défaut de valeur fournie par Garmin).

### 5. Zones

- Zones FC : celles de Garmin (profil de l'utilisateur), sinon à partir de la FC max.
- Zones de puissance course : en % du seuil de puissance (estimé ci-dessus, ou saisi dans le profil).
- Pour chaque terrain, le temps passé dans chaque zone.

### 6. Le diagnostic

Des constats calculés par règles simples, affichés en une phrase chacun, par exemple :

- cadence en montée raide < cadence à plat − 5 pas/min → « Tu allonges les poussées en montée au lieu de raccourcir les pas » ;
- foulée en descente > foulée à plat + 10 % et cadence non plus haute → « Grands pas en descente : plus de freinage et d'impact » ;
- VAP en montée nettement meilleure qu'en descente → « Tu montes mieux que tu ne descends » ;
- efficacité à plat qui progresse sur 8 semaines → « Tu cours plus vite pour les mêmes watts ».

Puis le commentaire de Claude (champ `analyseProfil`, écrit par un nouvel outil, comme `analyse` pour l'onglet Stats), avec les séances qu'il propose pour chaque terrain.

## D'où viennent les données

Les détails d'activité Garmin (`get_activity_details`, déjà appelé par `vap.py`) donnent les mesures point par point. On a déjà distance et altitude ; il faut ajouter, s'ils sont présents dans `metricDescriptors` :

- puissance (`directPower`), FC (`directHeartRate`), vitesse (`directSpeed`) ;
- cadence (`directRunCadence`), temps de contact au sol, longueur de foulée, oscillation verticale, ratio vertical.

**À vérifier sur une vraie sortie** : les noms exacts des clés pour la dynamique de course (Fenix 8 au poignet, ou ceinture HRM), et la résolution obtenue avec `maxChartSize = 4000` (une sortie de 3 h risque d'être sous-échantillonnée).

Strava (en pause) fournit puissance, FC, cadence, altitude et pente par ses flux, mais pas la dynamique de course : le profil serait alors partiel.

## Calcul (serveur)

Nouveau module `serveur/profil_avance.py`, sur le modèle de `vap.py` (fonctions pures, sans accès réseau, faciles à tester) :

1. `_series()` : lire toutes les mesures disponibles point par point.
2. Pente lissée par tronçons de 25 m (même méthode que la VAP), puis classement de chaque point par terrain et tranche de pente. Points à l'arrêt exclus (vitesse < 0,5 m/s).
3. `resume_activite()` : par terrain et tranche, totaux et moyennes pondérées par le temps ; montées détectées ; meilleures puissances par durée ; temps par zone.
4. `profil()` : agrège les résumés des activités de la fenêtre et calcule les règles du diagnostic.

On calcule le résumé **une fois par activité**, à l'import ou à la demande (comme la VAP), et on le garde : refaire l'appel à Garmin pour 8 semaines de sorties à chaque affichage serait trop lent et risquerait de faire bloquer le serveur par Garmin.

## Stockage

Nouvelle table `analyses_activites` (une ligne par activité analysée) :

```sql
create table if not exists public.analyses_activites (
  user_id  uuid not null references auth.users (id) on delete cascade,
  id       text not null,             -- même id que dans activites
  version  int  not null,             -- version du calcul, pour recalculer si la méthode change
  resume   jsonb not null,            -- terrains, tranches, montées, puissance-durée, zones
  maj      timestamptz not null default now(),
  primary key (user_id, id)
);
alter table public.analyses_activites enable row level security;
create policy "analyses_lire" on public.analyses_activites
  for select to authenticated using (user_id = (select auth.uid()));
```

Le profil agrégé n'est pas stocké : il se recalcule à partir des résumés (quelques dizaines de lignes).

Le document `donnees` gagne `profil.poids` (kg, indispensable pour les W/kg), `profil.seuilPuissanceCourse` (W, facultatif) et `analyseProfil: { date, texte }`. À mettre à jour dans l'app **et** le serveur, ainsi que dans la description du document de `CLAUDE.md`.

## Routes et outils

- Route `/garmin/analyse` (sur une activité) et analyse en lot des activités de la fenêtre qui n'ont pas encore de résumé, avec une pause entre deux appels à Garmin.
- Outil MCP `profil_avance` : renvoie le profil agrégé et le diagnostic à Claude.
- Outil MCP `enregistrer_analyse_profil` : Claude y écrit son commentaire et ses propositions de séances.
- Consignes de coach (`CONSIGNES`) : quand Claude propose un plan, s'appuyer sur le profil avancé pour cibler le terrain le plus faible au regard de l'objectif.

Rien de tout ça n'appelle l'API Claude : le diagnostic par règles tourne sur le serveur, le commentaire est écrit par Claude dans la conversation de l'utilisateur.

## Affichage (app)

Section « Profil avancé » dans l'onglet Stats (proposition, voir questions) : les trois cartes terrain, puis la foulée selon la pente, la courbe puissance-durée, les montées récentes, le diagnostic et le commentaire de Claude, avec le lien « 🗣️ Demander l'analyse du coach ».

Conventions de l'app à respecter : couleurs via les variables de `:root` (dont `--serie-*` pour les graphiques), détail au toucher sous le graphique (`.detail-graph`), `esc()` sur toute valeur insérée, version de l'app à augmenter.

## Étapes proposées

1. **Calcul** : `profil_avance.py` + tests sur les détails réels du trail du 29 octobre (vérifier qu'on retrouve les montées, la puissance par tiers et les VAM déjà calculées à la main).
2. **Stockage et serveur** : table, analyse à l'import et en lot, outils MCP, champ `poids` dans le profil.
3. **App** : section Profil avancé.
4. **Coaching** : consignes et outil d'analyse pour Claude, propositions de séances par terrain.

## Questions ouvertes

- Où vit l'écran : dans Stats (là où sont déjà les analyses), ou dans Profil comme le nom le suggère ?
- Fenêtre d'agrégation : 8 semaines par défaut, avec un sélecteur 4 / 8 / 12 semaines ?
- Course et trail ensemble, ou séparés ? (Ensemble donne plus de données en montée et descente ; séparés évite que les escaliers de Montmartre faussent le plat.)
- Seuil de puissance course : estimé seulement, ou aussi saisi à la main ?
- Le vélo a-t-il sa propre version (puissance par pente, courbe puissance-durée vélo) dans un second temps ?
