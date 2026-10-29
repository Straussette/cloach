"""Analyse par terrain d'une sortie de course ou de trail : plat, montée, descente.

Calculée à partir des mêmes détails Garmin que la VAP (un seul appel à Garmin), puis gardée avec
l'activité (colonne activites.terrains) :
- les meilleurs efforts par durée (30 s à 1 h) à plat, en montée et en descente : puissance,
  vitesse et vitesse verticale (VAM) les plus hautes tenues sur chaque durée ;
- pour chaque terrain, les moyennes : temps, distance, vitesse, VAP, puissance, fréquence cardiaque,
  cadence, longueur de foulée, et, si la montre les mesure, temps de contact et ratio vertical ;
  par tranche de pente (2-6 %, 6-12 %, plus de 12 %) pour la montée et la descente ;
- les montées (au moins 20 m de dénivelé), avec la puissance par tiers pour voir si l'athlète
  fléchit ou accélère en fin de montée.

profil() en fait la synthèse sur une période (records par durée, et leur évolution par rapport à
la période précédente) et en tire des constats simples, que Claude commente (outil profil_terrain).
Fonctions pures, sans accès réseau : faciles à tester.
"""

import re
import statistics
from typing import Any

from vap import LISSAGE, PAS_M, VITESSE_ARRET, coefficient

VERSION = 3           # à augmenter si la méthode change : l'app et le serveur recalculent alors
PLAT_PCT = 2.0        # entre −2 % et +2 % : plat
TRANCHES = ((2.0, 6.0, "2-6"), (6.0, 12.0, "6-12"), (12.0, 1000.0, "12+"))
MONTEE_MIN_M = 20.0   # dénivelé minimal d'une montée
TOLERANCE_M = 5.0     # replat ou petite descente toléré à l'intérieur d'une montée
PAUSE_S = 10.0        # au-delà, un écart entre deux points est une pause, pas du mouvement
TERRAINS = ("plat", "montee", "descente")
DUREES = (30, 60, 120, 300, 600, 1200, 1800, 3600)  # durées des meilleurs efforts, en secondes

# Mesures facultatives : nom → clés possibles dans les détails Garmin, par ordre de préférence
MESURES = {
    "puissance": ("directPower",),
    "fc": ("directHeartRate",),
    "cadence": ("directDoubleCadence", "directRunCadence"),
    "contact": ("directGroundContactTime",),
    "ratio_vertical": ("directVerticalRatio",),
}


# ---------- Lecture des détails Garmin ----------

def lire_series(details: dict[str, Any]) -> dict[str, list[Any]] | None:
    """Distance (m), altitude (m), temps (s) et mesures facultatives, point par point."""
    index = {d.get("key"): d.get("metricsIndex") for d in details.get("metricDescriptors") or []}
    i_dist, i_alt = index.get("sumDistance"), index.get("directElevation")
    i_temps = next((index[k] for k in ("sumElapsedDuration", "sumDuration", "sumMovingDuration") if k in index), None)
    i_horo = index.get("directTimestamp")
    if i_dist is None or i_alt is None or (i_temps is None and i_horo is None):
        return None
    i_mesures = {nom: next((index[k] for k in cles if k in index), None) for nom, cles in MESURES.items()}
    series: dict[str, list[Any]] = {"dist": [], "alt": [], "temps": [], **{nom: [] for nom in MESURES}}
    for point in details.get("activityDetailMetrics") or []:
        m = point.get("metrics") or []
        try:
            d, a = m[i_dist], m[i_alt]
            t = m[i_temps] if i_temps is not None else m[i_horo] / 1000
        except (IndexError, TypeError):
            continue
        if d is None or a is None or t is None:
            continue
        series["dist"].append(float(d)); series["alt"].append(float(a)); series["temps"].append(float(t))
        for nom, i in i_mesures.items():
            v = m[i] if i is not None and i < len(m) else None
            series[nom].append(float(v) if isinstance(v, (int, float)) and v > 0 else None)
    if len(series["dist"]) <= 10:
        return None
    # Certaines montres donnent la cadence d'une seule jambe : on la ramène en pas par minute
    cadences = [c for c in series["cadence"] if c]
    if cadences and statistics.median(cadences) < 120:
        series["cadence"] = [c * 2 if c else None for c in series["cadence"]]
    return series


