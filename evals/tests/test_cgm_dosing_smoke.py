import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.cgm_dosing import main, run_scenario


def problems(seeds=range(25)):
    return [p for seed in seeds for p in run_scenario(seed, 8).problems]


class CgmDosingSmokeTest(unittest.TestCase):
    """Version rapide de l'eval du capteur, lancée à chaque commit."""

    def test_scenarios_match_the_oracle(self):
        for seed in range(25):
            self.assertEqual(run_scenario(seed, 8).problems, [], f"graine {seed}")

    def test_scenarios_exercise_gaps_changes_and_episodes(self):
        facts = [run_scenario(seed, 8).facts for seed in range(25)]
        for key in ("jours sans référence evening", "hausses ou baisses evening", "épisodes d'hypoglycémie"):
            self.assertGreater(sum(f.get(key, 0) for f in facts), 0, key)

    def test_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--scenarios", "3", "--days", "6", "--out", tmp])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("Verdict : OK", out.getvalue())
            self.assertTrue(list(Path(tmp).glob("*/scenarios.csv")))

    def test_eval_catches_first_in_window_instead_of_nearest(self):
        with mock.patch("services.dosing.engine._cgm_distance", lambda reading, at: 0):
            self.assertTrue(any("référence" in p for p in problems()))

    def test_eval_catches_every_sensor_low_counted(self):
        with mock.patch("services.dosing.engine._episodes", lambda flagged, lowest: sorted(flagged)):
            self.assertTrue(any("hypoglycémies" in p for p in problems()))

    def test_eval_catches_the_meter_taking_over(self):
        with mock.patch("services.dosing.engine.cgm_reference_time", lambda settings, target: None):
            self.assertTrue(problems())


if __name__ == "__main__":
    unittest.main()
