"""Présentation de l'onglet Mesures, sans GTK."""

import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

from app.measures import (
    LEVEL_CSS,
    LEVEL_ICONS,
    MARKER_ICONS,
    MEAL_FILTERS,
    day_title,
    level_of,
    marker_of,
    measure_view,
    range_spans,
)
from contracts import MG_DL_HIGH, MG_DL_LOW, DosingSettings, Meal, MeterInfo, Reading
from services.charts import compute_stats
from services.dosing import fmt_g_l

SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
GUIDE = MeterInfo("Roche", "925", "AAA", "", "", "", "")
INSTANT = MeterInfo("Roche", "958", "BBB", "", "", "", "")
TODAY = date(2026, 9, 1)
ICONS_DIR = Path(__file__).resolve().parents[1] / "icons" / "hicolor" / "scalable" / "actions"


def reading(ts: str, mg: int, meal: Meal | None = None, serial: str | None = None) -> Reading:
    moment = datetime.fromisoformat(ts)
    return Reading(moment, mg, int(moment.timestamp()), meal=meal, meter_serial=serial)


def view(readings, period="all", meal="all", meters=(), today=TODAY):
    return measure_view(readings, SETTINGS, period, meal, list(meters), today=today)


def morning_set() -> list[Reading]:
    """07:00 sans marqueur, 08:30 à jeun (retenu), 09:00 après repas (ignoré pour le matin)."""
    return [
        reading("2026-09-01T07:00", 100),
        reading("2026-09-01T08:30", 112, Meal.FASTING),
        reading("2026-09-01T09:00", 180, Meal.AFTER_MEAL),
    ]


class MorningRetainedTest(unittest.TestCase):
    def test_fasting_wins_over_earlier_unmarked_and_after_meal_is_ignored(self):
        day = view(morning_set()).days[0]
        self.assertEqual([line.title for line in day.lines], ["09:00", "08:30", "07:00"])
        self.assertEqual([line.retained for line in day.lines], [False, True, False])
        self.assertEqual((day.morning.value, day.morning.level), ("1,12 g/L", "in"))
        self.assertEqual(day.count_text, "3 mesures")
        self.assertEqual([line.subtitle for line in day.lines], ["Après repas · Matin", "À jeun · Matin", "Sans marqueur · Matin"])

    def test_meal_filter_keeps_the_retained_morning_in_the_day_header(self):
        day = view(morning_set(), meal="after_meal").days[0]
        self.assertEqual([line.title for line in day.lines], ["09:00"])
        self.assertFalse(day.lines[0].retained)
        self.assertEqual(day.morning.value, "1,12 g/L")
        self.assertEqual(day.count_text, "1 mesure")

    def test_unmarked_filter(self):
        lines = view(morning_set(), meal="none").lines
        self.assertEqual([line.title for line in lines], ["07:00"])
        self.assertEqual(lines[0].marker.key, "none")

    def test_period_filter(self):
        readings = morning_set() + [reading("2026-09-01T19:00", 140, Meal.BEDTIME)]
        morning = view(readings, period="Matin")
        self.assertEqual([line.title for line in morning.lines], ["09:00", "08:30", "07:00"])
        evening = view(readings, period="Soir / nuit")
        self.assertEqual([line.title for line in evening.lines], ["19:00"])
        self.assertIsNone(evening.days[0].morning)
        self.assertEqual(evening.lines[0].subtitle, "Coucher · Soir / nuit")

    def test_days_newest_first(self):
        readings = morning_set() + [reading("2026-09-02T08:00", 95, Meal.FASTING)]
        days = view(readings, today=date(2026, 9, 2)).days
        self.assertEqual([d.day for d in days], [date(2026, 9, 2), date(2026, 9, 1)])
        self.assertEqual([d.title for d in days], ["Aujourd'hui", "Hier"])
        self.assertEqual([d.morning.value for d in days], ["0,95 g/L", "1,12 g/L"])

    def test_unknown_filters_are_rejected(self):
        with self.assertRaises(ValueError):
            view(morning_set(), period="Nuit")
        with self.assertRaises(ValueError):
            view(morning_set(), meal="snack")