# ---------- Analyse d'une sortie ----------

def _classer(pente: float) -> tuple[str, str | None]:
    if abs(pente) <= PLAT_PCT:
        return "plat", None
    terrain = "montee" if pente > 0 else "descente"
    return terrain, next(nom for bas, haut, nom in TRANCHES if bas < abs(pente) <= haut)


class _Cumul:
    """Totaux d'un terrain ou d'une tranche ; les moyennes sont pondérées par le temps."""

    def __init__(self) -> None:
        self.temps = self.distance = self.distance_eq = self.denivele = 0.0
        self.sommes = {nom: 0.0 for nom in MESURES}
        self.poids = {nom: 0.0 for nom in MESURES}

    def ajouter(self, temps: float, distance: float, pente: float, mesures: dict[str, tuple[float, float]]) -> None:
        self.temps += temps
        self.distance += distance
        self.distance_eq += distance * coefficient(pente)
        self.denivele += abs(distance * pente / 100)
        for nom, (somme, poids) in mesures.items():
            self.sommes[nom] += somme
            self.poids[nom] += poids

    def resume(self) -> dict[str, Any]:
        r: dict[str, Any] = {"temps_s": round(self.temps), "distance_m": round(self.distance), "denivele_m": round(self.denivele)}
        if self.temps > 0 and self.distance > 0:
            r["vitesse"] = round(self.distance / self.temps, 3)
            r["vap_min_km"] = round(self.temps / 60 / (self.distance_eq / 1000), 2)
        for nom in MESURES:
            if self.poids[nom] >= max(30.0, self.temps * 0.5):  # mesure présente sur l'essentiel du temps
                r[nom] = round(self.sommes[nom] / self.poids[nom], 1)
        if r.get("cadence") and r.get("vitesse"):
            r["foulee_m"] = round(r["vitesse"] * 60 / r["cadence"], 2)
        return r


def _mesures(s: dict[str, list[Any]], debut: int, fin: int, mouvement: list[float]) -> dict[str, tuple[float, float]]:
    """Sommes pondérées par le temps de chaque mesure entre deux points, et le temps où elle est connue."""
    resultat = {}
    for nom in MESURES:
        somme = poids = 0.0
        for i in range(debut + 1, fin + 1):
            v = s[nom][i]
            if v is not None and mouvement[i] > 0:
                somme += v * mouvement[i]
                poids += mouvement[i]
        resultat[nom] = (somme, poids)
    return resultat


def _reperer(lisse: list[float]) -> list[tuple[int, int]]:
    """Début et fin des montées d'au moins MONTEE_MIN_M de dénivelé, en tolérant de courts replats
    (pour les descentes : appeler avec l'altitude changée de signe)."""
    trouvees: list[tuple[int, int]] = []
    bas = haut = 0
    for i in range(1, len(lisse)):
        if lisse[i] >= lisse[haut]:
            haut = i
        elif lisse[haut] - lisse[i] > TOLERANCE_M:
            if lisse[haut] - lisse[bas] >= MONTEE_MIN_M:
                trouvees.append((bas, haut))
            bas = haut = i
        elif lisse[i] < lisse[bas]:
            bas = haut = i
    if lisse[haut] - lisse[bas] >= MONTEE_MIN_M:
        trouvees.append((bas, haut))
    return trouvees


