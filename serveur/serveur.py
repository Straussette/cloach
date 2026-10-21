"""Serveur MCP de Cloach, « le coaching by Claude ».

Chaque utilisateur l'ajoute comme connecteur dans son Claude (claude.ai, appli
mobile ou de bureau) et se connecte avec son compte Cloach : le coaching se passe
dans ses conversations, sur son propre abonnement Claude. Le serveur donne à Claude :
- les données Garmin de l'utilisateur (s'il a branché son compte dans l'app) ;
- son profil sportif, son plan d'entraînement et son journal, partagés avec l'app ;
- des consignes de coach (instructions du serveur) et des prompts prêts à l'emploi.

Variables d'environnement :
- CLE_SIGNATURE (obligatoire, 32 caractères minimum) : signe les jetons donnés à
  Claude et chiffre les jetons Garmin. La changer déconnecte tout le monde et
  oblige chacun à rebrancher Garmin.
- SUPABASE_URL, SUPABASE_CLE (clé publique sb_publishable_…) et
  SUPABASE_CLE_SECRETE (clé secrète sb_secret_…) du projet Supabase « cloach ».
- ORIGINE_APP : origine de l'app (par défaut https://straussette.github.io).
- URL_PUBLIQUE : adresse publique du serveur ; inutile sur Render (RENDER_EXTERNAL_URL).
- FUSEAU : fuseau horaire des dates (par défaut Europe/Paris).
- PORT : port d'écoute (par défaut 8000).
"""

import os
import threading
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import anyio
import uvicorn
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from acces import Acces
from donnees import Supabase
from garmin import CompteAbsent, Garmins

# strip() : un copier-coller ajoute souvent un retour à la ligne invisible.
CLE_SIGNATURE = os.environ.get("CLE_SIGNATURE", "").strip()
if len(CLE_SIGNATURE) < 32:
    raise SystemExit("CLE_SIGNATURE doit contenir au moins 32 caractères.")
URL_PUBLIQUE = (
    os.environ.get("URL_PUBLIQUE") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000"
).strip().rstrip("/")
ORIGINE_APP = os.environ.get("ORIGINE_APP", "https://straussette.github.io").strip().rstrip("/")
FUSEAU = ZoneInfo(os.environ.get("FUSEAU", "Europe/Paris"))

base = Supabase(
    os.environ.get("SUPABASE_URL", "").strip(),
    os.environ.get("SUPABASE_CLE", "").strip(),
    os.environ.get("SUPABASE_CLE_SECRETE", "").strip(),
    CLE_SIGNATURE,
)
garmins = Garmins(base, {ORIGINE_APP})
acces = Acces(CLE_SIGNATURE, URL_PUBLIQUE, base.verifier_compte)

CONSIGNES = """Tu es Cloach, le coach sportif personnel de l'utilisateur : bienveillant, précis, exigeant juste ce qu'il faut. Tu coaches la course à pied, le trail, le vélo (y compris home trainer / Zwift), le renforcement musculaire, la natation et le multisport. Tu parles en français et tu tutoies, sauf si l'utilisateur écrit dans une autre langue.

Avant de conseiller, appuie-toi sur ses vraies données :
- contexte_du_jour donne en un appel la date, le profil, les séances prévues autour d'aujourd'hui, le journal récent et si Garmin est branché ;
- si Garmin est branché, consulte ce qui est utile, sans excès : activités récentes, disponibilite_entrainement, statut_entrainement, sommeil, vfc, body_battery. Cite brièvement les chiffres.

Principes : progressivité (au plus +10 % de charge par semaine, une semaine allégée toutes les 3 ou 4), polarisation (beaucoup d'endurance facile, peu d'intensité), récupération. La forme du jour (sommeil, VFC, Body Battery, ressenti) prime sur le plan. Respecte disponibilités, contraintes et blessures ; douleur inhabituelle → prudence et conseil de consulter. Sois concret : durées, allures, zones de fréquence cardiaque ou de puissance quand tu les connais.

Le plan, le journal et le profil sont partagés avec l'app Cloach : quand tu crées ou changes le plan, enregistre-le avec enregistrer_plan (3 semaines à partir d'aujourd'hui, la première commence aujourd'hui, les suivantes le lundi) ; note les séances racontées par l'utilisateur avec noter_seance ; coche-les avec marquer_seance ; mets le profil à jour avec modifier_profil quand il t'apprend un objectif, une contrainte ou une blessure."""