class DayTitleTest(unittest.TestCase):
    def test_today_and_yesterday_carry_the_date_as_detail(self):
        self.assertEqual(day_title(date(2026, 10, 2), date(2026, 10, 2)), ("Aujourd'hui", "vendredi 2 octobre"))
        self.assertEqual(day_title(date(2026, 10, 1), date(2026, 10, 2)), ("Hier", "jeudi 1er octobre"))

    def test_older_days_are_written_out(self):
        self.assertEqual(day_title(date(2026, 9, 30), date(2026, 10, 2)), ("Mercredi 30 septembre", None))
        self.assertEqual(day_title(date(2026, 8, 15), date(2026, 10, 2)), ("Samedi 15 août", None))

    def test_year_only_when_it_differs(self):
        self.assertEqual(day_title(date(2025, 12, 31), date(2026, 1, 1)), ("Hier", "mercredi 31 décembre 2025"))
        self.assertEqual(day_title(date(2025, 2, 1), date(2026, 1, 1)), ("Samedi 1er février 2025", None))


class LevelTest(unittest.TestCase):
    def test_thresholds_are_in_range_like_compute_stats(self):
        levels = {mg: level_of(reading("2026-09-01T08:00", mg), SETTINGS) for mg in (79, 80, 150, 151)}
        self.assertEqual(levels, {79: "low", 80: "in", 150: "in", 151: "high"})

    def test_off_scale_values_colors_and_arrows(self):
        lines = view([
            reading("2026-09-01T08:00", MG_DL_HIGH),
            reading("2026-09-01T21:00", MG_DL_LOW),
            reading("2026-09-01T12:00", 150),
        ]).lines
        by_title = {line.title: line for line in lines}
        high, low, ok = by_title["08:00"], by_title["21:00"], by_title["12:00"]
        self.assertEqual((high.value, high.mg, high.css, high.level_icon), ("HI (> 6,00 g/L)", "> 600 mg/dL", "warning", "go-up-symbolic"))
        self.assertEqual((low.value, low.mg, low.css, low.level_icon), ("LO (< 0,10 g/L)", "< 10 mg/dL", "error", "go-down-symbolic"))
        self.assertEqual((ok.css, ok.level_icon), ("success", None))
        self.assertEqual(ok.level_label, "Dans l'objectif (0,80 g/L à 1,50 g/L)")
        self.assertEqual(low.level_label, "Sous l'objectif (moins de 0,80 g/L)")
        self.assertEqual(high.level_label, "Au-dessus de l'objectif (plus de 1,50 g/L)")

    def test_every_out_of_range_level_has_an_arrow(self):
        self.assertEqual({k for k, v in LEVEL_ICONS.items() if v}, {"low", "high"})
        self.assertEqual(set(LEVEL_CSS), {"low", "in", "high"})


class MarkerTest(unittest.TestCase):
    def test_each_meal_and_no_marker_have_label_and_icon(self):
        for meal in Meal:
            marker = marker_of(reading("2026-09-01T08:00", 100, meal))
            self.assertEqual(marker.key, meal.value)
            self.assertEqual(marker.icon, MARKER_ICONS[meal.value])
        none = marker_of(reading("2026-09-01T08:00", 100))
        self.assertEqual((none.key, none.label), ("none", "Sans marqueur"))

    def test_filters_and_icons_cover_the_same_keys(self):
        keys = {key for key, _label in MEAL_FILTERS}
        self.assertEqual(set(MARKER_ICONS), keys)
        self.assertLessEqual({m.value for m in Meal} | {"all", "none"}, keys)

    def test_app_icons_exist_and_are_filled_symbolic_svgs(self):
        """GTK repeint en couleur de texte le remplissage des icônes -symbolic : un tracé en contour seul deviendrait une tache."""
        for name in MARKER_ICONS.values():
            if not name.startswith("glucofi-"):
                continue
            path = ICONS_DIR / f"{name}.svg"
            self.assertTrue(path.is_file(), path)
            root = ET.parse(path).getroot()
            self.assertEqual(root.get("viewBox"), "0 0 16 16", path.name)
            shapes = [el for el in root.iter() if el.tag.endswith("path")]
            self.assertTrue(shapes, path.name)
            for shape in shapes:
                self.assertNotIn(shape.get("fill"), (None, "none"), path.name)
                self.assertIsNone(shape.get("stroke"), path.name)


