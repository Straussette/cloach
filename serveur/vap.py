"""VAP (allure ajustée à la pente) d'une sortie de course ou de trail, calculée comme Strava :
chaque tronçon du profil (distance et altitude point par point) est converti en distance « à plat »
selon sa pente, puis on rapporte le temps en mouvement à cette distance équivalente.

Le coefficient par pente reprend l'allure de la courbe publiée par Strava (2017) : environ ×1,47 à
+10 %, ×0,87 à −10 % (la descente douce aide, la descente raide coûte à nouveau). C'est une
approximation : Strava ne publie pas sa formule exacte.
"""

from typing import Any

PAS_M = 25.0          # longueur des tronçons sur lesquels on mesure la pente
LISSAGE = 5           # points de part et d'autre pour lisser l'altitude (bruit du baromètre)
VITESSE_ARRET = 0.5   # m/s : en dessous, on considère que l'athlète est arrêté


def coefficient(pente_pct: float) -> float:
    """Coût relatif de la course à cette pente, par rapport au plat (pente en %)."""
    courbe = lambda g: 0.98462 + 0.030266 * g + 0.0018814 * g ** 2 - 3.3882e-6 * g ** 3 - 4.5704e-7 * g ** 4
    g = max(-45.0, min(45.0, pente_pct))
    return max(0.5, courbe(g) / courbe(0))  # 1 sur le plat


def _series(details: dict[str, Any]) -> tuple[list[float], list[float], list[float]] | None:
    """Distance (m), altitude (m) et temps (s) point par point, depuis les détails Garmin."""
    index = {d.get("key"): d.get("metricsIndex") for d in details.get("metricDescriptors") or []}
    i_dist = index.get("sumDistance")
    i_alt = index.get("directElevation")
    i_temps = next((index[k] for k in ("sumElapsedDuration", "sumDuration", "sumMovingDuration") if k in index), None)
    i_horo = index.get("directTimestamp")
    if i_dist is None or i_alt is None or (i_temps is None and i_horo is None):
        return None
    dist, alt, temps = [], [], []
    for point in details.get("activityDetailMetrics") or []:
        m = point.get("metrics") or []
        try:
            d, a = m[i_dist], m[i_alt]
            t = m[i_temps] if i_temps is not None else m[i_horo] / 1000
        except (IndexError, TypeError):
            continue
        if d is None or a is None or t is None:
            continue
        dist.append(float(d)); alt.append(float(a)); temps.append(float(t))
    return (dist, alt, temps) if len(dist) > 10 else None


def calculer(details: dict[str, Any]) -> dict[str, float] | None:
    """VAP et allure en mouvement, en minutes par km, ou None si le profil manque."""
    series = _series(details)
    if not series:
        return None
    dist, alt, temps = series
    n = len(alt)
    lisse = [sum(alt[max(0, i - LISSAGE):i + LISSAGE + 1]) / len(alt[max(0, i - LISSAGE):i + LISSAGE + 1]) for i in range(n)]
    distance_eq = distance = temps_mouvement = 0.0
    debut = 0
    for i in range(1, n):
        dd, dt = dist[i] - dist[i - 1], temps[i] - temps[i - 1]
        if dt > 0 and dd / dt >= VITESSE_ARRET:
            temps_mouvement += dt
        # Pente mesurée par tronçons d'environ PAS_M mètres
        if dist[i] - dist[debut] >= PAS_M or i == n - 1:
            troncon = dist[i] - dist[debut]
            if troncon > 0:
                pente = (lisse[i] - lisse[debut]) / troncon * 100
                distance_eq += troncon * coefficient(pente)
                distance += troncon
            debut = i
    if distance < 500 or distance_eq <= 0 or temps_mouvement <= 0:
        return None
    return {
        "vap_min_km": round(temps_mouvement / 60 / (distance_eq / 1000), 2),
        "allure_mouvement_min_km": round(temps_mouvement / 60 / (distance / 1000), 2),
    }
