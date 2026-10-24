"""Accès au projet Supabase de Cloach.

- Comptes : vérification d'un e-mail et d'un mot de passe (connexion OAuth depuis
  claude.ai) et d'un jeton de connexion de l'app (branchement de Garmin).
- Données de chaque compte (table donnees) : profil, plan, journal, dans un seul
  document JSON partagé avec l'app.
- Historique des activités (table activites), importé depuis Garmin.
- Jetons Garmin (table garmin_comptes) et Strava (table strava_comptes), chiffrés avant d'être envoyés.

La clé secrète (SUPABASE_CLE_SECRETE) passe outre les règles RLS : chaque fonction
ci-dessous filtre donc elle-même sur l'identifiant de l'utilisateur.
"""

import base64
import hashlib
import time
from datetime import datetime, timezone
from typing import Any

import requests
from cryptography.fernet import Fernet, InvalidToken  # fourni par pyjwt[crypto], dépendance de mcp


class Supabase:
    def __init__(self, url: str, cle_publique: str, cle_secrete: str, cle_signature: str) -> None:
        self.url = url.rstrip("/")
        self.cle_publique = cle_publique
        self.entetes = {"apikey": cle_secrete, "Content-Type": "application/json"}
        if not cle_secrete.startswith("sb_"):
            # Ancienne clé « service_role » au format JWT : elle doit aussi passer en Bearer.
            self.entetes["Authorization"] = f"Bearer {cle_secrete}"
        self.chiffre = Fernet(base64.urlsafe_b64encode(
            hashlib.sha256(b"cloach/chiffre-garmin\0" + cle_signature.encode()).digest()
        ))

    # ---------- Comptes ----------

    def verifier_compte(self, email: str, mot_de_passe: str) -> str | None:
        """Identifiant de l'utilisateur si l'e-mail et le mot de passe sont bons."""
        try:
            r = requests.post(f"{self.url}/auth/v1/token?grant_type=password", timeout=10,
                              headers={"apikey": self.cle_publique, "Content-Type": "application/json"},
                              json={"email": email, "password": mot_de_passe})
            return (r.json().get("user") or {}).get("id") if r.ok else None
        except (requests.RequestException, ValueError):
            return None

    def utilisateur(self, jeton: str) -> str | None:
        """Identifiant de l'utilisateur d'un jeton de connexion de l'app."""
        if jeton.count(".") != 2:
            return None
        try:
            r = requests.get(f"{self.url}/auth/v1/user", timeout=10,
                             headers={"apikey": self.cle_publique, "Authorization": f"Bearer {jeton}"})
            return r.json().get("id") if r.ok else None
        except (requests.RequestException, ValueError):
            return None

    # ---------- Données du compte ----------

    def lire_etat(self, uid: str) -> dict[str, Any]:
        r = requests.get(f"{self.url}/rest/v1/donnees", headers=self.entetes, timeout=10,
                         params={"select": "data", "user_id": f"eq.{uid}"})
        r.raise_for_status()
        lignes = r.json()
        etat = lignes[0]["data"] if lignes else {}
        return etat if isinstance(etat, dict) else {}

    def ecrire_etat(self, uid: str, etat: dict[str, Any]) -> None:
        etat["maj"] = int(time.time() * 1000)  # même unité que l'app (millisecondes)
        r = requests.post(
            f"{self.url}/rest/v1/donnees", timeout=10,
            headers={**self.entetes, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"user_id": uid, "data": etat, "maj": datetime.now(timezone.utc).isoformat()},
        )
        r.raise_for_status()

    # ---------- Historique des activités ----------

    def ecrire_activites(self, uid: str, activites: list[dict[str, Any]]) -> None:
        for i in range(0, len(activites), 500):
            r = requests.post(
                f"{self.url}/rest/v1/activites", timeout=30,
                headers={**self.entetes, "Prefer": "resolution=merge-duplicates,return=minimal"},
                json=[{**a, "user_id": uid} for a in activites[i:i + 500]],
            )
            r.raise_for_status()

    def lire_activites(self, uid: str, debut: str | None = None, fin: str | None = None,
                       sport: str | None = None) -> list[dict[str, Any]]:
        """Activités de l'utilisateur, de la plus ancienne à la plus récente."""
        params: list[tuple[str, str]] = [("user_id", f"eq.{uid}"), ("order", "debut.asc")]
        if debut:
            params.append(("jour", f"gte.{debut}"))
        if fin:
            params.append(("jour", f"lte.{fin}"))
        if sport:
            params.append(("sport", f"eq.{sport}"))
        lignes: list[dict[str, Any]] = []
        while True:  # l'API renvoie au plus 1000 lignes par appel
            r = requests.get(f"{self.url}/rest/v1/activites", headers=self.entetes, timeout=30,
                             params=[*params, ("limit", "1000"), ("offset", str(len(lignes)))])
            r.raise_for_status()
            page = r.json()
            lignes.extend(page)
            if len(page) < 1000:
                return lignes

    # ---------- Jetons Strava (même principe que Garmin) ----------

    def lire_jetons_strava(self, uid: str) -> str | None:
        return self._lire_jetons("strava_comptes", uid)

    def ecrire_jetons_strava(self, uid: str, jetons: str) -> None:
        self._ecrire_jetons("strava_comptes", uid, jetons)

    def supprimer_jetons_strava(self, uid: str) -> None:
        r = requests.delete(f"{self.url}/rest/v1/strava_comptes", headers=self.entetes, timeout=10,
                            params={"user_id": f"eq.{uid}"})
        r.raise_for_status()

    def _lire_jetons(self, table: str, uid: str) -> str | None:
        r = requests.get(f"{self.url}/rest/v1/{table}", headers=self.entetes, timeout=10,
                         params={"select": "jetons", "user_id": f"eq.{uid}"})
        r.raise_for_status()
        lignes = r.json()
        if not lignes:
            return None
        try:
            return self.chiffre.decrypt(lignes[0]["jetons"].encode()).decode()
        except InvalidToken:
            return None

    def _ecrire_jetons(self, table: str, uid: str, jetons: str) -> None:
        r = requests.post(
            f"{self.url}/rest/v1/{table}", timeout=10,
            headers={**self.entetes, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"user_id": uid, "jetons": self.chiffre.encrypt(jetons.encode()).decode(),
                  "maj": datetime.now(timezone.utc).isoformat()},
        )
        r.raise_for_status()

    # ---------- Jetons Garmin ----------

    def lire_jetons_garmin(self, uid: str) -> str | None:
        r = requests.get(f"{self.url}/rest/v1/garmin_comptes", headers=self.entetes, timeout=10,
                         params={"select": "jetons", "user_id": f"eq.{uid}"})
        r.raise_for_status()
        lignes = r.json()
        if not lignes:
            return None
        try:
            return self.chiffre.decrypt(lignes[0]["jetons"].encode()).decode()
        except InvalidToken:
            return None

    def ecrire_jetons_garmin(self, uid: str, jetons: str) -> None:
        r = requests.post(
            f"{self.url}/rest/v1/garmin_comptes", timeout=10,
            headers={**self.entetes, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={"user_id": uid, "jetons": self.chiffre.encrypt(jetons.encode()).decode(),
                  "maj": datetime.now(timezone.utc).isoformat()},
        )
        r.raise_for_status()

    def supprimer_jetons_garmin(self, uid: str) -> None:
        r = requests.delete(f"{self.url}/rest/v1/garmin_comptes", headers=self.entetes, timeout=10,
                            params={"user_id": f"eq.{uid}"})
        r.raise_for_status()