class MeterTest(unittest.TestCase):
    def test_model_name_only_when_several_meters_are_known(self):
        sample = [reading("2026-09-01T08:00", 110, serial="AAA")]
        self.assertEqual(view(sample, meters=[GUIDE, INSTANT]).lines[0].subtitle, "Sans marqueur · Matin · Accu-Chek Guide (925)")
        single = view(sample, meters=[GUIDE]).lines[0]
        self.assertIsNone(single.meter)
        self.assertNotIn("AAA", single.subtitle)
        unknown = view([reading("2026-09-01T08:00", 110)], meters=[GUIDE, INSTANT])
        self.assertEqual(unknown.lines[0].meter, "Lecteur inconnu")

    def test_serial_does_not_change_reading_equality(self):
        moment = datetime(2026, 9, 1, 8, 0)
        self.assertEqual(Reading(moment, 110, 1, meter_serial="AAA"), Reading(moment, 110, 1, meter_serial="BBB"))


class RangeSpansTest(unittest.TestCase):
    def test_sum_is_total_and_rare_levels_stay_visible(self):
        self.assertEqual(range_spans([1, 999, 0]), (1, 99, 0))
        self.assertEqual(range_spans([0, 0, 0]), (0, 0, 0))
        self.assertEqual(range_spans([1, 1, 1]), (34, 33, 33))
        self.assertEqual(range_spans([5, 75, 20]), (5, 75, 20))
        # 2 sur 250 = 0,8 % : relevé à 1 part, sans recevoir en plus la part du reste (trouvé par evals.measures_view)
        self.assertEqual(range_spans([2, 197, 51]), (1, 79, 20))
        for counts in ([3, 0, 0], [1, 2, 3], [7, 11, 13], [0, 1, 5000], [333, 333, 334], [2, 1341, 3]):
            spans = range_spans(counts)
            self.assertEqual(sum(spans), 100, counts)
            exact = [100 * c / sum(counts) for c in counts]
            # chaque niveau relevé à 1 part (moins de 1 % exact) prend cette part aux autres
            tolerance = 1 + sum(0 < e < 1 for e in exact)
            for c, s, e in zip(counts, spans, exact):
                self.assertEqual(c == 0, s == 0, counts)
                self.assertLessEqual(abs(s - e), tolerance, counts)
                if 0 < e < 1:
                    self.assertEqual(s, 1, counts)


class SummaryTest(unittest.TestCase):
    def test_summary_follows_compute_stats(self):
        readings = [
            reading("2026-09-01T08:00", 60, Meal.FASTING),
            reading("2026-09-01T13:00", 100),
            reading("2026-09-01T19:00", 200, Meal.BEDTIME),
        ]
        summary = view(readings).summary
        stats = compute_stats(readings, SETTINGS)
        self.assertEqual((summary.count, summary.mean_mg, summary.pct_in_range, summary.pct_hypo), (stats.count, stats.mean_mg, stats.pct_in_range, stats.pct_hypo))
        self.assertEqual((summary.marked, summary.hypo_count), (2, 1))
        self.assertEqual(
            [(t.label, t.value, t.detail) for t in summary.tiles],
            [
                ("Moyenne", fmt_g_l(round(stats.mean_mg)), "3 mesures"),
                ("Dans l'objectif", "33 %", "0,80 g/L à 1,50 g/L"),
                ("Hypoglycémies", "1", "sous 0,70 g/L · 33 %"),
                ("Avec marqueur", "2", "sur 3"),
            ],
        )
        self.assertEqual(
            [(s.level, s.count, s.pct, s.span, s.bounds) for s in summary.segments],
            [("low", 1, "33 %", 34, "< 0,80 g/L"), ("in", 1, "33 %", 33, "0,80 g/L à 1,50 g/L"), ("high", 1, "33 %", 33, "> 1,50 g/L")],
        )

    def test_summary_counts_only_the_filtered_readings(self):
        summary = view(morning_set(), meal="fasting").summary
        self.assertEqual((summary.count, summary.marked), (1, 1))
        self.assertEqual(summary.tiles[0].detail, "1 mesure")

    def test_empty(self):
        result = view([])
        self.assertEqual(result.days, ())
        self.assertEqual(result.summary.tiles[0].value, "-")
        self.assertEqual([s.span for s in result.summary.segments], [0, 0, 0])


if __name__ == "__main__":
    unittest.main()
