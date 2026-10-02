import unittest
from dataclasses import replace
from datetime import date, datetime, time

from contracts import AlertLevel, DoseChange, DoseRule, DosingSettings, Meal, Reading
from services.dosing import apply_proposal, fmt_g_l, fmt_mg_dl, morning_readings, propose

SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
START = DoseChange(datetime(2026, 9, 1, 12, 0), 10, 6, DoseRule.START)


def r(ts: str, mg: int, meal: Meal | None = None) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()), meal=meal)


def mornings(values: list[int], first_day: int = 2, hour: str = "08:00") -> list[Reading]:
    return [r(f"2026-09-{first_day + i:02d} {hour}", mg) for i, mg in enumerate(values)]


def run(readings, changes=(START,), today=None, settings=SETTINGS):
    if today is None:
        today = max(x.day for x in readings) if readings else date(2026, 9, 2)
    return propose(readings, list(changes), settings, today)


class MorningDetectionTest(unittest.TestCase):
    def test_first_reading_in_window_wins(self):
        readings = [r("2026-09-02 04:59", 60), r("2026-09-02 07:10", 130), r("2026-09-02 09:00", 200)]
        found = morning_readings(readings, SETTINGS)
        self.assertEqual([m.reading.mg_dl for m in found], [130])

    def test_window_bounds_inclusive(self):
        readings = [r("2026-09-02 05:00", 100), r("2026-09-03 11:59", 110), r("2026-09-04 12:00", 120)]
        found = morning_readings(readings, SETTINGS)
        self.assertEqual([m.reading.mg_dl for m in found], [100, 110])

    def test_custom_window(self):
        settings = replace(SETTINGS, morning_start=time(9, 0), morning_end=time(10, 0))
        readings = [r("2026-09-02 08:00", 100), r("2026-09-02 09:30", 110)]
        self.assertEqual([m.reading.mg_dl for m in morning_readings(readings, settings)], [110])

    def test_unsorted_input(self):
        readings = [r("2026-09-02 10:00", 150), r("2026-09-02 07:00", 90)]
        self.assertEqual(morning_readings(readings, SETTINGS)[0].reading.mg_dl, 90)


class MealMarkerTest(unittest.TestCase):
    """Marqueurs saisis sur le lecteur : "à jeun" d'abord, puis "avant repas" ou sans marqueur, dans la plage."""

    def found(self, readings):
        return [(m.day.day, m.reading.mg_dl) for m in morning_readings(readings, SETTINGS)]

    def test_fasting_beats_an_earlier_reading(self):
        readings = [r("2026-09-02 06:10", 95, Meal.BEFORE_MEAL), r("2026-09-02 07:30", 130, Meal.FASTING)]
        self.assertEqual(self.found(readings), [(2, 130)])
        readings = [r("2026-09-02 06:10", 95), r("2026-09-02 07:30", 130, Meal.FASTING)]
        self.assertEqual(self.found(readings), [(2, 130)])

    def test_first_fasting_of_the_day(self):
        readings = [r("2026-09-02 07:30", 130, Meal.FASTING), r("2026-09-02 09:00", 90, Meal.FASTING)]
        self.assertEqual(self.found(readings), [(2, 130)])

    def test_before_meal_or_unmarked_when_no_fasting(self):
        self.assertEqual(self.found([r("2026-09-02 07:00", 140, Meal.BEFORE_MEAL), r("2026-09-02 08:00", 100)]), [(2, 140)])
        self.assertEqual(self.found([r("2026-09-02 07:00", 140), r("2026-09-02 08:00", 100, Meal.BEFORE_MEAL)]), [(2, 140)])

    def test_after_meal_bedtime_casual_never_count(self):
        """L'ancienne règle prenait la mesure d'après petit-déjeuner comme glycémie du matin."""
        for meal in (Meal.AFTER_MEAL, Meal.BEDTIME, Meal.CASUAL, Meal.OTHER):
            with self.subTest(meal=meal):
                readings = [r("2026-09-02 06:00", 210, meal), r("2026-09-02 08:00", 120, Meal.BEFORE_MEAL)]
                self.assertEqual(self.found(readings), [(2, 120)])
                self.assertEqual(self.found([r("2026-09-02 06:00", 210, meal)]), [])

    def test_fasting_outside_the_window_does_not_count(self):
        readings = [r("2026-09-02 14:00", 90, Meal.FASTING), r("2026-09-02 04:30", 85, Meal.FASTING)]
        self.assertEqual(self.found(readings), [])

    def test_after_meal_in_the_morning_cannot_trigger_an_increase(self):
        """Mesure après repas prise avant la mesure sans marqueur : l'ancienne règle proposait une hausse."""
        readings = []
        for day in (2, 3, 4):
            readings += [r(f"2026-09-{day:02d} 06:30", 220, Meal.AFTER_MEAL), r(f"2026-09-{day:02d} 09:30", 120)]
        self.assertEqual(run(readings).rule, DoseRule.KEEP)
        without_markers = [Reading(x.device_time, x.mg_dl, x.epoch) for x in readings]
        self.assertEqual(run(without_markers).rule, DoseRule.INCREASE_HIGH_MORNINGS)


