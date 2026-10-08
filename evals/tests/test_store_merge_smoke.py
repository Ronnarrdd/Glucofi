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

    def test_injection_tie_break_is_exercised(self):
        ties = sum(run_scenario(seed, 60).ops["injection à la même seconde que l'autre appareil"] for seed in range(6))
        self.assertGreater(ties, 0)

    def test_eval_catches_a_journal_that_is_not_merged(self):
        """Mutation : la fusion oublie le journal des injections."""
        from services.store import store as store_module

        original = store_module.Store._merge

        def forgetful(self, other, name, backup):
            other.db.execute("DELETE FROM injections")
            return original(self, other, name, backup)

        with mock.patch.object(store_module.Store, "_merge", forgetful):
            problems = [p for seed in range(6) for p in run_scenario(seed, 50).problems]
        self.assertTrue(any("injections" in p or "convergé" in p for p in problems))

    def test_eval_catches_an_erased_declaration_that_comes_back(self):
        """Mutation : l'effacement ne se propage pas (la ligne vide est ignorée à la fusion)."""
        from services.store import store as store_module

        original = store_module.Store._merge

        def keeps_old(self, other, name, backup):
            other.db.execute("DELETE FROM injections WHERE state IS NULL")
            return original(self, other, name, backup)

        with mock.patch.object(store_module.Store, "_merge", keeps_old):
            problems = [p for seed in range(6) for p in run_scenario(seed, 60).problems]
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
