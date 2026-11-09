"""Accès au projet Supabase de Cloach.

- Comptes : vérification d'un e-mail et d'un mot de passe (connexion OAuth depuis
  claude.ai) et d'un jeton de connexion de l'app (branchement de Garmin).
- Données de chaque compte (table donnees) : profil, plan, journal, dans un seul
  document JSON partagé avec l'app.
- Historique des activités (table activites), importé depuis Garmin.
- Jetons Garmin (table garmin_comptes) et Strava (table strava_comptes), chiffrés avant d'être envoyés.
- Suppression d'un compte et de tout ce qui s'y rattache (API d'administration de Supabase Auth).

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
        # Stockage : la clé secrète aussi en Bearer (Supabase l'accepte quand elle est identique à apikey)
        self.entetes_stockage = {"apikey": cle_secrete, "Authorization": f"Bearer {cle_secrete}"}
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

    # ---------- Profils détaillés des sorties (stockage « series ») ----------
    # Une copie compressée des détails Garmin de chaque sortie analysée : si la méthode de calcul change,
    # on recalcule sans redemander les sorties à Garmin. Fichier series/<uid>/<id>.json.gz.

    def ecrire_serie(self, uid: str, id_activite: str, contenu: bytes) -> None:
        r = requests.post(f"{self.url}/storage/v1/object/series/{uid}/{id_activite}.json.gz", timeout=20,
                          headers={**self.entetes_stockage, "Content-Type": "application/gzip", "x-upsert": "true"},
                          data=contenu)
        r.raise_for_status()

    def lire_serie(self, uid: str, id_activite: str) -> bytes | None:
        r = requests.get(f"{self.url}/storage/v1/object/series/{uid}/{id_activite}.json.gz", timeout=20,
                         headers=self.entetes_stockage)
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        return r.content

    def supprimer_series(self, uid: str) -> None:
        """Supprime tous les profils détaillés d'un compte (le stockage ne suit pas la cascade de la base)."""
        while True:
            r = requests.post(f"{self.url}/storage/v1/object/list/series", timeout=20,
                              headers={**self.entetes_stockage, "Content-Type": "application/json"},
                              json={"prefix": uid, "limit": 1000, "offset": 0})
            r.raise_for_status()
            noms = [f"{uid}/{f['name']}" for f in r.json() if f.get("name")]
            if not noms:
                return
            r = requests.delete(f"{self.url}/storage/v1/object/series", timeout=30,
                                headers={**self.entetes_stockage, "Content-Type": "application/json"},
                                json={"prefixes": noms})
            r.raise_for_status()

    def supprimer_utilisateur(self, uid: str) -> None:
        """Supprime le compte. Ses lignes (données, activités, jetons, retours) partent avec
        lui : toutes les tables référencent auth.users avec « on delete cascade »."""
        r = requests.delete(f"{self.url}/auth/v1/admin/users/{uid}", headers=self.entetes, timeout=15)
        r.raise_for_status()

    def uid_par_email(self, email: str) -> str | None:
        """Identifiant d'un compte d'après son e-mail (outils réservés aux administrateurs)."""
        email = email.strip().lower()
        for page in range(1, 20):
            r = requests.get(f"{self.url}/auth/v1/admin/users", headers=self.entetes, timeout=15,
                             params={"page": page, "per_page": 200})
            r.raise_for_status()
            comptes = r.json().get("users") or []
            for c in comptes:
                if (c.get("email") or "").lower() == email:
                    return c.get("id")
            if len(comptes) < 200:
                return None
        return None

    def est_admin(self, uid: str) -> bool:
        """Vrai si le compte est dans la table admins (options réservées, comme l'analyse de tout l'historique)."""
        try:
            r = requests.get(f"{self.url}/rest/v1/admins", headers=self.entetes, timeout=10,
                             params={"select": "user_id", "user_id": f"eq.{uid}"})
            return r.ok and bool(r.json())
        except (requests.RequestException, ValueError):
            return False

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

    def completer_activite(self, uid: str, id_activite: str, champs: dict[str, Any]) -> None:
        """Ajoute des valeurs calculées à une activité (VAP…)."""
        r = requests.patch(f"{self.url}/rest/v1/activites", headers={**self.entetes, "Prefer": "return=minimal"},
                           timeout=10, params={"user_id": f"eq.{uid}", "id": f"eq.{id_activite}"}, json=champs)
        r.raise_for_status()

    # Colonnes lues par défaut : sans les analyses par terrain ni les courbes de puissance (jsonb lourds,
    # environ 4 Ko par sortie), inutiles aux statistiques, à l'historique et au contexte du jour
    COLONNES_LEGERES = ("id,source,jour,debut,sport,type,nom,distance_km,duree_min,denivele_m,fc_moy,fc_max,charge,"
                        "te_aerobie,te_anaerobie,puissance_moy,puissance_norm,calories,vap_min_km,allure_mouvement_min_km")

    def lire_activites(self, uid: str, debut: str | None = None, fin: str | None = None,
                       sport: str | None = None, complet: bool = False) -> list[dict[str, Any]]:
        """Activités de l'utilisateur, de la plus ancienne à la plus récente ; `complet` : avec les analyses
        par terrain (terrains) et les courbes de puissance (puissances_max)."""
        params: list[tuple[str, str]] = [("user_id", f"eq.{uid}"), ("order", "debut.asc,id.asc"),
                                         ("select", "*" if complet else self.COLONNES_LEGERES)]
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
