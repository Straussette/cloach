"""Serveur MCP de Cloach, « le coaching by Claude ».

Chaque utilisateur l'ajoute comme connecteur dans son Claude (claude.ai, appli
mobile ou de bureau) et se connecte avec son compte Cloach : le coaching se passe
dans ses conversations, sur son propre abonnement Claude. Le serveur donne à Claude :
- les données Garmin de l'utilisateur (s'il a branché son compte dans l'app) ;
- son profil sportif, son plan d'entraînement, son journal et l'historique de ses
  activités (importé depuis Garmin par l'app), partagés avec l'app ;
- des consignes de coach (instructions du serveur) et des prompts prêts à l'emploi.

Variables d'environnement :
- CLE_SIGNATURE (obligatoire, 32 caractères minimum) : signe les jetons donnés à
  Claude et chiffre les jetons Garmin. La changer déconnecte tout le monde et
  oblige chacun à rebrancher Garmin.
- SUPABASE_URL, SUPABASE_CLE (clé publique sb_publishable_…) et
  SUPABASE_CLE_SECRETE (clé secrète sb_secret_…) du projet Supabase « cloach ».
- ORIGINE_APP : origine de l'app (par défaut https://straussette.github.io) ; URL_APP : adresse de
  l'app (par défaut ORIGINE_APP + /cloach/), où Strava renvoie l'utilisateur.
- STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET (facultatif) : application Strava, pour les utilisateurs sans
  Garmin (voir strava.py).
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
from starlette.responses import JSONResponse, PlainTextResponse, Response

from acces import Acces
from donnees import Supabase
from garmin import CompteAbsent, Garmins
from strava import Stravas
import terrains

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
URL_APP = os.environ.get("URL_APP", ORIGINE_APP + "/cloach/").strip()
stravas = Stravas(base, {ORIGINE_APP}, CLE_SIGNATURE, URL_PUBLIQUE, URL_APP)
acces = Acces(CLE_SIGNATURE, URL_PUBLIQUE, base.verifier_compte)

CONSIGNES = """Tu es Cloach, le coach sportif personnel de l'utilisateur : bienveillant, précis, exigeant juste ce qu'il faut. Tu coaches la course à pied, le trail, le vélo (y compris home trainer / Zwift), le ski de randonnée, le renforcement musculaire, la natation et le multisport. Tu parles en français et tu tutoies, sauf si l'utilisateur écrit dans une autre langue.

