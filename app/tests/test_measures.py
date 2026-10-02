"""Présentation de l'onglet Mesures, sans GTK."""

import unittest
from datetime import date, datetime

from app.measures import WEEKDAYS_FR, measure_view
from contracts import MG_DL_HIGH, MG_DL_LOW, DosingSettings, Meal, MeterInfo, Reading
from services.charts import compute_stats
from services.dosing import fmt_g_l

SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
GUIDE = MeterInfo("Roche", "925", "AAA", "", "", "", "")
INSTANT = MeterInfo("Roche", "958", "BBB", "", "", "", "")


def reading(ts: str, mg: int, meal: Meal | None = None, serial: str | None = None) -> Reading:
    moment = datetime.fromisoformat(ts)
    return Reading(moment, mg, int(moment.timestamp()), meal=meal, meter_serial=serial)


def morning_set() -> list[Reading]:
    """07:00 sans marqueur, 08:30 à jeun (retenu), 09:00 après repas (ignoré pour le matin)."""
    return [
        reading("2026-09-01T07:00", 100),
        reading("2026-09-01T08:30", 112, Meal.FASTING),
        reading("2026-09-01T09:00", 180, Meal.AFTER_MEAL),
    ]


def header(day: date, count: int, morning: str | None = None) -> str:
    word = "1 mesure" if count == 1 else f"{count} mesures"
    text = f"{WEEKDAYS_FR[day.weekday()]} {day:%d/%m} · {word}"
    if morning is not None:
        text += f" · matin {morning}"
    return text


class MorningRetainedTest(unittest.TestCase):
    def test_fasting_wins_over_earlier_unmarked_and_after_meal_is_ignored(self):
        view = measure_view(morning_set(), SETTINGS, "all", "all", [])
        lines = view.days[0].lines
        self.assertEqual([line.title for line in lines], ["09:00", "08:30", "07:00"])
        self.assertEqual([line.retained for line in lines], [False, True, False])
        self.assertEqual([line.header is not None for line in lines], [True, False, False])
        self.assertEqual(lines[0].header, header(date(2026, 9, 1), 3, "1,12 g/L"))
        self.assertIn("sans marqueur", lines[2].subtitle)
        self.assertIn("À jeun", lines[1].subtitle)
        self.assertIn("Après repas", lines[0].subtitle)

    def test_meal_filter_keeps_the_retained_morning_in_the_day_header(self):
        view = measure_view(morning_set(), SETTINGS, "all", "after_meal", [])
        self.assertEqual([line.title for line in view.lines], ["09:00"])
        self.assertFalse(view.lines[0].retained)
        self.assertEqual(view.lines[0].header, header(date(2026, 9, 1), 1, "1,12 g/L"))

    def test_unmarked_filter(self):
        view = measure_view(morning_set(), SETTINGS, "all", "none", [])
        self.assertEqual([line.title for line in view.lines], ["07:00"])
        self.assertIn("sans marqueur", view.lines[0].subtitle)

    def test_period_filter(self):
        readings = morning_set() + [reading("2026-09-01T19:00", 140, Meal.BEDTIME)]
        morning = measure_view(readings, SETTINGS, "Matin", "all", [])
        self.assertEqual([line.title for line in morning.lines], ["09:00", "08:30", "07:00"])
        evening = measure_view(readings, SETTINGS, "Soir / nuit", "all", [])
        self.assertEqual([line.title for line in evening.lines], ["19:00"])
        self.assertNotIn("matin", evening.lines[0].header or "")
        self.assertIn("Coucher", evening.lines[0].subtitle)

    def test_each_day_headers_only_its_first_line(self):
        readings = morning_set() + [reading("2026-09-02T08:00", 95, Meal.FASTING)]
        view = measure_view(readings, SETTINGS, "all", "all", [])
        self.assertEqual(
            [day.header for day in view.days],
            [header(date(2026, 9, 2), 1, "0,95 g/L"), header(date(2026, 9, 1), 3, "1,12 g/L")],
        )
        for day in view.days:
            self.assertEqual(day.lines[0].header, day.header)
            self.assertTrue(all(line.header is None for line in day.lines[1:]))


class ValueAndMeterTest(unittest.TestCase):
    def test_off_scale_labels_and_colors(self):
        view = measure_view(
            [reading("2026-09-01T08:00", MG_DL_HIGH), reading("2026-09-01T21:00", MG_DL_LOW),
             reading("2026-09-01T12:00", 150)],
            SETTINGS, "all", "all", [],
        )
        by_title = {line.title: line for line in view.lines}
        self.assertEqual((by_title["08:00"].value, by_title["08:00"].css), (fmt_g_l(MG_DL_HIGH), "warning"))
        self.assertEqual((by_title["21:00"].value, by_title["21:00"].css), (fmt_g_l(MG_DL_LOW), "error"))
        self.assertEqual(by_title["12:00"].css, "success")
        self.assertEqual(fmt_g_l(MG_DL_HIGH), "HI (> 6,00 g/L)")
        self.assertEqual(fmt_g_l(MG_DL_LOW), "LO (< 0,10 g/L)")

    def test_model_name_only_when_several_meters_are_known(self):
        sample = [reading("2026-09-01T08:00", 110, serial="AAA")]
        several = measure_view(sample, SETTINGS, "all", "all", [GUIDE, INSTANT])
        self.assertIn("Accu-Chek Guide (925)", several.lines[0].subtitle)
        single = measure_view(sample, SETTINGS, "all", "all", [GUIDE])
        self.assertNotIn("Accu-Chek", single.lines[0].subtitle)
        self.assertNotIn("AAA", single.lines[0].subtitle)
        unknown = measure_view([reading("2026-09-01T08:00", 110)], SETTINGS, "all", "all", [GUIDE, INSTANT])
        self.assertIn("Lecteur inconnu", unknown.lines[0].subtitle)

    def test_serial_does_not_change_reading_equality(self):
        moment = datetime(2026, 9, 1, 8, 0)
        self.assertEqual(Reading(moment, 110, 1, meter_serial="AAA"), Reading(moment, 110, 1, meter_serial="BBB"))


class SummaryTest(unittest.TestCase):
    def test_cards_follow_compute_stats_and_count_markers(self):
        readings = [
            reading("2026-09-01T08:00", 60, Meal.FASTING),
            reading("2026-09-01T13:00", 100),
            reading("2026-09-01T19:00", 200, Meal.BEDTIME),
        ]
        view = measure_view(readings, SETTINGS, "all", "all", [])
        stats = compute_stats(readings, SETTINGS)
        self.assertEqual(view.summary.count, stats.count)
        self.assertEqual(view.summary.mean_mg, stats.mean_mg)
        self.assertEqual(view.summary.pct_in_range, stats.pct_in_range)
        self.assertEqual(view.summary.pct_hypo, stats.pct_hypo)
        self.assertEqual(view.summary.marked, 2)
        self.assertEqual(
            view.summary.cards,
            (
                ("Mesures", "3"),
                ("Moyenne", fmt_g_l(round(stats.mean_mg))),
                ("Dans l'objectif", f"{stats.pct_in_range:.0f} %"),
                ("Hypoglycémies", f"{stats.pct_hypo:.0f} %"),
                ("Avec marqueur", "2"),
            ),
        )