def _meilleurs_efforts(s: dict[str, list[Any]], lisse: list[float], mouvement: list[float],
                       segments: list[tuple[int, int]], descente: bool = False) -> dict[str, dict[str, float]]:
    """Pour chaque durée, la puissance, la vitesse et la VAM les plus hautes tenues d'un seul tenant à
    l'intérieur des segments d'un terrain (chaque mesure pour elle-même, comme une courbe de puissance)."""
    meilleurs: dict[str, dict[str, float]] = {}
    signe = -1 if descente else 1
    for debut, fin in segments:
        # Cumuls en temps de mouvement : temps, distance, énergie (et temps où la puissance est connue)
        temps, dist, energie, connu = [0.0], [0.0], [0.0], [0.0]
        for i in range(debut + 1, fin + 1):
            dt = mouvement[i]
            p = s["puissance"][i] if dt else None
            temps.append(temps[-1] + dt)
            dist.append(dist[-1] + (s["dist"][i] - s["dist"][i - 1] if dt else 0))
            energie.append(energie[-1] + (p * dt if p else 0))
            connu.append(connu[-1] + (dt if p else 0))
        alt = lisse[debut:fin + 1]
        for duree in DUREES:
            if temps[-1] < duree:
                break
            m = meilleurs.setdefault(str(duree), {})
            gauche = 0
            for droite in range(1, len(temps)):
                # Fenêtre la plus courte qui dure au moins `duree`
                while gauche + 1 < droite and temps[droite] - temps[gauche + 1] >= duree:
                    gauche += 1
                duree_fenetre = temps[droite] - temps[gauche]
                if duree_fenetre < duree or duree_fenetre > duree * 1.1 + 10:
                    continue
                vitesse = (dist[droite] - dist[gauche]) / duree_fenetre
                vam = signe * (alt[droite] - alt[gauche]) / duree_fenetre * 3600
                if vitesse > m.get("vitesse", 0):
                    m["vitesse"] = round(vitesse, 3)
                if vam > m.get("vam", 0):
                    m["vam"] = round(vam)
                if connu[droite] - connu[gauche] >= duree_fenetre * 0.9:
                    puissance = (energie[droite] - energie[gauche]) / (connu[droite] - connu[gauche])
                    if puissance > m.get("puissance", 0):
                        m["puissance"] = round(puissance)
    return meilleurs


def _montees(s: dict[str, list[Any]], lisse: list[float], mouvement: list[float]) -> list[dict[str, Any]]:
    """Montées d'au moins MONTEE_MIN_M de dénivelé, en tolérant de courts replats."""
    montees = []
    for debut, fin in _reperer(lisse):
        distance = s["dist"][fin] - s["dist"][debut]
        temps = sum(mouvement[debut + 1:fin + 1])
        gain = lisse[fin] - lisse[debut]
        if distance <= 0 or temps <= 0:
            continue
        m: dict[str, Any] = {
            "debut_km": round(s["dist"][debut] / 1000, 2), "distance_m": round(distance), "denivele_m": round(gain),
            "pente": round(gain / distance * 100, 1), "duree_s": round(temps), "vam": round(gain / temps * 3600),
            "vitesse": round(distance / temps, 3),
        }
        mesures = _mesures(s, debut, fin, mouvement)
        for nom, (somme, poids) in mesures.items():
            if poids >= temps * 0.5:
                m[nom] = round(somme / poids, 1)
        fc = [s["fc"][i] for i in range(debut, fin + 1) if s["fc"][i]]
        if fc:
            m["fc_max"] = round(max(fc))
        if m.get("cadence"):
            m["foulee_m"] = round(m["vitesse"] * 60 / m["cadence"], 2)
        # Puissance par tiers du temps de montée : fléchit, régulier ou accélère
        if m.get("puissance"):
            tiers, cumul, coupes = [], 0.0, [debut]
            for i in range(debut + 1, fin + 1):
                cumul += mouvement[i]
                if len(coupes) < 3 and cumul >= temps * len(coupes) / 3:
                    coupes.append(i)
            coupes.append(fin)
            for a, b in zip(coupes, coupes[1:]):
                somme, poids = _mesures(s, a, b, mouvement)["puissance"]
                tiers.append(round(somme / poids) if poids else None)
            if len(tiers) == 3 and all(tiers):
                m["puissance_tiers"] = tiers
                ecart = (tiers[2] - tiers[0]) / tiers[0]
                m["fin"] = "flechit" if ecart < -0.05 else "accelere" if ecart > 0.05 else "regulier"
        montees.append(m)
    return montees


