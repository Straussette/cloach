"""Seuils de puissance en course et à vélo, et ratio entre les deux.

La puissance de course Garmin (qui compte aussi le vent) sort bien plus haut qu'un capteur de vélo pour
un même effort, et l'écart dépend de l'athlète : on ne compare donc pas les watts d'un sport à l'autre,
mais chaque effort à son propre seuil.
- Meilleurs efforts par durée : la courbe de puissance que Garmin donne dans le résumé de chaque sortie
  (colonne puissances_max, récupérée à l'import : tout l'historique sans appel de plus), sinon celle
  calculée à partir des détails (efforts « tout » de terrains.py).
- Seuil estimé d'après ces meilleurs efforts :
  1 h × 1,00, sinon 20 min × 0,95 (comme la FTP), sinon 10 min × 0,90 (provisoire).
- Ratio course / vélo de deux façons : seuil course ÷ FTP, et watts produits à la même fréquence
  cardiaque dans les deux sports (d'après les moyennes des sorties : aucun appel à Garmin).
Fonctions pures, sans accès réseau.
"""

from typing import Any

FACTEURS = ((3600, 1.00, "1 h"), (1200, 0.95, "20 min"), (600, 0.90, "10 min"))
DUREE_MIN_FC = 15     # minutes : en dessous, la FC moyenne d'une sortie ne dit pas grand-chose
ECART_FC = 10         # bpm : on ne compare pas au-delà des FC connues à vélo


def nombre(texte: Any) -> float | None:
    try:
        x = float(str(texte).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


COURBE = (5, 60, 300, 1200, 3600)  # durées montrées dans la courbe de puissance


def courbe_sortie(a: dict[str, Any]) -> dict[int, float]:
    """Meilleure puissance par durée (secondes → W) d'une sortie : Garmin, sinon calculée."""
    courbe = {int(d): float(p) for d, p in (a.get("puissances_max") or {}).items() if str(d).isdigit() and p}
    for d, m in (((a.get("terrains") or {}).get("efforts") or {}).get("tout") or {}).items():
        if m.get("puissance") and int(d) not in courbe:
            courbe[int(d)] = float(m["puissance"])
    return courbe


def meilleurs(lignes: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Meilleure puissance par durée, parmi les sorties { jour, nom, puissances_max, terrains }."""
    rec: dict[int, dict[str, Any]] = {}
    for l in lignes:
        for duree, p in courbe_sortie(l).items():
            if p > rec.get(duree, {}).get("valeur", 0):
                rec[duree] = {"valeur": round(p), "jour": l.get("jour"), "sortie": l.get("nom")}
    return rec


def estimer_seuil(lignes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Seuil estimé : le meilleur des efforts d'au moins 20 min ramenés à 1 h, sinon 10 min (provisoire)."""
    rec = meilleurs(lignes)
    candidats = [(round(rec[d]["valeur"] * f), nom, rec[d]) for d, f, nom in FACTEURS[:2] if d in rec]
    provisoire = not candidats
    if provisoire and 600 in rec:
        candidats = [(round(rec[600]["valeur"] * 0.90), "10 min", rec[600])]
    if not candidats:
        return None
    valeur, nom, effort = max(candidats, key=lambda c: c[0])
    return {"valeur": valeur, "d_apres": f"meilleur effort de {nom} : {effort['valeur']} W", "jour": effort["jour"],
            "sortie": effort["sortie"], "provisoire": provisoire}


def _points(activites: list[dict[str, Any]]) -> list[tuple[float, float, float]]:
    """(FC moyenne, puissance moyenne, durée) des sorties assez longues qui ont les deux."""
    points = []
    for a in activites:
        fc, p, duree = nombre(a.get("fc_moy")), nombre(a.get("puissance_moy")), nombre(a.get("duree_min"))
        if fc and p and duree and duree >= DUREE_MIN_FC:
            points.append((fc, p, duree))
    return points


def ratio_fc(courses: list[dict[str, Any]], velos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Watts en course ÷ watts à vélo à la même FC : droite puissance / FC à vélo (au moins deux sorties
    assez différentes), comparée à chaque sortie de course dont la FC tombe dans la plage connue."""
    v, c = _points(velos), _points(courses)
    if len(v) < 2 or not c:
        return None
    fc_min, fc_max = min(x[0] for x in v), max(x[0] for x in v)
    if fc_max - fc_min < 10:
        return None
    # Moindres carrés pondérés par la durée
    poids = sum(x[2] for x in v)
    mx = sum(x[0] * x[2] for x in v) / poids
    my = sum(x[1] * x[2] for x in v) / poids
    pente = sum(x[2] * (x[0] - mx) * (x[1] - my) for x in v) / sum(x[2] * (x[0] - mx) ** 2 for x in v)
    if pente <= 0:
        return None
    a_velo = lambda fc: my + pente * (fc - mx)
    comparables = [(p / a_velo(fc), fc, p, d) for fc, p, d in c if fc_min - ECART_FC <= fc <= fc_max + ECART_FC and a_velo(fc) > 0]
    if not comparables:
        return None
    total = sum(x[3] for x in comparables)
    fc_type = sum(x[1] * x[3] for x in comparables) / total
    return {
        "ratio": round(sum(x[0] * x[3] for x in comparables) / total, 2),
        "fc": round(fc_type), "course_w": round(sum(x[2] * x[3] for x in comparables) / total),
        "velo_w": round(a_velo(fc_type)), "sorties_course": len(comparables), "sorties_velo": len(v),
    }


def seuils(courses: list[dict[str, Any]], velos: list[dict[str, Any]], profil: dict[str, Any],
           meilleurs_seuils: bool = False) -> dict[str, Any]:
    """Seuil course et FTP (au profil ou estimés), courbes de puissance, et ratios course / vélo. `courses` et
    `velos` : activités (avec puissance_moy, fc_moy, duree_min, puissances_max et terrains) de la période ;
    `meilleurs_seuils` (de tous les temps) : seuils estimés seulement, sans les valeurs actuelles du profil."""
    estime_course, estime_ftp = estimer_seuil(courses), estimer_seuil(velos)
    poids = nombre(profil.get("poids"))
    seuil_profil, ftp_profil = nombre(profil.get("seuilCourse")), nombre(profil.get("ftp"))
    seuil = (None if meilleurs_seuils else seuil_profil) or (estime_course or {}).get("valeur")
    ftp = (None if meilleurs_seuils else ftp_profil) or (estime_ftp or {}).get("valeur")
    def courbe(lignes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        rec = meilleurs(lignes)
        return {f"{d}s": {**rec[d], **({"w_kg": round(rec[d]["valeur"] / poids, 2)} if poids else {})}
                for d in COURBE if d in rec}

    resultat: dict[str, Any] = {
        "course": {"seuil_w": seuil, "au_profil": seuil_profil, "estime": estime_course, "courbe": courbe(courses)},
        "velo": {"ftp_w": ftp, "au_profil": ftp_profil, "estime": estime_ftp, "courbe": courbe(velos)},
    }
    if seuil and ftp:
        resultat["ratio_seuils"] = round(seuil / ftp, 2)
    par_fc = ratio_fc(courses, velos)
    if par_fc:
        resultat["ratio_meme_fc"] = par_fc
    return resultat
