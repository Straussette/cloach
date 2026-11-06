"""Zones d'entraînement personnelles, calées sur deux seuils estimés dans les données de l'athlète.

Plutôt que des pourcentages de la FC max (Garmin par défaut), deux points de bascule, par famille de
sports (course + trail, vélo : la FC est souvent plus basse à vélo à effort égal) :
- seuil 2 (anaérobie) : d'après les meilleures FC moyennes tenues sur 60, 30 ou 20 min (× 1, 0,98 ou 0,95,
  la méthode de Friel), plafonné à 96 % de la FC max ;
- seuil 1 (aérobie) : d'après la dérive cardiaque des sorties régulières (découplage entre la première et
  la seconde moitié, hors échauffement) : sous le seuil 1, la FC reste stable à effort constant. On ajuste
  une droite découplage = f(FC) sur ces sorties, et le seuil 1 est la FC où elle atteint 5 % ;
  à défaut de données, 89 % du seuil 2 (confiance faible). Le DFA α1 (dfa.py), s'il a été calculé sur
  assez de sorties, prime : c'est la méthode la plus fine (α1 = 0,75 au seuil 1).
- FC max : la deuxième plus haute FC max des 12 derniers mois (la plus haute est souvent un artefact).
Les corrections de l'athlète (zones.manuel) priment sur tout.

Puissance et allure aux seuils : droite FC → puissance (ou vitesse « à plat ») sur les sorties récentes,
d'après l'histogramme cardiaque de chaque sortie (terrains.py, champ fc.h) ; à vélo, la FTP estimée
sur les meilleurs efforts (puissance.py) remplace la puissance au seuil 2.

5 zones : récupération, endurance (jusqu'au seuil 1), tempo, seuil (autour du seuil 2), VO2max.
Fonctions pures, sans accès réseau.
"""

from datetime import date, timedelta
from typing import Any

import puissance

FAMILLES = {"pied": ("course", "trail"), "velo": ("velo",)}
NOMS_ZONES = ("Récupération", "Endurance", "Tempo", "Seuil", "VO2max")
DECOUPLAGE_S1 = 5.0       # % : au-delà, la FC dérive, on a passé le seuil 1
JOURS_SEUILS = 180        # fenêtre pour les seuils
JOURS_DROITE = 120        # fenêtre pour la droite FC → puissance ou vitesse
CV_MAX = {"pied": 0.25, "velo": 0.2}  # régularité exigée pour la dérive (écart type / moyenne des blocs de 5 min)


def famille_de(sport: str | None) -> str | None:
    return next((f for f, sports in FAMILLES.items() if sport in sports), None)


def _nombre(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _physio(a: dict[str, Any]) -> dict[str, Any] | None:
    t = a.get("terrains") or {}
    return t.get("fc") if isinstance(t, dict) else None


def _droite(points: list[tuple[float, float, float]]) -> tuple[float, float] | None:
    """Moindres carrés pondérés y = a + b·x sur (x, y, poids)."""
    poids = sum(p for _, _, p in points)
    if poids <= 0 or len(points) < 3:
        return None
    mx = sum(x * p for x, _, p in points) / poids
    my = sum(y * p for _, y, p in points) / poids
    vx = sum(p * (x - mx) ** 2 for x, _, p in points)
    if vx <= 0:
        return None
    b = sum(p * (x - mx) * (y - my) for x, y, p in points) / vx
    return my - b * mx, b


def _correlation(points: list[tuple[float, float]]) -> float | None:
    n = len(points)
    if n < 3:
        return None
    mx, my = sum(x for x, _ in points) / n, sum(y for _, y in points) / n
    vx, vy = sum((x - mx) ** 2 for x, _ in points), sum((y - my) ** 2 for _, y in points)
    if vx <= 0 or vy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in points) / (vx * vy) ** 0.5


def fc_max(activites: list[dict[str, Any]], famille: str, aujourdhui: date) -> dict[str, Any] | None:
    depuis = (aujourdhui - timedelta(days=365)).isoformat()
    valeurs = sorted(((v, a) for a in activites if famille_de(a.get("sport")) == famille and (a.get("jour") or "") >= depuis
                      and (v := _nombre(a.get("fc_max"))) and 120 <= v <= 225), key=lambda x: -x[0])
    if not valeurs:
        return None
    v, a = valeurs[1] if len(valeurs) >= 3 else valeurs[0]
    return {"valeur": round(v), "jour": a.get("jour"), "sortie": a.get("nom"), "methode": "deuxième plus haute FC max sur 12 mois"}


