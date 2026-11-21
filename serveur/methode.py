"""Méthode d'entraînement et bibliothèque de séances types : la référence que Claude consulte (outils methode et
seances_types) au lieu d'improviser, pour que les plans soient cohérents d'un athlète et d'une conversation à l'autre.

Sources : Seiler (polarisation), Daniels et Pfitzinger (course sur route), Koop et Roche (trail, ultra), Friel et
Coggan (vélo, triathlon, puissance), Mujika (affûtage), Gabbett (charge aiguë / chronique). Les cibles des séances
sont en zones de FC Cloach (zone_fc 1 à 5 : récupération, endurance, tempo, seuil, VO2max) : à convertir en bornes
de FC, d'allure ou de puissance de l'athlète d'après mes_zones.
"""

from typing import Any

METHODE: dict[str, dict[str, str]] = {
    "general": {
        "principes": (
            "Polarisation : environ 80 % du temps en zones 1-2 (facile, conversation possible), 20 % en zones 4-5 ; peu de "
            "zone 3, sauf en phase spécifique pour un effort long (marathon, ultra, cyclosportive). "
            "Progressivité : au plus +10 % de volume d'une semaine à l'autre ; rapport charge aiguë (7 j) / chronique (28 j) "
            "entre 0,8 et 1,3. Cycles de 3 semaines de charge + 1 allégée (-30 % de volume, intensité réduite), ou 2 + 1 "
            "après 45 ans, en reprise ou si la récupération est lente. "
            "Deux séances dures jamais collées, sauf bloc délibéré ; 48 h entre deux séances clés. "
            "La régularité prime : mieux vaut 4 séances par semaine toute l'année que 7 pendant un mois."),
        "periodisation": (
            "Foncier (8-12 sem.) : volume facile, technique, renfo général, une séance soutenue au plus. "
            "Développement (6-8 sem.) : volume encore en hausse, 2 séances de qualité (seuil, VO2max), renfo spécifique. "
            "Spécifique (3-6 sem.) : séances au plus près de l'effort visé (allure, terrain, durée), sorties longues clés, "
            "répétition de la nutrition et du matériel. Affûtage : voir ce sujet. Récupération : 1 à 2 semaines faciles après "
            "un objectif (1 jour par 10 km de course longue pour reprendre l'intensité). Un objectif B ou C se prépare "
            "avec un mini-affûtage de 3 à 5 jours, sans casser le cycle."),
        "affutage": (
            "Mujika : réduire le volume de 40 à 60 %, garder la fréquence des séances et un peu d'intensité (courtes "
            "accélérations ou fractions à allure de course), sur 7 à 10 jours (route, vélo) à 14 jours (marathon, ultra, "
            "Ironman). Dernière séance clé 7 à 10 jours avant ; rien de nouveau la dernière semaine ; repos ou décrassage "
            "l'avant-veille, quelques accélérations la veille."),
        "recuperation": (
            "Signaux d'alerte : VFC sous la normale plusieurs jours, FC de repos +5 bpm, sommeil dégradé, jambes lourdes, "
            "motivation en baisse : alléger (séance facile ou repos) plutôt que forcer. Après une maladie : autant de jours "
            "faciles que de jours malade. Douleur qui modifie la foulée ou le pédalage : arrêt et avis médical."),
        "renfo": (
            "1 à 2 séances par semaine, 30 à 45 min, plus lourd loin de l'objectif, entretien près de lui, rien de lourd "
            "dans les 48 h avant une séance clé. Excentrique des quadriceps et mollets pour le trail, chaîne postérieure "
            "et gainage pour tous, haut du dos pour le vélo, pliométrie légère pour la course."),
    },
    "course": {
        "principes": (
            "Daniels : allures E (endurance), M (marathon), T (seuil, tenue ~1 h), I (VO2max, 3-5 min), R (vitesse, 200-400 m). "
            "Sortie longue : 20-30 % du volume hebdomadaire, plafonnée à 2 h 30-3 h en marathon. Seuil : 20-40 min cumulées "
            "par séance (fractions de 6-15 min). VO2max : 12-20 min cumulées. Ajouter des lignes droites (6-8 × 20 s) "
            "après 1 ou 2 footings par semaine."),
        "10km_semi": (
            "Spécifique 10 km : fractions à allure 10 km (5 × 2 km, 3 × 3 km), VMA (6 × 1000 m). Semi : seuil long "
            "(3 × 15 min, 2 × 5 km à allure semi), sortie longue 1 h 45-2 h dont 30-40 min à allure semi."),
        "marathon": (
            "Pfitzinger : 10 à 18 semaines, sorties longues jusqu'à 32-35 km, séances à allure marathon de 12 à 25 km "
            "(dans la sortie longue en fin de cycle), seuil chaque semaine, VO2max en phase de développement. Affûtage de "
            "2 à 3 semaines (-20 %, -40 %, -60 %). Tester ravitaillement et chaussures sur les sorties longues."),
    },
    "trail": {
        "principes": (
            "Koop, Roche : le volume se compte en heures et en D+ plutôt qu'en km. Spécificité du terrain : côtes longues "
            "au seuil, marche rapide avec ou sans bâtons, descentes techniques (et renfo excentrique pour les encaisser). "
            "Sortie longue clé : 2 h 30 à 5 h selon l'objectif, avec le D+ et le terrain de la course ; week-end choc "
            "(deux sorties longues enchaînées) en phase spécifique pour l'ultra. VAP pour juger l'allure en côte."),
        "ultra": (
            "Au-delà de 50 km : le facteur limitant est la durée (alimentation, descentes, fatigue musculaire) plus que la "
            "VO2max. Peu d'intensité en spécifique, beaucoup d'endurance en côte, marche active, répétition de "
            "l'alimentation (60-90 g de glucides par heure). Affûtage de 2 semaines."),
    },
    "velo": {
        "principes": (
            "Coggan : zones en % de la FTP (Z2 56-75 %, sweet spot 88-94 %, seuil 95-105 %, VO2max 106-120 %). "
            "Volume facile en Z2 (sorties longues de 3 à 5 h pour la longue distance), 2 séances de qualité par semaine. "
            "Sans capteur de puissance, travailler à la FC (et à la sensation) : le seuil se tient environ 1 h. "
            "Home trainer : séances courtes et précises (sweet spot, VO2max) ; la sortie longue dehors."),
        "longue_distance": (
            "Cyclosportive, Étape du Tour, raid de plusieurs jours : enchaîner les grosses journées (2-3 jours de suite) en "
            "spécifique, cols longs au tempo ou sweet spot (2 × 20-30 min), entraîner l'alimentation (60-90 g/h). "
            "Raid VTT : ajouter technique de descente et force (montées raides à basse cadence)."),
    },
    "triathlon": {
        "principes": (
            "Friel : répartir selon le point faible et le format ; natation 2-3 fois par semaine (technique d'abord), "
            "vélo = gros du volume, course protégée (fréquence plutôt que volume). Enchaînements vélo-course chaque semaine "
            "en spécifique (10-30 min de course après le vélo). Half ironman : sorties vélo de 2 h 30-3 h 30 avec des "
            "blocs à allure course, sortie longue course de 1 h 30-1 h 45. Allures natation d'après la CSS (vitesse critique)."),
    },
}

