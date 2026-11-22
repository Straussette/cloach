"""Garde-fous d'un plan d'entraînement : vérifications faites quand Claude enregistre un plan, renvoyées sous forme
d'avertissements pour qu'il le corrige (ou explique son choix à l'athlète).

Fonctions pures, testables sans réseau. Les volumes sont des durées (minutes), la seule mesure commune à tous les
sports ; seules les semaines complètes du plan sont comparées (la première commence le jour même, souvent en fin de
semaine : la ramener à 7 jours grossirait le volume).
"""

from datetime import date, timedelta
from typing import Any

DURES = {"soutenu", "tres-soutenu"}
HAUSSE_MAX = 1.15          # d'une semaine à l'autre (règle des 10 %, avec un peu de marge)
HAUSSE_MAX_REEL = 1.25     # première semaine du plan par rapport aux 4 dernières semaines réelles
DURES_MAX_SEMAINE = 3
ALLEGEE = 0.8              # une semaine allégée fait au plus 80 % de la plus grosse


def _jour(texte: str | None) -> date | None:
    try:
        return date.fromisoformat(str(texte)[:10])
    except (TypeError, ValueError):
        return None


def _minutes(s: dict[str, Any]) -> float:
    return 0.0 if s.get("intensite") == "repos" else float(s.get("duree") or 0)


def _fr(d: date) -> str:
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    return f"{jours[d.weekday()]} {d.day}/{d.month}"


def volumes_plan(semaines: list[dict[str, Any]]) -> list[tuple[int, float]]:
    """(numéro de semaine, minutes) des semaines complètes du plan (au moins 6 jours)."""
    vols = []
    for i, w in enumerate(semaines):
        debut = _jour(w.get("debut"))
        fin = _jour(semaines[i + 1].get("debut")) if i + 1 < len(semaines) else (debut + timedelta(days=7) if debut else None)
        if debut and fin and (fin - debut).days >= 6:
            vols.append((i + 1, sum(_minutes(s) for s in w.get("seances") or [])))
    return vols


def volumes_reels(activites: list[dict[str, Any]], aujourdhui: date, semaines: int = 4) -> list[float]:
    """Minutes d'activité des dernières semaines complètes (lundi-dimanche), la plus ancienne d'abord."""
    lundi = aujourdhui - timedelta(days=aujourdhui.weekday())
    vols = []
    for k in range(semaines, 0, -1):
        debut, fin = lundi - timedelta(days=7 * k), lundi - timedelta(days=7 * (k - 1))
        vols.append(sum(float(a.get("duree_min") or 0) for a in activites
                        if (j := _jour(a.get("jour"))) and debut <= j < fin))
    return vols


