"""Journal alimentaire côté application : état, vue de l'onglet Repas (sans GTK, sans réseau)."""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from app.meals import DAYS_SHOWN, baseline_of, entry_from_form, fmt_carbs, fmt_kcal, meals_view, range_text
from app.state import AppState, merge_message
from contracts import MealEntry, MealSlot
from services.meals import EstimateError, MealEstimate
from services.store import MergeSummary, Store

B, L, D = MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER
TODAY = date(2026, 9, 5)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.asked = []

        def estimator(text: str) -> MealEstimate:
            self.asked.append(text)
            if "rien" in text:
                raise EstimateError("Gemini est injoignable")
            return MealEstimate(660, 96.0, 85.0, 110.0)

        self.state = AppState(
            self.store, Path(self.tmp.name), today=lambda: TODAY, now=lambda: datetime(2026, 9, 5, 9, 30),
            meal_estimator=estimator,
        )


def entry(day=TODAY, slot=L, text="pâtes", kcal=660, carbs=96.0, source="gemini") -> MealEntry:
    return MealEntry(day, slot, text, kcal, carbs, carbs - 11, carbs + 14, source)


class StateTest(Base):
    def test_estimate_uses_the_injected_estimator_and_saves_nothing(self):
        self.assertEqual(self.state.estimate_meal("pâtes").carbs_g, 96.0)
        self.assertEqual(self.asked, ["pâtes"])
        self.assertEqual(self.state.meals(), [])

    def test_a_failed_estimate_reaches_the_caller_with_its_message(self):
        with self.assertRaisesRegex(EstimateError, "injoignable"):
            self.state.estimate_meal("rien")

    def test_save_and_clear(self):
        self.state.save_meal(entry())
        self.assertEqual([m.text for m in self.state.meals()], ["pâtes"])
        self.state.clear_meal(TODAY, L)
        self.assertEqual(self.state.meals(), [])

    def test_a_future_meal_cannot_be_saved(self):
        with self.assertRaises(ValueError):
            self.state.save_meal(entry(day=date(2026, 9, 6)))

    def test_merge_message_counts_meals(self):
        summary = MergeSummary("t.db", 0, 0, 0, 0, 0, 0, False, 0, False, (), None, 0, 0, 2, 1)
        self.assertEqual(merge_message(summary), "Fusion de t.db : 3 repas renseignés.")


class ViewTest(Base):
    def test_each_day_shows_three_meals_today_first(self):
        view = meals_view(self.state)
        self.assertEqual(len(view.days), DAYS_SHOWN)
        self.assertEqual([d.day for d in view.days][:2], [TODAY, date(2026, 9, 4)])
        self.assertEqual(view.days[0].title, "Aujourd'hui")
        self.assertEqual(view.days[1].title, "Hier")
        self.assertEqual([c.label for c in view.days[0].cells], ["Matin", "Midi", "Soir"])
        self.assertEqual(view.logged, 0)
        self.assertEqual({c.status for c in view.days[0].cells}, {"À renseigner"})

    def test_cells_carry_their_status_and_numbers(self):
        self.state.save_meal(entry(slot=B, text="tartines", kcal=300, carbs=45.0, source="manual"))
        self.state.save_meal(entry(slot=L))
        self.state.save_meal(MealEntry(TODAY, D, "soupe"))
        day = meals_view(self.state).days[0]
        b, l, d = day.cells
        self.assertEqual((b.status, l.status, d.status), ("Corrigé à la main", "Estimé par Gemini", "À estimer"))
        self.assertEqual(l.summary, "96 g de glucides · 660 kcal")
        self.assertEqual(l.range, "85 à 110 g de glucides")
        self.assertIsNone(d.summary)
        self.assertEqual(l.description, "Midi : pâtes, 96 g de glucides, 660 kcal")
        self.assertEqual(d.description, "Soir : soupe, à estimer")

    def test_day_total_counts_only_estimated_meals(self):
        self.state.save_meal(entry(slot=B, kcal=300, carbs=45.0))
        self.state.save_meal(entry(slot=L, kcal=660, carbs=96.0))
        self.state.save_meal(MealEntry(TODAY, D, "soupe"))
        day = meals_view(self.state).days[0]
        self.assertEqual((day.carbs_g, day.calories_kcal, day.pending), (141.0, 960, 1))
        self.assertEqual(day.total, "141 g de glucides · 960 kcal")
        self.assertIsNone(meals_view(self.state).days[1].total)

    def test_meals_older_than_the_window_are_not_shown(self):
        self.state.save_meal(entry(day=date(2026, 8, 1)))
        self.assertEqual(meals_view(self.state).logged, 0)

    def test_formats(self):
        self.assertEqual([fmt_carbs(x) for x in (96.0, 5.5, 0, 12.04)], ["96 g", "5,5 g", "0 g", "12 g"])
        self.assertEqual(fmt_kcal(1520), "1 520 kcal")
        self.assertIsNone(range_text(entry().__class__(TODAY, L, "x", 1, 5.0, 5.0, 5.0, "gemini")))
        self.assertIsNone(range_text(MealEntry(TODAY, L, "x")))


