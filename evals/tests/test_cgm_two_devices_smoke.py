import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.cgm_two_devices import main, run_scenario
from services.store import Store


def problems(seeds=range(3), days=2):
    return [p for seed in seeds for p in run_scenario(seed, days).problems]


class CgmTwoDevicesSmokeTest(unittest.TestCase):
    """Version rapide de l'eval PC + tablette, lancée à chaque commit."""

    @classmethod
    def setUpClass(cls):
        cls.scenarios = [run_scenario(seed, 2) for seed in range(3)]

    def test_scenarios_converge_without_duplicates(self):
        for scenario in self.scenarios:
            self.assertEqual(scenario.problems, [], f"graine {scenario.seed}")

    def test_each_device_reads_something_the_other_missed(self):
        self.assertGreater(sum(s.facts["points du PC seul"] for s in self.scenarios), 0)
        self.assertGreater(sum(s.facts["points de la tablette seule"] for s in self.scenarios), 0)

    def test_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--scenarios", "1", "--days", "2", "--out", tmp])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("Verdict : OK", out.getvalue())
            self.assertTrue(list(Path(tmp).glob("*/scenarios.csv")))

    def test_eval_catches_sensor_imports_written_to_the_journal(self):
        original = Store.import_readings

        def always_journal(self, *args, journal=True, **kwargs):
            return original(self, *args, journal=True, **kwargs)

        with mock.patch.object(Store, "import_readings", always_journal):
            self.assertTrue(any("journal" in p or "dernière récupération" in p for p in problems()))

    def test_eval_catches_thinning_anchored_on_the_window(self):
        """Mutation : tranches comptées depuis le début de la fenêtre de 12 h de chaque appareil, pas depuis minuit."""
        from datetime import timedelta

        def anchored(readings, every_min=15):
            kept, last = [], None
            for reading in sorted(readings):
                if last is None or reading.device_time - last >= timedelta(minutes=every_min):
                    kept.append(reading)
                    last = reading.device_time
            return tuple(kept)

        with mock.patch("services.device.librelinkup.thin", anchored):
            self.assertTrue(any("tranche" in p or "convergé" in p for p in problems()))


if __name__ == "__main__":
    unittest.main()