mcp = MCPServer(
    name="cloach",
    instructions=CONSIGNES,
    auth_server_provider=acces,
    auth=AuthSettings(
        issuer_url=URL_PUBLIQUE,
        resource_server_url=f"{URL_PUBLIQUE}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        # Les jetons sont signés par ce serveur seul : leur origine est déjà garantie.
        validate_token_resource=False,
    ),
)

Sport = Literal["course", "trail", "velo", "renfo", "natation", "autre"]
Intensite = Literal["repos", "facile", "modere", "soutenu", "tres-soutenu"]
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _uid() -> str:
    """Identifiant de l'utilisateur connecté (porté par le jeton donné à Claude)."""
    jeton = get_access_token()
    if not jeton or not jeton.subject:
        raise ToolError("Utilisateur inconnu : rebranche le connecteur Cloach.")
    return jeton.subject


def _aujourdhui() -> date:
    return datetime.now(FUSEAU).date()


def _causes(exc: BaseException) -> str:
    """Message d'erreur pour Claude, avec les causes enchaînées : garminconnect masque
    souvent la vraie (jeton refusé, blocage de l'adresse IP…) derrière un message générique."""
    causes, e = [], exc
    while e is not None and len(causes) < 4:
        causes.append(f"{type(e).__name__} : {str(e)[:300]}")
        e = e.__cause__ or e.__context__
    return " ← ".join(causes)


# ---------- Données du compte (profil, plan, journal) ----------

_verrous: dict[str, threading.Lock] = {}


def _verrou(uid: str) -> threading.Lock:
    return _verrous.setdefault(uid, threading.Lock())


async def _lire(uid: str) -> dict[str, Any]:
    try:
        return await anyio.to_thread.run_sync(base.lire_etat, uid)
    except Exception as exc:
        raise ToolError(_causes(exc)) from exc


async def _modifier(uid: str, changer) -> Any:
    """Lit l'état du compte, le modifie avec changer(etat) et l'enregistre."""

    def faire() -> Any:
        with _verrou(uid):
            etat = base.lire_etat(uid)
            resultat = changer(etat)
            base.ecrire_etat(uid, etat)
            return resultat

    try:
        return await anyio.to_thread.run_sync(faire)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(_causes(exc)) from exc


def _seances(etat: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for w in (etat.get("plan") or {}).get("semaines", []) for s in w.get("seances", [])]


def _id() -> str:
    return os.urandom(6).hex()


@mcp.tool()
async def contexte_du_jour() -> dict[str, Any]:
    """À appeler en premier : date du jour, profil sportif, séances prévues de J-7 à J+7,
    journal des 14 derniers jours et indication du branchement Garmin."""
    uid = _uid()
    etat = await _lire(uid)
    ajd = _aujourdhui()
    debut, fin = (ajd - timedelta(days=7)).isoformat(), (ajd + timedelta(days=7)).isoformat()
    garmin = await anyio.to_thread.run_sync(garmins.branche, uid)
    return {
        "aujourdhui": ajd.isoformat(),
        "jour": JOURS[ajd.weekday()],
        "profil": etat.get("profil") or {},
        "plan_resume": (etat.get("plan") or {}).get("resume", ""),
        "seances_prevues": [s for s in _seances(etat) if debut <= s.get("jour", "") <= fin],
        "journal": [s for s in etat.get("journal", []) if s.get("date", "") >= (ajd - timedelta(days=14)).isoformat()],
        "garmin_branche": garmin,
    }


@mcp.tool()
async def plan() -> dict[str, Any] | None:
    """Plan d'entraînement en cours, avec l'état de chaque séance (fait, saute ou vide = à faire)."""
    return (await _lire(_uid())).get("plan")


class Seance(BaseModel):
    jour: str = Field(description="Date AAAA-MM-JJ")
    sport: Sport
    titre: str = Field(description="Titre court, ex. « Footing + 6 lignes droites »")
    duree: int = Field(ge=0, le=600, description="Durée en minutes")
    intensite: Intensite
    details: str = Field(description="Contenu précis : échauffement, corps de séance, retour au calme, allures ou zones")


class Semaine(BaseModel):
    debut: str = Field(description="Date AAAA-MM-JJ du premier jour")
    theme: str = Field(description="Ex. « Reprise en endurance », « Semaine allégée »")
    seances: list[Seance] = Field(description="Une entrée par séance ; pas d'entrée pour les jours de repos")