def verifier(semaines: list[dict[str, Any]], activites: list[dict[str, Any]], profil: dict[str, Any],
             aujourdhui: date, evenements: list[dict[str, Any]] | None = None) -> list[str]:
    avertissements: list[str] = []
    completes = volumes_plan(semaines)
    vols = [v for _, v in completes]
    reels = volumes_reels(activites, aujourdhui)
    moyenne_reelle = sum(reels) / len(reels) if any(reels) else 0.0

    # 1. Progression de la charge (semaines complètes)
    for k, (numero, v) in enumerate(completes):
        if k == 0:
            if moyenne_reelle > 0 and v > moyenne_reelle * HAUSSE_MAX_REEL + 30:
                avertissements.append(
                    f"Semaine {numero} : {v / 60:.1f} h prévues contre {moyenne_reelle / 60:.1f} h en moyenne ces 4 dernières "
                    "semaines réelles : hausse trop brusque, rapproche-toi de ce que l'athlète fait vraiment.")
            continue
        reference = max(vols[:k] + ([moyenne_reelle] if moyenne_reelle else []))
        if reference and v > reference * HAUSSE_MAX:
            avertissements.append(
                f"Semaine {numero} : {v / 60:.1f} h, soit +{(v / reference - 1) * 100:.0f} % sur la plus grosse "
                "semaine précédente : au plus +10 % environ d'une semaine à l'autre.")

    # 2. Séances dures : pas deux jours de suite, pas plus de 3 par semaine
    seances = [s for w in semaines for s in w.get("seances") or []]
    dures = sorted({j for s in seances if s.get("intensite") in DURES and (j := _jour(s.get("jour")))})
    collees = [(a, b) for a, b in zip(dures, dures[1:]) if (b - a).days == 1]
    if collees:
        avertissements.append("Séances dures deux jours de suite : " + ", ".join(f"{_fr(a)} et {_fr(b)}" for a, b in collees)
                              + ". Intercale un jour facile ou de repos, sauf choix délibéré (bloc, week-end choc) à expliquer.")
    for i, w in enumerate(semaines):
        n = sum(1 for s in w.get("seances") or [] if s.get("intensite") in DURES)
        if n > DURES_MAX_SEMAINE:
            avertissements.append(f"Semaine {i + 1} : {n} séances dures, c'est trop pour rester polarisé (3 au plus, 2 en général).")

    # 3. Semaine allégée : pas plus de 3 semaines de charge d'affilée
    suite = reels + vols
    if len(suite) >= 4:
        plus_grosse = max(suite)
        depuis = 0
        for v in suite:
            depuis = 0 if v <= plus_grosse * ALLEGEE else depuis + 1
        if depuis >= 5:
            avertissements.append(f"Aucune semaine allégée depuis {depuis} semaines (réelles et prévues) : prévois-en une "
                                  "(environ -30 % de volume, intensité réduite) dans ce plan.")

    # 4. Calendrier : affûtage avant un objectif A, mini-affûtage avant un B, pas de séance dure collée à un C,
    # récupération après une course, rien de prévu pendant une indisponibilité
    fin_plan = max((j for s in seances if (j := _jour(s.get("jour")))), default=None)
    pic = max(vols + reels) if (vols or reels) else 0
    evts = evenements if evenements is not None else (
        [{"nom": "l'objectif", "date": profil.get("dateObjectif"), "type": "A"}] if profil.get("dateObjectif") else [])
    for e in evts:
        d = _jour(e.get("date"))
        if not d or not fin_plan or not (aujourdhui - timedelta(days=7) <= d <= fin_plan + timedelta(days=1)):
            continue
        nom, type_ = e.get("nom") or "la course", e.get("type")

        def entre(debut: date, fin: date) -> list[dict[str, Any]]:
            return [s for s in seances if (j := _jour(s.get("jour"))) and debut <= j <= fin]
        if type_ == "indispo":
            fin_indispo = _jour(e.get("fin")) or d
            prevues = [s for s in entre(d, fin_indispo) if _minutes(s) > 0]
            if prevues:
                avertissements.append(f"Indisponible du {_fr(d)} au {_fr(fin_indispo)} ({nom}) : {len(prevues)} séance(s) prévue(s) "
                                      "pendant cette période ; retire-les ou adapte-les.")
            continue
        if type_ == "A":
            if any(s.get("intensite") == "tres-soutenu" for s in entre(d - timedelta(days=2), d - timedelta(days=1))):
                avertissements.append(f"Séance très soutenue dans les 2 jours avant {nom} ({_fr(d)}) : garde seulement "
                                      "quelques accélérations courtes.")
            semaine_avant = sum(_minutes(s) for s in entre(d - timedelta(days=7), d - timedelta(days=1)))
            if pic and semaine_avant > pic * 0.7:
                avertissements.append(f"Semaine avant {nom} : {semaine_avant / 60:.1f} h, trop près du pic ({pic / 60:.1f} h). "
                                      "Affûtage : environ -40 à -60 % de volume, en gardant un peu d'intensité.")
            apres = [s for s in entre(d + timedelta(days=1), d + timedelta(days=7)) if s.get("intensite") in DURES]
            if apres:
                avertissements.append(f"Séance dure dans la semaine après {nom} ({_fr(d)}) : récupération d'abord.")
        elif type_ == "B":
            if any(s.get("intensite") in DURES for s in entre(d - timedelta(days=3), d - timedelta(days=1))):
                avertissements.append(f"Séance dure dans les 3 jours avant {nom} (course B, {_fr(d)}) : mini-affûtage, "
                                      "seulement des rappels courts.")
            if any(s.get("intensite") in DURES for s in entre(d + timedelta(days=1), d + timedelta(days=3))):
                avertissements.append(f"Séance dure dans les 3 jours après {nom} ({_fr(d)}) : laisse récupérer.")
        elif type_ == "C":
            if any(s.get("intensite") in DURES for s in entre(d - timedelta(days=1), d - timedelta(days=1))
                   ) or any(s.get("intensite") in DURES for s in entre(d + timedelta(days=1), d + timedelta(days=1))):
                avertissements.append(f"Séance dure collée à {nom} (course de préparation, {_fr(d)}) : la course compte "
                                      "comme la séance dure, garde la veille et le lendemain faciles.")
    return avertissements