ESTIMATE = MealEstimate(660, 96.0, 85.0, 110.0)


class FormTest(unittest.TestCase):
    def form(self, text="pâtes", kcal="", carbs="", estimate=None):
        return entry_from_form(TODAY, L, text, kcal, carbs, estimate)

    def test_text_only_is_saved_to_estimate_later(self):
        self.assertEqual(self.form(), MealEntry(TODAY, L, "pâtes"))

    def test_untouched_estimate_keeps_gemini_as_source_and_its_range(self):
        got = self.form(kcal="660", carbs="96", estimate=ESTIMATE)
        self.assertEqual((got.source, got.carbs_low_g, got.carbs_high_g), ("gemini", 85.0, 110.0))

    def test_edited_numbers_become_manual_without_a_range(self):
        got = self.form(kcal="660", carbs="80", estimate=ESTIMATE)
        self.assertEqual((got.source, got.carbs_g, got.carbs_low_g, got.carbs_high_g), ("manual", 80.0, None, None))

    def test_typed_numbers_without_any_estimate_are_manual(self):
        got = self.form(kcal="500", carbs="55,5")
        self.assertEqual((got.source, got.carbs_g, got.calories_kcal), ("manual", 55.5, 500))

    def test_comma_decimals_and_spaces_are_accepted(self):
        self.assertEqual(self.form(kcal="1 200", carbs="  96,0 ", estimate=ESTIMATE).source, "manual")
        self.assertEqual(self.form(kcal="660", carbs="96,0", estimate=ESTIMATE).source, "gemini")

    def test_bad_input_says_what_to_fix(self):
        for kwargs, message in (
            (dict(text="  "), "décrivez"),
            (dict(text="x" * 501), "dépasse"),
            (dict(kcal="500"), "ou aucun"),
            (dict(carbs="50"), "ou aucun"),
            (dict(kcal="beaucoup", carbs="5"), "Calories"),
            (dict(kcal="500", carbs="-3"), "Glucides"),
            (dict(kcal="500", carbs="900"), "Glucides"),
            (dict(kcal="99999", carbs="5"), "Calories"),
            (dict(kcal="500", carbs="nan"), "Glucides"),
        ):
            with self.subTest(kwargs), self.assertRaisesRegex(ValueError, message):
                self.form(**kwargs)

    def test_baseline_only_for_a_saved_gemini_estimate(self):
        self.assertEqual(baseline_of(entry()), MealEstimate(660, 96.0, 85.0, 110.0))
        self.assertIsNone(baseline_of(entry(source="manual")))
        self.assertIsNone(baseline_of(MealEntry(TODAY, L, "x")))
        self.assertIsNone(baseline_of(None))


if __name__ == "__main__":
    unittest.main()
