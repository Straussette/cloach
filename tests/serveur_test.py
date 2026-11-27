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

# ---------- Zones ----------
verifier("zone d'une FC", zones.zone_de(150, [130, 145, 160, 170]) == 2)

if ECHECS:
    print(f"✗ Serveur : {len(ECHECS)} problème(s)\n  - " + "\n  - ".join(ECHECS))
    sys.exit(1)
print(f"✓ Serveur : {len(outils)} outils, garde-fous, calendrier, cycle, {len(types)} séances types, conversion Garmin")