class DecreaseRuleTest(unittest.TestCase):
    def test_low_morning_decreases_evening(self):
        p = run(mornings([79]))
        self.assertEqual((p.rule, p.proposed_evening_ui), (DoseRule.DECREASE_LOW_MORNING, 4))
        self.assertTrue(p.changes_dose)
        self.assertIn("0,79 g/L", p.reason)

    def test_exactly_080_does_not_decrease(self):
        p = run(mornings([80]))
        self.assertEqual((p.rule, p.proposed_evening_ui), (DoseRule.KEEP, 6))

    def test_floor_at_zero(self):
        at_zero = DoseChange(datetime(2026, 9, 1, 12), 10, 0, DoseRule.MANUAL)
        p = run(mornings([60]), changes=[at_zero])
        self.assertEqual((p.rule, p.proposed_evening_ui), (DoseRule.KEEP, 0))
        self.assertIn("evening_zero", [a.code for a in p.alerts])

    def test_one_ui_left_goes_to_zero_not_negative(self):
        one = DoseChange(datetime(2026, 9, 1, 12), 10, 1, DoseRule.MANUAL)
        self.assertEqual(run(mornings([60]), changes=[one]).proposed_evening_ui, 0)

    def test_only_latest_morning_counts_for_decrease(self):
        p = run(mornings([70, 120]))
        self.assertEqual(p.rule, DoseRule.KEEP)

    def test_decrease_has_priority_over_high_streak(self):
        p = run(mornings([200, 200, 200, 75]))
        self.assertEqual(p.rule, DoseRule.DECREASE_LOW_MORNING)


class IncreaseRuleTest(unittest.TestCase):
    def test_three_consecutive_high_mornings(self):
        p = run(mornings([151, 180, 220]))
        self.assertEqual((p.rule, p.proposed_evening_ui), (DoseRule.INCREASE_HIGH_MORNINGS, 8))
        self.assertEqual([x.mg_dl for x in p.evidence], [151, 180, 220])

    def test_exactly_150_does_not_count(self):
        p = run(mornings([160, 150, 170]))
        self.assertEqual(p.rule, DoseRule.KEEP)

    def test_two_high_days_not_enough(self):
        p = run(mornings([120, 170, 180]))
        self.assertEqual(p.rule, DoseRule.KEEP)
        self.assertIn("2 jour(s)", p.reason)

    def test_missing_day_breaks_streak(self):
        readings = [r("2026-09-02 08:00", 200), r("2026-09-04 08:00", 200), r("2026-09-05 08:00", 200)]
        self.assertEqual(run(readings).rule, DoseRule.KEEP)

    def test_day_without_morning_reading_breaks_streak(self):
        readings = [r("2026-09-02 08:00", 200), r("2026-09-03 15:00", 200), r("2026-09-04 08:00", 200), r("2026-09-05 08:00", 200)]
        self.assertEqual(run(readings).rule, DoseRule.KEEP)

    def test_high_evening_values_ignored(self):
        readings = mornings([120, 120, 120]) + mornings([250, 250, 250], hour="19:00")
        self.assertEqual(run(readings).rule, DoseRule.KEEP)

    def test_second_reading_same_morning_ignored(self):
        readings = mornings([160, 160, 120]) + [r("2026-09-04 10:30", 200)]
        self.assertEqual(run(readings).rule, DoseRule.KEEP)

    def test_longer_streak_uses_last_three(self):
        p = run(mornings([151, 152, 153, 154, 155]))
        self.assertEqual([x.mg_dl for x in p.evidence], [153, 154, 155])


