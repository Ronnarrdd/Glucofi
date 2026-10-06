"""Paliers configurables, dose du matin ajustée sur la glycémie du soir, suivi propre à chaque dose."""

import unittest
from dataclasses import replace
from datetime import date, datetime, time

from contracts import DoseChange, DoseRule, DoseTarget, DosingSettings, HighTier, LowTier, Meal, Reading, Titration
from services.dosing import (
    apply_adjustment,
    apply_proposal,
    evening_readings,
    propose,
    reference_target,
    tracking_start,
)

BASE = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
TIERED = replace(BASE, low_tiers=(LowTier(0.60, 4),), high_tiers=(HighTier(2.00, 4, 2),))
MORNING = Titration(0.90, 1.60, 1, 2, (LowTier(0.70, 2),), (HighTier(2.20, 3, 1),))
BOTH = replace(BASE, morning_titration=MORNING)
START = DoseChange(datetime(2026, 9, 1, 12, 0), 10, 6, DoseRule.START)


def r(ts: str, mg: int, meal: Meal | None = None) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()), meal=meal)


def days(values: list[int], hour: str, first_day: int = 2, meal: Meal | None = None) -> list[Reading]:
    return [r(f"2026-09-{first_day + i:02d} {hour}", mg, meal) for i, mg in enumerate(values)]


def run(readings, settings, changes=(START,), today=None):
    today = today or max(x.day for x in readings)
    return propose(readings, list(changes), settings, today)


class LowTierTest(unittest.TestCase):
    def test_base_step_above_the_tier(self):
        e = run(days([70], "08:00"), TIERED).evening
        self.assertEqual((e.proposed_ui, e.rule), (4, DoseRule.DECREASE_LOW_MORNING))

    def test_deepest_tier_crossed_wins(self):
        e = run(days([55], "08:00"), TIERED).evening
        self.assertEqual(e.proposed_ui, 2)
        self.assertIn("sous 0,60 g/L", e.reason)
        self.assertIn("de 4 UI", e.reason)

    def test_exactly_on_tier_threshold_uses_the_previous_step(self):
        self.assertEqual(run(days([60], "08:00"), TIERED).evening.proposed_ui, 4)

    def test_tier_never_goes_below_zero(self):
        start = replace(START, evening_ui=3)
        self.assertEqual(run(days([50], "08:00"), TIERED, (start,)).evening.proposed_ui, 0)


class HighTierTest(unittest.TestCase):
    def test_higher_tier_needs_fewer_days_and_adds_more(self):
        e = run(days([210, 220], "08:00"), TIERED).evening
        self.assertEqual((e.proposed_ui, e.rule), (10, DoseRule.INCREASE_HIGH_MORNINGS))
        self.assertIn("au-dessus de 2,00 g/L 2 jours de suite", e.reason)
        self.assertEqual(len(e.evidence), 2)

    def test_tier_not_reached_falls_back_to_base_streak(self):
        e = run(days([160, 210, 170], "08:00"), TIERED).evening
        self.assertEqual(e.proposed_ui, 8)
        self.assertIn("au-dessus de 1,50 g/L 3 jours", e.reason)

    def test_partial_base_streak_keeps_the_dose(self):
        e = run(days([160, 205], "08:00"), TIERED).evening
        self.assertEqual((e.proposed_ui, e.rule), (6, DoseRule.KEEP))
        self.assertIn("2 jour(s) consécutif(s) sur 3", e.reason)

    def test_without_tiers_behaviour_is_unchanged(self):
        self.assertEqual(run(days([210, 220], "08:00"), BASE).evening.rule, DoseRule.KEEP)


class EveningReadingTest(unittest.TestCase):
    """Glycémie du soir = avant le dîner : plage 17:00-21:59, "avant repas" d'abord, sinon sans marqueur."""

    def found(self, readings, settings=BOTH):
        return [(m.day.day, m.reading.mg_dl) for m in evening_readings(readings, settings)]

    def test_before_meal_beats_an_earlier_unmarked_reading(self):
        readings = [r("2026-09-02 17:30", 100), r("2026-09-02 19:00", 150, Meal.BEFORE_MEAL)]
        self.assertEqual(self.found(readings), [(2, 150)])

    def test_first_unmarked_when_no_before_meal(self):
        self.assertEqual(self.found([r("2026-09-02 18:00", 120), r("2026-09-02 20:00", 180)]), [(2, 120)])

    def test_after_meal_bedtime_fasting_never_count(self):
        readings = [
            r("2026-09-02 18:00", 250, Meal.AFTER_MEAL),
            r("2026-09-02 21:30", 240, Meal.BEDTIME),
            r("2026-09-02 19:00", 230, Meal.FASTING),
        ]
        self.assertEqual(self.found(readings), [])

    def test_window_bounds_and_custom_window(self):
        readings = [r("2026-09-02 16:59", 90), r("2026-09-03 17:00", 100), r("2026-09-04 21:59", 110), r("2026-09-05 22:00", 120)]
        self.assertEqual(self.found(readings), [(3, 100), (4, 110)])
        late = replace(BOTH, evening_start=time(19, 0), evening_end=time(20, 0))
        self.assertEqual(self.found([r("2026-09-02 18:00", 100), r("2026-09-02 19:30", 130)], late), [(2, 130)])

    def test_reference_target(self):
        self.assertIs(reference_target(r("2026-09-02 07:00", 100), BOTH), DoseTarget.EVENING)
        self.assertIs(reference_target(r("2026-09-02 19:00", 100), BOTH), DoseTarget.MORNING)
        self.assertIsNone(reference_target(r("2026-09-02 14:00", 100), BOTH))
        self.assertIsNone(reference_target(r("2026-09-02 19:00", 100), BASE), "sans titration du matin, le soir ne compte pas")


