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
import gzip
import json
import os
from datetime import date
import secrets
import threading
import time
from typing import Any

import requests
from garminconnect import Garmin
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from donnees import Supabase
from freins import Frein
import terrains
import vap
from entrainements import vers_garmin

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
    "ski_rando": {"backcountry_skiing", "backcountry_skiing_snowboarding_ws", "backcountry_snowboarding"},
}


# Records personnels Garmin : typeId → (clé, nom, nature de la valeur)
# (1 à 7 : course à pied, d'après la documentation de garminconnect ; à compléter au besoin)
RECORDS_GARMIN = {
    1: ("1km", "1 km", "temps"),
    2: ("mile", "1 mile", "temps"),
    3: ("5km", "5 km", "temps"),
    4: ("10km", "10 km", "temps"),
    5: ("semi", "Semi-marathon", "temps"),
    6: ("marathon", "Marathon", "temps"),
    7: ("plus_longue_course", "Plus longue course", "distance"),
    8: ("plus_longue_sortie_velo", "Plus longue sortie vélo", "distance"),
}


def sport_cloach(type_garmin: str) -> str:
    return next((s for s, types in SPORTS_GARMIN.items() if type_garmin in types), "autre")


def puissances_max(a: dict[str, Any]) -> dict[str, int] | None:
    """Meilleure puissance moyenne par durée (secondes → W), que Garmin donne dans le résumé des sorties
    avec puissance (maxAvgPower_1 … maxAvgPower_18000, ou au moins la meilleure sur 20 min)."""
    courbe = {}
    for cle, valeur in a.items():
        if cle.startswith("maxAvgPower_") and cle[12:].isdigit() and isinstance(valeur, (int, float)) and valeur > 0:
            courbe[cle[12:]] = round(valeur)
    vingt = a.get("max20MinPower") or a.get("maxPowerTwentyMinutes")
    if "1200" not in courbe and isinstance(vingt, (int, float)) and vingt > 0:
        courbe["1200"] = round(vingt)
    return courbe or None


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
        # Toujours présent (même vide) : toutes les lignes d'un envoi groupé doivent avoir les mêmes clés
        "puissances_max": puissances_max(a),
    }


class CompteAbsent(RuntimeError):
    """L'utilisateur n'a pas encore branché son compte Garmin."""


# Connexions à Garmin (identifiants ou code) tolérées par compte Cloach, sur 15 minutes
ESSAIS_CONNEXION = Frein(6, 15 * 60)