@mcp.tool()
async def enregistrer_plan(resume: str, semaines: list[Semaine], conseils: list[str]) -> str:
    """Enregistre un nouveau plan d'entraînement (remplace le précédent) ; il apparaît aussitôt
    dans l'app. resume : 2 à 3 phrases sur la logique du plan. conseils : 2 à 4 conseils pratiques.
    L'état des séances déjà faites est conservé quand le jour et le sport correspondent."""
    uid = _uid()
    if not semaines or not any(w.seances for w in semaines):
        raise ToolError("Le plan ne contient aucune séance.")

    def changer(etat: dict[str, Any]) -> str:
        anciennes = [s for s in _seances(etat) if s.get("etat")]
        nouvelles = []
        for w in semaines[:6]:
            seances = []
            for s in w.seances[:14]:
                seance = {"id": _id(), **s.model_dump()}
                avant = next((x for x in anciennes if x.get("jour") == s.jour and x.get("sport") == s.sport), None)
                if avant:
                    seance["etat"] = avant["etat"]
                seances.append(seance)
            nouvelles.append({"debut": w.debut, "theme": w.theme, "seances": seances})
        etat["plan"] = {"cree": _aujourdhui().isoformat(), "resume": resume, "conseils": conseils[:6], "semaines": nouvelles}
        return f"Plan enregistré : {sum(len(w['seances']) for w in nouvelles)} séances sur {len(nouvelles)} semaines."

    return await _modifier(uid, changer)


@mcp.tool()
async def marquer_seance(id_seance: str, etat_seance: Literal["fait", "saute", "a_faire"]) -> str:
    """Coche une séance du plan comme faite, sautée, ou de nouveau à faire (identifiant donné par plan
    ou contexte_du_jour)."""
    uid = _uid()

    def changer(etat: dict[str, Any]) -> str:
        seance = next((s for s in _seances(etat) if s.get("id") == id_seance), None)
        if not seance:
            raise ToolError("Séance introuvable dans le plan.")
        seance["etat"] = "" if etat_seance == "a_faire" else etat_seance
        return f"Séance « {seance.get('titre', '')} » du {seance.get('jour')} : {etat_seance}."

    return await _modifier(uid, changer)


@mcp.tool()
async def journal(jours: int = 42) -> list[dict[str, Any]]:
    """Séances notées par l'utilisateur (date, sport, durée, distance, ressenti 1-5, notes) sur les derniers jours."""
    limite = (_aujourdhui() - timedelta(days=max(1, min(jours, 365)))).isoformat()
    etat = await _lire(_uid())
    return sorted((s for s in etat.get("journal", []) if s.get("date", "") >= limite), key=lambda s: s.get("date", ""))


@mcp.tool()
async def noter_seance(
    date_seance: str, sport: Sport, duree: int, distance_km: float | None = None,
    ressenti: int | None = None, notes: str = "",
) -> str:
    """Ajoute une séance au journal (date AAAA-MM-JJ, durée en minutes, ressenti de 1 = épuisé à 5 = excellent)
    et coche la séance du plan prévue ce jour-là pour ce sport."""
    uid = _uid()
    try:
        date.fromisoformat(date_seance)
    except ValueError:
        raise ToolError("Date attendue au format AAAA-MM-JJ.")

    def changer(etat: dict[str, Any]) -> str:
        etat.setdefault("journal", []).append({
            "id": _id(), "date": date_seance, "sport": sport, "duree": max(0, min(duree, 900)),
            "distance": distance_km, "ressenti": ressenti if ressenti in (1, 2, 3, 4, 5) else None,
            "notes": notes[:1000],
        })
        prevue = next((s for s in _seances(etat) if s.get("jour") == date_seance and s.get("sport") == sport and not s.get("etat")), None)
        if prevue:
            prevue["etat"] = "fait"
        return "Séance notée" + (f" ; « {prevue.get('titre', '')} » cochée dans le plan." if prevue else ".")

    return await _modifier(uid, changer)