class MorningDoseTest(unittest.TestCase):
    def test_no_morning_titration_no_morning_adjustment(self):
        proposal = run(days([120], "08:00") + days([300], "19:00"), BASE)
        self.assertIsNone(proposal.morning)
        self.assertEqual([a.target for a in proposal.adjustments], [DoseTarget.EVENING])

    def test_low_evening_decreases_the_morning_dose(self):
        proposal = run(days([120], "08:00") + days([80], "19:00"), BOTH)
        m = proposal.morning
        self.assertEqual((m.current_ui, m.proposed_ui, m.rule), (10, 9, DoseRule.DECREASE_LOW_EVENING))
        self.assertIn("Glycémie du soir du 02/09 à 0,80 g/L, sous 0,90 g/L : diminuer la dose du matin de 1 UI.", m.reason)
        self.assertEqual(proposal.evening.rule, DoseRule.KEEP)

    def test_morning_low_tier(self):
        self.assertEqual(run(days([65], "19:00"), BOTH).morning.proposed_ui, 8)

    def test_high_evenings_increase_the_morning_dose(self):
        m = run(days([170, 180], "19:00"), BOTH).morning
        self.assertEqual((m.proposed_ui, m.rule), (11, DoseRule.INCREASE_HIGH_EVENINGS))
        self.assertEqual(run(days([230], "19:00"), BOTH).morning.proposed_ui, 13, "palier : 1 jour > 2,20 g/L, +3 UI")

    def test_both_doses_change_independently(self):
        readings = days([60], "08:00") + days([175, 185], "19:00", first_day=1)
        proposal = run(readings, BOTH, today=date(2026, 9, 2))
        self.assertEqual((proposal.evening.proposed_ui, proposal.morning.proposed_ui), (4, 11))
        self.assertTrue(proposal.changes_dose)

    def test_morning_zero_alert(self):
        start = replace(START, morning_ui=0)
        codes = [a.code for a in run(days([120], "08:00"), BOTH, (start,)).alerts]
        self.assertIn("morning_zero", codes)
        self.assertNotIn("morning_zero", [a.code for a in run(days([120], "08:00"), BASE, (start,)).alerts])

    def test_stale_evening_has_its_own_alert(self):
        proposal = run(days([120], "08:00", first_day=9) + days([300], "19:00"), BOTH, today=date(2026, 9, 9))
        self.assertIn("stale_morning", [a.code for a in proposal.alerts])
        self.assertEqual(proposal.morning.rule, DoseRule.KEEP)
        self.assertNotIn("stale", [a.code for a in proposal.alerts])


class PerDoseTrackingTest(unittest.TestCase):
    """Valider une dose remet à zéro le décompte de cette dose seulement."""

    def test_validating_the_evening_dose_keeps_the_morning_streak(self):
        readings = days([200, 210, 220], "08:00") + days([170], "19:00", first_day=3)
        proposal = run(readings, BOTH, today=date(2026, 9, 4))
        change = apply_adjustment(proposal, DoseTarget.EVENING, datetime(2026, 9, 4, 20, 30))
        self.assertEqual((change.morning_ui, change.evening_ui), (10, 8))
        readings += days([175], "19:00", first_day=4)
        after = run(readings, BOTH, (START, change), today=date(2026, 9, 4))
        self.assertEqual(after.morning.since, START.effective)
        self.assertEqual(after.morning.proposed_ui, 11, "les soirs du 03 et du 04 comptent toujours")
        self.assertEqual(after.evening.since, change.effective)
        self.assertEqual(after.evening.rule, DoseRule.KEEP)

    def test_tracking_start(self):
        evening = DoseChange(datetime(2026, 9, 5, 20), 10, 8, DoseRule.INCREASE_HIGH_MORNINGS)
        morning = DoseChange(datetime(2026, 9, 7, 20), 11, 8, DoseRule.INCREASE_HIGH_EVENINGS)
        changes = [morning, START, evening]
        self.assertIs(tracking_start(changes, DoseTarget.EVENING), evening)
        self.assertIs(tracking_start(changes, DoseTarget.MORNING), morning)
        restart = DoseChange(datetime(2026, 9, 9, 9), 11, 8, DoseRule.START)
        self.assertIs(tracking_start(changes + [restart], DoseTarget.EVENING), restart)

    def test_manual_change_resets_only_the_dose_it_changes(self):
        manual = DoseChange(datetime(2026, 9, 3, 9), 10, 4, DoseRule.MANUAL)
        self.assertIs(tracking_start([START, manual], DoseTarget.EVENING), manual)
        self.assertIs(tracking_start([START, manual], DoseTarget.MORNING), START)

    def test_apply_refuses_a_dose_that_does_not_change(self):
        proposal = run(days([120], "08:00") + days([80], "19:00"), BOTH)
        with self.assertRaises(ValueError):
            apply_proposal(proposal, datetime(2026, 9, 2, 21))
        change = apply_adjustment(proposal, DoseTarget.MORNING, datetime(2026, 9, 2, 21), "vu avec le médecin")
        self.assertEqual((change.morning_ui, change.evening_ui, change.rule, change.note), (9, 6, DoseRule.DECREASE_LOW_EVENING, "vu avec le médecin"))
        self.assertEqual(change.evidence, ("02/09/2026 19:00 : 0,80 g/L",))

    def test_compatibility_properties_follow_the_evening_dose(self):
        proposal = run(days([70], "08:00"), BOTH)
        self.assertEqual(
            (proposal.proposed_evening_ui, proposal.rule, proposal.reason),
            (proposal.evening.proposed_ui, proposal.evening.rule, proposal.evening.reason),
        )


if __name__ == "__main__":
    unittest.main()
