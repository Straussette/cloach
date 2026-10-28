"""Connexion OAuth pour que chaque utilisateur branche Cloach dans son Claude.

claude.ai enregistre son client (inscription dynamique), envoie l'utilisateur sur
une page où il se connecte avec son compte Cloach (e-mail et mot de passe, vérifiés
par Supabase), puis échange le code obtenu contre des jetons qui portent l'identifiant
de l'utilisateur : chaque outil sait ainsi à qui il parle.

Rien n'est stocké : identifiants de client, codes et jetons sont des messages
signés (HMAC) avec CLE_SIGNATURE. Le serveur peut donc redémarrer (veille de
Render) sans que claude.ai perde la connexion. Changer CLE_SIGNATURE déconnecte tout le monde.
"""

import asyncio
import base64
import hashlib
import hmac
import html
import json
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from freins import Frein

DUREE_CODE = 5 * 60
DUREE_ACCES = 60 * 60
DUREE_RENOUVELLEMENT = 365 * 24 * 3600

# Seules les applications Claude peuvent recevoir un code : un tiers ne peut pas
# fabriquer un lien de connexion qui renverrait le code chez lui.
HOTES_AUTORISES = {"claude.ai", "claude.com"}

# Échecs de connexion tolérés par adresse e-mail et par adresse IP, sur 15 minutes
ECHECS_CONNEXION = Frein(8, 15 * 60)


def _b64(donnees: bytes) -> str:
    return base64.urlsafe_b64encode(donnees).rstrip(b"=").decode()


def _deb64(texte: str) -> bytes:
    return base64.urlsafe_b64decode(texte + "=" * (-len(texte) % 4))