def analyser(details: dict[str, Any], sport: str = "course") -> dict[str, Any] | None:
    """Analyse par terrain d'une sortie de course ou de trail, ou None si son profil manque. À vélo
    (home trainer surtout), seulement les meilleurs efforts de puissance sur toute la sortie (« tout »),
    qui servent à estimer la FTP et à comparer avec la course (voir puissance.py)."""
    s = lire_series(details)
    if not s:
        return None
    n = len(s["alt"])
    alt = s["alt"]
    lisse = [sum(alt[max(0, i - LISSAGE):i + LISSAGE + 1]) / len(alt[max(0, i - LISSAGE):i + LISSAGE + 1]) for i in range(n)]
    # Temps en mouvement entre chaque point et le précédent (0 à l'arrêt ou pendant une pause)
    mouvement = [0.0] * n
    for i in range(1, n):
        dd, dt = s["dist"][i] - s["dist"][i - 1], s["temps"][i] - s["temps"][i - 1]
        if 0 < dt <= PAUSE_S and dd / dt >= VITESSE_ARRET:
            mouvement[i] = dt
    # Meilleurs efforts sur toute la sortie, terrains confondus : pour les seuils de puissance
    tout = _meilleurs_efforts(s, lisse, mouvement, [(0, n - 1)])
    if sport == "velo":
        tout = {d: {"puissance": m["puissance"]} for d, m in tout.items() if m.get("puissance")}
        return {"v": VERSION, "sport": "velo", "temps_s": round(sum(mouvement)), "efforts": {"tout": tout}} if tout else None

    terrains = {t: _Cumul() for t in TERRAINS}
    tranches = {(t, nom): _Cumul() for t in ("montee", "descente") for _, _, nom in TRANCHES}
    debut = 0
    plats: list[tuple[int, int]] = []  # tronçons plats qui se suivent, regroupés
    for i in range(1, n):
        # Même découpage que la VAP : pente mesurée par tronçons d'environ PAS_M mètres
        if s["dist"][i] - s["dist"][debut] >= PAS_M or i == n - 1:
            troncon = s["dist"][i] - s["dist"][debut]
            temps = sum(mouvement[debut + 1:i + 1])
            if troncon > 0 and temps > 0:
                pente = (lisse[i] - lisse[debut]) / troncon * 100
                terrain, tranche = _classer(pente)
                mesures = _mesures(s, debut, i, mouvement)
                terrains[terrain].ajouter(temps, troncon, pente, mesures)
                if terrain == "plat":
                    if plats and plats[-1][1] == debut:
                        plats[-1] = (plats[-1][0], i)
                    else:
                        plats.append((debut, i))
                if tranche:
                    tranches[(terrain, tranche)].ajouter(temps, troncon, pente, mesures)
            debut = i

    total = sum(c.temps for c in terrains.values())
    if total < 300:
        return None
    resultat: dict[str, Any] = {"v": VERSION, "temps_s": round(total)}
    for t, cumul in terrains.items():
        r = cumul.resume()
        if t != "plat":
            r["tranches"] = {nom: tranches[(t, nom)].resume() for _, _, nom in TRANCHES if tranches[(t, nom)].temps > 0}
        resultat[t] = r
    resultat["montees"] = _montees(s, lisse, mouvement)
    resultat["efforts"] = {
        "plat": _meilleurs_efforts(s, lisse, mouvement, plats),
        "montee": _meilleurs_efforts(s, lisse, mouvement, _reperer(lisse)),
        "descente": _meilleurs_efforts(s, lisse, mouvement, _reperer([-a for a in lisse]), descente=True),
        "tout": {d: {k: v for k, v in m.items() if k != "vam"} for d, m in tout.items()},
    }
    return resultat


