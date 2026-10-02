import unittest

from evals.accuchek_errors import check_trace
from evals.accuchek_replay import DEFAULT_BIN, FIXTURES


@unittest.skipUnless(DEFAULT_BIN.exists(), "accuchek non compilé (scripts/gate.sh le compile)")
class AccuchekErrorsSmokeTest(unittest.TestCase):
    """Version rapide de l'eval des erreurs : une trace synthétique, toutes les pannes."""

    def test_every_fault_is_reported(self):
        cases = check_trace(DEFAULT_BIN, FIXTURES / "two_segments.trace")
        self.assertGreater(len(cases), 20)
        self.assertEqual([(c.line, c.fault, c.problems) for c in cases if not c.ok], [])

    def test_faults_during_meal_markers_and_clock_setting(self):
        for name in ("meals.trace", "set_time.trace"):
            with self.subTest(trace=name):
                cases = check_trace(DEFAULT_BIN, FIXTURES / name)
                self.assertEqual([(c.line, c.fault, c.problems) for c in cases if not c.ok], [])


if __name__ == "__main__":
    unittest.main()
