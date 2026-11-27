"""Comptes Polar des utilisateurs de Cloach (montres Polar, par l'API officielle Polar AccessLink v3).

Branchement (OAuth 2) :
- l'app demande à /polar/lien l'adresse d'autorisation (avec son jeton de connexion Cloach) ;
- l'utilisateur accepte sur flow.polar.com, qui le renvoie sur /polar/retour avec un code ;
- le serveur échange ce code contre un jeton (authentification HTTP Basic du client), inscrit l'utilisateur
  auprès d'AccessLink (POST /v3/users, 409 = déjà inscrit), garde le jeton chiffré (table polar_comptes)
  et renvoie l'utilisateur dans l'app.
Ensuite, /polar/importer copie les sorties d'une période dans la table activites, comme l'import Garmin.

Limite de Polar : l'API ne renvoie que les sorties envoyées sur Polar Flow ces 30 derniers jours, et
seulement après le branchement. Le passé s'importe depuis l'export des données de Polar (à venir).
Le jeton Polar n'expire pas ; débrancher retire l'inscription (DELETE /v3/users/{id}), ce qui le révoque.

Variables : POLAR_CLIENT_ID et POLAR_CLIENT_SECRET (client créé sur https://admin.polaraccesslink.com,
adresse de retour = <serveur>/polar/retour).
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from donnees import Supabase

API = "https://www.polaraccesslink.com/v3"
AUTORISATION = "https://flow.polar.com/oauth2/authorization"
JETON = "https://polarremote.com/v2/oauth2/token"

# Sports de Polar (champ detailed-sport-info, sinon sport) → sports de Cloach, par mots-clés, dans l'ordre :
# le trail avant la course, le triathlon avant la natation et le vélo
MOTS_SPORTS = [
    ("triathlon", ("TRIATHLON", "DUATHLON", "MULTISPORT", "SWIMRUN")),
    ("trail", ("TRAIL",)),
    ("ski_rando", ("BACKCOUNTRY", "SKI_TOURING", "SKIMO", "TELEMARK")),
    ("course", ("RUNNING", "JOGGING", "TREADMILL", "TRACK_AND_FIELD", "ORIENTEERING")),
    ("velo", ("CYCLING", "BIKING", "BIKE", "SPINNING", "INDOOR_CYCLING")),
    ("natation", ("SWIMMING",)),
    ("renfo", ("STRENGTH", "CORE", "CIRCUIT", "CROSSFIT", "GYM", "YOGA", "PILATES", "FUNCTIONAL", "BODY_AND_MIND",
               "STRETCHING", "CROSS_TRAINER", "ROWING", "HIIT", "WEIGHT")),
    ("tennis", ("TENNIS",)),
    ("padel", ("PADEL", "SQUASH", "BADMINTON", "PICKLEBALL")),
    ("foot", ("SOCCER", "FOOTBALL", "FUTSAL")),
    ("basket", ("BASKETBALL",)),
]


def sport_cloach(sport: str | None, detail: str | None) -> str:
    for texte in (detail, sport):
        t = str(texte or "").upper()
        if not t:
            continue
        for nom, mots in MOTS_SPORTS:
            if any(m in t for m in mots) and not (nom == "foot" and "AMERICAN" in t):
                return nom
    return "autre"


def secondes(duree: str | None) -> float | None:
    """Durée ISO 8601 de Polar (PT1H2M3.5S) → secondes."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?", str(duree or ""))
    if not m or not any(m.groups()):
        return None
    j, h, mi, s = (float(x or 0) for x in m.groups())
    return j * 86400 + h * 3600 + mi * 60 + s