def seuil2(analysees: list[dict[str, Any]], fcmax: float | None) -> dict[str, Any] | None:
    candidats = []
    for a in analysees:
        meilleures = (_physio(a) or {}).get("max") or {}
        for duree, facteur in (("3600", 1.0), ("1800", 0.98), ("1200", 0.95)):
            if _nombre(meilleures.get(duree)):
                candidats.append((meilleures[duree] * facteur, duree, a))
    if not candidats:
        return None
    valeur, duree, a = max(candidats, key=lambda c: c[0])
    plafonne = bool(fcmax and valeur > fcmax * 0.96)
    if plafonne:
        valeur = fcmax * 0.96
    minutes = int(duree) // 60
    return {"fc": round(valeur), "jour": a.get("jour"), "sortie": a.get("nom"),
            "methode": f"meilleure FC moyenne sur {minutes} min" + (" (plafonnée à 96 % de la FC max)" if plafonne else ""),
            "confiance": "haute" if duree in ("3600", "1800") and not plafonne else "moyenne"}


def seuil1_derive(analysees: list[dict[str, Any]], famille: str, fc_s2: float) -> dict[str, Any] | None:
    """Seuil 1 par la dérive cardiaque des sorties régulières d'au moins 40 min."""
    points = []
    for a in analysees:
        d = (_physio(a) or {}).get("derive")
        if not d or d.get("cv") is None or d["cv"] > CV_MAX[famille] or d.get("duree_s", 0) < 2400:
            continue
        if d["fc"] < fc_s2 and -15 < d["decouplage"] < 30:
            points.append((d["fc"], d["decouplage"], a))
    base = {"sorties": len(points)}
    if len(points) < 6 or max(p[0] for p in points) - min(p[0] for p in points) < 8:
        return base
    droite = _droite([(fc, dec, 1.0) for fc, dec, _ in points])
    r = _correlation([(fc, dec) for fc, dec, _ in points])
    if not droite or droite[1] <= 0:
        return base
    a0, b = droite
    valeur = (DECOUPLAGE_S1 - a0) / b
    bornee = min(max(valeur, fc_s2 * 0.8), fc_s2 * 0.95)
    confiance = "haute" if len(points) >= 12 and (r or 0) >= 0.4 and bornee == valeur else "moyenne"
    return {**base, "fc": round(bornee), "methode": f"dérive cardiaque sur {len(points)} sorties régulières",
            "confiance": confiance, "correlation": round(r, 2) if r is not None else None}


# Tapis : la montre ne connaît pas l'inclinaison, une côte à 10 % y passe pour du plat (allure et puissance fausses)
TYPES_INTERIEUR = {"treadmill_running", "indoor_running", "virtual_run"}


def droites(analysees: list[dict[str, Any]], fc_bas: float, fc_haut: float) -> dict[str, tuple[float, float]]:
    """Droites FC → puissance et FC → vitesse (m/s), entre deux FC, d'après les histogrammes. Hors tapis ; la
    vitesse vient des passages plats quand l'analyse les distingue (version 7), sinon de la vitesse « à plat »."""
    pts_p, pts_v = [], []
    for a in analysees:
        if a.get("type") in TYPES_INTERIEUR:
            continue
        for bpm, valeurs in ((_physio(a) or {}).get("h") or {}).items():
            t, t_p, somme_p, dist = valeurs[:4]
            t_plat, dist_plat = (valeurs[4], valeurs[5]) if len(valeurs) >= 6 else (None, None)
            fc = float(bpm)
            if not fc_bas <= fc <= fc_haut:
                continue
            if t_p >= 30:
                pts_p.append((fc, somme_p / t_p, t_p))
            if t_plat is not None:
                if t_plat >= 30 and dist_plat > 0:
                    pts_v.append((fc, dist_plat / t_plat, t_plat))
            elif t >= 30 and dist > 0:
                pts_v.append((fc, dist / t, t))
    resultat = {}
    for nom, pts in (("puissance", pts_p), ("vitesse", pts_v)):
        if sum(p for *_, p in pts) >= 3600:
            d = _droite(pts)
            if d and d[1] > 0:
                resultat[nom] = d
    return resultat


def _borne(valeur: float | None) -> float | None:
    return round(valeur, 3) if valeur is not None else None


