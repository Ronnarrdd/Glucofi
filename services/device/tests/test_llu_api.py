"""Client LibreLinkUp (llu_api.py) contre le faux serveur : connexion, redirection de région, étapes, erreurs."""

import ast
import unittest
from pathlib import Path

from services.device import llu_api
from services.device.llu_api import LluAuthError, LluClient, LluError, LluRateLimit
from services.device.tests.fake_llu import PATIENT_ID, FakeLlu, graph_data, item


def client(fake, region="FR"):
    return LluClient("suivi@example.org", "secret", region, fake)


class LoginTest(unittest.TestCase):
    def test_login_then_calls_carry_the_token(self):
        fake = FakeLlu()
        c = client(fake)
        c.login()
        c.patients()
        self.assertNotIn("authorization", fake.calls[0]["headers"])
        self.assertIn("authorization", fake.calls[1]["headers"])

    def test_a_region_redirect_is_followed_once_and_remembered(self):
        fake = FakeLlu()
        fake.login_replies = [(200, {"status": 0, "data": {"redirect": True, "region": "eu"}})]
        c = client(fake, "FR")
        c.login()
        c.patients()
        self.assertEqual([x["host"] for x in fake.calls], ["api-fr.libreview.io", "api-eu.libreview.io", "api-eu.libreview.io"])
        self.assertEqual(c.region, "EU")

    def test_a_second_redirect_is_not_followed(self):
        fake = FakeLlu()
        redirect = (200, {"status": 0, "data": {"redirect": True, "region": "eu"}})
        fake.login_replies = [redirect, redirect]
        with self.assertRaises(LluAuthError):
            client(fake).login()
        self.assertEqual(len(fake.calls), 2)

    def test_an_unknown_redirect_region_is_refused(self):
        fake = FakeLlu()
        fake.login_replies = [(200, {"status": 0, "data": {"redirect": True, "region": "mars"}})]
        with self.assertRaisesRegex(LluError, "région 'MARS'"):
            client(fake).login()

    def test_steps_the_server_imposes_are_named(self):
        for step, words in (("tou", "conditions d'utilisation"), ("pp", "politique de confidentialité"), ("verifyEmail", "adresse e-mail")):
            fake = FakeLlu()
            fake.login_replies = [(200, {"status": 0, "data": {"step": {"type": step}}})]
            with self.subTest(step), self.assertRaisesRegex(LluError, words):
                client(fake).login()

    def test_an_unknown_step_is_still_reported(self):
        fake = FakeLlu()
        fake.login_replies = [(200, {"status": 0, "data": {"step": {"type": "nouveau"}}})]
        with self.assertRaisesRegex(LluError, "nouveau"):
            client(fake).login()

    def test_bad_credentials_in_any_form(self):
        for status, reply in ((200, {"status": 2, "error": {"message": "notAuthenticated"}}), (200, {"status": 0, "data": {}}), (401, {}), (403, {})):
            fake = FakeLlu()
            fake.login_replies = [(status, reply)]
            with self.subTest(status=status, reply=reply), self.assertRaises(LluAuthError):
                client(fake).login()

    def test_rate_limit(self):
        fake = FakeLlu()
        fake.login_replies = [(429, {})]
        with self.assertRaises(LluRateLimit):
            client(fake).login()

    def test_the_password_never_appears_in_an_error(self):
        fake = FakeLlu()
        fake.login_replies = [(200, {"status": 2})]
        with self.assertRaises(LluError) as caught:
            client(fake).login()
        self.assertNotIn("secret", str(caught.exception))

    def test_unknown_region(self):
        with self.assertRaisesRegex(LluError, "Région LibreLinkUp inconnue"):
            client(FakeLlu(), "MARS")

    def test_region_name_is_case_insensitive(self):
        self.assertEqual(client(FakeLlu(), "fr").base, llu_api.REGION_URLS["FR"])


class DataTest(unittest.TestCase):
    def logged(self, fake):
        c = client(fake)
        c.login()
        return c

    def test_patients_and_graph(self):
        fake = FakeLlu(graph_data([item("10/29/2026 7:00:00 PM", 150)], None))
        c = self.logged(fake)
        self.assertEqual(c.patients(), [{"patientId": PATIENT_ID}])
        self.assertEqual(c.graph(PATIENT_ID)["graphData"][0]["ValueInMgPerDl"], 150)

    def test_a_refused_request_is_an_error_even_with_http_200(self):
        fake = FakeLlu()
        fake.fail["/llu/connections"] = (200, {}, b'{"status": 4, "data": []}')
        with self.assertRaisesRegex(LluError, "a refusé la demande"):
            self.logged(fake).patients()

    def test_wrong_shapes_are_refused(self):
        fake = FakeLlu()
        fake.fail["/llu/connections"] = (200, {}, b'{"status": 0, "data": {"pas": "une liste"}}')
        fake.fail[f"/llu/connections/{PATIENT_ID}/graph"] = (200, {}, b'{"status": 0, "data": []}')
        c = self.logged(fake)
        with self.assertRaisesRegex(LluError, "liste des patients"):
            c.patients()
        with self.assertRaisesRegex(LluError, "graphique"):
            c.graph(PATIENT_ID)

    def test_a_json_list_instead_of_an_object_is_refused(self):
        fake = FakeLlu()
        fake.fail["/llu/connections"] = (200, {}, b"[1, 2]")
        with self.assertRaisesRegex(LluError, "Réponse inattendue"):
            self.logged(fake).patients()


class NoDependencyTest(unittest.TestCase):
    """Le client tourne sur la tablette (Chaquopy) : rien d'autre que la bibliothèque standard."""

    def test_only_the_standard_library_is_imported(self):
        tree = ast.parse(Path(llu_api.__file__).read_text(encoding="utf-8"))
        imported = {
            (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in (node.names if isinstance(node, ast.Import) else [node])
        }
        import sys

        self.assertEqual(sorted(imported - set(sys.stdlib_module_names) - {"__future__"}), [])


if __name__ == "__main__":
    unittest.main()
