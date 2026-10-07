"""Application GTK partagée par les tests qui construisent la fenêtre principale.

Une seule application anonyme peut être enregistrée par processus (elle s'exporte sur D-Bus à un chemin fixe) :
tous les tests passent par celle-ci.
"""

from functools import cache

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402


@cache
def application() -> Adw.Application:
    """Enregistrée sans identifiant ni D-Bus, « startup » émis avant d'y ajouter une fenêtre."""
    app = Adw.Application()
    app.register(None)
    return app
