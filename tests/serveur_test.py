"""Vérifications du serveur sans réseau : chargement de tous les modules, liste des outils MCP,
modèles des outils, et fonctions pures (garde-fous, cycle, séances types, conversion Garmin, zones).
Lancement : tests/verifier.sh (ou python tests/serveur_test.py avec les dépendances de serveur/)."""

import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "serveur"))
# Variables factices : aucun appel réseau n'est fait ici
os.environ.setdefault("CLE_SIGNATURE", "x" * 40)
os.environ.setdefault("SUPABASE_URL", "https://exemple.supabase.co")
os.environ.setdefault("SUPABASE_CLE", "publique")
os.environ.setdefault("SUPABASE_CLE_SECRETE", "secrete")

import cycle  # noqa: E402
import entrainements  # noqa: E402
import garde_fous  # noqa: E402
import methode  # noqa: E402
import polar  # noqa: E402
import serveur  # noqa: E402
import zones  # noqa: E402

ECHECS: list[str] = []


def verifier(nom: str, condition: bool) -> None:
    if not condition:
        ECHECS.append(nom)


AJD = date(2026, 11, 23)  # un lundi


def seance(jour: date, duree: int, intensite: str, sport: str = "course") -> dict:
    return {"jour": jour.isoformat(), "duree": duree, "intensite": intensite, "sport": sport}


# ---------- Outils MCP ----------
outils = {t.name: t for t in asyncio.run(serveur.mcp.list_tools())}
for nom in ("contexte_du_jour", "enregistrer_plan", "avis_seance", "avis_du_jour", "calendrier", "ajouter_evenement",
            "supprimer_evenement", "enregistrer_cycle", "noter_memoire", "methode", "seances_types", "courbe_activite",
            "structurer_seance", "envoyer_seance_garmin", "mes_zones"):
    verifier(f"outil {nom} présent", nom in outils)
verifier("toutes les descriptions d'outils sont remplies", all(t.description for t in outils.values()))
verifier("consignes du coach non vides", len(serveur.CONSIGNES) > 1000)

# ---------- Garde-fous ----------
plan = [{"debut": (AJD + timedelta(days=7 * k)).isoformat(),
         "seances": [seance(AJD + timedelta(days=7 * k + 1), 60, "soutenu"), seance(AJD + timedelta(days=7 * k + 3), 60, "facile"),
                     seance(AJD + timedelta(days=7 * k + 6), 90, "facile")]} for k in range(3)]
reelles = [{"jour": (AJD - timedelta(days=d)).isoformat(), "duree_min": 50} for d in range(1, 29) if d % 2]
verifier("plan raisonnable : aucun avertissement", garde_fous.verifier(plan, reelles, {}, AJD, []) == [])
dur = [{"debut": AJD.isoformat(), "seances": [seance(AJD, 60, "soutenu"), seance(AJD + timedelta(days=1), 60, "tres-soutenu")]}]
verifier("deux séances dures collées signalées", any("deux jours de suite" in a for a in garde_fous.verifier(dur, reelles, {}, AJD, [])))
gros = [{"debut": AJD.isoformat(), "seances": [seance(AJD + timedelta(days=i), 120, "facile") for i in range(7)]}]
verifier("hausse brusque du volume signalée", any("hausse" in a for a in garde_fous.verifier(gros, reelles, {}, AJD, [])))
evts = [{"nom": "Cross", "date": (AJD + timedelta(days=6)).isoformat(), "type": "C"},
        {"nom": "Semi", "date": (AJD + timedelta(days=13)).isoformat(), "type": "B"},
        {"nom": "Vacances", "date": (AJD + timedelta(days=17)).isoformat(), "fin": (AJD + timedelta(days=20)).isoformat(), "type": "indispo"}]
plan_evts = [{"debut": AJD.isoformat(), "seances": [seance(AJD + timedelta(days=5), 60, "soutenu")]},
             {"debut": (AJD + timedelta(days=7)).isoformat(), "seances": [seance(AJD + timedelta(days=11), 60, "soutenu")]},
             {"debut": (AJD + timedelta(days=14)).isoformat(), "seances": [seance(AJD + timedelta(days=18), 60, "facile")]}]
alertes = garde_fous.verifier(plan_evts, reelles, {}, AJD, evts)
verifier("course C : séance dure la veille signalée", any("Cross" in a for a in alertes))
verifier("course B : séance dure dans les 3 jours avant signalée", any("Semi" in a for a in alertes))
verifier("indisponibilité : séance prévue signalée", any("Vacances" in a for a in alertes))
verifier("objectif du profil sans calendrier : traité comme course A",
         isinstance(garde_fous.verifier(plan, reelles, {"dateObjectif": (AJD + timedelta(days=10)).isoformat()}, AJD), list))

