"""Tuiles, figures et rapport partagés entre l'onglet Graphiques du PC et la tablette (sans GTK)."""

import importlib.util
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from app.charts_data import CHART_TITLES, PERIODS, chart_data, chart_figures
from app.state import AppState
from contracts import DosingSettings, DoseTarget, InjectionState, Reading
from services.store import Store

HAS_MPL = importlib.util.find_spec("matplotlib") is not None
SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)


def r(ts: str, mg: int) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()))


class ChartsDataTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.state = AppState(
            self.store, Path(self.tmp.name), today=lambda: date(2026, 9, 20), now=lambda: datetime(2026, 9, 20, 10, 0)
        )

    def start(self):
        self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, 6, SETTINGS)
        self.store.import_readings([r("2026-09-18 08:00", 100), r("2026-09-19 08:00", 200), r("2026-09-19 13:00", 70)], "démo")

    def test_nothing_without_a_protocol(self):
        self.assertIsNone(chart_data(self.state, 30))

    def test_tiles(self):
        self.start()
        data = chart_data(self.state, 14)
        self.assertEqual([t for t, _ in data.tiles], ["Mesures", "Moyenne", "Dans l'objectif", "Sous l'objectif", "Au-dessus"])
        self.assertEqual(dict(data.tiles)["Mesures"], "3")
        self.assertEqual(dict(data.tiles)["Moyenne"], "1,23 g/L")
        self.assertEqual(dict(data.tiles)["Dans l'objectif"], "33 %")
        self.assertEqual((data.since, data.until), (datetime(2026, 9, 7), datetime(2026, 9, 21)))

    def test_period_limits_the_readings(self):
        self.start()
        self.store.import_readings([r("2026-08-01 08:00", 90)], "démo")
        self.assertEqual(len(chart_data(self.state, 14).readings), 3)
        self.assertEqual(len(chart_data(self.state, 90).readings), 4)

    def test_periods_are_the_three_chips(self):
        self.assertEqual([d for d, _ in PERIODS], [14, 30, 90])

    @unittest.skipUnless(HAS_MPL, "matplotlib absent")
    def test_three_figures_in_order(self):
        self.start()
        figures = chart_figures(chart_data(self.state, 30))
        self.assertEqual([title for title, _ in figures], list(CHART_TITLES))
        for _title, fig in figures:
            self.assertGreater(fig.get_size_inches()[0], 0)


class ReportInputTest(ChartsDataTest):
    def test_carries_everything_the_pdf_reads(self):
        self.start()
        self.state.set_injection(date(2026, 9, 19), DoseTarget.EVENING, InjectionState.MISSED)
        data = self.state.report_input(30)
        self.assertEqual(data.patient_name, "Jean")
        self.assertEqual(len(data.readings), 3)
        self.assertEqual([(i.day.day, i.state) for i in data.injections], [(19, InjectionState.MISSED)])
        self.assertEqual(data.generated_at, datetime(2026, 9, 20, 10, 0))
        self.assertEqual((data.since, data.until), (datetime(2026, 8, 22), datetime(2026, 9, 21)))
        self.assertIn("Insuline", [title for title, _ in data.protocol])
        self.assertEqual(len(data.protocol_history), 1)
        self.assertIsNotNone(data.proposal)

    @unittest.skipUnless(HAS_MPL and importlib.util.find_spec("reportlab"), "matplotlib ou reportlab absent")
    def test_builds_a_pdf(self):
        from services.report.pdf import build_report

        self.start()
        out = build_report(self.state.report_input(30), Path(self.tmp.name) / "rapport.pdf", dpi=40)
        self.assertTrue(out.read_bytes().startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