# Séances types : nom, sport, but, phases conseillées, durée (min), intensité, étapes (format Cloach, cibles en zones de FC)
_E = lambda type_, duree_s, zone=None, note=None: {k: v for k, v in {  # noqa: E731
    "type": type_, "duree_s": duree_s, "cible": {"type": "zone_fc", "zone": zone} if zone else None, "note": note}.items() if v is not None}
_R = lambda n, *etapes: {"type": "repetition", "repetitions": n, "etapes": list(etapes)}  # noqa: E731

SEANCES: list[dict[str, Any]] = [
    # Course et trail
    {"nom": "Endurance fondamentale", "sport": "course", "but": "endurance", "phases": ["foncier", "developpement", "specifique", "affutage"],
     "duree": 50, "intensite": "facile", "etapes": [_E("effort", 3000, 2, "Conversation possible")]},
    {"nom": "Footing + lignes droites", "sport": "course", "but": "vitesse", "phases": ["foncier", "developpement", "affutage"],
     "duree": 45, "intensite": "facile", "etapes": [_E("effort", 2100, 2), _R(6, _E("effort", 20, 5, "Rapide et relâché"), _E("recuperation", 60, 1)), _E("retour_au_calme", 300, 1)]},
    {"nom": "Seuil 3 × 10 min", "sport": "course", "but": "seuil", "phases": ["developpement", "specifique"],
     "duree": 60, "intensite": "soutenu", "etapes": [_E("echauffement", 900, 2), _R(3, _E("effort", 600, 4), _E("recuperation", 120, 1)), _E("retour_au_calme", 600, 1)]},
    {"nom": "Tempo 25 min", "sport": "course", "but": "seuil", "phases": ["developpement", "specifique"],
     "duree": 55, "intensite": "soutenu", "etapes": [_E("echauffement", 900, 2), _E("effort", 1500, 3, "Allure semi-marathon environ"), _E("retour_au_calme", 600, 1)]},
    {"nom": "VMA 30/30", "sport": "course", "but": "vo2max", "phases": ["foncier", "developpement"],
     "duree": 50, "intensite": "tres-soutenu", "etapes": [_E("echauffement", 1200, 2), _R(10, _E("effort", 30, 5), _E("recuperation", 30, 1)), _E("recuperation", 180, 1, "Récupération entre les deux séries"), _R(10, _E("effort", 30, 5), _E("recuperation", 30, 1)), _E("retour_au_calme", 600, 1)]},
    {"nom": "VO2max 5 × 3 min", "sport": "course", "but": "vo2max", "phases": ["developpement", "specifique"],
     "duree": 55, "intensite": "tres-soutenu", "etapes": [_E("echauffement", 1200, 2), _R(5, _E("effort", 180, 5), _E("recuperation", 150, 1)), _E("retour_au_calme", 600, 1)]},
    {"nom": "Côtes courtes 10 × 30 s", "sport": "course", "but": "force", "phases": ["foncier", "developpement"],
     "duree": 50, "intensite": "soutenu", "etapes": [_E("echauffement", 1200, 2), _R(10, _E("effort", 30, 5, "Côte raide, foulée dynamique"), _E("recuperation", 90, 1, "Descente en trottinant")), _E("retour_au_calme", 600, 1)]},
    {"nom": "Sortie longue progressive", "sport": "course", "but": "endurance", "phases": ["developpement", "specifique"],
     "duree": 110, "intensite": "modere", "etapes": [_E("effort", 4800, 2), _E("effort", 1200, 3, "Allure marathon environ"), _E("retour_au_calme", 600, 1)]},
    {"nom": "Côtes longues au seuil", "sport": "trail", "but": "seuil", "phases": ["developpement", "specifique"],
     "duree": 75, "intensite": "soutenu", "etapes": [_E("echauffement", 1200, 2), _R(5, _E("effort", 360, 4, "Montée régulière, bâtons si course avec"), _E("recuperation", 240, 1, "Descente souple")), _E("retour_au_calme", 600, 1)]},
    {"nom": "Sortie longue en D+", "sport": "trail", "but": "endurance", "phases": ["foncier", "developpement", "specifique"],
     "duree": 180, "intensite": "modere", "etapes": [_E("effort", 10800, 2, "Marche active dans le raide, ravitaillement toutes les 30-45 min")]},
    {"nom": "Descentes techniques", "sport": "trail", "but": "technique", "phases": ["developpement", "specifique"],
     "duree": 70, "intensite": "modere", "etapes": [_E("echauffement", 1200, 2), _R(6, _E("effort", 120, 3, "Montée facile"), _E("effort", 180, 3, "Descente technique, regard loin, petits appuis")), _E("retour_au_calme", 600, 1)]},
    # Vélo
    {"nom": "Endurance Z2", "sport": "velo", "but": "endurance", "phases": ["foncier", "developpement", "specifique"],
     "duree": 150, "intensite": "facile", "etapes": [_E("effort", 9000, 2, "Cadence 85-95, manger toutes les 30 min")]},
    {"nom": "Sweet spot 3 × 15 min", "sport": "velo", "but": "seuil", "phases": ["foncier", "developpement"],
     "duree": 80, "intensite": "soutenu", "etapes": [_E("echauffement", 900, 2), _R(3, _E("effort", 900, 4, "88-94 % de la FTP"), _E("recuperation", 300, 1)), _E("retour_au_calme", 600, 1)]},
    {"nom": "Seuil 2 × 20 min", "sport": "velo", "but": "seuil", "phases": ["developpement", "specifique"],
     "duree": 85, "intensite": "soutenu", "etapes": [_E("echauffement", 1200, 2), _R(2, _E("effort", 1200, 4, "95-100 % de la FTP"), _E("recuperation", 420, 1)), _E("retour_au_calme", 600, 1)]},
    {"nom": "VO2max 5 × 4 min", "sport": "velo", "but": "vo2max", "phases": ["developpement", "specifique"],
     "duree": 70, "intensite": "tres-soutenu", "etapes": [_E("echauffement", 1200, 2), _R(5, _E("effort", 240, 5, "110-120 % de la FTP"), _E("recuperation", 240, 1)), _E("retour_au_calme", 600, 1)]},
    {"nom": "Force à basse cadence", "sport": "velo", "but": "force", "phases": ["foncier", "developpement"],
     "duree": 75, "intensite": "modere", "etapes": [_E("echauffement", 900, 2), _R(5, _E("effort", 300, 3, "Cadence 55-65, assis, en côte"), _E("recuperation", 300, 2)), _E("retour_au_calme", 600, 1)]},
    {"nom": "Col au tempo", "sport": "velo", "but": "specifique", "phases": ["specifique"],
     "duree": 180, "intensite": "modere", "etapes": [_E("effort", 3600, 2), _R(2, _E("effort", 1800, 3, "Allure de cyclosportive"), _E("recuperation", 900, 2)), _E("retour_au_calme", 1800, 2)]},
    # Triathlon et natation
    {"nom": "Enchaînement vélo-course", "sport": "triathlon", "but": "specifique", "phases": ["developpement", "specifique"],
     "duree": 105, "intensite": "soutenu", "etapes": [
         {**_E("echauffement", 900, 2), "sport": "velo"}, _E("effort", 3600, 3, "Allure de course"),
         {**_E("effort", 1200, 3, "Allure course dès la sortie du parc, cadence haute"), "sport": "course"}, _E("retour_au_calme", 300, 1)]},
    {"nom": "Natation technique", "sport": "natation", "but": "technique", "phases": ["foncier", "developpement", "specifique", "affutage"],
     "duree": 45, "intensite": "facile", "etapes": [_E("echauffement", 600, 2, "Nage variée"), _R(8, _E("effort", 120, 2, "Éducatifs : rattrapé, 3 temps, poings fermés"), _E("recuperation", 30, 1)), _E("effort", 600, 3, "Nage complète, allongement"), _E("retour_au_calme", 300, 1)]},
]

BUTS = sorted({s["but"] for s in SEANCES})


def methode(discipline: str, sujet: str | None = None) -> dict[str, Any]:
    """Les principes d'une discipline (et les principes généraux), ou un sujet précis."""
    generale = METHODE["general"]
    propre = METHODE.get(discipline, {})
    if sujet:
        texte = propre.get(sujet) or generale.get(sujet)
        if not texte:
            return {"sujets": sorted(set(generale) | set(propre))}
        return {"discipline": discipline, "sujet": sujet, "texte": texte}
    return {"discipline": discipline, "general": generale, "specifique": propre,
            "sujets": sorted(set(generale) | set(propre))}


def seances_types(sport: str | None = None, but: str | None = None, phase: str | None = None) -> list[dict[str, Any]]:
    familles = {"course": {"course", "trail"}, "trail": {"trail", "course"}, "velo": {"velo"},
                "triathlon": {"triathlon", "velo", "course", "natation"}, "natation": {"natation"}}
    garder = familles.get(sport or "", None)
    return [s for s in SEANCES
            if (garder is None or s["sport"] in garder) and (not but or s["but"] == but) and (not phase or phase in s["phases"])]