class Garmins:
    def __init__(self, base: Supabase, origines: set[str]) -> None:
        self.base = base
        self.origines = origines
        self._clients: dict[str, Garmin] = {}
        self._derniers_sauves: dict[str, str] = {}
        # Connexions en attente du code de double authentification : id → (uid, client, échéance)
        self._attente: dict[str, tuple[str, Garmin, float]] = {}
        self._verrou = threading.Lock()
        # Analyse de tout l'historique, en tâche de fond : uid → état ; une seule à la fois sur le serveur
        self._lots: dict[str, dict[str, Any]] = {}
        self._arrets: set[str] = set()

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

    def records(self, uid: str) -> list[dict[str, Any]]:
        """Records personnels calculés par Garmin (meilleurs efforts), mis en forme."""
        brut = self.appeler(uid, "get_personal_record") or []
        records = []
        for r in brut if isinstance(brut, list) else []:
            connu = RECORDS_GARMIN.get(r.get("typeId"))
            date_record = str(r.get("actStartDateTimeInGMTFormatted") or r.get("prStartTimeGmtFormatted") or "")[:10]
            records.append({
                "type": r.get("typeId"),
                "cle": connu[0] if connu else None,
                "nom": connu[1] if connu else None,
                "nature": connu[2] if connu else None,
                "valeur": r.get("value"),
                "date": date_record or None,
                "activite_id": str(r["activityId"]) if r.get("activityId") else None,
                "activite_nom": r.get("activityName"),
                "type_activite": r.get("activityType"),
            })
        return records

    def entrainement(self, uid: str, id_seance: str, alternative: int | None = None) -> dict[str, Any]:
        """Crée (ou met à jour) l'entraînement Garmin d'une séance structurée, ou de l'une de ses alternatives,
        et le programme au jour prévu."""
        etat = self.base.lire_etat(uid)
        seances = [s for w in (etat.get("plan") or {}).get("semaines", []) for s in w.get("seances", [])]
        principale = next((s for s in seances if s.get("id") == id_seance), None)
        if not principale:
            raise RuntimeError("Séance introuvable dans le plan.")
        alternatives = principale.get("alternatives") or []
        if alternative is not None and not 0 <= alternative < len(alternatives):
            raise RuntimeError("Alternative introuvable.")
        # L'entraînement est noté sur l'objet exporté (séance ou alternative) ; le jour est celui de la séance
        seance = principale if alternative is None else alternatives[alternative]
        ftp = float((etat.get("profil") or {}).get("ftp") or 0) or None
        entrainement = vers_garmin({**seance, "jour": principale["jour"]}, ftp)
        deja = (seance.get("garmin") or {}).get("workoutId")
        if deja:
            try:
                self.appeler(uid, "update_workout", deja, entrainement)
                id_garmin = deja
            except Exception:
                deja = None  # supprimé dans Garmin Connect entre-temps : on le recrée
        if not deja:
            cree = self.appeler(uid, "upload_workout", entrainement)
            id_garmin = cree.get("workoutId")
        if not deja or (seance.get("garmin") or {}).get("programme") != principale.get("jour"):
            self.appeler(uid, "schedule_workout", id_garmin, principale["jour"])
        seance["garmin"] = {"workoutId": id_garmin, "programme": principale["jour"]}
        self.base.ecrire_etat(uid, etat)
        return seance["garmin"]

    # Mesures gardées dans la copie des détails : celles que lisent vap.py et terrains.py
    CLES_SERIE = ("sumDistance", "directElevation", "sumElapsedDuration", "sumDuration", "sumMovingDuration",
                  "directTimestamp", "directPower", "directHeartRate", "directDoubleCadence", "directRunCadence",
                  "directBikeCadence", "directGroundContactTime", "directVerticalRatio")

    def details(self, uid: str, id_activite: str) -> tuple[dict[str, Any], bool]:
        """Détails point par point d'une sortie : la copie gardée dans le stockage s'il y en a une, sinon
        Garmin (et on en garde une copie réduite et compressée). Renvoie aussi si Garmin a été appelé."""
        try:
            brut = self.base.lire_serie(uid, id_activite)
            if brut:
                return json.loads(gzip.decompress(brut)), False
        except Exception as e:
            print(f"Lecture du profil {id_activite} : {type(e).__name__} : {e}", flush=True)
        details = self.appeler(uid, "get_activity_details", str(int(id_activite)), 4000, 0) or {}
        index = [(d.get("key"), d.get("metricsIndex")) for d in details.get("metricDescriptors") or []
                 if d.get("key") in self.CLES_SERIE and isinstance(d.get("metricsIndex"), int)]
        if index:
            points = [p.get("metrics") or [] for p in details.get("activityDetailMetrics") or []]
            reduits = {"metricDescriptors": [{"key": k, "metricsIndex": n} for n, (k, _) in enumerate(index)],
                       "activityDetailMetrics": [{"metrics": [m[i] if i < len(m) else None for _, i in index]} for m in points]}
            try:
                self.base.ecrire_serie(uid, id_activite, gzip.compress(json.dumps(reduits, separators=(",", ":")).encode()))
            except Exception as e:
                print(f"Copie du profil {id_activite} : {type(e).__name__} : {e}", flush=True)
        return details, True

    def vap(self, uid: str, id_activite: str, sport: str | None = None) -> dict[str, Any]:
        """Calcule la VAP et l'analyse par terrain d'une sortie à partir de son profil détaillé (copie gardée,
        sinon un appel à Garmin), et les enregistre avec l'activité. À vélo : pas de VAP."""
        details, garmin_appele = self.details(uid, str(int(id_activite)))
        velo = sport == "velo"
        resultat: dict[str, Any] = {} if velo else vap.calculer(details) or {}
        analyse = terrains.analyser(details, "velo" if velo else "course")
        # Même sans profil exploitable, on note la version : l'app ne redemandera pas l'analyse
        champs = {**resultat, "terrains": analyse or {"v": terrains.version(sport), "vide": True}}
        self.base.completer_activite(uid, str(id_activite), champs)
        return {"vap_min_km": None, **resultat, "terrains": terrains.version(sport), "garmin": garmin_appele}

    # ---------- Analyse de tout l'historique (tâche de fond) ----------

    PAUSE_LOT = 2.0           # secondes entre deux sorties
    PAUSE_FREINAGE = 600.0    # quand Garmin limite les requêtes (429)

    def lot(self, uid: str) -> dict[str, Any]:
        """État de l'analyse de l'historique de l'utilisateur."""
        with self._verrou:
            return {"autorise": True, **(self._lots.get(uid) or {"en_cours": False})}

    def lancer_lot(self, uid: str) -> dict[str, Any]:
        """Lance l'analyse (VAP et terrains) de toutes les sorties de course, de trail et de vélo pas encore
        analysées avec la version actuelle, les plus récentes d'abord. Reprend là où une analyse précédente
        s'est arrêtée : chaque sortie est enregistrée dès qu'elle est faite."""
        with self._verrou:
            if any(l.get("en_cours") for l in self._lots.values()):
                autre = next(u for u, l in self._lots.items() if l.get("en_cours"))
                return self._lots[uid] if autre == uid else {"en_cours": False, "erreur": "Une autre analyse tourne sur le serveur : réessaie plus tard."}
            self._lots[uid] = {"en_cours": True, "faites": 0, "total": None, "erreurs": 0, "debut": time.time(), "message": "Préparation…"}
            self._arrets.discard(uid)
        threading.Thread(target=self._travailler_lot, args=(uid,), daemon=True).start()
        return self.lot(uid)

    def arreter_lot(self, uid: str) -> dict[str, Any]:
        with self._verrou:
            self._arrets.add(uid)
        return self.lot(uid)

    def _maj_lot(self, uid: str, **champs: Any) -> None:
        with self._verrou:
            self._lots.setdefault(uid, {}).update(champs)

    def _travailler_lot(self, uid: str) -> None:
        # Render endort le service sans requête entrante pendant 15 min : on se réveille soi-même
        adresse = (os.environ.get("URL_PUBLIQUE") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
        dernier_reveil = time.time()
        try:
            a_faire = [a for a in self.base.lire_activites(uid)
                       if a.get("sport") in ("course", "trail", "velo") and str(a.get("id", "")).isdigit()
                       and float(a.get("distance_km") or 0) >= 1
                       and ((a.get("terrains") or {}).get("v") or 0) < terrains.version(a.get("sport"))]
            a_faire.sort(key=lambda a: str(a.get("debut") or a["jour"]), reverse=True)
            self._maj_lot(uid, total=len(a_faire), message=None)
            freinages = 0
            for a in a_faire:
                garmin_appele = True
                if uid in self._arrets:
                    self._maj_lot(uid, message="Arrêtée")
                    break
                if adresse and time.time() - dernier_reveil > 300:
                    try:
                        requests.get(f"{adresse}/sante", timeout=10)
                    except requests.RequestException:
                        pass
                    dernier_reveil = time.time()
                while True:  # on réessaie la même sortie après une pause si Garmin freine
                    try:
                        garmin_appele = self.vap(uid, str(a["id"]), "velo" if a["sport"] == "velo" else None).get("garmin")
                        freinages = 0
                        with self._verrou:
                            self._lots[uid]["faites"] += 1
                            self._lots[uid]["derniere"] = a["jour"]
                    except Exception as e:
                        texte = f"{type(e).__name__} {e}".lower()
                        if "429" in texte or "too many" in texte:
                            freinages += 1
                            if freinages < 3 and uid not in self._arrets:
                                self._maj_lot(uid, message="Garmin limite les requêtes : pause de 10 minutes…")
                                time.sleep(self.PAUSE_FREINAGE)
                                self._maj_lot(uid, message=None)
                                continue
                        else:
                            print(f"Analyse de l'historique ({a['id']}) : {type(e).__name__} : {e}", flush=True)
                            with self._verrou:
                                self._lots[uid]["erreurs"] += 1
                    break
                if freinages >= 3:
                    self._maj_lot(uid, message="Garmin limite les requêtes : analyse arrêtée, relance-la plus tard.")
                    break
                if garmin_appele:  # recalcul depuis la copie gardée : pas besoin de ménager Garmin
                    time.sleep(self.PAUSE_LOT)
        except Exception as e:
            print(f"Analyse de l'historique : {type(e).__name__} : {e}", flush=True)
            self._maj_lot(uid, message="L'analyse s'est arrêtée sur une erreur : relance-la.")
        finally:
            self._maj_lot(uid, en_cours=False, fin=time.time())

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
                self.oublier(uid)
                await asyncio.to_thread(self.base.supprimer_jetons_garmin, uid)
                return repondre({"connecte": False})
            if action in ("connexion", "code"):
                if ESSAIS_CONNEXION.bloque(uid):
                    return repondre({"erreur": "Trop d'essais de connexion à Garmin : réessaie dans 15 minutes."}, 429)
                ESSAIS_CONNEXION.noter(uid)
                return repondre(*await asyncio.to_thread(self._connexion if action == "connexion" else self._code, uid, corps))
            if action == "entrainement":
                try:
                    alt = corps.get("alternative")
                    return repondre(await asyncio.to_thread(self.entrainement, uid, str(corps.get("id", "")),
                                                            int(alt) if isinstance(alt, (int, str)) and str(alt).isdigit() else None))
                except (RuntimeError, ValueError) as e:
                    return repondre({"erreur": str(e)}, 400)
            if action == "vap":
                id_activite = str(corps.get("id", ""))
                if not id_activite.isdigit():
                    return repondre({"erreur": "Activité inconnue"}, 400)
                sport = "velo" if corps.get("sport") == "velo" else None
                return repondre(await asyncio.to_thread(self.vap, uid, id_activite, sport))
            if action == "lot":
                # Réservé aux comptes de la table admins : des milliers d'appels à Garmin depuis l'adresse du serveur
                if not await asyncio.to_thread(self.base.est_admin, uid):
                    return repondre({"autorise": False})
                ordre = str(corps.get("ordre", "etat"))
                if ordre == "lancer":
                    return repondre(await asyncio.to_thread(self.lancer_lot, uid))
                if ordre == "arreter":
                    return repondre(self.arreter_lot(uid))
                return repondre(self.lot(uid))
            if action == "records":
                return repondre({"records": await asyncio.to_thread(self.records, uid)})
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

    def oublier(self, uid: str) -> None:
        """Ferme la session Garmin gardée en mémoire (déconnexion, suppression du compte)."""
        with self._verrou:
            self._clients.pop(uid, None)
            self._derniers_sauves.pop(uid, None)
            self._attente = {k: v for k, v in self._attente.items() if v[0] != uid}

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
