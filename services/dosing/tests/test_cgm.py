"""Glycémie de référence donnée par le capteur en continu (LibreLinkUp) : plus proche de l'heure réglée, lecteur ignoré."""

import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from contracts import AlertLevel, DoseChange, DoseRule, DosingSettings, Meal, NoteTag, Reading, ReadingNote, Titration
from services.dosing import evening_readings, listed_in_tables, morning_readings, propose

METER = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
CGM = replace(METER, cgm_morning=time(7, 0))
START = DoseChange(datetime(2026, 9, 1, 12, 0), 10, 6, DoseRule.START)


def r(ts: str, mg: int, cgm: bool = True, meal: Meal | None = None, note: ReadingNote | None = None) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()), meal=meal, note=note, cgm=cgm)


def run(readings, settings=CGM, today=None):
    return propose(readings, [START], settings, today or max(x.day for x in readings))


class SettingsTest(unittest.TestCase):
    def test_default_is_the_meter(self):
        self.assertFalse(METER.uses_cgm)
        self.assertTrue(CGM.uses_cgm)

    def test_rejects_invalid_combinations(self):
        morning = Titration(0.8, 1.5, 2, 3)
        for label, values in (
            ("soir sans matin", {"cgm_evening": time(18, 0)}),
            ("hors plage du matin", {"cgm_morning": time(14, 0)}),
            ("tolérance nulle", {"cgm_morning": time(7, 0), "cgm_window_min": 0}),
            ("tolérance trop large", {"cgm_morning": time(7, 0), "cgm_window_min": 61}),
            ("dose du matin sans heure du soir", {"cgm_morning": time(7, 0), "morning_titration": morning}),
            ("heure du soir hors plage", {"cgm_morning": time(7, 0), "morning_titration": morning, "cgm_evening": time(15, 0)}),
        ):
            with self.subTest(label), self.assertRaises(ValueError):
                replace(METER, **values)

    def test_accepts_both_times(self):
        settings = replace(
            METER, cgm_morning=time(7, 0), cgm_evening=time(18, 30), morning_titration=Titration(0.8, 1.5, 2, 3)
        )
        self.assertTrue(settings.uses_cgm)


class ReferenceTest(unittest.TestCase):
    def test_nearest_to_the_configured_time_wins(self):
        readings = [r("2026-09-02 06:45", 150), r("2026-09-02 07:05", 100), r("2026-09-02 07:15", 90), r("2026-09-02 05:00", 60)]
        found = morning_readings(readings, CGM)
        self.assertEqual([m.reading.mg_dl for m in found], [100])

    def test_tie_goes_to_the_earlier_reading(self):
        readings = [r("2026-09-02 07:10", 110), r("2026-09-02 06:50", 120)]
        self.assertEqual(morning_readings(readings, CGM)[0].reading.mg_dl, 120)

    def test_window_is_inclusive_and_bounded(self):
        readings = [r("2026-09-02 07:20", 100), r("2026-09-03 07:21", 110), r("2026-09-04 06:40", 120), r("2026-09-05 06:39", 130)]
        self.assertEqual([m.reading.mg_dl for m in morning_readings(readings, CGM)], [100, 120])

    def test_custom_tolerance(self):
        settings = replace(CGM, cgm_window_min=5)
        readings = [r("2026-09-02 07:06", 100), r("2026-09-03 07:05", 110)]
        self.assertEqual([m.reading.mg_dl for m in morning_readings(readings, settings)], [110])

    def test_meter_readings_no_longer_count(self):
        readings = [r("2026-09-02 07:00", 40, cgm=False, meal=Meal.FASTING), r("2026-09-02 07:10", 110)]
        self.assertEqual(morning_readings(readings, CGM)[0].reading.mg_dl, 110)

    def test_day_without_sensor_value_has_no_reference_even_with_a_meter_value(self):
        readings = [r("2026-09-02 07:00", 100, cgm=False, meal=Meal.FASTING)]
        self.assertEqual(morning_readings(readings, CGM), [])

    def test_sensor_readings_are_ignored_when_the_meter_is_the_source(self):
        readings = [r("2026-09-02 05:00", 60), r("2026-09-02 07:30", 130, cgm=False)]
        self.assertEqual([m.reading.mg_dl for m in morning_readings(readings, METER)], [130])

    def test_evening_reference(self):
        settings = replace(CGM, cgm_evening=time(18, 30), morning_titration=Titration(0.8, 1.5, 2, 3))
        readings = [r("2026-09-02 18:20", 100), r("2026-09-02 18:50", 140), r("2026-09-02 07:00", 90)]
        self.assertEqual([m.reading.mg_dl for m in evening_readings(readings, settings)], [100])

    def test_note_can_exclude_a_sensor_reading_and_the_next_nearest_takes_over(self):
        note = ReadingNote((NoteTag.ILLNESS,), "", True)
        readings = [r("2026-09-02 07:00", 200, note=note), r("2026-09-02 07:15", 120)]
        self.assertEqual(morning_readings(readings, CGM)[0].reading.mg_dl, 120)