@mcp.tool()
async def modifier_profil(
    objectif: str | None = None, date_objectif: str | None = None, niveau: str | None = None,
    seances_semaine: int | None = None, jours_disponibles: list[str] | None = None,
    contraintes: str | None = None, sports: list[Sport] | None = None,
) -> dict[str, Any]:
    """Met à jour le profil sportif (seuls les champs donnés changent) : objectif, date de l'objectif
    (AAAA-MM-JJ), niveau, séances par semaine, jours disponibles (lundi…dimanche), contraintes ou
    blessures, sports pratiqués. Renvoie le profil à jour."""
    uid = _uid()
    changements = {
        "objectif": objectif, "dateObjectif": date_objectif, "niveau": niveau,
        "seancesSemaine": str(seances_semaine) if seances_semaine is not None else None,
        "jours": [j for j in (jours_disponibles or []) if j in JOURS] if jours_disponibles is not None else None,
        "contraintes": contraintes, "sports": sports,
    }

    def changer(etat: dict[str, Any]) -> dict[str, Any]:
        profil = etat.setdefault("profil", {})
        profil.update({k: v for k, v in changements.items() if v is not None})
        return profil

    return await _modifier(uid, changer)


# ---------- Outils Garmin (compte de l'utilisateur) ----------

async def _garmin(nom: str, *args: Any) -> Any:
    uid = _uid()
    try:
        return await anyio.to_thread.run_sync(garmins.appeler, uid, nom, *args)
    except CompteAbsent as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(_causes(exc)) from exc


def _jour(d: str | None) -> str:
    return d or _aujourdhui().isoformat()


CHAMPS_ACTIVITE = (
    "activityId", "activityName", "startTimeLocal", "distance", "duration",
    "movingDuration", "elevationGain", "averageSpeed", "averageHR", "maxHR",
    "calories", "aerobicTrainingEffect", "anaerobicTrainingEffect",
    "trainingEffectLabel", "activityTrainingLoad", "vO2MaxValue",
    "averageRunningCadenceInStepsPerMinute", "avgPower", "normPower",
)


def _resume_activite(a: dict[str, Any]) -> dict[str, Any]:
    r = {k: a[k] for k in CHAMPS_ACTIVITE if a.get(k) is not None}
    r["type"] = (a.get("activityType") or {}).get("typeKey")
    return r


@mcp.tool()
async def activites_recentes(nombre: int = 20, type_activite: str | None = None) -> list[dict[str, Any]]:
    """Garmin : dernières activités (résumé). type_activite : running, cycling, swimming, hiking…"""
    liste = await _garmin("get_activities", 0, max(1, min(nombre, 100)), type_activite)
    return [_resume_activite(a) for a in liste]


@mcp.tool()
async def activites_par_dates(debut: str, fin: str | None = None, type_activite: str | None = None) -> list[dict[str, Any]]:
    """Garmin : activités entre deux dates incluses (AAAA-MM-JJ), en résumé."""
    liste = await _garmin("get_activities_by_date", debut, fin, type_activite)
    return [_resume_activite(a) for a in liste]


@mcp.tool()
async def detail_activite(id_activite: str) -> dict[str, Any]:
    """Garmin : détail complet d'une activité (identifiant donné par les autres outils)."""
    return await _garmin("get_activity", id_activite)


@mcp.tool()
async def tours_activite(id_activite: str) -> dict[str, Any]:
    """Garmin : tours et intervalles d'une activité."""
    return await _garmin("get_activity_splits", id_activite)


@mcp.tool()
async def resume_journee(jour: str | None = None) -> dict[str, Any]:
    """Garmin : résumé d'une journée (pas, calories, FC repos, stress, Body Battery, intensité)."""
    return await _garmin("get_user_summary", _jour(jour))


@mcp.tool()
async def sommeil(jour: str | None = None) -> dict[str, Any]:
    """Garmin : sommeil de la nuit qui précède le jour donné (phases, score, respiration)."""
    donnees = await _garmin("get_sleep_data", _jour(jour))
    # Les courbes minute par minute sont très longues et peu utiles ici.
    for cle in ("sleepMovement", "sleepLevels", "sleepRestlessMoments",
                "sleepHeartRate", "sleepStress", "sleepBodyBattery", "hrvData",
                "wellnessEpochRespirationDataDTOList", "sleepRespirationData"):
        donnees.pop(cle, None)
    return donnees


@mcp.tool()
async def vfc(jour: str | None = None) -> dict[str, Any] | None:
    """Garmin : variabilité de la fréquence cardiaque (VFC/HRV) de la nuit (moyenne, base, statut)."""
    donnees = await _garmin("get_hrv_data", _jour(jour))
    if donnees:
        donnees.pop("hrvReadings", None)
    return donnees


