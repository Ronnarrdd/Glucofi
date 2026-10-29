"""AppState et capteur : compte LibreLinkUp, import des mesures, dose proposée d'après le capteur."""

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path
from unittest import mock

from app.state import AppState
from contracts import DoseRule, DosingSettings, Reading
from services.device import Credentials, ParseResult
from services.store import Store

SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
ACCOUNT = Credentials("suivi@example.org", "secret", "FR")


def r(ts: str, mg: int) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()))


class CgmStateTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.state = AppState(self.store, Path(directory.name), today=lambda: date(2026, 9, 5))

    def no_other_env_files(self):
        return mock.patch("app.state.env_file_candidates", return_value=())

    def test_account_is_saved_in_the_data_dir_and_read_back(self):
        with self.no_other_env_files(), mock.patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(self.state.cgm_credentials())
            self.state.save_cgm_credentials(ACCOUNT)
            self.assertEqual(self.state.cgm_credentials(), ACCOUNT)
            self.state.save_cgm_credentials(None)
            self.assertIsNone(self.state.cgm_credentials())

    def test_password_is_not_in_the_database(self):
        with self.no_other_env_files():
            self.state.save_cgm_credentials(ACCOUNT)
        dump = "\n".join(self.store.db.iterdump())
        self.assertNotIn("secret", dump)
        self.assertNotIn("suivi@example.org", dump)

    def test_data_dir_file_wins_over_the_other_env_files(self):
        other = Path(self.state.data_dir) / "autre.env"
        other.write_text("LIBRELINKUP_EMAIL=vieux@example.org\nLIBRELINKUP_PASSWORD=vieux\n")
        with mock.patch("app.state.env_file_candidates", return_value=(other,)), mock.patch.dict("os.environ", {}, clear=True):
            self.state.save_cgm_credentials(ACCOUNT)
            self.assertEqual(self.state.cgm_credentials().email, "suivi@example.org")

    def test_import_marks_readings_as_sensor_and_records_the_import(self):
        result = ParseResult((r("2026-09-05 07:00", 110), r("2026-09-05 07:15", 118)), ("mesure n°2 : hors plage",))
        summary = self.state.import_cgm(result)
        self.assertEqual((summary.source, summary.received, summary.added, summary.rejected), ("librelinkup", 2, 2, 1))
        self.assertEqual([x.cgm for x in self.state.readings()], [True, True])

    def test_dose_follows_the_sensor_not_the_meter(self):
        sensor = replace(SETTINGS, cgm_morning=time(7, 0))
        self.state.start_protocol("Test", datetime(2026, 9, 1), 10, 6, sensor)
        self.store.import_readings([r("2026-09-03 07:00", 140)], "lecteur")  # lecteur : ignoré
        self.state.import_cgm(ParseResult((r("2026-09-03 07:05", 70),), ()))
        proposal = self.state.proposal()
        self.assertEqual((proposal.evening.proposed_ui, proposal.evening.rule), (4, DoseRule.DECREASE_LOW_MORNING))


if __name__ == "__main__":
    unittest.main()
