import importlib.util
import io
import unittest
from datetime import datetime, timedelta

from contracts import DoseChange, DoseRule, DosingSettings, Reading
from services.charts import compute_stats, period_of, stats_by_period

SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
HAS_MPL = importlib.util.find_spec("matplotlib") is not None


def r(ts: str, mg: int) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()))


def sample() -> list[Reading]:
    start = datetime(2026, 9, 1)
    out = []
    for d in range(20):
        for hour, mg in ((8, 90 + d * 7), (13, 140), (20, 190)):
            t = start + timedelta(days=d, hours=hour)
            out.append(Reading(t, mg, int(t.timestamp())))
    return out


class StatsTest(unittest.TestCase):
    def test_empty(self):
        s = compute_stats([], SETTINGS)
        self.assertEqual((s.count, s.mean_mg, s.pct_in_range), (0, None, 0.0))

    def test_bounds_are_in_range(self):
        s = compute_stats([r("2026-09-01 08:00", 80), r("2026-09-01 09:00", 150)], SETTINGS)
        self.assertEqual((s.pct_in_range, s.pct_low, s.pct_high), (100.0, 0.0, 0.0))

    def test_percentages(self):
        values = [65, 75, 100, 120, 200]
        s = compute_stats([r(f"2026-09-0{i + 1} 08:00", v) for i, v in enumerate(values)], SETTINGS)
        self.assertEqual(s.pct_hypo, 20.0)
        self.assertEqual(s.pct_low, 40.0)
        self.assertEqual(s.pct_in_range, 40.0)
        self.assertEqual(s.pct_high, 20.0)
        self.assertAlmostEqual(s.pct_low + s.pct_in_range + s.pct_high, 100.0)
        self.assertEqual((s.min_mg, s.max_mg, s.mean_mg), (65, 200, 112))

    def test_periods(self):
        self.assertEqual(period_of(r("2026-09-01 07:00", 100), SETTINGS), "Matin")
        self.assertEqual(period_of(r("2026-09-01 12:00", 100), SETTINGS), "Après-midi")
        self.assertEqual(period_of(r("2026-09-01 18:00", 100), SETTINGS), "Soir / nuit")
        self.assertEqual(period_of(r("2026-09-01 03:00", 100), SETTINGS), "Soir / nuit")
        by = stats_by_period(sample(), SETTINGS)
        self.assertEqual([by[p].count for p in by], [20, 20, 20])


@unittest.skipUnless(HAS_MPL, "matplotlib absent (urpmi python3-matplotlib)")
class FiguresTest(unittest.TestCase):
    def render(self, fig) -> bytes:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=50)
        return buf.getvalue()

    def test_all_figures_render(self):
        from services.charts.figures import distribution_figure, morning_trend_figure, timeline_figure

        readings = sample()
        changes = [
            DoseChange(datetime(2026, 8, 30, 20), 8, 4, DoseRule.START),
            DoseChange(datetime(2026, 9, 10, 20), 8, 6, DoseRule.INCREASE_HIGH_MORNINGS),
        ]
        since, until = datetime(2026, 9, 1), datetime(2026, 9, 21)
        for fig in (
            timeline_figure(readings, changes, SETTINGS, since, until),
            morning_trend_figure(readings, changes, SETTINGS, since, until),
            distribution_figure(readings, SETTINGS),
        ):
            png = self.render(fig)
            self.assertTrue(png.startswith(b"\x89PNG"))
            self.assertGreater(len(png), 2000)

    def test_empty_figures_render(self):
        from services.charts.figures import distribution_figure, morning_trend_figure, timeline_figure

        for fig in (timeline_figure([], [], SETTINGS), morning_trend_figure([], [], SETTINGS), distribution_figure([], SETTINGS)):
            self.assertTrue(self.render(fig).startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
