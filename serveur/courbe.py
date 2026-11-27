"""Courbe d'une sortie pour Claude : FC, allure (ou vitesse), puissance et altitude, résumées par tranches de
temps (avec la FC max de chaque tranche : la moyenne d'une minute écrase un effort court), les efforts repérés
sur la FC point par point (pics, avec la FC réelle, la durée, l'allure ou la puissance et le D+ de la montée), le temps
passé dans chaque zone de FC de l'athlète, et les kilomètres un par un.

À partir des mêmes détails Garmin que terrains.py (lire_series). Fonctions pures, testables sans réseau.
"""

from typing import Any

POINTS_MAX = 120      # tranches au plus dans la courbe (une par minute pour une sortie de 2 h)
PAUSE_S = 10.0        # au-delà, un écart entre deux points est une pause
NOMS_ZONES = ("Z1 récupération", "Z2 endurance", "Z3 tempo", "Z4 seuil", "Z5 VO2max")
LISSAGE_S = 8.0       # lissage de la FC avant de chercher les pics (bruit du capteur)
VOISINAGE_S = 45.0    # un pic est le plus haut point à ± 45 s
REMONTEE_S = 150.0    # on cherche le creux qui précède dans les 2 min 30 d'avant
HAUSSE_MIN = 8.0      # un effort fait monter la FC d'au moins 8 bpm depuis ce creux
EFFORTS_MAX = 40


def efforts(series: dict[str, list[Any]], velo: bool) -> list[dict[str, Any]]:
    """Pics de FC point par point : chaque effort (côte, fraction, relance) avec sa FC de départ et son pic réel,
    la durée de la montée de FC, l'allure (ou la vitesse), la puissance et le D+ sur cette durée."""
    t0 = series["temps"][0]
    pts = [(series["temps"][i] - t0, f, i) for i, f in enumerate(series["fc"]) if f]
    if len(pts) < 20:
        return []
    # FC lissée sur LISSAGE_S (fenêtre glissante)
    lisse, debut, somme = [], 0, 0.0
    for j, (t, f, _) in enumerate(pts):
        somme += f
        while pts[debut][0] < t - LISSAGE_S:
            somme -= pts[debut][1]
            debut += 1
        lisse.append(somme / (j - debut + 1))
    trouves: list[dict[str, Any]] = []
    a = b = c = 0  # bornes des fenêtres glissantes (voisinage avant, voisinage après, remontée)
    for j, (t, _, _) in enumerate(pts):
        while pts[a][0] < t - VOISINAGE_S:
            a += 1
        while b + 1 < len(pts) and pts[b + 1][0] <= t + VOISINAGE_S:
            b += 1
        while pts[c][0] < t - REMONTEE_S:
            c += 1
        if lisse[j] < max(lisse[a:b + 1]) or (trouves and t - trouves[-1]["t"] < VOISINAGE_S):
            continue
        # Creux : le dernier point proche du plus bas avant le pic (début réel de la montée de FC)
        bas = min(lisse[c:j + 1])
        creux = max(k for k in range(c, j + 1) if lisse[k] <= bas + 2)
        if lisse[j] - bas < HAUSSE_MIN:
            continue
        trouves.append({"t": t, "j": j, "creux": creux})
    resultat = []
    for e in trouves[:EFFORTS_MAX]:
        j, k = e["j"], e["creux"]
        autour = [f for (t, f, _) in pts[k:] if t <= e["t"] + 15]
        i0, i1 = pts[k][2], pts[j][2]
        duree = series["temps"][i1] - series["temps"][i0]
        metres = series["dist"][i1] - series["dist"][i0]
        d_plus = sum(max(0.0, series["alt"][i] - series["alt"][i - 1]) for i in range(i0 + 1, i1 + 1))
        p = [series["puissance"][i] for i in range(i0, i1 + 1) if series["puissance"][i] is not None]
        ligne: dict[str, Any] = {"min": round(e["t"] / 60, 1), "fc_depart": round(lisse[k]), "fc_pic": round(max(autour)),
                                 "montee_fc_s": round(duree), "d_plus": round(d_plus)}
        if duree > 0 and metres > 0:
            if velo:
                ligne["kmh"] = round(metres / duree * 3.6, 1)
            else:
                ligne["allure"] = _allure(metres, duree)
        if p:
            ligne["w"] = round(sum(p) / len(p))
        resultat.append(ligne)
    return resultat


def _moyenne(valeurs: list[tuple[float, float]]) -> float | None:
    poids = sum(p for _, p in valeurs)
    return sum(v * p for v, p in valeurs) / poids if poids > 0 else None


