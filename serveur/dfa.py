"""DFA α1 : seuils 1 et 2 d'après la variabilité de la fréquence cardiaque pendant l'effort.

L'exposant α1 de l'analyse des fluctuations redressées (DFA) des intervalles entre battements (RR)
baisse quand l'intensité monte : autour de 1 à faible intensité, 0,75 au seuil 1, 0,5 au seuil 2
(Rogers et al., 2021). Il faut l'enregistrement battement par battement : une ceinture cardio et
l'option « Enregistrer la VFC » (ou équivalent) sur la montre ; le fichier d'origine (FIT) de la
sortie contient alors des messages « hrv ».

Méthode : intervalles RR nettoyés (hors 0,3-2 s, écart de plus de 20 % à la médiane locale), fenêtres
de 2 min toutes les 30 s (moins de 5 % d'artefacts), α1 par DFA sur des boîtes de 4 à 16 battements,
FC moyenne de la fenêtre ; puis droite α1 = f(FC) sur la sortie, et FC où elle vaut 0,75 et 0,5.
Une sortie compte si la FC couvre au moins 20 bpm et que la corrélation est nette (r ≤ −0,5).
"""

import io
import math
import statistics
import zipfile
from typing import Any

FENETRE_S = 120.0
PAS_S = 30.0
BOITES = range(4, 17)
ARTEFACTS_MAX = 0.05
ALPHA_S1, ALPHA_S2 = 0.75, 0.5


def lire_rr(contenu: bytes) -> list[float]:
    """Intervalles RR (secondes) d'un fichier FIT, éventuellement dans une archive zip (téléchargement
    « original » de Garmin Connect). Liste vide si la montre ne les a pas enregistrés."""
    import fitdecode  # chargé à la demande : seul le DFA s'en sert

    if contenu[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(contenu)) as archive:
            fit = next((n for n in archive.namelist() if n.lower().endswith(".fit")), None)
            if not fit:
                return []
            contenu = archive.read(fit)
    rr: list[float] = []
    with fitdecode.FitReader(io.BytesIO(contenu), check_crc=fitdecode.CrcCheck.DISABLED) as lecteur:
        for trame in lecteur:
            if isinstance(trame, fitdecode.FitDataMessage) and trame.name == "hrv" and trame.has_field("time"):
                valeurs = trame.get_value("time")
                for v in valeurs if isinstance(valeurs, (list, tuple)) else [valeurs]:
                    if isinstance(v, (int, float)) and 0 < v < 65:  # 65,535 = valeur absente
                        rr.append(float(v))
    return rr


def nettoyer(rr: list[float]) -> tuple[list[float], list[bool]]:
    """Garde les intervalles plausibles ; renvoie aussi, pour chaque intervalle d'origine, s'il est un artefact."""
    artefacts = []
    propres = []
    for i, v in enumerate(rr):
        voisins = [x for x in rr[max(0, i - 5):i + 6] if 0.3 <= x <= 2.0]
        mediane = statistics.median(voisins) if voisins else v
        mauvais = not (0.3 <= v <= 2.0) or abs(v - mediane) > 0.2 * mediane
        artefacts.append(mauvais)
        if not mauvais:
            propres.append(v)
    return propres, artefacts


def alpha1(rr: list[float]) -> float | None:
    """Exposant α1 de la DFA (boîtes de 4 à 16 battements)."""
    n = len(rr)
    if n < 50:
        return None
    moyenne = sum(rr) / n
    y, cumul = [], 0.0
    for v in rr:
        cumul += v - moyenne
        y.append(cumul)
    logs = []
    for taille in BOITES:
        boites = n // taille
        if boites < 2:
            continue
        somme = 0.0
        xs = list(range(taille))
        mx = (taille - 1) / 2
        vx = sum((x - mx) ** 2 for x in xs)
        for b in range(boites):
            seg = y[b * taille:(b + 1) * taille]
            my = sum(seg) / taille
            pente = sum((x - mx) * (s - my) for x, s in zip(xs, seg)) / vx
            somme += sum((s - (my + pente * (x - mx))) ** 2 for x, s in zip(xs, seg))
        f = math.sqrt(somme / (boites * taille))
        if f > 0:
            logs.append((math.log(taille), math.log(f)))
    if len(logs) < 5:
        return None
    mx = sum(x for x, _ in logs) / len(logs)
    my = sum(y for _, y in logs) / len(logs)
    return sum((x - mx) * (y - my) for x, y in logs) / sum((x - mx) ** 2 for x, _ in logs)


def fenetres(rr: list[float]) -> list[tuple[float, float]]:
    """(FC moyenne, α1) de chaque fenêtre de 2 min, toutes les 30 s."""
    _, artefacts = nettoyer(rr)
    temps = []
    t = 0.0
    for v in rr:
        t += v
        temps.append(t)
    points = []
    debut = 0.0
    i0 = 0
    while debut + FENETRE_S <= (temps[-1] if temps else 0):
        while i0 < len(temps) and temps[i0] < debut:
            i0 += 1
        i1 = i0
        while i1 < len(temps) and temps[i1] < debut + FENETRE_S:
            i1 += 1
        idx = range(i0, i1)
        if len(idx) >= 60:
            mauvais = sum(1 for i in idx if artefacts[i])
            if mauvais / len(idx) <= ARTEFACTS_MAX:
                propres = [rr[i] for i in idx if not artefacts[i]]
                a = alpha1(propres)
                if a is not None and 0.1 < a < 2.0:
                    points.append((60 / (sum(propres) / len(propres)), a))
        debut += PAS_S
    return points


def estimer(points: list[tuple[float, float]]) -> dict[str, Any]:
    """FC aux seuils d'après la droite α1 = f(FC) d'une sortie."""
    if len(points) < 10:
        return {"fenetres": len(points), "valable": False, "raison": "trop peu de fenêtres exploitables"}
    fcs = [p[0] for p in points]
    etendue = max(fcs) - min(fcs)
    n = len(points)
    mx, my = sum(fcs) / n, sum(p[1] for p in points) / n
    vx = sum((x - mx) ** 2 for x in fcs)
    vy = sum((y - my) ** 2 for _, y in points)
    if vx <= 0 or vy <= 0:
        return {"fenetres": n, "valable": False, "raison": "FC trop constante"}
    cov = sum((x - mx) * (y - my) for x, y in points)
    b = cov / vx
    r = cov / math.sqrt(vx * vy)
    resultat: dict[str, Any] = {"fenetres": n, "etendue_fc": round(etendue), "correlation": round(r, 2)}
    if etendue < 20 or r > -0.5 or b >= 0:
        return {**resultat, "valable": False,
                "raison": "intensité pas assez variée (il faut monter progressivement en effort)" if etendue < 20
                else "lien FC / α1 trop faible"}
    a0 = my - b * mx
    return {**resultat, "valable": True, "fc_s1": round((ALPHA_S1 - a0) / b), "fc_s2": round((ALPHA_S2 - a0) / b)}


def synthese(sorties: list[dict[str, Any]], famille_de) -> dict[str, Any]:
    """Médiane des seuils des sorties valables, par famille de sports."""
    resultat: dict[str, Any] = {}
    for f in ("pied", "velo"):
        valables = [s for s in sorties if s.get("valable") and famille_de(s.get("sport")) == f]
        if valables:
            resultat[f] = {"fc_s1": round(statistics.median(s["fc_s1"] for s in valables)),
                           "fc_s2": round(statistics.median(s["fc_s2"] for s in valables)), "sorties": len(valables)}
    return resultat
