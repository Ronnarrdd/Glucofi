import unittest

from evals.accuchek_replay import DEFAULT_BIN, FIXTURES, TraceResult, check_clock, check_meals, check_trace, read_trace


@unittest.skipUnless(DEFAULT_BIN.exists(), "accuchek non compilé (scripts/gate.sh le compile)")
class AccuchekReplaySmokeTest(unittest.TestCase):
    """Version rapide de l'eval accuchek : traces synthétiques versionnées."""

    def test_fixtures_respect_the_output_contract(self):
        traces = sorted(FIXTURES.glob("*.trace"))
        self.assertTrue(traces)
        results = {}
        for trace in traces:
            result = check_trace(DEFAULT_BIN, trace)
            self.assertEqual(result.problems, [], trace.name)
            results[trace.name] = result
        self.assertEqual(results["empty_meter.trace"].readings, 0)
        self.assertEqual(results["two_segments.trace"].readings, 3)
        self.assertEqual((results["meals.trace"].readings, results["meals.trace"].markers), (4, 3))
        self.assertEqual(results["set_time.trace"].clock_action, "set")
        self.assertEqual(results["undescribed_meter.trace"].clock_action, "")

    def test_misattached_marker_is_caught(self):
        """L'oracle relit les octets : un marqueur déplacé sur une autre mesure doit être signalé."""
        content = read_trace(FIXTURES / "meals.trace")
        self.assertEqual([content.meal_for(i) for i in range(4)], ["fasting", "after_meal", "bedtime", None])
        self.assertEqual(content.unmatched, 1)
        result = TraceResult(FIXTURES / "meals.trace", 0)
        output = {"readings": [{"id": i, "meal": "fasting"} for i in range(4)], "meal": {"received": 4, "unmatched": 1}}
        check_meals(content, output, result)
        self.assertIn("3 marqueur(s) mal rattaché(s)", result.problems[0])

    def test_clock_set_without_need_is_caught(self):
        content = read_trace(FIXTURES / "set_time.trace")
        result = TraceResult(FIXTURES / "set_time.trace", 0)
        output = {"clock": {"meter": "2026/10/01 20:43:00", "pc": "2026/10/01 20:42:52", "offset_s": 8,
                            "settable": True, "action": "set"}}
        check_clock(content, output, result)
        self.assertEqual(result.problems, ["1 mise(s) à l'heure envoyée(s) pour un écart de 8 s"])


if __name__ == "__main__":
    unittest.main()