Avant de conseiller, appuie-toi sur ses vraies données :
- contexte_du_jour donne en un appel la date, le profil, les séances prévues autour d'aujourd'hui, le journal récent, les activités des 14 derniers jours et si Garmin ou Strava est branché (avec Strava seul, les outils Garmin ne marchent pas : appuie-toi sur l'historique, et demande à l'utilisateur son sommeil et sa fatigue) ;
- pour le passé plus lointain, utilise statistiques (totaux par semaine, mois ou année, records) puis historique (le détail d'une période) : l'historique importé est plus rapide que les outils Garmin ;
- si Garmin est branché, consulte ce qui est utile, sans excès : activités récentes, disponibilite_entrainement, statut_entrainement, sommeil, vfc, body_battery. Cite brièvement les chiffres.

Principes : progressivité (au plus +10 % de charge par semaine, une semaine allégée toutes les 3 ou 4), polarisation (beaucoup d'endurance facile, peu d'intensité), récupération. La forme du jour (sommeil, VFC, Body Battery, ressenti) prime sur le plan : le jour J, vérifie avec forme_du_jour si la séance prévue reste pertinente, puis enregistre ton verdict avec avis_du_jour (garder, alléger, remplacer ou repos), avec la séance de remplacement s'il y en a une. Respecte disponibilités, contraintes et blessures ; douleur inhabituelle → prudence et conseil de consulter. Sois concret : durées, allures, zones de fréquence cardiaque ou de puissance quand tu les connais.

Le plan, le journal et le profil sont partagés avec l'app Cloach : quand tu crées ou changes le plan, enregistre-le avec enregistrer_plan (3 semaines à partir d'aujourd'hui, la première commence aujourd'hui, les suivantes le lundi). Pour chaque séance, écris son objectif (son intérêt en une courte phrase) : c'est ce que l'utilisateur voit d'abord dans le plan. Le plan n'est pas rigide : donne à chaque séance 1 à 3 alternatives utiles (par exemple « Si fatigue », « Si peu de temps », « En intérieur », ou un autre sport au même effet), que l'utilisateur peut choisir dans l'app. Pour une séance de course sur tapis, ajoute toujours une alternative structurée « Sur route » (et pour une séance sur route ou sur piste, une alternative « Sur tapis »), qui garde le même effet : allures adaptées, côtes remplacées par l'inclinaison du tapis ou l'inverse. Structure aussi les alternatives. Structure les séances de course, de trail et de vélo étape par étape (champ etapes, ou structurer_seance) avec des cibles adaptées à l'athlète (zones de FC, allures, puissance ou % de FTP) : l'app peut alors les envoyer sur sa montre Garmin ou en faire un fichier Zwift ; note avec noter_seance les séances que la montre n'a pas enregistrées, ou le ressenti et les remarques de l'utilisateur sur une séance (ne recopie jamais dans le journal une activité Garmin sans rien y ajouter : l'app l'affiche déjà) ; coche-les avec marquer_seance (dans l'app, l'utilisateur coche lui-même ses séances : l'activité Garmin y est rattachée, avec son ressenti et son commentaire, visibles dans contexte_du_jour et historique) ; mets le profil à jour avec modifier_profil quand il t'apprend un objectif, une contrainte ou une blessure. Quand tu analyses son historique, enregistre ton commentaire avec enregistrer_analyse : il s'affiche dans l'onglet Stats de l'app. Quand tu commentes une séance réalisée, enregistre ton avis avec avis_seance : il s'affiche sous la séance dans l'app. Pour la course et le trail, profil_terrain montre comment l'athlète court à plat, en montée et en descente (puissance, FC, cadence, foulée, VAP, montées) : quand tu construis un plan, cible le terrain le plus faible au regard de l'objectif ; quand tu fais ce diagnostic, enregistre-le avec enregistrer_analyse_terrain."""

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

Sport = Literal["course", "trail", "velo", "renfo", "natation", "ski_rando", "autre"]
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
    try:
        activites = await anyio.to_thread.run_sync(base.lire_activites, uid, (ajd - timedelta(days=14)).isoformat())
    except Exception:
        activites = []
    return {
        "aujourdhui": ajd.isoformat(),
        "jour": JOURS[ajd.weekday()],
        "profil": etat.get("profil") or {},
        "plan_resume": (etat.get("plan") or {}).get("resume", ""),
        "seances_prevues": [s for s in _seances(etat) if debut <= s.get("jour", "") <= fin],
        "journal": [s for s in etat.get("journal", []) if s.get("date", "") >= (ajd - timedelta(days=14)).isoformat()],
        "activites_14_jours": [_compacte(a, etat.get("commentaires"), etat.get("avisSeances")) for a in activites],
        "garmin_branche": garmin,
        "strava_branche": await anyio.to_thread.run_sync(lambda: bool(base.lire_jetons_strava(uid))),
    }


@mcp.tool()
async def plan() -> dict[str, Any] | None:
    """Plan d'entraînement en cours, avec l'état de chaque séance (fait, saute ou vide = à faire)."""
    return (await _lire(_uid())).get("plan")


class Cible(BaseModel):
    type: Literal["zone_fc", "fc", "allure", "puissance", "zone_puissance", "ftp"]
    min: float | None = Field(default=None, description="fc en bpm, allure en min/km (4.5 = 4:30), puissance en W, ftp en %")
    max: float | None = None
    zone: int | None = Field(default=None, description="Zone (1 à 5 pour la FC, 1 à 7 pour la puissance)")


class EtapeSimple(BaseModel):
    type: Literal["echauffement", "effort", "recuperation", "retour_au_calme", "repos"]
    duree_s: int | None = Field(default=None, ge=1, le=36000, description="Durée en secondes")
    distance_m: int | None = Field(default=None, ge=1, le=300000, description="Ou distance en mètres")
    cible: Cible | None = None
    note: str | None = Field(default=None, description="Consigne courte affichée sur la montre")


class Etape(EtapeSimple):
    type: Literal["echauffement", "effort", "recuperation", "retour_au_calme", "repos", "repetition"]
    repetitions: int | None = Field(default=None, ge=1, le=50, description="Pour une répétition : nombre de fois")
    etapes: list[EtapeSimple] | None = Field(default=None, description="Pour une répétition : les étapes répétées (ex. effort puis récupération)")


class Alternative(BaseModel):
    pour: str = Field(description="Dans quel cas la choisir, ex. « Si fatigue », « Si peu de temps », « En intérieur »")
    sport: Sport
    titre: str = Field(description="Titre court")
    duree: int = Field(ge=0, le=600, description="Durée en minutes")
    intensite: Intensite
    details: str = Field(description="Contenu précis de la séance")
    etapes: list[Etape] = Field(default_factory=list, description="Structure étape par étape (facultatif)")


class Seance(BaseModel):
    jour: str = Field(description="Date AAAA-MM-JJ")
    sport: Sport
    titre: str = Field(description="Titre court, ex. « Footing + 6 lignes droites »")
    duree: int = Field(ge=0, le=600, description="Durée en minutes")
    intensite: Intensite
    details: str = Field(description="Contenu précis : échauffement, corps de séance, retour au calme, allures ou zones")
    objectif: str = Field(default="", description="L'intérêt de la séance en une courte phrase, affichée dans le plan simplifié (ex. « Développer la VMA », « Endurance fondamentale, récupération active »)")
    alternatives: list[Alternative] = Field(default_factory=list, description="1 à 3 variantes que l'utilisateur peut choisir à la place")
    etapes: list[Etape] = Field(default_factory=list, description="Structure étape par étape, pour l'envoyer sur la montre Garmin ou dans Zwift : échauffement, efforts (souvent dans une répétition effort + récupération), retour au calme, avec des cibles (zones de FC, allures, puissance ou % de FTP)")


class Semaine(BaseModel):
    debut: str = Field(description="Date AAAA-MM-JJ du premier jour")
    theme: str = Field(description="Ex. « Reprise en endurance », « Semaine allégée »")
    seances: list[Seance] = Field(description="Une entrée par séance ; pas d'entrée pour les jours de repos")


@mcp.tool()
async def enregistrer_plan(resume: str, semaines: list[Semaine], conseils: list[str]) -> str:
    """Enregistre un nouveau plan d'entraînement (remplace le précédent) ; il apparaît aussitôt
    dans l'app. resume : 2 à 3 phrases sur la logique du plan. conseils : 2 à 4 conseils pratiques.
    Donne à chaque séance de course, de trail et de vélo ses etapes (échauffement, répétitions, retour au calme,
    avec cibles), indispensables pour l'envoyer sur la montre Garmin ou dans Zwift.
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
                seance = {"id": _id(), **s.model_dump(exclude_none=True)}
                seance["alternatives"] = seance["alternatives"][:3]
                avant = next((x for x in anciennes if x.get("jour") == s.jour and x.get("sport") == s.sport), None)
                if avant:
                    seance["etat"] = avant["etat"]
                seances.append(seance)
            nouvelles.append({"debut": w.debut, "theme": w.theme, "seances": seances})
        etat["plan"] = {"cree": _aujourdhui().isoformat(), "resume": resume, "conseils": conseils[:6], "semaines": nouvelles}
        message = f"Plan enregistré : {sum(len(w['seances']) for w in nouvelles)} séances sur {len(nouvelles)} semaines."
        # Les séances de course, trail et vélo doivent être structurées pour partir sur la montre ou dans Zwift
        a_structurer = [s for w in nouvelles for s in w["seances"] if s["sport"] in ("course", "trail", "velo") and not s.get("etapes")]
        if a_structurer:
            message += (f" {len(a_structurer)} séance(s) de course, trail ou vélo sans étapes : structure-les maintenant avec "
                        "structurer_seance (ids : " + ", ".join(s["id"] for s in a_structurer) + ") pour qu'elles puissent "
                        "partir sur la montre Garmin ou dans Zwift.")
        return message

    return await _modifier(uid, changer)


@mcp.tool()
async def structurer_seance(id_seance: str, etapes: list[Etape]) -> str:
    """Donne (ou remplace) la structure étape par étape d'une séance du plan, pour qu'elle puisse partir sur
    la montre Garmin ou dans Zwift. Exemple de fractionné : échauffement 15 min zone_fc 2, répétition 6 fois
    [effort 3 min allure 4:10-4:20, récupération 2 min zone_fc 1], retour au calme 10 min."""
    uid = _uid()

    def changer(etat: dict[str, Any]) -> str:
        seance = next((x for x in _seances(etat) if x.get("id") == id_seance), None)
        if not seance:
            raise ToolError("Séance introuvable dans le plan.")
        seance["etapes"] = [e.model_dump(exclude_none=True) for e in etapes]
        seance.pop("garmin", None)
        return f"Séance « {seance.get('titre', '')} » structurée ({len(etapes)} étapes) : elle peut partir sur la montre depuis l'app."

    return await _modifier(uid, changer)


@mcp.tool()
async def envoyer_seance_garmin(id_seance: str, alternative: int | None = None) -> str:
    """Crée dans Garmin Connect l'entraînement d'une séance structurée et le programme au jour prévu : la
    montre le reçoit à la synchronisation suivante. alternative : numéro (à partir de 0) d'une alternative de
    la séance à envoyer à la place (ou en plus) de la séance elle-même."""
    uid = _uid()
    try:
        r = await anyio.to_thread.run_sync(garmins.entrainement, uid, id_seance, alternative)
    except CompteAbsent as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(_causes(exc)) from exc
    return f"Entraînement Garmin créé et programmé le {r['programme']}."


@mcp.tool()
async def forme_du_jour() -> dict[str, Any]:
    """Garmin, en un appel : les signaux de récupération du jour (score de sommeil, statut de VFC, Body Battery,
    FC de repos et sa moyenne sur 7 jours, stress, Training Readiness si la montre le fournit)."""
    uid = _uid()
    try:
        return await anyio.to_thread.run_sync(garmins.forme, uid, _aujourdhui().isoformat())
    except CompteAbsent as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(_causes(exc)) from exc


@mcp.tool()
async def vap_activite(id_activite: str) -> dict[str, Any]:
    """Garmin : VAP (allure ajustée à la pente, comme Strava) et allure en mouvement d'une sortie de course
    ou de trail, en minutes par km, calculées à partir de son profil détaillé et gardées avec l'activité."""
    uid = _uid()
    try:
        return await anyio.to_thread.run_sync(garmins.vap, uid, id_activite)
    except CompteAbsent as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(_causes(exc)) from exc


@mcp.tool()
async def records_garmin() -> list[dict[str, Any]]:
    """Garmin : records personnels (meilleurs efforts) : meilleurs temps sur 1 km, 1 mile, 5 km, 10 km,
    semi-marathon et marathon (valeur en secondes), plus longues sorties (valeur en mètres), avec la date
    et l'activité. Les records sans nom sont d'autres types Garmin (pas, vélo…)."""
    uid = _uid()
    try:
        return await anyio.to_thread.run_sync(garmins.records, uid)
    except CompteAbsent as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(_causes(exc)) from exc


@mcp.tool()
async def avis_du_jour(
    verdict: Literal["garder", "alleger", "remplacer", "repos"], explication: str,
    id_seance: str | None = None, remplacement: Alternative | None = None,
) -> str:
    """Enregistre ton verdict sur la séance du jour, affiché dans le Calendrier de l'app : garder, alleger,
    remplacer ou repos, avec une explication courte qui cite les données de forme. Avec id_seance et
    remplacement (pour alleger ou remplacer), la séance prévue est remplacée ; elle reste disponible dans
    ses alternatives (« Prévu initialement ») si l'utilisateur préfère la faire quand même."""
    uid = _uid()

    def changer(etat: dict[str, Any]) -> str:
        message = "Avis enregistré"
        if id_seance and remplacement:
            seance = next((x for x in _seances(etat) if x.get("id") == id_seance), None)
            if not seance:
                raise ToolError("Séance introuvable dans le plan.")
            initiale = {k: seance.get(k) for k in ("sport", "titre", "duree", "intensite", "details", "etapes") if seance.get(k) is not None}
            nouvelle = remplacement.model_dump(exclude_none=True)
            nouvelle.pop("pour", None)
            seance.pop("etapes", None)
            seance.pop("garmin", None)  # l'entraînement envoyé sur la montre ne correspond plus
            seance.update(nouvelle)
            alternatives = [a for a in seance.get("alternatives") or [] if a.get("pour") != "Prévu initialement"]
            seance["alternatives"] = [{"pour": "Prévu initialement", **initiale}, *alternatives][:4]
            message += f" ; séance remplacée par « {remplacement.titre} »"
        etat["avisDuJour"] = {"date": _aujourdhui().isoformat(), "verdict": verdict,
                              "explication": explication[:1500], "id": id_seance}
        return message + "."

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
    et coche la séance du plan prévue ce jour-là pour ce sport. À utiliser pour une séance que la montre n'a
    pas enregistrée, ou pour ajouter le ressenti et les remarques de l'utilisateur sur une activité Garmin
    (mêmes date, sport et durée : l'app les rattache à l'activité). N'y recopie pas une activité Garmin
    sans rien y ajouter : elle est déjà affichée."""
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
    contraintes: str | None = None, sports: list[Sport] | None = None, ftp: int | None = None,
) -> dict[str, Any]:
    """Met à jour le profil sportif (seuls les champs donnés changent) : objectif, date de l'objectif
    (AAAA-MM-JJ), niveau, séances par semaine, jours disponibles (lundi…dimanche), contraintes ou
    blessures, sports pratiqués, FTP vélo en watts. Renvoie le profil à jour."""
    uid = _uid()
    changements = {
        "objectif": objectif, "dateObjectif": date_objectif, "niveau": niveau,
        "seancesSemaine": str(seances_semaine) if seances_semaine is not None else None,
        "jours": [j for j in (jours_disponibles or []) if j in JOURS] if jours_disponibles is not None else None,
        "contraintes": contraintes, "sports": sports,
        "ftp": str(ftp) if ftp else None,
    }

    def changer(etat: dict[str, Any]) -> dict[str, Any]:
        profil = etat.setdefault("profil", {})
        profil.update({k: v for k, v in changements.items() if v is not None})
        return profil

    return await _modifier(uid, changer)


# ---------- Historique des activités (table activites) ----------

CHAMPS_HISTORIQUE = ("id", "debut", "sport", "type", "nom", "distance_km", "duree_min", "denivele_m",
                     "fc_moy", "fc_max", "charge", "te_aerobie", "te_anaerobie", "puissance_moy", "puissance_norm",
                     "vap_min_km", "allure_mouvement_min_km")


def _compacte(a: dict[str, Any], commentaires: dict[str, Any] | None = None,
              avis_coachs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Activité sans les champs vides, avec le ressenti et le commentaire laissés par l'utilisateur dans l'app."""
    compacte = {k: a[k] for k in CHAMPS_HISTORIQUE if a.get(k) not in (None, "")}
    # Allure moyenne (course, trail) en min/km ; la VAP (allure ajustée à la pente) est calculée à la
    # demande à partir du profil de la sortie (outil vap_activite) et gardée avec l'activité
    km, minutes = float(a.get("distance_km") or 0), float(a.get("duree_min") or 0)
    if km > 0 and minutes > 0 and a.get("sport") in ("course", "trail"):
        compacte["allure_min_km"] = round(minutes / km, 2)
    avis = (commentaires or {}).get(str(a.get("id")))
    avis_coach = (avis_coachs or {}).get(str(a.get("id")))
    if avis_coach:
        compacte["avis_coach"] = avis_coach.get("texte")
    if avis:
        compacte["ressenti"] = avis.get("ressenti")
        compacte["commentaire"] = avis.get("notes")
    return compacte


async def _activites(uid: str, debut: str | None, fin: str | None, sport: str | None) -> list[dict[str, Any]]:
    try:
        return await anyio.to_thread.run_sync(base.lire_activites, uid, debut, fin, sport)
    except Exception as exc:
        raise ToolError(_causes(exc)) from exc


@mcp.tool()
async def historique(debut: str | None = None, fin: str | None = None, sport: Sport | None = None) -> dict[str, Any]:
    """Détail des activités importées entre deux dates (AAAA-MM-JJ, incluses), éventuellement pour un sport.
    Au plus 300 activités (les plus récentes) : pour une longue période, commence par statistiques."""
    uid = _uid()
    activites = await _activites(uid, debut, fin, sport)
    etat = await _lire(uid)
    return {
        "nombre": len(activites),
        "tronque": len(activites) > 300,
        "activites": [_compacte(a, etat.get("commentaires"), etat.get("avisSeances")) for a in activites[-300:]],
    }


def _sorties(activites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recolle une sortie enregistrée en plusieurs morceaux (même jour, même famille de sport, relancée
    moins de 30 min après la fin du morceau précédent) : utile pour les records."""
    famille = lambda s: "pied" if s in ("course", "trail") else s
    triees = sorted((a for a in activites if len(str(a.get("debut") or "")) >= 16), key=lambda a: a["debut"])
    resultat: list[dict[str, Any]] = []
    for a in triees:
        debut = datetime.fromisoformat(str(a["debut"]))
        fin = debut + timedelta(minutes=float(a.get("duree_min") or 0))
        prec = resultat[-1] if resultat else None
        if (prec and prec["jour"] == a["jour"] and famille(prec["sport"]) == famille(a["sport"])
                and timedelta(0) <= debut - prec["_fin"] <= timedelta(minutes=30)):
            for k in ("distance_km", "duree_min", "denivele_m", "charge"):
                prec[k] = float(prec.get(k) or 0) + float(a.get(k) or 0)
            prec["nom"] = f"{prec.get('nom', '')} + {a.get('nom', '')}"
            prec["_fin"] = fin
        else:
            resultat.append({**a, "_fin": fin})
    for a in resultat:
        a.pop("_fin", None)
    return resultat + [a for a in activites if len(str(a.get("debut") or "")) < 16]


def _ajouter(total: dict[str, Any], a: dict[str, Any]) -> None:
    total["seances"] += 1
    total["km"] += float(a.get("distance_km") or 0)
    total["heures"] += float(a.get("duree_min") or 0) / 60
    total["denivele_m"] += float(a.get("denivele_m") or 0)
    total["charge"] += float(a.get("charge") or 0)


def _vide() -> dict[str, Any]:
    return {"seances": 0, "km": 0.0, "heures": 0.0, "denivele_m": 0.0, "charge": 0.0}


def _arrondir(t: dict[str, Any]) -> dict[str, Any]:
    return {k: round(v, 1) if isinstance(v, float) else v for k, v in t.items()}


@mcp.tool()
async def statistiques(
    regroupement: Literal["semaine", "mois", "annee"] = "mois",
    debut: str | None = None, fin: str | None = None, sport: Sport | None = None,
) -> dict[str, Any]:
    """Totaux de l'historique importé (séances, km, heures, dénivelé, charge Garmin) par semaine
    (lundi), mois ou année, avec les totaux par sport et les records. Sans dates : tout l'historique."""
    activites = await _activites(_uid(), debut, fin, sport)
    if not activites:
        return {"nombre": 0, "message": "Aucune activité importée sur cette période : l'utilisateur peut importer son historique dans l'app (Profil → Garmin)."}
    periodes: dict[str, dict[str, Any]] = {}
    par_sport: dict[str, dict[str, Any]] = {}
    for a in activites:
        j = date.fromisoformat(str(a["jour"]))
        cle = ((j - timedelta(days=j.weekday())).isoformat() if regroupement == "semaine"
               else j.isoformat()[:7] if regroupement == "mois" else str(j.year))
        _ajouter(periodes.setdefault(cle, _vide()), a)
        _ajouter(par_sport.setdefault(a["sport"], _vide()), a)
    entieres = _sorties(activites)
    record = lambda champ, filtre=None: max(
        (a for a in entieres if a.get(champ) and (filtre is None or a["sport"] in filtre)),
        key=lambda a: float(a[champ]), default=None)
    records = {
        "plus_longue_course": record("distance_km", {"course", "trail"}),
        "plus_longue_sortie_velo": record("distance_km", {"velo"}),
        "plus_gros_denivele": record("denivele_m"),
        "plus_longue_duree": record("duree_min"),
        "plus_grosse_charge": record("charge"),
    }
    return {
        "nombre": len(activites),
        "du": activites[0]["jour"], "au": activites[-1]["jour"],
        "par_periode": {k: _arrondir(v) for k, v in sorted(periodes.items())},
        "par_sport": {k: _arrondir(v) for k, v in par_sport.items()},
        "records": {k: _compacte(v) for k, v in records.items() if v},
    }


@mcp.tool()
async def avis_seance(texte: str, id_activite: str | None = None, id_seance: str | None = None) -> str:
    """Enregistre ton avis de coach sur une séance réalisée (quelques phrases : ce qui s'est bien passé, ce
    qui interroge, ce que ça change pour la suite) ; il s'affiche dans l'app sous l'activité ou la séance.
    id_activite : l'activité Garmin (historique, contexte_du_jour) ; id_seance : la séance du plan, si elle
    a été faite sans activité enregistrée. Remplace l'avis précédent sur la même séance."""
    uid = _uid()
    if not id_activite and not id_seance:
        raise ToolError("Indique l'activité (id_activite) ou la séance (id_seance) concernée.")

    def changer(etat: dict[str, Any]) -> str:
        cle = str(id_activite) if id_activite else f"seance:{id_seance}"
        etat.setdefault("avisSeances", {})[cle] = {"date": _aujourdhui().isoformat(), "texte": texte[:3000],
                                                   "seance": id_seance}
        return "Avis enregistré : il s'affiche dans l'app sous la séance."

    return await _modifier(uid, changer)


@mcp.tool()
async def enregistrer_analyse(texte: str) -> str:
    """Enregistre ton commentaire sur l'historique de l'utilisateur (quelques paragraphes en texte simple,
    listes à tirets possibles) ; il s'affiche dans l'onglet Stats de l'app et remplace le précédent."""
    uid = _uid()

    def changer(etat: dict[str, Any]) -> str:
        etat["analyse"] = {"date": _aujourdhui().isoformat(), "texte": texte[:6000]}
        return "Analyse enregistrée : elle apparaît dans l'onglet Stats de l'app."

    return await _modifier(uid, changer)


@mcp.tool()
async def profil_terrain(semaines: int = 8) -> dict[str, Any]:
    """Profil par terrain (plat, montée, descente) des sorties de course et de trail des dernières semaines :
    part du temps, vitesse, VAP, puissance, FC, cadence, longueur de foulée (temps de contact et ratio
    vertical si la montre les mesure), par tranche de pente (2-6 %, 6-12 %, 12 % et plus) ; montées
    récentes (D+, pente, VAM, puissance par tiers : fléchit, régulier ou accélère) ; évolution de
    l'efficacité à plat (vitesse pour 100 W) ; constats calculés. Les sorties pas encore analysées le
    sont au passage (8 au plus par appel, Garmin requis). Sers-t'en pour repérer le terrain le plus
    faible au regard de l'objectif et cibler les séances, puis enregistre ton commentaire avec
    enregistrer_analyse_terrain."""
    uid = _uid()
    semaines = max(2, min(26, semaines))
    debut = (_aujourdhui() - timedelta(weeks=semaines)).isoformat()

    async def lire() -> list[dict[str, Any]]:
        return [a for a in await _activites(uid, debut, None, None) if a.get("sport") in ("course", "trail")]

    a_analyser = lambda lignes: [a for a in lignes if (a.get("terrains") or {}).get("v") != terrains.VERSION
                                 and str(a.get("id", "")).isdigit() and float(a.get("distance_km") or 0) >= 1]
    lignes = await lire()
    probleme = None
    branche = bool(a_analyser(lignes)) and await anyio.to_thread.run_sync(garmins.branche, uid)
    if branche:
        # Les plus récentes d'abord, une à la fois, avec une pause : Garmin n'aime pas les rafales
        for a in sorted(a_analyser(lignes), key=lambda x: str(x.get("debut") or x["jour"]), reverse=True)[:8]:
            try:
                await anyio.to_thread.run_sync(garmins.vap, uid, str(a["id"]))
            except Exception as exc:
                probleme = _causes(exc)
                break
            await anyio.sleep(1)
        lignes = await lire()
    resultat = terrains.profil(lignes)
    resultat["semaines"] = semaines
    restant = len(a_analyser(lignes))
    if restant:
        resultat["restant_a_analyser"] = restant
        suite = (" : Garmin n'est pas branché" if not branche else f" (Garmin : {probleme})" if probleme
                 else " : rappelle profil_terrain pour continuer")
        resultat["message"] = f"{restant} sortie(s) de la période ne sont pas encore analysées{suite}."
    if not resultat["sorties"] and not restant:
        resultat["message"] = "Aucune sortie de course ou de trail sur la période."
    return resultat


@mcp.tool()
async def enregistrer_analyse_terrain(texte: str) -> str:
    """Enregistre ton commentaire sur le profil par terrain (diagnostic et séances proposées pour chaque
    terrain, en texte simple, listes à tirets possibles) ; il s'affiche dans l'onglet Stats de l'app,
    sous « Par terrain », et remplace le précédent."""
    uid = _uid()

    def changer(etat: dict[str, Any]) -> str:
        etat["analyseTerrain"] = {"date": _aujourdhui().isoformat(), "texte": texte[:6000]}
        return "Analyse par terrain enregistrée : elle apparaît dans l'onglet Stats de l'app."

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
                "wellnessEpochRespirationDataDTOList", "sleepRespirationData",
                "wellnessEpochSPO2DataDTOList", "wellnessEpochRespirationAveragesList"):
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


@mcp.prompt(title="Ma séance tient-elle ?")
def verifier_seance() -> str:
    """Le coach vérifie, d'après ta récupération, si la séance du jour reste pertinente."""
    return ("Vérifie si ma séance prévue aujourd'hui reste pertinente : regarde ma forme du jour (sommeil, VFC, "
            "Body Battery, FC de repos) et ma charge récente. Dis-moi s'il faut la garder, l'alléger, la remplacer "
            "ou me reposer, puis enregistre ton avis (avec la séance de remplacement s'il y en a une).")


@mcp.prompt(title="Créer ou réajuster mon plan")
def creer_plan(demande: str = "") -> str:
    """Le coach construit (ou réajuste) tes 3 prochaines semaines et les enregistre dans l'app."""
    return ("Construis mon plan d'entraînement des 3 prochaines semaines d'après mon profil, ce que j'ai fait "
            "récemment et ma forme, puis enregistre-le avec enregistrer_plan."
            + (f" Ma demande : {demande}" if demande else ""))


@mcp.prompt(title="Analyser mon historique")
def analyse_historique() -> str:
    """Le coach commente ton historique d'activités : volume, régularité, progression, points d'attention."""
    return ("Analyse mon historique d'activités : regarde mes statistiques par mois (et par semaine sur les 3 "
            "derniers mois), mes records et la répartition par sport. Commente le volume, la régularité, la "
            "progression, l'équilibre entre intensité et endurance, et les points d'attention, en lien avec mon "
            "objectif. Puis enregistre ce commentaire avec enregistrer_analyse.")


@mcp.prompt(title="Analyser ma foulée par terrain")
def analyse_terrain() -> str:
    """Le coach compare ta course à plat, en montée et en descente, et propose des séances par terrain."""
    return ("Analyse mon profil par terrain (profil_terrain) : comment je cours à plat, en montée et en descente "
            "(puissance, FC, cadence, foulée, VAP), comment je gère mes montées, et ce qui évolue. Dis-moi mon "
            "terrain le plus faible au regard de mon objectif, propose une ou deux séances ciblées par terrain, "
            "puis enregistre ce diagnostic avec enregistrer_analyse_terrain.")


@mcp.prompt(title="Avis sur ma dernière séance")
def avis_derniere_seance() -> str:
    """Le coach analyse ta dernière séance et enregistre son avis dans l'app."""
    return ("Donne-moi ton avis de coach sur ma dernière séance : regarde son détail (tours, FC, allure, VAP), "
            "compare avec ce qui était prévu, mon ressenti et ma forme, puis enregistre ton avis avec avis_seance.")


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


@mcp.custom_route("/strava/retour", methods=["GET"])
async def route_strava_retour(request: Request):
    """Retour de strava.com après l'accord de l'utilisateur (voir strava.py)."""
    return await stravas.retour(request)


@mcp.custom_route("/strava/{action}", methods=["POST", "OPTIONS"])
async def route_strava(request: Request):
    """Branchement et import Strava depuis l'app (voir strava.py)."""
    return await stravas.route(request)


@mcp.custom_route("/compte/supprimer", methods=["POST", "OPTIONS"])
async def supprimer_compte(request: Request):
    """Suppression du compte depuis l'app : accès Strava retiré, session Garmin fermée,
    puis compte supprimé avec toutes ses données (profil, plan, journal, activités, jetons)."""
    cors = {"Access-Control-Allow-Origin": ORIGINE_APP, "Access-Control-Allow-Headers": "authorization, content-type",
            "Access-Control-Allow-Methods": "POST, OPTIONS", "Vary": "Origin"}
    if request.method == "OPTIONS":
        return Response(status_code=204, headers=cors)
    if request.headers.get("origin", "") != ORIGINE_APP:
        return JSONResponse({"erreur": "Origine non autorisée"}, 403, headers=cors)
    jeton = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    uid = await anyio.to_thread.run_sync(base.utilisateur, jeton)
    if not uid:
        return JSONResponse({"erreur": "Connecte-toi à Cloach"}, 401, headers=cors)

    def supprimer() -> None:
        if stravas.configure and base.lire_jetons_strava(uid):
            stravas._debrancher(uid)
        garmins.oublier(uid)
        base.supprimer_utilisateur(uid)

    try:
        await anyio.to_thread.run_sync(supprimer)
    except Exception as e:
        print(f"Suppression du compte : {type(e).__name__} : {e}", flush=True)
        return JSONResponse({"erreur": "La suppression a échoué, réessaie dans un moment."}, 500, headers=cors)
    print("Compte supprimé", flush=True)
    return JSONResponse({"supprime": True}, headers=cors)


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