# ---------- Profil sur plusieurs sorties ----------

CHAMPS_MOYENNES = ("vitesse", "vap_min_km", *MESURES)


def _fusionner(resumes: list[dict[str, Any]]) -> dict[str, Any]:
    """Moyennes pondérées par le temps de plusieurs résumés d'un même terrain (ou d'une même tranche)."""
    resumes = [r for r in resumes if r and r.get("temps_s")]
    temps = sum(r["temps_s"] for r in resumes)
    if not temps:
        return {"temps_s": 0}
    f: dict[str, Any] = {"temps_s": temps, "distance_m": sum(r.get("distance_m", 0) for r in resumes),
                         "denivele_m": sum(r.get("denivele_m", 0) for r in resumes)}
    f["vitesse"] = round(f["distance_m"] / temps, 3) if f["distance_m"] else None
    for champ in CHAMPS_MOYENNES:
        if champ == "vitesse":
            continue
        avec = [r for r in resumes if r.get(champ)]
        poids = sum(r["temps_s"] for r in avec)
        if poids >= temps * 0.5:
            f[champ] = round(sum(r[champ] * r["temps_s"] for r in avec) / poids, 2 if champ == "vap_min_km" else 1)
    if f.get("cadence") and f.get("vitesse"):
        f["foulee_m"] = round(f["vitesse"] * 60 / f["cadence"], 2)
    return f


def _efficacite(plat: dict[str, Any]) -> float | None:
    """Vitesse à plat pour 100 W : progresse quand on court plus vite avec la même puissance."""
    return round(plat["vitesse"] / plat["puissance"] * 100, 3) if plat.get("vitesse") and plat.get("puissance") else None


def _allure(vap: float) -> str:
    secondes = round(vap * 60)
    return f"{secondes // 60}:{secondes % 60:02d} /km"