class ResetAfterValidationTest(unittest.TestCase):
    def test_counter_resets_after_validated_change(self):
        readings = mornings([200, 200, 200])
        first = run(readings)
        change = apply_proposal(first, now=datetime(2026, 9, 4, 20, 0))
        self.assertEqual((change.evening_ui, change.morning_ui), (8, 10))
        again = run(readings, changes=[START, change])
        self.assertEqual(again.rule, DoseRule.KEEP)
        self.assertEqual(again.current.evening_ui, 8)

        more = readings + mornings([200, 200], first_day=5)
        self.assertEqual(run(more, changes=[START, change]).rule, DoseRule.KEEP)
        more += mornings([200], first_day=7)
        self.assertEqual(run(more, changes=[START, change]).proposed_evening_ui, 10)

    def test_mornings_before_start_ignored(self):
        old = [r("2026-08-30 08:00", 60), r("2026-08-31 08:00", 60)]
        p = run(old, today=date(2026, 9, 1))
        self.assertEqual(p.rule, DoseRule.KEEP)
        self.assertIn("Pas encore", p.reason)

    def test_morning_of_validation_day_before_validation_not_reused(self):
        change = DoseChange(datetime(2026, 9, 2, 8, 30), 10, 4, DoseRule.DECREASE_LOW_MORNING)
        readings = [r("2026-09-02 08:00", 70), r("2026-09-02 09:00", 70)]
        p = run(readings, changes=[START, change])
        self.assertEqual(p.rule, DoseRule.KEEP)

    def test_apply_refuses_keep(self):
        with self.assertRaises(ValueError):
            apply_proposal(run(mornings([120])), now=datetime(2026, 9, 3))

    def test_apply_refuses_time_travel(self):
        p = run(mornings([60]))
        with self.assertRaises(ValueError):
            apply_proposal(p, now=datetime(2026, 8, 1))

    def test_no_start_dose(self):
        with self.assertRaises(ValueError):
            propose(mornings([120]), [], SETTINGS, date(2026, 9, 2))


class AlertsTest(unittest.TestCase):
    def test_stale_data_blocks_change(self):
        p = run(mornings([60]), today=date(2026, 9, 5))
        self.assertEqual(p.rule, DoseRule.KEEP)
        self.assertIn("stale", [a.code for a in p.alerts])

    def test_two_days_old_is_not_stale(self):
        p = run(mornings([60]), today=date(2026, 9, 4))
        self.assertEqual(p.rule, DoseRule.DECREASE_LOW_MORNING)

    def test_hypo_alert(self):
        readings = mornings([120]) + [r("2026-09-02 16:00", 65)]
        p = run(readings)
        hypo = [a for a in p.alerts if a.code == "hypo"]
        self.assertEqual(len(hypo), 1)
        self.assertEqual(hypo[0].level, AlertLevel.DANGER)

    def test_070_exact_is_not_hypo(self):
        p = run(mornings([70]))
        self.assertNotIn("hypo", [a.code for a in p.alerts])

    def test_hyper_alert(self):
        p = run(mornings([120]) + [r("2026-09-02 15:00", 307)])
        self.assertIn("hyper", [a.code for a in p.alerts])

    def test_old_hypo_not_alerted(self):
        readings = [r("2026-09-02 16:00", 50)] + mornings([120], first_day=20)
        p = run(readings)
        self.assertNotIn("hypo", [a.code for a in p.alerts])


class FormatTest(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(fmt_g_l(80), "0,80 g/L")
        self.assertEqual(fmt_g_l(307), "3,07 g/L")
        self.assertEqual(fmt_g_l(600), "6,00 g/L")
        self.assertEqual(fmt_g_l(10), "0,10 g/L")

    def test_fmt_off_scale(self):
        self.assertEqual(fmt_g_l(601), "HI (> 6,00 g/L)")
        self.assertEqual(fmt_g_l(9), "LO (< 0,10 g/L)")
        self.assertEqual(fmt_mg_dl(601), "> 600 mg/dL")
        self.assertEqual(fmt_mg_dl(9), "< 10 mg/dL")
        self.assertEqual(fmt_mg_dl(133), "133 mg/dL")


class OffScaleReadingsTest(unittest.TestCase):
    """HI (601) et LO (9) passent par les mêmes règles que les autres mesures."""

    def test_lo_morning_decreases_and_alerts(self):
        p = run(mornings([9]))
        self.assertEqual((p.rule, p.proposed_evening_ui), (DoseRule.DECREASE_LOW_MORNING, 4))
        self.assertIn("hypo", [a.code for a in p.alerts])
        self.assertIn("LO", p.reason)

    def test_hi_mornings_count_as_high(self):
        p = run(mornings([601, 601, 601]))
        self.assertEqual(p.rule, DoseRule.INCREASE_HIGH_MORNINGS)
        self.assertIn("hyper", [a.code for a in p.alerts])


if __name__ == "__main__":
    unittest.main()
