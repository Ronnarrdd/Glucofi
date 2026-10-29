"""Lecture du capteur dans la fenêtre : action manuelle, rafraîchissement automatique, erreurs (ignoré sans affichage)."""

import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from app.state import AppState  # noqa: E402
from app.tests.gtk_app import application  # noqa: E402
from contracts import DoseChange, DoseRule, DosingSettings, Reading  # noqa: E402
from services import device  # noqa: E402
from services.device import Credentials, ParseResult  # noqa: E402
from services.store import Store  # noqa: E402

HAS_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
PROTOCOL = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.30, step_ui=2, high_streak_days=3)
ACCOUNT = Credentials("suivi@example.org", "secret", "FR")


def reading(ts: str, mg: int) -> Reading:
    t = datetime.fromisoformat(ts)
    return Reading(t, mg, int(t.timestamp()))


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class WindowCgmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        from app.window import MainWindow

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = Store(Path(directory.name) / "glucofi.db")
        self.addCleanup(store.close)
        store.set_setting("patient_name", "Patient fictif")
        store.save_dosing_settings(PROTOCOL, effective=datetime(2026, 9, 1, 9))
        store.add_dose_change(DoseChange(datetime(2026, 9, 1, 12), 10, 6, DoseRule.START))
        self.store = store
        self.window = MainWindow(application(), AppState(store, Path(directory.name)))
        self.toasts: list[str] = []
        self.window.toast = lambda message, **kw: self.toasts.append(message)
        account = mock.patch.object(self.window.state, "cgm_credentials", return_value=ACCOUNT)
        account.start()
        self.addCleanup(account.stop)

    def pump(self):
        deadline = time.monotonic() + 5
        while self.window.cgm_busy and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        GLib.MainContext.default().iteration(False)
        self.assertFalse(self.window.cgm_busy, "la lecture ne s'est pas terminée")

    def fetch(self, result=None, error=None, auto=False):
        fake = mock.Mock(return_value=result, side_effect=error)
        with mock.patch.object(device, "fetch_cgm", fake):
            self.window.fetch_cgm(auto=auto)
            self.pump()
        return fake

    def test_manual_fetch_imports_flagged_readings_and_says_so(self):
        fake = self.fetch(ParseResult((reading("2026-09-03T07:00", 120), reading("2026-09-03T07:15", 125)), ()))
        fake.assert_called_once_with(ACCOUNT)
        self.assertEqual([x.cgm for x in self.store.readings()], [True, True])
        self.assertEqual(self.toasts[-1], "Capteur : 2 nouvelles mesures sur 2 lues")

    def test_auto_fetch_is_silent_when_nothing_is_new(self):
        result = ParseResult((reading("2026-09-03T07:00", 120),), ())
        self.fetch(result, auto=True)
        self.assertEqual(len(self.toasts), 1)
        self.fetch(result, auto=True)
        self.assertEqual(len(self.toasts), 1, "rien de nouveau : pas de message")

    def test_manual_fetch_always_answers(self):
        result = ParseResult((reading("2026-09-03T07:00", 120),), ())
        self.fetch(result)
        self.fetch(result)
        self.assertEqual(self.toasts[-1], "Capteur : 0 nouvelles mesures sur 1 lue")

    def test_an_auto_error_is_shown_once_then_again_when_it_changes(self):
        self.fetch(error=device.LibreLinkUpError("Identifiants LibreLinkUp refusés."), auto=True)
        self.fetch(error=device.LibreLinkUpError("Identifiants LibreLinkUp refusés."), auto=True)
        self.assertEqual(self.toasts, ["Capteur illisible : Identifiants LibreLinkUp refusés."])
        self.fetch(error=device.LibreLinkUpError("LibreLinkUp injoignable."), auto=True)
        self.assertEqual(len(self.toasts), 2)
        self.fetch(error=device.LibreLinkUpError("LibreLinkUp injoignable."))
        self.assertEqual(len(self.toasts), 4, "un clic manuel annonce la lecture puis montre toujours l'erreur")
        self.assertIn("Capteur illisible", self.toasts[-1])

    def test_a_good_read_resets_the_error_memory(self):
        error = device.LibreLinkUpError("LibreLinkUp injoignable.")
        self.fetch(error=error, auto=True)
        self.fetch(ParseResult((), ()), auto=True)
        self.fetch(error=error, auto=True)
        self.assertEqual(len(self.toasts), 2)

    def test_unexpected_exception_is_reported_not_swallowed(self):
        self.fetch(error=RuntimeError("boum"))
        self.assertIn("Erreur inattendue : boum", self.toasts[-1])

    def test_without_an_account_manual_points_to_preferences_and_auto_says_nothing(self):
        with mock.patch.object(self.window.state, "cgm_credentials", return_value=None):
            with mock.patch.object(device, "fetch_cgm") as fake:
                self.window.fetch_cgm(auto=True)
                self.assertEqual(self.toasts, [])
                self.window.fetch_cgm(auto=False)
                self.assertIn("Préférences", self.toasts[-1])
        fake.assert_not_called()

    def test_a_new_value_refreshes_the_screens(self):
        with mock.patch.object(self.window, "refresh") as refresh:
            self.fetch(ParseResult((reading("2026-09-03T07:00", 120),), ()))
            self.assertEqual(refresh.call_count, 1)
            self.fetch(ParseResult((reading("2026-09-03T07:00", 120),), ()), auto=True)
            self.assertEqual(refresh.call_count, 1)

    def test_the_window_never_starts_polling_by_itself(self):
        self.assertIsNone(self.window._cgm_timer)

    def test_start_is_idempotent(self):
        with mock.patch.object(GLib, "timeout_add_seconds", return_value=7) as timer, mock.patch.object(GLib, "idle_add"):
            self.window.start_cgm_refresh()
            self.window.start_cgm_refresh()
        timer.assert_called_once()
        self.assertEqual(timer.call_args.args[0], 15 * 60)


if __name__ == "__main__":
    unittest.main()