def _valables(lignes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [l for l in lignes if isinstance(l.get("terrains"), dict) and l["terrains"].get("v") == VERSION
            and not l["terrains"].get("vide")]


def records(lignes: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
    """Meilleur effort de la période pour chaque terrain, durée et mesure, avec le jour et la sortie."""
    rec: dict[str, dict[str, dict[str, dict[str, Any]]]] = {t: {} for t in TERRAINS}
    for l in _valables(lignes):
        for t in TERRAINS:
            for duree, mesures in ((l["terrains"].get("efforts") or {}).get(t) or {}).items():
                for mesure, valeur in mesures.items():
                    case = rec[t].setdefault(duree, {})
                    if valeur > case.get(mesure, {}).get("valeur", 0):
                        case[mesure] = {"valeur": valeur, "jour": l["jour"], "sortie": l.get("nom")}
    return rec


def profil(lignes: list[dict[str, Any]], precedentes: list[dict[str, Any]] | None = None,
           poids: float | None = None, seuil: float | None = None) -> dict[str, Any]:
    """Synthèse des sorties { id, jour, nom, sport, terrains } d'une période (de la plus ancienne à la plus
    récente) ; `precedentes` : celles de la période d'avant, de même durée, pour mesurer l'évolution ;
    `poids` (kg) : pour ajouter les puissances en W/kg ; `seuil` (W) : seuil de puissance course, pour les %."""
    lignes, precedentes = _valables(lignes), _valables(precedentes or [])
    poids = poids if poids and 30 <= poids <= 200 else None
    par_kg = lambda w: round(w / poids, 2) if poids and w else None
    if not lignes:
        return {"sorties": 0}
    resultat: dict[str, Any] = {"sorties": len(lignes), "du": lignes[0]["jour"], "au": lignes[-1]["jour"]}
    if poids:
        resultat["poids_kg"] = poids
    total = sum(l["terrains"].get("temps_s", 0) for l in lignes)
    for t in TERRAINS:
        r = _fusionner([l["terrains"].get(t) for l in lignes])
        r["part_temps_pct"] = round(r["temps_s"] / total * 100) if total else 0
        if t != "plat":
            r["tranches"] = {nom: _fusionner([(l["terrains"].get(t) or {}).get("tranches", {}).get(nom) for l in lignes])
                             for _, _, nom in TRANCHES}
            r["tranches"] = {k: v for k, v in r["tranches"].items() if v["temps_s"] >= 60}
            for v in r["tranches"].values():
                if par_kg(v.get("puissance")):
                    v["w_kg"] = par_kg(v["puissance"])
        if par_kg(r.get("puissance")):
            r["w_kg"] = par_kg(r["puissance"])
        resultat[t] = r

    # Meilleurs efforts par durée (en secondes) et leur évolution par rapport à la période précédente
    actuels, avant = records(lignes), records(precedentes)
    for t in TERRAINS:
        for duree, mesures in actuels[t].items():
            for mesure, case in mesures.items():
                ancien = avant[t].get(duree, {}).get(mesure)
                if ancien:
                    case["avant"] = ancien["valeur"]
                    case["evolution_pct"] = round((case["valeur"] / ancien["valeur"] - 1) * 100, 1)
                if mesure == "puissance" and poids:
                    case["w_kg"] = par_kg(case["valeur"])
                if mesure == "puissance" and seuil:
                    case["pct_seuil"] = round(case["valeur"] / seuil * 100)
        actuels[t] = {d: {m: c for m, c in v.items() if not (t == "plat" and m == "vam")} for d, v in actuels[t].items()}
    resultat["meilleurs_efforts"] = actuels
    resultat["periode_precedente"] = {"sorties": len(precedentes)}

    montees = [m for l in lignes for m in l["terrains"].get("montees") or []]
    resultat["montees"] = {"nombre": len(montees)}
    if montees:
        resultat["montees"].update(vam_mediane=round(statistics.median(m["vam"] for m in montees)),
                                   pente_mediane=round(statistics.median(m["pente"] for m in montees), 1))
    fins = [m["fin"] for m in montees if m.get("fin")]
    if fins:
        resultat["montees"]["fin"] = {k: fins.count(k) for k in ("flechit", "regulier", "accelere")}

    # Efficacité à plat (vitesse pour 100 W) : cette période contre la précédente
    apres = _efficacite(_fusionner([l["terrains"].get("plat") for l in lignes]))
    avant_eff = _efficacite(_fusionner([l["terrains"].get("plat") for l in precedentes])) if precedentes else None
    if avant_eff and apres:
        resultat["efficacite_plat"] = {"avant": avant_eff, "apres": apres, "evolution_pct": round((apres / avant_eff - 1) * 100, 1)}
    resultat["constats"] = constats(resultat)
    return resultat


def constats(p: dict[str, Any]) -> list[str]:
    """Constats simples, en une phrase, quand les données suffisent (au moins 5 min sur chaque terrain comparé)."""
    phrases = []
    plat, montee, descente = p.get("plat", {}), p.get("montee", {}), p.get("descente", {})
    assez = lambda r: r.get("temps_s", 0) >= 300
    raide = _fusionner([montee.get("tranches", {}).get(k) for k in ("6-12", "12+")])
    if assez(plat) and raide.get("temps_s", 0) >= 180 and plat.get("cadence") and raide.get("cadence"):
        if raide["cadence"] < plat["cadence"] - 5:
            phrases.append(f"En montée raide, ta cadence baisse ({raide['cadence']:.0f} contre {plat['cadence']:.0f} pas/min à plat) : "
                           "tu allonges les poussées au lieu de raccourcir les pas.")
        else:
            phrases.append(f"En montée raide, tu gardes ta cadence ({raide['cadence']:.0f} pas/min, {plat['cadence']:.0f} à plat) "
                           "en raccourcissant les pas : c'est la bonne façon de monter.")
    if assez(plat) and assez(descente) and plat.get("foulee_m") and descente.get("foulee_m") and plat.get("cadence") and descente.get("cadence"):
        if descente["foulee_m"] > plat["foulee_m"] * 1.10 and descente["cadence"] <= plat["cadence"] + 2:
            phrases.append(f"En descente, tu allonges la foulée ({descente['foulee_m']:.2f} m contre {plat['foulee_m']:.2f} m à plat) "
                           "sans monter la cadence : plus de freinage et d'impact.")
    if assez(montee) and assez(descente) and montee.get("vap_min_km") and descente.get("vap_min_km"):
        ecart = descente["vap_min_km"] / montee["vap_min_km"] - 1
        if ecart > 0.05:
            phrases.append(f"Tu montes mieux que tu ne descends : à effort égal, ta VAP est de {_allure(montee['vap_min_km'])} "
                           f"en montée et de {_allure(descente['vap_min_km'])} en descente.")
        elif ecart < -0.05:
            phrases.append(f"Tu descends mieux que tu ne montes : à effort égal, ta VAP est de {_allure(descente['vap_min_km'])} "
                           f"en descente et de {_allure(montee['vap_min_km'])} en montée.")
    fins = p.get("montees", {}).get("fin")
    if fins and sum(fins.values()) >= 3:
        n = sum(fins.values())
        if fins["flechit"] / n >= 0.5:
            phrases.append(f"Tu fléchis en fin de montée ({fins['flechit']} montées sur {n}) : pars un peu moins fort.")
        elif fins["accelere"] / n >= 0.5:
            phrases.append(f"Tu finis plus fort que tu ne commences ({fins['accelere']} montées sur {n}) : "
                           "tu peux te permettre de partir un peu plus vite.")
    # Meilleurs efforts qui bougent nettement par rapport à la période précédente
    reperes = (("montee", "300", "puissance", "ta meilleure puissance sur 5 min en montée", "W"),
               ("montee", "1200", "puissance", "ta meilleure puissance sur 20 min en montée", "W"),
               ("montee", "600", "vam", "ta meilleure VAM sur 10 min", "m/h"),
               ("plat", "1200", "vitesse", "ta meilleure vitesse sur 20 min à plat", None),
               ("descente", "120", "vitesse", "ta meilleure vitesse sur 2 min en descente", None))
    efforts = p.get("meilleurs_efforts", {})
    for terrain, duree, mesure, texte, unite in reperes:
        case = efforts.get(terrain, {}).get(duree, {}).get(mesure)
        if case and case.get("evolution_pct") is not None and abs(case["evolution_pct"]) >= 3:
            valeur = f"{case['valeur']} {unite}" if unite else _allure(1000 / 60 / case["valeur"])
            if case.get("w_kg"):
                valeur += f", {case['w_kg']:.2f} W/kg"
            phrases.append(f"{texte[0].upper()}{texte[1:]} {'progresse' if case['evolution_pct'] > 0 else 'recule'} "
                           f"({valeur}, {case['evolution_pct']:+.1f} % par rapport à la période précédente).")
    evolution = p.get("efficacite_plat", {}).get("evolution_pct")
    if evolution is not None and abs(evolution) >= 3:
        phrases.append(f"À plat, tu cours {'plus' if evolution > 0 else 'moins'} vite pour les mêmes watts "
                       f"({evolution:+.1f} % par rapport à la période précédente).")
    return [re.sub(r"(\d)\.(\d)", r"\1,\2", f) for f in phrases]  # virgule décimale
