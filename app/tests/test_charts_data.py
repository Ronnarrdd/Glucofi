"""Tuiles, figures et rapport partagés entre l'onglet Graphiques du PC et la tablette (sans GTK)."""

import importlib.util
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from app.charts_data import CHART_TITLES, PERIODS, Hit, chart_data, chart_figures, chart_hits, image_rect, nearest_hit
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


class ChartHoverTest(ChartsDataTest):
    @unittest.skipUnless(HAS_MPL, "matplotlib absent")
    def test_hits_land_on_the_drawn_points(self):
        """Eval du survol : le pixel sous chaque point annoncé porte la couleur de son niveau (sinon l'infobulle mentirait)."""
        import io

        from matplotlib.colors import to_rgb
        from matplotlib.image import imread

        from services.charts.figures import figure_png
        from services.charts.palette import PDF_PALETTE as P

        self.start()
        data = chart_data(self.state, 14)
        figures = chart_figures(data)
        pngs = [figure_png(fig, dpi=100) for _t, fig in figures]
        hits = chart_hits(data, figures)
        self.assertEqual([len(h) for h in hits], [3, 2, 0], "courbe : 3 mesures ; matin : 2 jours ; répartition : rien")
        wanted = {"1,00 g/L": P.in_range, "2,00 g/L": P.high, "0,70 g/L": P.low}
        image = imread(io.BytesIO(pngs[0]))[:, :, :3]
        height, width = image.shape[:2]
        for hit in hits[0]:
            colour = to_rgb(next(c for label, c in wanted.items() if label in hit.text))
            cx, cy = round(hit.x * width), round(hit.y * height)
            window = image[max(cy - 4, 0):cy + 5, max(cx - 4, 0):cx + 5].reshape(-1, 3)
            self.assertLess(min(max(abs(px - want) for px, want in zip(pixel, colour)) for pixel in window), 0.12, hit.text)

    @unittest.skipUnless(HAS_MPL, "matplotlib absent")
    def test_hit_texts_say_day_time_value_and_marker(self):
        self.start()
        data = chart_data(self.state, 14)
        hits = chart_hits(data, chart_figures(data))
        self.assertEqual(hits[0][0].text, "ven. 18/09 08:00 · 1,00 g/L · Sans marqueur")
        self.assertEqual(hits[1][0].text, "ven. 18/09 · glycémie du matin 1,00 g/L")

    @unittest.skipUnless(HAS_MPL, "matplotlib absent")
    def test_no_reading_no_hit(self):
        self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, 6, SETTINGS)
        data = chart_data(self.state, 14)
        self.assertEqual(chart_hits(data, chart_figures(data)), [[], [], []])

    def test_image_rect_contains_the_picture(self):
        self.assertEqual(image_rect(900, 400, 2.0), (50.0, 0.0, 800.0, 400.0), "limité par la hauteur, centré")
        self.assertEqual(image_rect(600, 400, 2.0), (0.0, 50.0, 600.0, 300.0), "limité par la largeur, centré")

    def test_nearest_hit_within_radius_only(self):
        hits = [Hit(0.25, 0.5, "a"), Hit(0.75, 0.5, "b")]
        rect = (0.0, 0.0, 400.0, 200.0)  # a à (100, 100), b à (300, 100)
        self.assertEqual(nearest_hit(hits, 104, 98, rect).text, "a")
        self.assertEqual(nearest_hit(hits, 296, 103, rect).text, "b")
        self.assertIsNone(nearest_hit(hits, 200, 100, rect), "entre les deux : pas d'infobulle")
        self.assertIsNone(nearest_hit([], 10, 10, rect))


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