class Acces:
    """Implémente OAuthAuthorizationServerProvider du SDK MCP, sans base de données."""

    def __init__(
        self, cle_signature: str, url_publique: str,
        verifier_compte: Callable[[str, str], str | None],
    ) -> None:
        self.url_publique = url_publique.rstrip("/")
        self.cle = hashlib.sha256(b"cloach/signature\0" + cle_signature.encode()).digest()
        # (e-mail, mot de passe) → identifiant Supabase de l'utilisateur, ou None
        self.verifier_compte = verifier_compte
        self._codes_utilises: dict[str, float] = {}

    # ---------- Messages signés ----------

    def _signer(self, genre: str, contenu: dict[str, Any]) -> str:
        corps = _b64(json.dumps({"g": genre, **contenu}, separators=(",", ":")).encode())
        signature = _b64(hmac.new(self.cle, corps.encode(), hashlib.sha256).digest()[:24])
        return f"{corps}.{signature}"

    def _lire(self, genre: str, jeton: str) -> dict[str, Any] | None:
        try:
            corps, signature = jeton.split(".")
            attendue = _b64(hmac.new(self.cle, corps.encode(), hashlib.sha256).digest()[:24])
            if not hmac.compare_digest(signature, attendue):
                return None
            contenu = json.loads(_deb64(corps))
        except (ValueError, json.JSONDecodeError):
            return None
        if contenu.get("g") != genre:
            return None
        if "exp" in contenu and contenu["exp"] < time.time():
            return None
        return contenu

    def _secret_client(self, client_id: str) -> str:
        return hmac.new(self.cle, b"client\0" + client_id.encode(), hashlib.sha256).hexdigest()

    # ---------- Clients ----------

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in client_info.redirect_uris or []]
        for uri in uris:
            u = urlparse(uri)
            if u.scheme != "https" or (u.hostname or "") not in HOTES_AUTORISES:
                raise RegistrationError("invalid_redirect_uri", f"Adresse de retour refusée : {uri}")
        # L'identifiant contient la fiche du client : rien à mémoriser.
        client_info.client_id = self._signer(
            "client", {"r": uris, "m": client_info.token_endpoint_auth_method}
        )
        if client_info.client_secret is not None:
            client_info.client_secret = self._secret_client(client_info.client_id)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        fiche = self._lire("client", client_id)
        if fiche is None:
            return None
        methode = fiche["m"]
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=None if methode == "none" else self._secret_client(client_id),
            client_secret_expires_at=None if methode == "none" else 0,
            redirect_uris=fiche["r"],
            token_endpoint_auth_method=methode,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )

    # ---------- Autorisation ----------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        demande = self._signer("demande", {
            "c": client.client_id,
            "u": str(params.redirect_uri),
            "x": params.redirect_uri_provided_explicitly,
            "p": params.code_challenge,
            "s": params.state,
            "o": params.scopes or [],
            "r": params.resource,
            "exp": int(time.time()) + 15 * 60,
        })
        return f"{self.url_publique}/connexion?demande={demande}"

    async def page_connexion(self, request: Request) -> Response:
        """Page où l'utilisateur se connecte avec son compte Cloach (GET), puis vérification (POST)."""
        titre = "Brancher Cloach à Claude"
        if request.method == "GET":
            demande = request.query_params.get("demande", "")
            if self._lire("demande", demande) is None:
                return _page("Lien expiré", "Relance la connexion depuis claude.ai.", None, 400)
            return _page(titre, "Connecte-toi avec ton compte Cloach.", demande)

        formulaire = await request.form()
        demande = str(formulaire.get("demande", ""))
        contenu = self._lire("demande", demande)
        if contenu is None:
            return _page("Lien expiré", "Relance la connexion depuis claude.ai.", None, 400)
        email = str(formulaire.get("email", "")).strip()
        mot_de_passe = str(formulaire.get("mot_de_passe", ""))
        cles = ("email:" + email.lower(), "ip:" + (request.client.host if request.client else ""))
        if ECHECS_CONNEXION.bloque(*cles):
            return _page(titre, "Trop d'essais : réessaie dans 15 minutes.", demande, 429)
        uid = await asyncio.to_thread(self.verifier_compte, email, mot_de_passe) if email and mot_de_passe else None
        if not uid:
            ECHECS_CONNEXION.noter(*cles)
            await asyncio.sleep(2)  # freine les essais à la chaîne
            return _page(titre, "E-mail ou mot de passe incorrect, réessaie.", demande, 401)

        code = self._signer("code", {
            "c": contenu["c"], "u": contenu["u"], "x": contenu["x"], "p": contenu["p"],
            "o": contenu["o"], "r": contenu["r"], "n": secrets.token_hex(8), "sub": uid,
            "exp": int(time.time()) + DUREE_CODE,
        })
        retour = construct_redirect_uri(contenu["u"], code=code, state=contenu["s"])
        return RedirectResponse(retour, status_code=302)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        c = self._lire("code", authorization_code)
        if c is None or c["c"] != client.client_id or authorization_code in self._codes_utilises:
            return None
        return AuthorizationCode(
            code=authorization_code, scopes=c["o"], expires_at=c["exp"], client_id=c["c"],
            code_challenge=c["p"], redirect_uri=c["u"], redirect_uri_provided_explicitly=c["x"],
            resource=c["r"], subject=c["sub"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        maintenant = time.time()
        self._codes_utilises = {k: v for k, v in self._codes_utilises.items() if v > maintenant}
        self._codes_utilises[authorization_code.code] = authorization_code.expires_at
        return self._jetons(client.client_id, authorization_code.scopes, authorization_code.resource,
                            authorization_code.subject or "")

    # ---------- Jetons ----------

    def _jetons(self, client_id: str, scopes: list[str], resource: str | None, uid: str) -> OAuthToken:
        maintenant = int(time.time())
        acces = self._signer("acces", {
            "c": client_id, "o": scopes, "r": resource, "exp": maintenant + DUREE_ACCES, "sub": uid,
        })
        renouvellement = self._signer("renouvellement", {
            "c": client_id, "o": scopes, "r": resource, "sub": uid,
            "exp": maintenant + DUREE_RENOUVELLEMENT, "n": secrets.token_hex(8),
        })
        return OAuthToken(
            access_token=acces, token_type="Bearer", expires_in=DUREE_ACCES,
            refresh_token=renouvellement, scope=" ".join(scopes) or None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        r = self._lire("renouvellement", refresh_token)
        if r is None or r["c"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token, client_id=r["c"], scopes=r["o"], expires_at=r["exp"], resource=r["r"],
            subject=r["sub"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        return self._jetons(client.client_id, scopes or refresh_token.scopes, refresh_token.resource,
                            refresh_token.subject or "")

    async def load_access_token(self, token: str) -> AccessToken | None:
        a = self._lire("acces", token)
        if a is None:
            return None
        return AccessToken(
            token=token, client_id=a["c"], scopes=a["o"], expires_at=a["exp"], resource=a["r"],
            subject=a["sub"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Sans stockage, un jeton ne peut pas être révoqué seul : changer CLE_SIGNATURE
        # les invalide tous.
        return None


def _page(titre: str, message: str, demande: str | None, statut: int = 200) -> HTMLResponse:
    formulaire = ""
    if demande:
        formulaire = f"""
      <form method="post" action="/connexion">
        <input type="hidden" name="demande" value="{html.escape(demande)}">
        <input type="email" name="email" placeholder="E-mail" autocomplete="username" autofocus required>
        <input type="password" name="mot_de_passe" placeholder="Mot de passe" autocomplete="current-password" required>
        <button type="submit">Se connecter</button>
      </form>"""
    return HTMLResponse(f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(titre)}</title>
<style>
  :root {{ color-scheme: light dark; --fond: #f3f6f5; --carte: #fff; --texte: #14211f; --accent: #0f6b63; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fond: #0e1514; --carte: #18211f; --texte: #e9f1ef; }} }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--fond);
         color: var(--texte); font: 16px/1.5 system-ui, sans-serif; padding: 16px; box-sizing: border-box; }}
  main {{ background: var(--carte); padding: 28px; border-radius: 14px; width: 100%; max-width: 360px;
         box-shadow: 0 2px 12px rgb(0 0 0 / .08); }}
  h1 {{ font-size: 1.25rem; margin: 0 0 8px; }}
  input, button {{ width: 100%; box-sizing: border-box; font: inherit; padding: 12px; border-radius: 8px; }}
  input {{ border: 1px solid #8884; margin: 0 0 12px; background: transparent; color: inherit; }}
  form {{ margin-top: 16px; }}
  button {{ border: 0; background: var(--accent); color: #fff; font-weight: 600; cursor: pointer; }}
</style></head>
<body><main><h1>{html.escape(titre)}</h1><p>{html.escape(message)}</p>{formulaire}
</main></body></html>""", status_code=statut, headers={
        # La page ne doit pas pouvoir être affichée dans le cadre d'un autre site (vol de clics)
        "X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
    })
