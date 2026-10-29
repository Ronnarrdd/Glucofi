"""Saisie du compte LibreLinkUp et message de l'essai de connexion, sans GTK (PC et tablette)."""

from __future__ import annotations

from contracts import count_fr
from services.device import REGIONS, Credentials, ParseResult


class AccountError(ValueError):
    """Formulaire du compte incomplet ; `field` : "email" ou "password"."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def resolve_account(existing: Credentials | None, email: str, password: str, region: str) -> Credentials | None:
    """Compte saisi ; None si les deux champs sont vides (rien à enregistrer). Mot de passe vide : celui déjà enregistré."""
    email, password = email.strip(), password.strip()
    if not email and not password:
        return None
    if not email:
        raise AccountError("email", "indiquez l'e-mail du compte LibreLinkUp")
    if not password and existing is not None and existing.email == email:
        password = existing.password
    if not password:
        raise AccountError("password", "indiquez le mot de passe du compte LibreLinkUp")
    if region not in REGIONS:
        raise AccountError("email", f"région inconnue : {region}")
    return Credentials(email, password, region)


def connection_message(result: ParseResult) -> str:
    """Résultat d'un essai de connexion, en une phrase."""
    if not result.readings:
        return "Connexion réussie, mais le capteur n'a encore envoyé aucune valeur."
    latest = result.readings[-1]
    sensor = f" (capteur {result.meter.serial})" if result.meter is not None else ""
    read = count_fr(len(result.readings), "valeur lue", "valeurs lues")
    return f"Connexion réussie{sensor} : {latest.mg_dl} mg/dL à {latest.device_time:%H:%M}, {read}."
