"""Comptes Strava des utilisateurs de Cloach (pour ceux qui n'ont pas de Garmin).

Branchement par l'API officielle de Strava (OAuth) :
- l'app demande à /strava/lien l'adresse d'autorisation (avec son jeton de connexion Cloach) ;
- l'utilisateur accepte sur strava.com, qui le renvoie sur /strava/retour avec un code ;
- le serveur échange ce code contre des jetons, les garde chiffrés (table strava_comptes)
  et renvoie l'utilisateur dans l'app.
Ensuite, /strava/importer copie les activités d'une période dans la table activites,
comme l'import Garmin.

Variables : STRAVA_CLIENT_ID et STRAVA_CLIENT_SECRET (application créée sur
https://www.strava.com/settings/api, « Authorization Callback Domain » = domaine du serveur).
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from donnees import Supabase

API = "https://www.strava.com/api/v3"

# Types d'activité Strava (sport_type) → sports de Cloach (les autres deviennent « autre »)
SPORTS_STRAVA = {
    "course": {"Run", "VirtualRun"},
    "trail": {"TrailRun"},
    "velo": {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "EBikeRide", "EMountainBikeRide",
             "Velomobile", "Handcycle"},
    "renfo": {"WeightTraining", "Workout", "Crossfit", "HighIntensityIntervalTraining", "Yoga", "Pilates",
              "Elliptical", "StairStepper", "Rowing"},
    "natation": {"Swim"},
}


def vers_activite(a: dict[str, Any]) -> dict[str, Any] | None:
    """Activité Strava → ligne de la table activites."""
    debut = str(a.get("start_date_local") or "")
    if not a.get("id") or len(debut) < 10:
        return None
    type_strava = a.get("sport_type") or a.get("type") or ""
    arrondi = lambda x, n=1: round(float(x), n) if isinstance(x, (int, float)) else None
    return {
        "id": f"strava-{a['id']}", "source": "strava", "jour": debut[:10], "debut": debut[:19].replace("T", " "),
        "sport": next((s for s, types in SPORTS_STRAVA.items() if type_strava in types), "autre"),
        "type": type_strava, "nom": str(a.get("name") or "")[:200],
        "distance_km": arrondi((a.get("distance") or 0) / 1000, 2) if a.get("distance") else None,
        "duree_min": arrondi((a.get("moving_time") or 0) / 60) if a.get("moving_time") else None,
        "denivele_m": arrondi(a.get("total_elevation_gain"), 0),
        "fc_moy": arrondi(a.get("average_heartrate"), 0), "fc_max": arrondi(a.get("max_heartrate"), 0),
        # « Effort relatif » de Strava, à la place de la charge Garmin (même ordre de grandeur, calcul différent)
        "charge": arrondi(a.get("suffer_score")),
        "te_aerobie": None, "te_anaerobie": None,
        "puissance_moy": arrondi(a.get("average_watts"), 0), "puissance_norm": arrondi(a.get("weighted_average_watts"), 0),
        "calories": None,
    }


def _b64(donnees: bytes) -> str:
    return base64.urlsafe_b64encode(donnees).rstrip(b"=").decode()


class Stravas:
    def __init__(self, base: Supabase, origines: set[str], cle_signature: str, url_publique: str, url_app: str) -> None:
        self.base = base
        self.origines = origines
        self.cle = hashlib.sha256(b"cloach/strava-etat\0" + cle_signature.encode()).digest()
        self.url_publique = url_publique.rstrip("/")
        self.url_app = url_app
        self.client_id = os.environ.get("STRAVA_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "").strip()

    @property
    def configure(self) -> bool:
        return bool(self.client_id and self.client_secret)

    # ---------- Paramètre « state » signé : relie le retour de Strava au compte Cloach ----------

    def _etat(self, uid: str) -> str:
        corps = _b64(json.dumps({"u": uid, "exp": int(time.time()) + 15 * 60}).encode())
        return f"{corps}.{_b64(hmac.new(self.cle, corps.encode(), hashlib.sha256).digest())}"

    def _lire_etat(self, etat: str) -> str | None:
        try:
            corps, signature = etat.split(".")
            if not hmac.compare_digest(signature, _b64(hmac.new(self.cle, corps.encode(), hashlib.sha256).digest())):
                return None
            contenu = json.loads(base64.urlsafe_b64decode(corps + "=" * (-len(corps) % 4)))
        except (ValueError, json.JSONDecodeError):
            return None
        return contenu.get("u") if contenu.get("exp", 0) > time.time() else None

    # ---------- Jetons Strava (renouvelés automatiquement) ----------

    def _jeton(self, uid: str) -> str:
        brut = self.base.lire_jetons_strava(uid)
        if not brut:
            raise RuntimeError("Aucun compte Strava branché.")
        jetons = json.loads(brut)
        if jetons.get("expires_at", 0) - time.time() < 300:
            r = requests.post("https://www.strava.com/oauth/token", timeout=15, data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "grant_type": "refresh_token", "refresh_token": jetons["refresh_token"],
            })
            r.raise_for_status()
            nouveaux = r.json()
            jetons.update({k: nouveaux[k] for k in ("access_token", "refresh_token", "expires_at") if k in nouveaux})
            self.base.ecrire_jetons_strava(uid, json.dumps(jetons))
        return jetons["access_token"]

    def importer(self, uid: str, debut: date, fin: date) -> int:
        """Copie dans la table activites les activités Strava entre deux dates incluses."""
        apres = int(datetime.combine(debut - timedelta(days=1), datetime.min.time()).timestamp())
        avant = int(datetime.combine(fin + timedelta(days=2), datetime.min.time()).timestamp())
        entetes = {"Authorization": f"Bearer {self._jeton(uid)}"}
        activites: list[dict[str, Any]] = []
        for page in range(1, 20):
            r = requests.get(f"{API}/athlete/activities", headers=entetes, timeout=30,
                             params={"after": apres, "before": avant, "per_page": 200, "page": page})
            if r.status_code == 429:
                raise RuntimeError("Strava limite le nombre de requêtes : réessaie dans 15 minutes.")
            r.raise_for_status()
            lot = r.json()
            activites += [a for a in map(vers_activite, lot) if a and debut.isoformat() <= a["jour"] <= fin.isoformat()]
            if len(lot) < 200:
                break
        if activites:
            self.base.ecrire_activites(uid, activites)
        return len(activites)

    # ---------- Routes ----------

    def _cors(self, request: Request) -> dict[str, str]:
        origine = request.headers.get("origin", "")
        return {
            "Access-Control-Allow-Origin": origine if origine in self.origines else next(iter(self.origines)),
            "Access-Control-Allow-Headers": "authorization, content-type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Vary": "Origin",
        }

    async def retour(self, request: Request) -> Response:
        """Strava renvoie ici l'utilisateur après son accord (GET, hors de l'app)."""
        uid = self._lire_etat(request.query_params.get("state", ""))
        code = request.query_params.get("code", "")
        portee = request.query_params.get("scope", "")
        if not uid or not code or request.query_params.get("error"):
            return RedirectResponse(f"{self.url_app}#strava=annule", status_code=302)
        if "activity:read" not in portee:
            return RedirectResponse(f"{self.url_app}#strava=droits", status_code=302)

        def echanger() -> None:
            r = requests.post("https://www.strava.com/oauth/token", timeout=15, data={
                "client_id": self.client_id, "client_secret": self.client_secret,
                "code": code, "grant_type": "authorization_code",
            })
            r.raise_for_status()
            j = r.json()
            athlete = j.get("athlete") or {}
            self.base.ecrire_jetons_strava(uid, json.dumps({
                "access_token": j["access_token"], "refresh_token": j["refresh_token"], "expires_at": j["expires_at"],
                "nom": f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
            }))

        try:
            await asyncio.to_thread(echanger)
        except Exception as e:
            print(f"Strava (retour) : {type(e).__name__} : {e}", flush=True)
            return RedirectResponse(f"{self.url_app}#strava=erreur", status_code=302)
        return RedirectResponse(f"{self.url_app}#strava=ok", status_code=302)

    async def route(self, request: Request) -> Response:
        cors = self._cors(request)
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors)

        def repondre(corps: dict[str, Any], statut: int = 200) -> Response:
            return JSONResponse(corps, statut, headers=cors)

        if request.headers.get("origin", "") not in self.origines:
            return repondre({"erreur": "Origine non autorisée"}, 403)
        if not self.configure:
            return repondre({"erreur": "Strava n'est pas encore configuré sur le serveur."}, 503)
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
            if action == "lien":
                params = {"client_id": self.client_id, "response_type": "code", "approval_prompt": "auto",
                          "redirect_uri": f"{self.url_publique}/strava/retour", "scope": "read,activity:read_all",
                          "state": self._etat(uid)}
                return repondre({"url": "https://www.strava.com/oauth/authorize?" + urlencode(params)})
            if action == "etat":
                brut = await asyncio.to_thread(self.base.lire_jetons_strava, uid)
                return repondre({"connecte": bool(brut), "nom": json.loads(brut).get("nom", "") if brut else ""})
            if action == "deconnexion":
                await asyncio.to_thread(self._debrancher, uid)
                return repondre({"connecte": False})
            if action == "importer":
                try:
                    debut = date.fromisoformat(str(corps.get("debut", "")))
                    fin = date.fromisoformat(str(corps.get("fin") or date.today().isoformat()))
                except ValueError:
                    return repondre({"erreur": "Dates attendues au format AAAA-MM-JJ"}, 400)
                if fin < debut or (fin - debut).days > 400:
                    return repondre({"erreur": "Période d'un an au plus"}, 400)
                return repondre({"nombre": await asyncio.to_thread(self.importer, uid, debut, fin)})
        except Exception as e:
            print(f"Strava ({action}) : {type(e).__name__} : {e}", flush=True)
            message = str(e) if isinstance(e, RuntimeError) else "Strava ne répond pas pour le moment, réessaie plus tard."
            return repondre({"erreur": message}, 400)
        return repondre({"erreur": "Action inconnue"}, 404)

    def _debrancher(self, uid: str) -> None:
        """Retire l'accès chez Strava, puis supprime les jetons."""
        try:
            requests.post("https://www.strava.com/oauth/deauthorize", timeout=10,
                          data={"access_token": self._jeton(uid)})
        except Exception:
            pass  # on supprime quand même les jetons de notre côté
        self.base.supprimer_jetons_strava(uid)
