import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evals.store_merge import main, run_scenario


class StoreMergeSmokeTest(unittest.TestCase):
    """Version rapide de l'eval de fusion, lancée à chaque commit."""

    def test_scenarios_converge(self):
        ties = 0
        for seed in range(6):
            scenario = run_scenario(seed, 50)
            self.assertEqual(scenario.problems, [], f"graine {seed}")
            ties += scenario.ops["note à la même seconde que l'autre appareil"]
        self.assertGreater(ties, 0, "le départage à la même seconde n'est pas exercé")

    def test_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["--scenarios", "2", "--steps", "30", "--out", tmp])
            self.assertEqual(code, 0, out.getvalue())
            self.assertIn("Verdict : OK", out.getvalue())
            self.assertTrue(list(Path(tmp).glob("*/fusions.csv")))

    def test_eval_catches_erasures_that_come_back(self):
        """Mutation : une note effacée redevient une note vide visible au lieu de disparaître."""
        with mock.patch("services.store.store._is_erased", lambda *_: False):
            problems = [p for seed in range(6) for p in run_scenario(seed, 50).problems]
        self.assertTrue(any("note" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
