import unittest
from unittest import mock

from contracts import Meal
from evals.measures_view import all_markers_patient, check


class MeasuresViewSmokeTest(unittest.TestCase):
    """Version rapide de l'eval de l'onglet Mesures, lancée à chaque commit."""

    def test_view_matches_oracle_on_synthetic_patients(self):
        for seed in range(3):
            views, failures = check(f"synthetique-{seed}", all_markers_patient(seed, days=30))
            self.assertGreater(views, 0)
            self.assertEqual(failures, [], f"graine {seed}")

    def test_synthetic_patients_use_every_marker(self):
        seen = {r.meal for seed in range(3) for r in all_markers_patient(seed, days=30)}
        self.assertEqual(seen, set(Meal) | {None})

    def test_eval_catches_a_double_counted_minimum_span(self):
        def old_spans(counts, total=100):
            n = sum(counts)
            exact = [total * c / n for c in counts] if n else [0] * len(counts)
            spans = [max(1, int(e)) if c else 0 for c, e in zip(counts, exact)]
            order = sorted(range(len(counts)), key=lambda i: exact[i] - int(exact[i]), reverse=True)
            for i in order[: max(0, total - sum(spans))]:
                spans[i] += 1
            return tuple(spans)

        with mock.patch("app.measures.range_spans", old_spans):
            _views, failures = check("synthetique-2", all_markers_patient(2, days=60))
        self.assertTrue(any("part" in f["echec"] for f in failures))


if __name__ == "__main__":
    unittest.main()