@mcp.tool()
async def body_battery(debut: str | None = None, fin: str | None = None) -> list[dict[str, Any]]:
    """Garmin : Body Battery jour par jour (charge, dépense, valeurs extrêmes)."""
    fin = fin or _jour(None)
    debut = debut or (date.fromisoformat(fin) - timedelta(days=6)).isoformat()
    jours = await _garmin("get_body_battery", debut, fin)
    for j in jours:
        j.pop("bodyBatteryValuesArray", None)
    return jours


@mcp.tool()
async def stress(jour: str | None = None) -> dict[str, Any]:
    """Garmin : stress de la journée (moyenne, maximum, durées par niveau)."""
    donnees = await _garmin("get_stress_data", _jour(jour))
    for cle in ("stressValuesArray", "bodyBatteryValuesArray"):
        donnees.pop(cle, None)
    return donnees


@mcp.tool()
async def frequence_cardiaque(jour: str | None = None) -> dict[str, Any]:
    """Garmin : fréquence cardiaque de la journée (repos, min, max, moyenne de repos sur 7 jours)."""
    donnees = await _garmin("get_heart_rates", _jour(jour))
    donnees.pop("heartRateValues", None)
    return donnees


@mcp.tool()
async def disponibilite_entrainement(jour: str | None = None) -> list[dict[str, Any]]:
    """Garmin : Training Readiness, score et facteurs (sommeil, récupération, charge, VFC)."""
    return await _garmin("get_training_readiness", _jour(jour))


@mcp.tool()
async def statut_entrainement(jour: str | None = None) -> dict[str, Any]:
    """Garmin : statut d'entraînement (productif, maintien, surmenage…), charge aiguë et chronique."""
    return await _garmin("get_training_status", _jour(jour))


@mcp.tool()
async def vo2max(jour: str | None = None) -> dict[str, Any]:
    """Garmin : VO2 max estimée (course et vélo) et âge physique."""
    return await _garmin("get_max_metrics", _jour(jour))


@mcp.tool()
async def predictions_course() -> dict[str, Any]:
    """Garmin : temps prédits sur 5 km, 10 km, semi-marathon et marathon."""
    return await _garmin("get_race_predictions")


# ---------- Prompts prêts à l'emploi ----------

@mcp.prompt(title="Que faire aujourd'hui ?")
def seance_du_jour() -> str:
    """Le coach regarde ta forme et ton plan, et te dit quoi faire aujourd'hui."""
    return ("Que dois-je faire aujourd'hui ? Regarde mon contexte du jour et, si Garmin est branché, ma forme "
            "(sommeil, VFC, Body Battery, disponibilité). Dis-moi si je garde, allège ou décale la séance prévue, "
            "puis détaille la séance.")


@mcp.prompt(title="Créer ou réajuster mon plan")
def creer_plan(demande: str = "") -> str:
    """Le coach construit (ou réajuste) tes 3 prochaines semaines et les enregistre dans l'app."""
    return ("Construis mon plan d'entraînement des 3 prochaines semaines d'après mon profil, ce que j'ai fait "
            "récemment et ma forme, puis enregistre-le avec enregistrer_plan."
            + (f" Ma demande : {demande}" if demande else ""))


@mcp.prompt(title="Bilan de la semaine")
def bilan_semaine() -> str:
    """Le coach fait le point sur ta semaine : ce qui a été fait, la charge, la forme, la suite."""
    return ("Fais le bilan de ma semaine : séances prévues et faites, charge, forme (Garmin si branché), "
            "points positifs, points d'attention, et ce que je dois ajuster la semaine prochaine.")


# ---------- Routes HTTP ----------

@mcp.custom_route("/garmin/{action}", methods=["POST", "OPTIONS"])
async def route_garmin(request: Request):
    """Branchement du compte Garmin depuis l'app (voir garmin.py)."""
    return await garmins.route(request)


@mcp.custom_route("/sante", methods=["GET"])
async def sante(_: Request) -> PlainTextResponse:
    """Point de contrôle pour l'hébergeur et réveil depuis l'app ; ne touche pas à Garmin."""
    return PlainTextResponse(f"ok\nadresse du connecteur : {URL_PUBLIQUE}/mcp",
                             headers={"Access-Control-Allow-Origin": "*"})


@mcp.custom_route("/connexion", methods=["GET", "POST"])
async def connexion(request: Request):
    return await acces.page_connexion(request)


app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    stateless_http=True,
    host="0.0.0.0",
)

if __name__ == "__main__":
    # forwarded_allow_ips : derrière le proxy de l'hébergeur, garder https dans les redirections.
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), forwarded_allow_ips="*")
