"""Comptes Garmin des utilisateurs de Cloach.

Chaque utilisateur branche son compte depuis l'app (Profil → Garmin) : l'app envoie
e-mail et mot de passe Garmin à /garmin/connexion, avec son jeton de connexion
Cloach ; le serveur se connecte à Garmin (code de double authentification via
/garmin/code si besoin) et garde seulement les jetons Garmin, chiffrés. Le mot de
passe n'est stocké nulle part.

Il utilise la bibliothèque non officielle garminconnect : Garmin ne propose pas
d'API pour les particuliers, ça peut cesser de marcher si Garmin change son site.
"""

import asyncio
import json
from datetime import date
import secrets
import threading
import time
from typing import Any

from garminconnect import Garmin
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from donnees import Supabase

DUREE_ATTENTE_CODE = 5 * 60

# Types d'activité Garmin → sports de Cloach (les autres deviennent « autre »)
SPORTS_GARMIN = {
    "course": {"running", "treadmill_running", "track_running", "street_running", "indoor_running",
               "virtual_run", "obstacle_run", "ultra_run"},
    "trail": {"trail_running"},
    "velo": {"cycling", "road_biking", "virtual_ride", "indoor_cycling", "mountain_biking", "gravel_cycling",
             "cyclocross", "track_cycling", "recumbent_cycling", "e_bike_fitness", "e_bike_mountain", "bmx"},
    "renfo": {"strength_training", "hiit", "indoor_cardio", "fitness_equipment", "pilates", "yoga",
              "elliptical", "stair_climbing", "indoor_rowing"},
    "natation": {"lap_swimming", "open_water_swimming", "swimming"},
}


def sport_cloach(type_garmin: str) -> str:
    return next((s for s, types in SPORTS_GARMIN.items() if type_garmin in types), "autre")


def vers_activite(a: dict[str, Any]) -> dict[str, Any] | None:
    """Activité Garmin → ligne de la table activites."""
    debut = str(a.get("startTimeLocal") or "")
    if not a.get("activityId") or len(debut) < 10:
        return None
    type_garmin = (a.get("activityType") or {}).get("typeKey") or ""
    arrondi = lambda x, n=1: round(float(x), n) if isinstance(x, (int, float)) else None
    return {
        "id": str(a["activityId"]), "source": "garmin", "jour": debut[:10], "debut": debut[:19],
        "sport": sport_cloach(type_garmin), "type": type_garmin, "nom": str(a.get("activityName") or "")[:200],
        "distance_km": arrondi((a.get("distance") or 0) / 1000, 2) if a.get("distance") else None,
        "duree_min": arrondi((a.get("duration") or 0) / 60) if a.get("duration") else None,
        "denivele_m": arrondi(a.get("elevationGain"), 0),
        "fc_moy": arrondi(a.get("averageHR"), 0), "fc_max": arrondi(a.get("maxHR"), 0),
        "charge": arrondi(a.get("activityTrainingLoad")),
        "te_aerobie": arrondi(a.get("aerobicTrainingEffect")), "te_anaerobie": arrondi(a.get("anaerobicTrainingEffect")),
        "puissance_moy": arrondi(a.get("avgPower"), 0), "puissance_norm": arrondi(a.get("normPower"), 0),
        "calories": arrondi(a.get("calories"), 0),
    }


class CompteAbsent(RuntimeError):
    """L'utilisateur n'a pas encore branché son compte Garmin."""