# ---------- Calendrier (serveur) ----------
etat = {"profil": {"objectif": "Trail", "dateObjectif": "2027-05-08"}, "evenements": evts}
liste = serveur._evenements(etat)
verifier("objectif du profil ajouté comme course A", liste[-1]["type"] == "A" and liste[-1]["id"] == "profil")
verifier("calendrier trié par date", [e["date"] for e in liste] == sorted(e["date"] for e in liste))

# ---------- Cycle ----------
for semaines in (2, 6, 12, 30):
    phases = cycle.proposer(AJD + timedelta(weeks=semaines), AJD)
    verifier(f"cycle de {semaines} semaines : phases contiguës",
             all(date.fromisoformat(b["debut"]) == date.fromisoformat(a["fin"]) + timedelta(days=1) for a, b in zip(phases, phases[1:])))
    verifier(f"cycle de {semaines} semaines : finit par affûtage puis récupération", [p["nom"] for p in phases[-2:]] == ["affutage", "recuperation"])
c = {"objectif": {"date": (AJD + timedelta(weeks=12)).isoformat()}, "phases": cycle.proposer(AJD + timedelta(weeks=12), AJD)}
verifier("phase du jour trouvée", (cycle.phase_du(c, AJD) or {}).get("nom") == "foncier")

# ---------- Méthode et séances types : toutes convertibles pour Garmin ----------
verifier("méthode générale disponible", bool(methode.methode("general")))
types = methode.seances_types()
verifier("bibliothèque de séances non vide", len(types) >= 10)
for s in types:
    if s.get("etapes") and s.get("sport") in ("course", "trail", "velo"):
        try:
            entrainements.vers_garmin({**s, "titre": s.get("nom", "Séance"), "duree": s.get("duree", 60)}, ftp=250)
        except Exception as exc:  # noqa: BLE001
            ECHECS.append(f"séance type « {s.get('nom')} » non convertible pour Garmin : {exc}")
    if s.get("etapes"):
        try:
            serveur.Etape.model_validate(s["etapes"][0])
            [serveur.Etape.model_validate(e) for e in s["etapes"]]
        except Exception as exc:  # noqa: BLE001
            ECHECS.append(f"séance type « {s.get('nom')} » refusée par le modèle Etape : {exc}")

# ---------- Conversion Garmin : enchaînement et musculation ----------
tri = {"titre": "Enchaînement", "sport": "triathlon", "etapes": [
    {"type": "effort", "duree_s": 3600, "sport": "velo", "cible": {"type": "ftp", "min": 70, "max": 80}},
    {"type": "effort", "duree_s": 1200, "sport": "course", "cible": {"type": "zone_fc", "zone": 3}}]}
garmins, _ = entrainements.vers_garmin_tous(tri, ftp=250)
verifier("enchaînement vélo + course : deux entraînements Garmin", len(garmins) == 2)
muscu = entrainements.etapes_musculation([{"nom": "Squat", "series": 3, "repetitions": "8-10", "repos_s": 60}])
verifier("séance de renfo convertie", bool(muscu))

# ---------- Bibliothèque garminconnect : les méthodes appelées par le serveur existent toujours ----------
# (vérifie d'office les PR de Dependabot qui changent sa version)
import glob  # noqa: E402
import re  # noqa: E402

import garminconnect  # noqa: E402

DOSSIER = os.path.join(os.path.dirname(__file__), "..", "serveur")
methodes = {"login", "resume_login", "get_full_name"}
for fichier in glob.glob(os.path.join(DOSSIER, "*.py")):
    methodes |= set(re.findall(r'"((?:get|upload|update|delete|schedule|download)_[a-z_0-9]+)"', open(fichier).read()))
for m in sorted(methodes):
    verifier(f"garminconnect.Garmin.{m} existe", hasattr(garminconnect.Garmin, m))
client = getattr(garminconnect.Garmin("a@b.c", "x"), "client", None)
verifier("garminconnect : client.dumps() existe (sauvegarde de la session)", hasattr(client, "dumps"))

