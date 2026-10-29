"""Compte LibreLinkUp dans les préférences : e-mail, mot de passe, région, essai de connexion. Mot de passe jamais relu.

La partie sans GTK (resolve_account, connection_message) est testée à part ; CgmAccountPage n'est que le formulaire.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from gi.repository import Adw, GLib, Gtk

from app.protocol_editor import entry_row, form_group
from contracts import count_fr
from services import device
from services.device import REGIONS, Credentials

log = logging.getLogger("glucofi.ui")

INTRO = (
    "Le compte de suivi LibreLinkUp (celui de l'application « LibreLinkUp » de votre téléphone), pas le compte du "
    "capteur. La personne qui porte le capteur vous invite depuis LibreLink : Partager, puis LibreLinkUp. "
    "Le mot de passe est gardé dans un fichier privé de ce PC (jamais dans la base ni envoyé à la tablette)."
)


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


def connection_message(result: device.ParseResult) -> str:
    """Résultat d'un essai de connexion, en une phrase."""
    if not result.readings:
        return "Connexion réussie, mais le capteur n'a encore envoyé aucune valeur."
    latest = result.readings[-1]
    sensor = f" (capteur {result.meter.serial})" if result.meter is not None else ""
    read = count_fr(len(result.readings), "valeur lue", "valeurs lues")
    return f"Connexion réussie{sensor} : {latest.mg_dl} mg/dL à {latest.device_time:%H:%M}, {read}."




class CgmAccountPage:
    """Page « Capteur » des préférences. `save()` rend le compte (ou None) et lève AccountError si incomplet."""

    def __init__(self, existing: Credentials | None, toast: Callable[[str], None]):
        self.existing = existing
        self.toast = toast
        self.page = Adw.PreferencesPage(title="Capteur", icon_name="glucofi-sync-symbolic")
        group = form_group("Compte LibreLinkUp", INTRO)
        self.email = entry_row("E-mail du compte de suivi", existing.email if existing else "")
        self.password = Adw.PasswordEntryRow(title="Mot de passe (enregistré, laissez vide pour le garder)" if existing else "Mot de passe")
        self.password.connect("changed", lambda r: r.remove_css_class("error"))
        self.region = Adw.ComboRow(title="Région du compte", model=Gtk.StringList.new(list(REGIONS)))
        self.region.set_selected(REGIONS.index(existing.region) if existing and existing.region in REGIONS else 0)
        for row in (self.email, self.password, self.region):
            group.add(row)
        self.try_button = Adw.ButtonRow(title="Tester la connexion", start_icon_name="glucofi-sync-symbolic")
        self.try_button.connect("activated", lambda _r: self._try())
        group.add(self.try_button)
        self.remove_button = Adw.ButtonRow(title="Retirer le compte de ce PC", visible=existing is not None)
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.connect("activated", lambda _r: self._remove())
        group.add(self.remove_button)
        self.removed = False
        self.page.add(group)

    def account(self) -> Credentials | None:
        return resolve_account(
            None if self.removed else self.existing,
            self.email.get_text(),
            self.password.get_text(),
            REGIONS[self.region.get_selected()],
        )

    def _mark(self, error: AccountError) -> None:
        widget = self.email if error.field == "email" else self.password
        widget.add_css_class("error")
        widget.grab_focus()

    def _remove(self) -> None:
        self.removed = True
        for row in (self.email, self.password):
            row.set_text("")
        self.remove_button.set_visible(False)
        self.toast("Compte retiré : enregistrez les préférences pour l'effacer du PC.")

    def _try(self) -> None:
        try:
            account = self.account()
        except AccountError as exc:
            self._mark(exc)
            self.toast(f"À corriger : {exc}")
            return
        if account is None:
            self.toast("Saisissez l'e-mail et le mot de passe du compte LibreLinkUp.")
            return
        self.try_button.set_sensitive(False)
        self.toast("Connexion à LibreLinkUp…")

        def work() -> None:
            try:
                outcome = connection_message(device.fetch_cgm(account))
            except device.DeviceError as exc:
                outcome = f"Connexion impossible : {exc}"
            except Exception as exc:  # noqa: BLE001 - l'essai doit toujours répondre
                log.exception("essai LibreLinkUp")
                outcome = f"Erreur inattendue : {exc}"
            GLib.idle_add(self._tried, outcome)

        threading.Thread(target=work, daemon=True).start()

    def _tried(self, outcome: str) -> bool:
        self.try_button.set_sensitive(True)
        self.toast(outcome)
        return False
