"""Freins contre les essais à la chaîne (mots de passe Cloach et Garmin).

Tout passe par l'adresse IP du serveur : sans frein, quelqu'un pourrait s'en servir pour
essayer des mots de passe en masse, et Garmin ou Supabase finiraient par bloquer cette
adresse pour tous les utilisateurs. Compteurs en mémoire, remis à zéro au redémarrage.
"""

import threading
import time


class Frein:
    """Au plus `maximum` essais par clé sur une fenêtre glissante de `fenetre` secondes."""

    def __init__(self, maximum: int, fenetre: float) -> None:
        self.maximum = maximum
        self.fenetre = fenetre
        self._essais: dict[str, list[float]] = {}
        self._verrou = threading.Lock()

    def _recents(self, cle: str, maintenant: float) -> list[float]:
        return [t for t in self._essais.get(cle, []) if t > maintenant - self.fenetre]

    def bloque(self, *cles: str) -> bool:
        """Vrai si l'une des clés a déjà épuisé ses essais."""
        maintenant = time.time()
        with self._verrou:
            return any(len(self._recents(c, maintenant)) >= self.maximum for c in cles if c)

    def noter(self, *cles: str) -> None:
        """Compte un essai pour chaque clé."""
        maintenant = time.time()
        with self._verrou:
            if len(self._essais) > 10_000:  # ménage de temps en temps
                self._essais = {c: r for c, t in self._essais.items() if (r := self._recents(c, maintenant))}
            for c in cles:
                if c:
                    self._essais[c] = [*self._recents(c, maintenant), maintenant]
