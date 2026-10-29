"""Saisie du compte LibreLinkUp et message de l'essai, sans GTK : tourne aussi sur la tablette."""

import unittest
from datetime import datetime

from app.cgm_form import AccountError, connection_message, resolve_account
from contracts import MeterInfo, Reading
from services.device import Credentials, ParseResult

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


if __name__ == "__main__":
    unittest.main()
