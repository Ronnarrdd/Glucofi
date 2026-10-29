"""Compte LibreLinkUp des préférences : saisie, essai de connexion, page GTK (ignorée sans affichage)."""

import unittest
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from app.cgm_account import AccountError, CgmAccountPage, connection_message, resolve_account  # noqa: E402
from contracts import MeterInfo, Reading  # noqa: E402
from services.device import Credentials, ParseResult  # noqa: E402

HAS_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
SAVED = Credentials("suivi@example.org", "secret", "FR")


class ResolveAccountTest(unittest.TestCase):
    def test_both_empty_means_no_account(self):
        self.assertIsNone(resolve_account(None, "", "", "FR"))
        self.assertIsNone(resolve_account(SAVED, "  ", " ", "FR"))

    def test_new_account(self):
        self.assertEqual(resolve_account(None, " a@b.c ", " pw ", "EU"), Credentials("a@b.c", "pw", "EU"))

    def test_empty_password_keeps_the_saved_one_for_the_same_email(self):
        self.assertEqual(resolve_account(SAVED, "suivi@example.org", "", "EU"), Credentials("suivi@example.org", "secret", "EU"))

    def test_empty_password_with_another_email_is_refused(self):
        with self.assertRaises(AccountError) as caught:
            resolve_account(SAVED, "autre@example.org", "", "FR")
        self.assertEqual(caught.exception.field, "password")

    def test_missing_email_or_unknown_region_is_refused(self):
        for args, field in (((None, "", "pw", "FR"), "email"), ((None, "a@b.c", "pw", "MARS"), "email")):
            with self.subTest(args), self.assertRaises(AccountError) as caught:
                resolve_account(*args)
            self.assertEqual(caught.exception.field, field)


class ConnectionMessageTest(unittest.TestCase):
    def test_with_values(self):
        readings = tuple(Reading(datetime(2026, 10, 29, 20, m), 170 + m, 0) for m in (0, 1))
        text = connection_message(ParseResult(readings, (), MeterInfo(manufacturer="Abbott", model="Libre 3", serial="SN1")))
        self.assertEqual(text, "Connexion réussie (capteur SN1) : 171 mg/dL à 20:01, 2 valeurs lues.")

    def test_one_value_is_singular(self):
        text = connection_message(ParseResult((Reading(datetime(2026, 10, 29, 20, 0), 179, 0),), ()))
        self.assertEqual(text, "Connexion réussie : 179 mg/dL à 20:00, 1 valeur lue.")

    def test_without_values(self):
        self.assertIn("aucune valeur", connection_message(ParseResult((), ())))


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def page(self, existing=None):
        toasts = []
        return CgmAccountPage(existing, toasts.append), toasts

    def test_prefilled_from_the_saved_account_without_the_password(self):
        page, _ = self.page(SAVED)
        self.assertEqual(page.email.get_text(), "suivi@example.org")
        self.assertEqual(page.password.get_text(), "")
        self.assertEqual(page.account(), SAVED)
        self.assertTrue(page.remove_button.get_visible())

    def test_empty_page_has_no_account_and_no_remove_button(self):
        page, _ = self.page()
        self.assertIsNone(page.account())
        self.assertFalse(page.remove_button.get_visible())

    def test_region_is_read_back(self):
        page, _ = self.page(Credentials("a@b.c", "pw", "DE"))
        self.assertEqual(page.account().region, "DE")

    def test_remove_clears_the_account(self):
        page, toasts = self.page(SAVED)
        page._remove()
        self.assertIsNone(page.account())
        self.assertIn("enregistrez", toasts[0])

    def test_mark_flags_the_field(self):
        page, _ = self.page()
        page._mark(AccountError("password", "x"))
        self.assertTrue(page.password.has_css_class("error"))
        self.assertFalse(page.email.has_css_class("error"))


if __name__ == "__main__":
    unittest.main()