def vers_activite(e: dict[str, Any]) -> dict[str, Any] | None:
    """Sortie Polar (GET /v3/exercises) → ligne de la table activites. start-time est en heure locale."""
    debut = str(e.get("start-time") or "")
    if not e.get("id") or len(debut) < 10:
        return None
    arrondi = lambda x, n=1: round(float(x), n) if isinstance(x, (int, float)) else None
    fc = e.get("heart-rate") or {}
    duree = secondes(e.get("duration"))
    detail = e.get("detailed-sport-info") or e.get("sport") or ""
    return {
        "id": f"polar-{e['id']}", "source": "polar", "jour": debut[:10], "debut": debut[:19].replace("T", " "),
        "sport": sport_cloach(e.get("sport"), e.get("detailed-sport-info")),
        "type": str(detail).lower(), "nom": str(detail).replace("_", " ").capitalize()[:200],
        "distance_km": arrondi(e["distance"] / 1000, 2) if isinstance(e.get("distance"), (int, float)) and e["distance"] > 0 else None,
        "duree_min": arrondi(duree / 60) if duree else None,
        "denivele_m": None,  # absent du résumé de Polar
        "fc_moy": arrondi(fc.get("average"), 0), "fc_max": arrondi(fc.get("maximum"), 0),
        # « Training load » de Polar, à la place de la charge Garmin (même ordre de grandeur, calcul différent)
        "charge": arrondi(e.get("training-load")),
        "te_aerobie": None, "te_anaerobie": None, "puissance_moy": None, "puissance_norm": None,
        "calories": arrondi(e.get("calories"), 0),
    }


def _b64(donnees: bytes) -> str:
    return base64.urlsafe_b64encode(donnees).rstrip(b"=").decode()


