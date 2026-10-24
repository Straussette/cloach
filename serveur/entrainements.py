"""Séances structurées : conversion en entraînement Garmin Connect.

Une séance du plan peut avoir des « etapes » (écrites par Claude) :
    { type: echauffement | effort | recuperation | retour_au_calme | repetition,
      duree_s, distance_m,                       # l'un ou l'autre (sinon : bouton « tour »)
      cible: { type: zone_fc | fc | allure | puissance | zone_puissance | ftp, min, max, zone },
      repetitions, etapes }                       # pour une répétition (un seul niveau)
Unités des cibles : fc en bpm, allure en minutes par km (4.5 = 4:30), puissance en watts,
ftp en % de la FTP du profil, zones de 1 à 5 (FC) ou 1 à 7 (puissance).
"""

from typing import Any

SPORTS_GARMIN = {
    "course": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "trail": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "velo": {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2},
}
TYPES_ETAPE = {
    "echauffement": (1, "warmup"),
    "retour_au_calme": (2, "cooldown"),
    "effort": (3, "interval"),
    "recuperation": (4, "recovery"),
    "repos": (5, "rest"),
}
SANS_CIBLE = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}


def _cible(cible: dict[str, Any] | None, ftp: float | None) -> dict[str, Any]:
    """Champs Garmin de la cible d'une étape."""
    if not cible:
        return {"targetType": SANS_CIBLE}
    genre = cible.get("type")
    bas, haut, zone = cible.get("min"), cible.get("max"), cible.get("zone")
    fc = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 4}
    puissance = {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone", "displayOrder": 2}
    if genre == "zone_fc" and zone:
        return {"targetType": fc, "zoneNumber": int(zone)}
    if genre == "fc" and bas and haut:
        return {"targetType": fc, "targetValueOne": float(bas), "targetValueTwo": float(haut)}
    if genre == "allure" and bas and haut:
        # Garmin veut des m/s, l'allure la plus lente en premier
        lente, rapide = max(float(bas), float(haut)), min(float(bas), float(haut))
        return {"targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 6},
                "targetValueOne": round(1000 / (lente * 60), 3), "targetValueTwo": round(1000 / (rapide * 60), 3)}
    if genre == "zone_puissance" and zone:
        return {"targetType": puissance, "zoneNumber": int(zone)}
    if genre == "puissance" and bas and haut:
        return {"targetType": puissance, "targetValueOne": float(bas), "targetValueTwo": float(haut)}
    if genre == "ftp" and bas and haut and ftp:
        return {"targetType": puissance, "targetValueOne": round(float(bas) * ftp / 100),
                "targetValueTwo": round(float(haut) * ftp / 100)}
    return {"targetType": SANS_CIBLE}


def _fin(etape: dict[str, Any]) -> tuple[dict[str, Any], float | None]:
    if etape.get("duree_s"):
        return {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}, float(etape["duree_s"])
    if etape.get("distance_m"):
        return {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3, "displayable": True}, float(etape["distance_m"])
    return {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}, None


def vers_garmin(seance: dict[str, Any], ftp: float | None = None) -> dict[str, Any]:
    """Séance structurée → entraînement Garmin Connect (format de l'API workout-service)."""
    sport = SPORTS_GARMIN.get(seance.get("sport", ""))
    if not sport:
        raise ValueError("Seules les séances de course, de trail et de vélo peuvent aller sur la montre.")
    etapes = seance.get("etapes") or []
    if not etapes:
        raise ValueError("Cette séance n'est pas encore structurée : demande à Claude de la détailler étape par étape.")
    ordre = 0
    groupe = 0
    duree_totale = 0.0

    def executable(e: dict[str, Any], enfant: int | None) -> dict[str, Any]:
        nonlocal ordre, duree_totale
        ordre += 1
        type_id, type_cle = TYPES_ETAPE.get(e.get("type", ""), (3, "interval"))
        fin, valeur = _fin(e)
        duree_totale += float(e.get("duree_s") or 0)
        pas = {"type": "ExecutableStepDTO", "stepOrder": ordre,
               "stepType": {"stepTypeId": type_id, "stepTypeKey": type_cle, "displayOrder": type_id},
               "endCondition": fin, "endConditionValue": valeur, "description": (e.get("note") or None),
               **_cible(e.get("cible"), ftp)}
        if enfant is not None:
            pas["childStepId"] = enfant
        return {k: v for k, v in pas.items() if v is not None}

    pas_garmin = []
    for e in etapes:
        if e.get("type") == "repetition" and e.get("etapes"):
            groupe += 1
            ordre += 1
            ordre_groupe = ordre
            n = max(1, int(e.get("repetitions") or 1))
            avant = duree_totale
            enfants = [executable(x, groupe) for x in e["etapes"]]
            duree_totale = avant + (duree_totale - avant) * n
            pas_garmin.append({
                "type": "RepeatGroupDTO", "stepOrder": ordre_groupe, "childStepId": groupe,
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
                "numberOfIterations": n, "smartRepeat": False, "workoutSteps": enfants,
                "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False},
                "endConditionValue": n,
            })
        else:
            pas_garmin.append(executable(e, None))
    return {
        "workoutName": f"Cloach · {seance.get('titre', 'Séance')}"[:50],
        "description": (seance.get("details") or "")[:500] or None,
        "sportType": sport,
        "estimatedDurationInSecs": int(duree_totale or (seance.get("duree") or 0) * 60),
        "workoutSegments": [{"segmentOrder": 1, "sportType": sport, "workoutSteps": pas_garmin}],
    }
