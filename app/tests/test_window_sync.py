"""Bouton « Synchroniser avec la tablette » : threads, fusion des deux côtés, messages (ignoré sans affichage)."""

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
from contracts import DoseChange, DoseRule, DosingSettings, Reading  # noqa: E402
from services.store import Store  # noqa: E402
from services.tablet import Adb, TabletLink  # noqa: E402
from services.tablet.tests.fake_tablet import FakeTablet  # noqa: E402

HAS_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
PROTOCOL = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.30, step_ui=2, high_streak_days=3)


def reading(ts: str, mg: int) -> Reading:
    t = datetime.fromisoformat(ts)
    return Reading(t, mg, int(t.timestamp()))


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class WindowSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        from app.window import MainWindow

        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        (root / "pc").mkdir()
        store = Store(root / "pc" / "glucofi.db")
        self.addCleanup(store.close)
        store.set_setting("patient_name", "Patient fictif")
        store.save_dosing_settings(PROTOCOL, effective=datetime(2026, 9, 1, 9))
        store.add_dose_change(DoseChange(datetime(2026, 9, 1, 12), 10, 6, DoseRule.START))
        store.import_readings([reading("2026-09-02T07:30", 150)], "lecteur")
        self.store = store
        self.tablet = FakeTablet(root / "tablette")
        self.addCleanup(self.tablet.close)
        self.tablet.store.import_readings([reading("2026-09-03T07:20", 160)], "lecteur")
        self.app = Adw.Application()
        self.window = MainWindow(self.app, AppState(store, root / "pc"))
        self.errors: list[tuple[str, str]] = []
        self.toasts: list[str] = []
        self.window.error = lambda heading, body: self.errors.append((heading, body))
        self.window.toast = lambda message, **kw: self.toasts.append(message)

    def link(self):
        """TabletLink.connect() de la fenêtre, branché sur la fausse tablette au lieu du vrai adb."""
        adb = Adb("/usr/bin/adb", run=self.tablet)
        real = TabletLink.connect.__func__
        return mock.patch.object(TabletLink, "connect", classmethod(lambda cls, _adb=None: real(cls, adb)))

    def run_sync(self):
        with self.link():
            self.window.sync_tablet()
            deadline = time.monotonic() + 10
            while self.window.busy and time.monotonic() < deadline:
                GLib.MainContext.default().iteration(False)
                time.sleep(0.01)
        self.assertFalse(self.window.busy, "la synchronisation ne s'est pas terminée")

    def test_sync_merges_both_ways_then_says_up_to_date(self):
        self.run_sync()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.store.count_readings(), 2)
        self.assertEqual(self.tablet.store.count_readings(), 2)
        self.assertEqual(self.tablet.store.dosing_settings(), PROTOCOL)
        self.assertEqual(self.toasts[-1], "PC et tablette (SM X210) synchronisés.")
        self.assertTrue(self.window.sync_button.get_sensitive())
        self.run_sync()
        self.assertEqual(self.toasts[-1], "PC et tablette (SM X210) déjà à jour.")
        self.assertEqual(self.tablet.files(), [])

    def test_errors_are_shown_and_nothing_stays_busy(self):
        self.tablet.state = "unauthorized"
        self.run_sync()
        self.assertEqual(self.errors[-1][0], "Synchronisation impossible")
        self.assertIn("Autoriser le débogage USB", self.errors[-1][1])
        self.assertEqual(self.store.count_readings(), 1, "rien n'a changé sur le PC")

    def test_failure_on_the_way_back_says_what_did_happen(self):
        self.tablet.fail["merge"] = "La base reçue est abîmée."
        self.run_sync()
        heading, body = self.errors[-1]
        self.assertEqual(heading, "Synchronisation impossible")
        self.assertIn("Fusion de glucofi-tablette.db", body)
        self.assertIn("la tablette n'a pas reçu la base du PC : La base reçue est abîmée.", body)
        self.assertEqual(self.store.count_readings(), 2, "la fusion côté PC est faite et le dit")


if __name__ == "__main__":
    unittest.main()