def calculer(activites: list[dict[str, Any]], aujourdhui: date, manuel: dict[str, Any] | None = None,
             dfa: dict[str, Any] | None = None, fc_repos: float | None = None) -> dict[str, Any]:
    """Seuils et zones de chaque famille. activites : avec terrains (complet) et fc_max."""
    manuel = manuel or {}
    resultat: dict[str, Any] = {"date": aujourdhui.isoformat()}
    if fc_repos:
        resultat["fc_repos"] = round(fc_repos)
    depuis = (aujourdhui - timedelta(days=JOURS_SEUILS)).isoformat()
    depuis_droite = (aujourdhui - timedelta(days=JOURS_DROITE)).isoformat()
    for famille, sports in FAMILLES.items():
        lignes = [a for a in activites if a.get("sport") in sports]
        analysees = [a for a in lignes if (a.get("jour") or "") >= depuis and _physio(a)]
        m = manuel.get(famille) or {}
        fm = fc_max(lignes, famille, aujourdhui)
        if _nombre(m.get("fc_max")):
            fm = {"valeur": round(float(m["fc_max"])), "methode": "saisie par l'athlète"}
        s2 = seuil2(analysees, fm["valeur"] if fm else None)
        if _nombre(m.get("fc_s2")):
            s2 = {"fc": round(float(m["fc_s2"])), "methode": "saisi par l'athlète", "confiance": "saisie"}
        if not s2:
            resultat[famille] = {"fc_max": fm, "sorties_analysees": len(analysees),
                                 "manque": "Pas assez de sorties analysées avec la fréquence cardiaque."}
            continue
        s1 = seuil1_derive(analysees, famille, s2["fc"])
        sorties_derive = s1.get("sorties", 0) if s1 else 0
        if not s1 or "fc" not in s1:
            s1 = {"fc": round(s2["fc"] * 0.89), "methode": "89 % du seuil 2 (pas assez de sorties régulières pour la dérive)",
                  "confiance": "faible", "sorties": sorties_derive}
        d = (dfa or {}).get(famille) or {}
        if _nombre(d.get("fc_s1")) and d.get("sorties", 0) >= 2:
            s1 = {"fc": round(d["fc_s1"]), "methode": f"DFA α1 = 0,75 sur {d['sorties']} sorties", "confiance": "haute",
                  "derive": s1.get("fc")}
        if _nombre(m.get("fc_s1")):
            s1 = {"fc": round(float(m["fc_s1"])), "methode": "saisi par l'athlète", "confiance": "saisie"}
        s1["fc"] = min(s1["fc"], s2["fc"] - 3)

        # Puissance et vitesse aux seuils
        recentes = [a for a in analysees if (a.get("jour") or "") >= depuis_droite] or analysees
        lignes_droite = droites(recentes, s2["fc"] * 0.7, s2["fc"] * 1.03)
        sorties = {}
        for nom, (a0, b) in lignes_droite.items():
            sorties[nom] = [a0 + b * s1["fc"], a0 + b * s2["fc"]]
        if famille == "velo":
            ftp = _nombre(m.get("ftp")) or (puissance.estimer_seuil(lignes) or {}).get("valeur")
            if ftp:
                sorties.setdefault("puissance", [ftp * 0.75, ftp])[1] = ftp
                sorties["puissance"][0] = min(sorties["puissance"][0], ftp * 0.85)
        for nom, (v1, v2) in list(sorties.items()):
            if not (v1 > 0 and v2 > v1):
                del sorties[nom]
        s1.update({nom: _borne(v[0]) for nom, v in sorties.items()})
        s2.update({nom: _borne(v[1]) for nom, v in sorties.items()})

        # Zones : bornes en FC, puis converties par la droite passant par les deux seuils
        f1, f2 = s1["fc"], s2["fc"]
        bornes_fc = [round(f1 * 0.92), f1, round(f2 * 0.96), round(f2 * 1.02)]
        def convertir(fc: float, v: list[float]) -> float:
            return v[0] + (v[1] - v[0]) * (fc - f1) / (f2 - f1)
        zones = []
        for k, nom_zone in enumerate(NOMS_ZONES):
            bas = bornes_fc[k - 1] if k > 0 else None
            haut = bornes_fc[k] if k < 4 else (fm["valeur"] if fm else None)
            z: dict[str, Any] = {"zone": k + 1, "nom": nom_zone, "fc": [bas, haut]}
            for nom, v in sorties.items():
                z[nom] = [_borne(convertir(bas, v)) if bas else None, _borne(convertir(bornes_fc[k], v)) if k < 4 else None]
            zones.append(z)
        resultat[famille] = {"fc_max": fm, "seuil1": s1, "seuil2": s2, "zones": zones,
                             "bornes_fc": bornes_fc, "sorties_analysees": len(analysees)}
    return resultat


def zone_de(fc: float, bornes: list[int]) -> int:
    """Numéro de zone (0 à 4) d'une FC d'après les 4 bornes."""
    return sum(1 for b in bornes if fc >= b)


def repartition(activites: list[dict[str, Any]], zones: dict[str, Any], debut: str, fin: str,
                famille: str | None = None) -> dict[str, Any]:
    """Temps passé dans chaque zone de FC, par semaine (lundi), d'après les histogrammes des sorties."""
    semaines: dict[str, dict[str, Any]] = {}
    sans_detail = 0
    for a in activites:
        f = famille_de(a.get("sport"))
        jour = a.get("jour") or ""
        if not f or not (debut <= jour <= fin) or (famille and f != famille):
            continue
        bornes = (zones.get(f) or {}).get("bornes_fc")
        h = (_physio(a) or {}).get("h")
        if not bornes or not h:
            sans_detail += 1
            continue
        lundi = (date.fromisoformat(jour) - timedelta(days=date.fromisoformat(jour).weekday())).isoformat()
        s = semaines.setdefault(lundi, {"debut": lundi, "secondes": [0] * 5, "sorties": 0})
        s["sorties"] += 1
        for bpm, valeurs in h.items():
            s["secondes"][zone_de(float(bpm), bornes)] += valeurs[0]
    liste = sorted(semaines.values(), key=lambda s: s["debut"])
    total = [sum(s["secondes"][k] for s in liste) for k in range(5)]
    return {"semaines": liste, "total": total, "sans_detail": sans_detail}
