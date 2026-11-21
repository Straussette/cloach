"""Périodisation : le macrocycle qui mène à l'objectif, découpé en phases (foncier, développement, spécifique,
affûtage, récupération). Claude l'enregistre (outil enregistrer_cycle), chaque plan de 3 semaines s'inscrit dans la
phase en cours (contexte_du_jour la donne), et garde_fous vérifie que le plan respecte la phase.

Fonctions pures, testables sans réseau.
"""

from datetime import date, timedelta
from typing import Any

PHASES = {
    "foncier": "Volume en endurance facile, technique, renfo général ; au plus une séance soutenue par semaine.",
    "developpement": "Volume qui monte encore, deux séances de qualité par semaine (seuil, VO2max), renfo spécifique.",
    "specifique": "Séances au plus près de l'effort de l'objectif (allure, terrain, durée, enchaînements), sorties longues clés.",
    "affutage": "Volume réduit de 40 à 60 %, un peu d'intensité gardée, fraîcheur avant tout.",
    "recuperation": "Après l'objectif : repos actif, rien d'intense, reprise progressive.",
}
# Séances dures (soutenu, très soutenu) par semaine, au plus, selon la phase
DURES_MAX = {"foncier": 1, "developpement": 3, "specifique": 3, "affutage": 2, "recuperation": 0}


def proposer(date_objectif: date, aujourdhui: date) -> list[dict[str, Any]]:
    """Découpage par défaut d'ici l'objectif (Claude peut l'ajuster) : affûtage de 1 à 2 semaines, phase spécifique
    de 3 à 6 semaines, développement puis foncier pour le reste, et une semaine de récupération après."""
    semaines = max(1, (date_objectif - aujourdhui).days // 7)
    affutage = 2 if semaines >= 10 else 1
    dispo = max(0, semaines - affutage)
    specifique = dispo if dispo <= 4 else min(6, max(3, dispo // 3))  # objectif proche : tout en spécifique
    reste = max(0, semaines - affutage - specifique)
    developpement = reste // 2 if reste >= 4 else reste
    foncier = reste - developpement
    phases = []
    debut = aujourdhui
    for nom, n in (("foncier", foncier), ("developpement", developpement), ("specifique", specifique)):
        if n:
            fin = debut + timedelta(weeks=n) - timedelta(days=1)
            phases.append({"nom": nom, "debut": debut.isoformat(), "fin": fin.isoformat(), "but": PHASES[nom]})
            debut = fin + timedelta(days=1)
    phases.append({"nom": "affutage", "debut": debut.isoformat(), "fin": date_objectif.isoformat(), "but": PHASES["affutage"]})
    lendemain = date_objectif + timedelta(days=1)
    phases.append({"nom": "recuperation", "debut": lendemain.isoformat(), "fin": (lendemain + timedelta(days=6)).isoformat(),
                   "but": PHASES["recuperation"]})
    return phases


def phase_du(cycle: dict[str, Any] | None, jour: date) -> dict[str, Any] | None:
    """La phase qui contient ce jour, avec les semaines restantes avant l'objectif."""
    if not cycle:
        return None
    for p in cycle.get("phases") or []:
        if p.get("debut", "") <= jour.isoformat() <= p.get("fin", ""):
            resultat = {**p}
            try:
                objectif = date.fromisoformat(str((cycle.get("objectif") or {}).get("date")))
                resultat["semaines_avant_objectif"] = max(0, (objectif - jour).days // 7)
            except ValueError:
                pass
            return resultat
    return None


def verifier_phases(semaines: list[dict[str, Any]], cycle: dict[str, Any] | None) -> list[str]:
    """Le plan respecte-t-il la phase de chaque semaine ? (nombre de séances dures)"""
    avertissements = []
    for i, w in enumerate(semaines):
        try:
            jour = date.fromisoformat(str(w.get("debut")))
        except ValueError:
            continue
        phase = phase_du(cycle, jour + timedelta(days=3))
        if not phase:
            continue
        maxi = DURES_MAX.get(phase.get("nom"))
        n = sum(1 for s in w.get("seances") or [] if s.get("intensite") in ("soutenu", "tres-soutenu"))
        if maxi is not None and n > maxi:
            avertissements.append(f"Semaine {i + 1} en phase « {phase['nom']} » : {n} séances dures pour {maxi} au plus "
                                  f"({PHASES.get(phase['nom'], '')}).")
    return avertissements