def _allure(metres: float, secondes: float) -> str | None:
    if metres <= 0 or secondes <= 0:
        return None
    s = secondes / metres * 1000
    return f"{int(s // 60)}:{int(round(s % 60)):02d}" if s < 1200 else None


def resumer(series: dict[str, list[Any]], velo: bool, bornes_fc: list[float] | None = None) -> dict[str, Any]:
    dist, alt, temps, fc, puissance = series["dist"], series["alt"], series["temps"], series["fc"], series["puissance"]
    n = len(dist)
    duree = temps[-1] - temps[0]
    pas = max(60, int(-(-duree // POINTS_MAX) // 30 * 30) or 60)  # 1 min, ou plus pour les longues sorties

    tranches: list[dict[str, Any]] = []
    zones_s = [0.0] * 5
    fc_max = 0.0
    km: list[dict[str, Any]] = []
    debut_km = {"t": 0.0, "d": dist[0], "alt": alt[0], "fc": [], "dplus": 0.0}
    cours: dict[str, Any] | None = None

    for i in range(1, n):
        dt = temps[i] - temps[i - 1]
        if dt <= 0 or dt > PAUSE_S:
            continue
        dd = max(0.0, dist[i] - dist[i - 1])
        t = temps[i] - temps[0]
        k = int(t // pas)
        if cours is None or cours["k"] != k:
            cours = {"k": k, "t": 0.0, "d": 0.0, "fc": [], "p": [], "alt": alt[i], "fc_max": 0.0}
            tranches.append(cours)
        cours["t"] += dt
        cours["d"] += dd
        f = fc[i]
        if f:
            cours["fc"].append((f, dt))
            cours["fc_max"] = max(cours["fc_max"], f)
            fc_max = max(fc_max, f)
            if bornes_fc:
                zones_s[sum(1 for b in bornes_fc if f >= b)] += dt
        if puissance[i] is not None:
            cours["p"].append((puissance[i], dt))
        # Kilomètres (à pied) ou tranches de 5 km (à vélo)
        debut_km["t"] += dt
        if f:
            debut_km["fc"].append((f, dt))
        debut_km["dplus"] += max(0.0, alt[i] - alt[i - 1])
        longueur = 5000 if velo else 1000
        if dist[i] - debut_km["d"] >= longueur:
            fm = _moyenne(debut_km["fc"])
            ligne = {"km": round(dist[i] / 1000, 1), "duree_s": round(debut_km["t"]), "fc": round(fm) if fm else None,
                     "d_plus": round(debut_km["dplus"])}
            if velo:
                ligne["vitesse_kmh"] = round((dist[i] - debut_km["d"]) / debut_km["t"] * 3.6, 1)
            else:
                ligne["allure"] = _allure(dist[i] - debut_km["d"], debut_km["t"])
            km.append(ligne)
            debut_km = {"t": 0.0, "d": dist[i], "alt": alt[i], "fc": [], "dplus": 0.0}

    courbe = []
    for c in tranches:
        fm, pm = _moyenne(c["fc"]), _moyenne(c["p"])
        point: dict[str, Any] = {"min": round(c["k"] * pas / 60, 1), "fc": round(fm) if fm else None,
                                 "fc_max": round(c["fc_max"]) if c["fc_max"] else None, "alt": round(c["alt"])}
        if velo:
            point["kmh"] = round(c["d"] / c["t"] * 3.6, 1) if c["t"] > 0 else None
        else:
            point["allure"] = _allure(c["d"], c["t"])
        if pm is not None:
            point["w"] = round(pm)
        courbe.append(point)

    toutes_fc = [(f, temps[i] - temps[i - 1]) for i, f in enumerate(fc) if i and f and 0 < temps[i] - temps[i - 1] <= PAUSE_S]
    resultat: dict[str, Any] = {
        "duree_s": round(duree),
        "pas_s": pas,
        "courbe": courbe,
        "efforts": efforts(series, velo),
        "kilometres" if not velo else "tranches_5_km": km[:80],
    }
    fm = _moyenne(toutes_fc)
    if fm:
        resultat["fc"] = {"moyenne": round(fm), "max": round(fc_max)}
    total = sum(zones_s)
    if bornes_fc and total > 0:
        resultat["temps_par_zone"] = [
            {"zone": NOMS_ZONES[z], "fc": [round(bornes_fc[z - 1]) if z else None, round(bornes_fc[z]) if z < 4 else None],
             "secondes": round(s), "pct": round(s / total * 100)}
            for z, s in enumerate(zones_s)]
    return resultat
