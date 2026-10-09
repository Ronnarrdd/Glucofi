import unittest

from evals.meal_estimates import CASES, NOT_MEALS, run, verdict
from services.meals import EstimateError, MealEstimate, NotAMealError


def oracle(text, key, models=None):
    """Gemini idéal : le centre de la fourchette de référence ; refuse ce qui n'est pas un repas."""
    for case in CASES:
        if case.text == text:
            carbs = sum(case.carbs) / 2
            return MealEstimate(round(sum(case.kcal) / 2), carbs, case.carbs[0], case.carbs[1])
    raise NotAMealError("Ce texte ne décrit pas un repas")


class MealEvalSmokeTest(unittest.TestCase):
    """Le harnais de l'eval des repas, sans réseau : un estimateur parfait réussit, des estimateurs fautifs échouent."""

    def test_a_perfect_estimator_passes(self):
        rates, ok = verdict(*run(oracle))
        self.assertTrue(ok, rates)
        self.assertEqual(set(rates.values()), {1.0})

    def test_wrong_numbers_fail(self):
        def double(text, key, models=None):
            e = oracle(text, key)
            return MealEstimate(e.calories_kcal * 3, e.carbs_g * 3, e.carbs_low_g * 3, e.carbs_high_g * 3)

        self.assertFalse(verdict(*run(double))[1])

    def test_an_incoherent_range_fails(self):
        def inverted(text, key, models=None):
            e = oracle(text, key)
            return MealEstimate(e.calories_kcal, e.carbs_g, e.carbs_g * 2, e.carbs_g * 3)

        rates, ok = verdict(*run(inverted))
        self.assertLess(rates["fourchette annoncée cohérente"], 0.8)
        self.assertFalse(ok)

    def test_accepting_a_non_meal_fails(self):
        def gullible(text, key, models=None):
            if text in NOT_MEALS:
                return MealEstimate(100, 10, 5, 15)
            return oracle(text, key)

        _, not_refused = run(gullible)
        self.assertEqual(sorted(not_refused), sorted(NOT_MEALS))
        self.assertFalse(verdict(*run(gullible))[1])

    def test_a_network_error_is_not_a_refusal(self):
        def offline(text, key, models=None):
            if text in NOT_MEALS:
                raise EstimateError("hors ligne")
            return oracle(text, key)

        _, not_refused = run(offline)
        self.assertEqual(sorted(not_refused), sorted(NOT_MEALS))

    def test_a_passing_outage_is_replayed_but_a_lasting_one_is_a_miss(self):
        calls = {}

        def flaky(text, key, models=None):
            calls[text] = calls.get(text, 0) + 1
            if calls[text] < 3 and text in {c.text for c in CASES}:
                raise EstimateError("Gemini est indisponible (erreur 503)")
            return oracle(text, key)

        rates, ok = verdict(*run(flaky))
        self.assertTrue(ok, rates)
        self.assertTrue(all(calls[c.text] == 3 for c in CASES))

    def test_errors_count_as_misses(self):
        def broken(text, key, models=None):
            raise EstimateError("hors ligne")

        outcomes, _ = run(broken)
        self.assertTrue(all(o.error for o in outcomes))
        self.assertFalse(verdict(outcomes, [])[1])


if __name__ == "__main__":
    unittest.main()