class DoseTest(unittest.TestCase):
    def days(self, values, hour="07:00"):
        return [r(f"2026-09-{2 + i:02d} {hour}", mg) for i, mg in enumerate(values)]

    def test_low_sensor_reference_lowers_the_evening_dose(self):
        proposal = run(self.days([70]))
        self.assertEqual((proposal.evening.proposed_ui, proposal.evening.rule), (4, DoseRule.DECREASE_LOW_MORNING))

    def test_high_streak_raises_the_dose(self):
        proposal = run(self.days([170, 180, 190]))
        self.assertEqual(proposal.evening.proposed_ui, 8)

    def test_manual_low_does_not_lower_the_dose_when_the_sensor_is_the_source(self):
        readings = [r("2026-09-02 07:00", 120), r("2026-09-02 07:05", 50, cgm=False, meal=Meal.FASTING)]
        self.assertFalse(run(readings).evening.changes_dose)

    def test_night_low_outside_the_window_does_not_lower_the_dose(self):
        readings = [r("2026-09-02 03:00", 50), r("2026-09-02 07:00", 120)]
        self.assertFalse(run(readings).evening.changes_dose)

    def test_no_sensor_value_means_no_proposal(self):
        proposal = run([r("2026-09-02 07:00", 120, cgm=False, meal=Meal.FASTING)], today=date(2026, 9, 2))
        self.assertIn("Pas encore de glycémie du matin", proposal.evening.reason)

    def test_stale_alert_points_to_librelinkup(self):
        proposal = run(self.days([120]), today=date(2026, 9, 10))
        stale = next(a for a in proposal.alerts if a.code == "stale")
        self.assertIn("LibreLinkUp", stale.message)
        self.assertNotIn("lecteur", stale.message)


class EpisodeAlertTest(unittest.TestCase):
    def lows(self, start, count, step=15, mg=60):
        base = datetime.strptime(start, "%Y-%m-%d %H:%M")
        return [Reading(base + timedelta(minutes=step * i), mg - i, 0, cgm=True) for i in range(count)]

    def alert(self, readings, code="hypo"):
        proposal = run([r("2026-09-02 07:00", 120), *readings], today=date(2026, 9, 3))
        return next((a for a in proposal.alerts if a.code == code), None)

    def test_a_long_sensor_low_is_one_hypoglycemia(self):
        alert = self.alert(self.lows("2026-09-03 02:00", 8))
        self.assertIn("1 hypoglycémie ", alert.message)
        self.assertEqual(alert.level, AlertLevel.DANGER)

    def test_two_sensor_lows_far_apart_are_two(self):
        alert = self.alert(self.lows("2026-09-02 02:00", 2) + self.lows("2026-09-03 02:00", 2))
        self.assertIn("2 hypoglycémies", alert.message)

    def test_episode_is_reported_by_its_lowest_value(self):
        alert = self.alert(self.lows("2026-09-03 02:00", 4))
        self.assertIn("0,57 g/L", alert.message)

    def test_meter_lows_still_count_one_by_one(self):
        meter = [Reading(datetime(2026, 9, 3, 2, m), 60, 0) for m in (0, 10)]
        self.assertIn("2 hypoglycémies", self.alert(meter).message)

    def test_sensor_high_episode(self):
        highs = [Reading(datetime(2026, 9, 3, 14, 0) + timedelta(minutes=15 * i), 320, 0, cgm=True) for i in range(6)]
        self.assertIn("1 glycémie ", self.alert(highs, "hyper").message)


class ListedInTablesTest(unittest.TestCase):
    def test_meter_readings_are_always_listed(self):
        for settings in (METER, CGM):
            self.assertTrue(listed_in_tables(r("2026-09-02 15:00", 120, cgm=False), settings))

    def test_sensor_readings_are_listed_only_near_the_reference_time(self):
        self.assertTrue(listed_in_tables(r("2026-09-02 07:15", 120), CGM))
        self.assertFalse(listed_in_tables(r("2026-09-02 07:21", 120), CGM))
        self.assertFalse(listed_in_tables(r("2026-09-02 15:00", 120), CGM))

    def test_sensor_readings_are_not_listed_when_the_meter_is_the_source(self):
        self.assertFalse(listed_in_tables(r("2026-09-02 07:00", 120), METER))


if __name__ == "__main__":
    unittest.main()