# ---------- Polar : conversion des sorties ----------
verifier("Polar : durée ISO 8601", polar.secondes("PT1H2M3.5S") == 3723.5 and polar.secondes("PT45M") == 2700)
verifier("Polar : durée vide", polar.secondes("") is None)
for sport, detail, attendu in (("RUNNING", "TRAIL_RUNNING", "trail"), ("RUNNING", "RUNNING", "course"),
                               ("RUNNING", "TREADMILL_RUNNING", "course"), ("CYCLING", "ROAD_BIKING", "velo"),
                               ("CYCLING", "INDOOR_CYCLING", "velo"), ("OTHER", "POOL_SWIMMING", "natation"),
                               ("OTHER", "TRIATHLON", "triathlon"), ("OTHER", "STRENGTH_TRAINING", "renfo"),
                               ("OTHER", "TENNIS", "tennis"), ("OTHER", "SOCCER", "foot"),
                               ("OTHER", "AMERICAN_FOOTBALL", "autre"), ("OTHER", "BACKCOUNTRY_SKIING", "ski_rando"),
                               ("OTHER", None, "autre")):
    verifier(f"Polar : {detail} → {attendu}", polar.sport_cloach(sport, detail) == attendu)
sortie = polar.vers_activite({"id": "abc", "start-time": "2026-11-20T07:15:00", "duration": "PT1H5M", "distance": 12345.0,
                              "heart-rate": {"average": 142, "maximum": 171}, "training-load": 96.4, "calories": 812,
                              "sport": "RUNNING", "detailed-sport-info": "TRAIL_RUNNING"})
verifier("Polar : sortie convertie", sortie and sortie["id"] == "polar-abc" and sortie["source"] == "polar"
         and sortie["jour"] == "2026-11-20" and sortie["sport"] == "trail" and sortie["distance_km"] == 12.35
         and sortie["duree_min"] == 65.0 and sortie["fc_moy"] == 142 and sortie["charge"] == 96.4)
verifier("Polar : sortie sans date ignorée", polar.vers_activite({"id": "x"}) is None)
verifier("Polar : colonnes identiques à Strava", sortie and set(sortie) == set(__import__("strava").vers_activite(
    {"id": 1, "start_date_local": "2026-11-20T07:15:00Z", "sport_type": "Run"})))
verifier("Polar : routes /polar/* déclarées", any("/polar/" in getattr(r, "path", "") for r in serveur.mcp._custom_starlette_routes)
         if hasattr(serveur.mcp, "_custom_starlette_routes") else True)

# ---------- Résultats d'outils : des constats, jamais d'ordre adressé à Claude ----------
# Claude prend un ordre glissé dans un résultat d'outil pour une tentative de manipulation (cas de l'avis de séance
# de Jérôme, 27/11 : « appelle mes_zones, puis reprends cette courbe »). Les ordres vont dans CONSIGNES.
import ast  # noqa: E402

ORDRES = re.compile(r"\b(appelle|rappelle|reprends|réenregistre|structure-les|détaille-les|construis-le|demande-le|"
                    r"enregistre-le|enregistre-la|note avec|utilise l'outil|corrige le plan)\b", re.IGNORECASE)


def chaines_renvoyees(fichier: str) -> list[tuple[int, str]]:
    """Chaînes du code, hors docstrings, consignes, prompts et descriptions de champs (lus comme instructions)."""
    arbre = ast.parse(open(fichier).read())
    exclues: set[int] = set()
    for n in ast.walk(arbre):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and n.body \
                and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant):
            exclues.add(id(n.body[0].value))
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("CONSIGNES", "METHODE") for t in n.targets):
            exclues |= {id(x) for x in ast.walk(n)}
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and any("prompt" in ast.unparse(d) for d in n.decorator_list):
            exclues |= {id(x) for x in ast.walk(n)}
        if isinstance(n, ast.keyword) and n.arg in ("description", "message"):
            exclues |= {id(x) for x in ast.walk(n.value)}
    return [(n.lineno, n.value) for n in ast.walk(arbre)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in exclues and ORDRES.search(n.value)]


for nom in ("serveur.py", "garmin.py", "courbe.py", "zones.py", "puissance.py", "polar.py", "strava.py"):
    for ligne, texte in chaines_renvoyees(os.path.join(DOSSIER, nom)):
        ECHECS.append(f"ordre adressé à Claude dans un résultat d'outil ({nom}:{ligne}) : « {texte[:80]} »")

# ---------- Zones ----------
verifier("zone d'une FC", zones.zone_de(150, [130, 145, 160, 170]) == 2)

if ECHECS:
    print(f"✗ Serveur : {len(ECHECS)} problème(s)\n  - " + "\n  - ".join(ECHECS))
    sys.exit(1)
print(f"✓ Serveur : {len(outils)} outils, garde-fous, calendrier, cycle, {len(types)} séances types, conversion Garmin, "
      f"{len(methodes)} méthodes garminconnect")