class Garmins:
    def __init__(self, base: Supabase, origines: set[str]) -> None:
        self.base = base
        self.origines = origines
        self._clients: dict[str, Garmin] = {}
        self._derniers_sauves: dict[str, str] = {}
        # Connexions en attente du code de double authentification : id → (uid, client, échéance)
        self._attente: dict[str, tuple[str, Garmin, float]] = {}
        self._verrou = threading.Lock()

    # ---------- Session Garmin d'un utilisateur ----------

    def client(self, uid: str, neuf: bool = False) -> Garmin:
        """Session Garmin de l'utilisateur, ouverte depuis ses jetons enregistrés."""
        with self._verrou:
            if neuf:
                self._clients.pop(uid, None)
            if uid in self._clients:
                return self._clients[uid]
        jetons = self.base.lire_jetons_garmin(uid)
        if not jetons:
            raise CompteAbsent("Aucun compte Garmin branché : l'utilisateur peut le faire dans l'app Cloach (Profil → Garmin).")
        g = Garmin()
        g.login(jetons)
        with self._verrou:
            self._clients[uid] = g
            self._derniers_sauves[uid] = jetons
        return g

    def appeler(self, uid: str, nom: str, *args: Any) -> Any:
        """Exécute une méthode (bloquante) du client Garmin de l'utilisateur."""
        g = self.client(uid)
        try:
            resultat = getattr(g, nom)(*args)
        except Exception:
            # Session sans doute expirée : on réessaie une fois avec une session neuve.
            g = self.client(uid, neuf=True)
            resultat = getattr(g, nom)(*args)
        self._sauver(uid, g)
        return resultat

    def _sauver(self, uid: str, g: Garmin) -> None:
        """Enregistre les jetons s'ils ont été renouvelés par Garmin."""
        jetons = g.client.dumps()
        if jetons == self._derniers_sauves.get(uid):
            return
        try:
            self.base.ecrire_jetons_garmin(uid, jetons)
            self._derniers_sauves[uid] = jetons
        except Exception as e:  # un échec ici ne doit pas faire échouer la requête
            print(f"Sauvegarde des jetons Garmin impossible : {e}", flush=True)

    def forme(self, uid: str, jour: str) -> dict[str, Any]:
        """Signaux de récupération du jour, chacun lu à part : une donnée absente ne bloque pas les autres."""
        def lire(nom: str, *args: Any) -> Any:
            try:
                return self.appeler(uid, nom, *args)
            except CompteAbsent:
                raise
            except Exception:
                return None

        forme: dict[str, Any] = {"jour": jour}
        sommeil = (lire("get_sleep_data", jour) or {}).get("dailySleepDTO") or {}
        score = (sommeil.get("sleepScores") or {}).get("overall") or {}
        if score.get("value") or sommeil.get("sleepTimeSeconds"):
            forme["sommeil"] = {"score": score.get("value"), "qualite": score.get("qualifierKey"),
                                "duree_h": round((sommeil.get("sleepTimeSeconds") or 0) / 3600, 1) or None}
        vfc = (lire("get_hrv_data", jour) or {}).get("hrvSummary") or {}
        if vfc.get("status"):
            base = vfc.get("baseline") or {}
            forme["vfc"] = {"statut": vfc.get("status"), "nuit": vfc.get("lastNightAvg"), "semaine": vfc.get("weeklyAvg"),
                            "normale_basse": base.get("balancedLow"), "normale_haute": base.get("balancedUpper")}
        resume = lire("get_user_summary", jour) or {}
        if resume.get("bodyBatteryMostRecentValue") is not None:
            forme["body_battery"] = {"actuel": resume.get("bodyBatteryMostRecentValue"), "reveil": resume.get("bodyBatteryAtWakeTime")}
        if resume.get("restingHeartRate"):
            forme["fc_repos"] = {"jour": resume.get("restingHeartRate"), "moyenne_7j": resume.get("lastSevenDaysAvgRestingHeartRate")}
        if resume.get("averageStressLevel") is not None and resume.get("averageStressLevel") >= 0:
            forme["stress_moyen"] = resume.get("averageStressLevel")
        dispo = lire("get_training_readiness", jour) or []
        if isinstance(dispo, list) and dispo and dispo[0].get("score") is not None:
            forme["disponibilite"] = {"score": dispo[0].get("score"), "niveau": dispo[0].get("level")}
        return forme

    def branche(self, uid: str) -> bool:
        return self.base.lire_jetons_garmin(uid) is not None

    # ---------- Routes appelées par l'app ----------

    def _cors(self, request: Request) -> dict[str, str]:
        origine = request.headers.get("origin", "")
        return {
            "Access-Control-Allow-Origin": origine if origine in self.origines else next(iter(self.origines)),
            "Access-Control-Allow-Headers": "authorization, content-type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Vary": "Origin",
        }

    async def route(self, request: Request) -> Response:
        cors = self._cors(request)
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors)

        def repondre(corps: dict[str, Any], statut: int = 200) -> Response:
            return JSONResponse(corps, statut, headers=cors)

        if request.headers.get("origin", "") not in self.origines:
            return repondre({"erreur": "Origine non autorisée"}, 403)
        jeton = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        uid = await asyncio.to_thread(self.base.utilisateur, jeton)
        if not uid:
            return repondre({"erreur": "Connecte-toi à Cloach"}, 401)
        try:
            corps = await request.json()
        except (ValueError, json.JSONDecodeError):
            corps = {}
        action = request.path_params.get("action", "")
        try:
            if action == "etat":
                return repondre({"connecte": await asyncio.to_thread(self.branche, uid)})
            if action == "deconnexion":
                with self._verrou:
                    self._clients.pop(uid, None)
                    self._derniers_sauves.pop(uid, None)
                await asyncio.to_thread(self.base.supprimer_jetons_garmin, uid)
                return repondre({"connecte": False})
            if action == "connexion":
                return repondre(*await asyncio.to_thread(self._connexion, uid, corps))
            if action == "code":
                return repondre(*await asyncio.to_thread(self._code, uid, corps))
            if action == "forme":
                jour = str(corps.get("jour") or date.today().isoformat())[:10]
                return repondre(await asyncio.to_thread(self.forme, uid, jour))
            if action == "importer":
                return repondre(*await asyncio.to_thread(self._importer, uid, corps))
        except Exception as e:
            print(f"Garmin ({action}) : {type(e).__name__} : {e}", flush=True)
            await asyncio.sleep(2)  # freine les essais à la chaîne
            return repondre({"erreur": _message_erreur(e)}, 400)
        return repondre({"erreur": "Action inconnue"}, 404)

    def _connexion(self, uid: str, corps: dict[str, Any]) -> tuple[dict[str, Any], int]:
        email = str(corps.get("email", "")).strip()
        mot_de_passe = str(corps.get("mot_de_passe", ""))
        if not email or not mot_de_passe:
            return {"erreur": "E-mail et mot de passe Garmin requis"}, 400
        g = Garmin(email, mot_de_passe, return_on_mfa=True)
        statut, _ = g.login()
        if statut == "needs_mfa":
            maintenant = time.time()
            attente = secrets.token_urlsafe(18)
            with self._verrou:
                self._attente = {k: v for k, v in self._attente.items() if v[2] > maintenant}
                self._attente[attente] = (uid, g, maintenant + DUREE_ATTENTE_CODE)
            return {"code": True, "attente": attente}, 200
        return self._terminer(uid, g), 200

    def _code(self, uid: str, corps: dict[str, Any]) -> tuple[dict[str, Any], int]:
        attente = str(corps.get("attente", ""))
        en_attente = self._attente.get(attente)
        if not en_attente or en_attente[0] != uid or en_attente[2] < time.time():
            return {"erreur": "Connexion expirée, recommence."}, 400
        g = en_attente[1]
        g.resume_login(None, str(corps.get("code", "")).strip())
        with self._verrou:
            self._attente.pop(attente, None)
        return self._terminer(uid, g), 200

    def _importer(self, uid: str, corps: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Importe les activités Garmin entre deux dates (l'app découpe l'historique par année)."""
        try:
            debut = date.fromisoformat(str(corps.get("debut", "")))
            fin = date.fromisoformat(str(corps.get("fin") or date.today().isoformat()))
        except ValueError:
            return {"erreur": "Dates attendues au format AAAA-MM-JJ"}, 400
        if fin < debut or (fin - debut).days > 400:
            return {"erreur": "Période d'un an au plus"}, 400
        try:
            brutes = self.appeler(uid, "get_activities_by_date", debut.isoformat(), fin.isoformat())
        except CompteAbsent:
            return {"erreur": "Branche d'abord ton compte Garmin."}, 400
        activites = [a for a in map(vers_activite, brutes or []) if a]
        if activites:
            self.base.ecrire_activites(uid, activites)
        return {"nombre": len(activites)}, 200

    def _terminer(self, uid: str, g: Garmin) -> dict[str, Any]:
        jetons = g.client.dumps()
        self.base.ecrire_jetons_garmin(uid, jetons)
        with self._verrou:
            self._clients[uid] = g
            self._derniers_sauves[uid] = jetons
        return {"connecte": True, "nom": g.get_full_name() or ""}


def _message_erreur(e: Exception) -> str:
    texte = f"{type(e).__name__} {e}".lower()
    if "429" in texte or "too many" in texte:
        return "Garmin refuse temporairement les connexions : réessaie dans quelques minutes."
    if "mfa" in texte or ("code" in texte and "invalid" in texte):
        return "Code de vérification refusé, réessaie."
    if "401" in texte or "authentication" in texte or "password" in texte or "credentials" in texte:
        return "E-mail ou mot de passe Garmin incorrect."
    return "Connexion à Garmin impossible pour le moment, réessaie plus tard."
