"""Page « Capteur » des préférences GTK (ignorée sans affichage) ; la saisie elle-même : test_cgm_form.py."""

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from app.cgm_account import AccountError, CgmAccountPage  # noqa: E402
from services.device import Credentials  # noqa: E402

HAS_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
SAVED = Credentials("suivi@example.org", "secret", "FR")


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


class NoGtkTest(unittest.TestCase):
    def test_the_module_does_not_pull_gtk_in(self):
        import subprocess
        import sys as _sys

        code = "import sys, app.cgm_form; sys.exit(1 if 'gi' in sys.modules else 0)"
        done = subprocess.run([_sys.executable, "-c", code], cwd=__import__("pathlib").Path(__file__).resolve().parents[2])
        self.assertEqual(done.returncode, 0, "app.cgm_form importe GTK : la tablette ne peut pas le charger")


if __name__ == "__main__":
    unittest.main()