class Polars:
    def __init__(self, base: Supabase, origines: set[str], cle_signature: str, url_publique: str, url_app: str) -> None:
        self.base = base
        self.origines = origines
        self.cle = hashlib.sha256(b"cloach/polar-etat\0" + cle_signature.encode()).digest()
        self.url_publique = url_publique.rstrip("/")
        self.url_app = url_app
        self.client_id = os.environ.get("POLAR_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("POLAR_CLIENT_SECRET", "").strip()

    @property
    def configure(self) -> bool:
        return bool(self.client_id and self.client_secret)

    # ---------- Paramètre « state » signé : relie le retour de Polar au compte Cloach ----------

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

    # ---------- Appels à AccessLink ----------

    def _jetons(self, uid: str) -> dict[str, Any]:
        brut = self.base.lire_jetons_polar(uid)
        if not brut:
            raise RuntimeError("Aucun compte Polar branché.")
        return json.loads(brut)

    @staticmethod
    def _entetes(jeton: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {jeton}", "Accept": "application/json"}

    def importer(self, uid: str, debut: date, fin: date) -> int:
        """Copie dans la table activites les sorties Polar entre deux dates incluses (30 derniers jours au plus)."""
        r = requests.get(f"{API}/exercises", headers=self._entetes(self._jetons(uid)["access_token"]), timeout=30)
        if r.status_code == 429:
            raise RuntimeError("Polar limite le nombre de requêtes : réessaie dans quelques minutes.")
        if r.status_code in (401, 403):
            raise RuntimeError("Polar refuse l'accès : débranche puis rebranche ton compte Polar.")
        if r.status_code == 204:
            return 0
        r.raise_for_status()
        activites = [a for a in map(vers_activite, r.json() or [])
                     if a and debut.isoformat() <= a["jour"] <= fin.isoformat()]
        if activites:
            self.base.ecrire_activites(uid, activites)
        return len(activites)

    def _brancher(self, uid: str, code: str) -> None:
        r = requests.post(JETON, timeout=15, auth=(self.client_id, self.client_secret),
                          headers={"Accept": "application/json;charset=UTF-8"},
                          data={"grant_type": "authorization_code", "code": code,
                                "redirect_uri": f"{self.url_publique}/polar/retour"})
        r.raise_for_status()
        j = r.json()
        jeton, polar_id = j["access_token"], str(j.get("x_user_id") or "")
        # Inscription auprès d'AccessLink (obligatoire avant toute lecture) ; 409 : déjà inscrit
        inscription = requests.post(f"{API}/users", timeout=15, headers={**self._entetes(jeton), "Content-Type": "application/json"},
                                    json={"member-id": uid})
        if inscription.status_code not in (200, 201, 409):
            inscription.raise_for_status()
        nom = ""
        try:
            infos = inscription.json() if inscription.status_code in (200, 201) else requests.get(
                f"{API}/users/{polar_id}", headers=self._entetes(jeton), timeout=15).json()
            nom = f"{infos.get('first-name', '')} {infos.get('last-name', '')}".strip()
            polar_id = polar_id or str(infos.get("polar-user-id") or "")
        except (ValueError, requests.RequestException):
            pass
        self.base.ecrire_jetons_polar(uid, json.dumps({"access_token": jeton, "polar_id": polar_id, "nom": nom}))

    def _debrancher(self, uid: str) -> None:
        """Retire l'inscription chez Polar (ce qui révoque le jeton), puis supprime le jeton."""
        try:
            j = self._jetons(uid)
            if j.get("polar_id"):
                requests.delete(f"{API}/users/{j['polar_id']}", headers=self._entetes(j["access_token"]), timeout=10)
        except Exception:
            pass  # on supprime quand même le jeton de notre côté
        self.base.supprimer_jetons_polar(uid)

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
        """Polar renvoie ici l'utilisateur après son accord (GET, hors de l'app)."""
        uid = self._lire_etat(request.query_params.get("state", ""))
        code = request.query_params.get("code", "")
        if not uid or not code or request.query_params.get("error"):
            return RedirectResponse(f"{self.url_app}#polar=annule", status_code=302)
        try:
            await asyncio.to_thread(self._brancher, uid, code)
        except Exception as e:
            print(f"Polar (retour) : {type(e).__name__} : {e}", flush=True)
            return RedirectResponse(f"{self.url_app}#polar=erreur", status_code=302)
        return RedirectResponse(f"{self.url_app}#polar=ok", status_code=302)

    async def route(self, request: Request) -> Response:
        cors = self._cors(request)
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors)

        def repondre(corps: dict[str, Any], statut: int = 200) -> Response:
            return JSONResponse(corps, statut, headers=cors)

        if request.headers.get("origin", "") not in self.origines:
            return repondre({"erreur": "Origine non autorisée"}, 403)
        action = request.path_params.get("action", "")
        if not self.configure:
            # L'app masque la carte Polar tant que le serveur n'a pas de client Polar
            if action == "etat":
                return repondre({"configure": False, "connecte": False})
            return repondre({"erreur": "Polar n'est pas encore configuré sur le serveur."}, 503)
        jeton = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        uid = await asyncio.to_thread(self.base.utilisateur, jeton)
        if not uid:
            return repondre({"erreur": "Connecte-toi à Cloach"}, 401)
        try:
            corps = await request.json()
        except (ValueError, json.JSONDecodeError):
            corps = {}
        try:
            if action == "lien":
                params = {"response_type": "code", "client_id": self.client_id,
                          "redirect_uri": f"{self.url_publique}/polar/retour", "state": self._etat(uid)}
                return repondre({"url": f"{AUTORISATION}?{urlencode(params)}"})
            if action == "etat":
                brut = await asyncio.to_thread(self.base.lire_jetons_polar, uid)
                return repondre({"configure": True, "connecte": bool(brut), "nom": json.loads(brut).get("nom", "") if brut else ""})
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
                if fin < date.today() - timedelta(days=40):
                    return repondre({"nombre": 0})  # rien chez Polar au-delà de 30 jours : inutile d'appeler
                return repondre({"nombre": await asyncio.to_thread(self.importer, uid, debut, fin)})
        except Exception as e:
            print(f"Polar ({action}) : {type(e).__name__} : {e}", flush=True)
            message = str(e) if isinstance(e, RuntimeError) else "Polar ne répond pas pour le moment, réessaie plus tard."
            return repondre({"erreur": message}, 400)
        return repondre({"erreur": "Action inconnue"}, 404)
