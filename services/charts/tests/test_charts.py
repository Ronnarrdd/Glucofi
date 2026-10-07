import importlib.util
import io
import unittest
from datetime import datetime, timedelta

from contracts import DoseChange, DoseRule, DosingSettings, Reading
from services.charts import compute_stats, period_of, stats_by_period
from services.charts.palette import PDF_PALETTE, ChartPalette

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


# palette d'écran fictive (sombre), police livrée avec matplotlib : le service ne dépend pas de app/
THEMED = ChartPalette(
    low="#FFB4AB", in_range="#8ED6AE", high="#FFBE7A", band="#8ED6AE", band_alpha=0.12, hypo="#FFB4AB",
    line="#8ED3CB", morning="#FFB1C0", dose="#8ED3CB", muted="#B5C4C1", point_edge="#1A2423",
    annotation_bg="#1A2423", bar_text="#101819", box_face="#0E4E54", box_alpha=1.0, median="#E3ECE9",
    background="#1A2423", text="#B5C4C1", title="#E3ECE9", edge="#34403F", grid="#34403F", grid_alpha=1.0,
    grid_below=True, fonts=("DejaVu Sans",),
)
CHANGES = [
    DoseChange(datetime(2026, 8, 30, 20), 8, 4, DoseRule.START),
    DoseChange(datetime(2026, 9, 10, 20), 8, 6, DoseRule.INCREASE_HIGH_MORNINGS),
]
SINCE, UNTIL = datetime(2026, 9, 1), datetime(2026, 9, 21)


def hex_of(color) -> str:
    from matplotlib.colors import to_hex

    return to_hex(color).upper()


@unittest.skipUnless(HAS_MPL, "matplotlib absent (urpmi python3-matplotlib)")
class PaletteTest(unittest.TestCase):
    """Le PDF garde ses couleurs ; l'écran prend celles de sa palette, élément par élément."""

    @staticmethod
    def figures(palette=None):
        """Couleurs et polices sont posées à la construction : pas besoin de rendu pour les lire."""
        from services.charts.figures import distribution_figure, morning_trend_figure, timeline_figure

        kw = {} if palette is None else {"palette": palette}
        readings = sample()
        return (
            timeline_figure(readings, CHANGES, SETTINGS, SINCE, UNTIL, **kw),
            morning_trend_figure(readings, CHANGES, SETTINGS, SINCE, UNTIL, **kw),
            distribution_figure(readings, SETTINGS, **kw),
        )

    @classmethod
    def setUpClass(cls):
        import matplotlib

        cls.rc_before = dict(matplotlib.rcParams)
        cls.themed = cls.figures(THEMED)
        cls.themed_png = [cls.render(fig) for fig in cls.themed]
        cls.rc_after = dict(matplotlib.rcParams)
        cls.default = cls.figures()

    @staticmethod
    def render(fig) -> bytes:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=40)
        return buf.getvalue()

    def test_themed_figures_render(self):
        for png in self.themed_png:
            self.assertTrue(png.startswith(b"\x89PNG"))

    def test_pdf_palette_keeps_the_report_colors(self):
        self.assertEqual(
            (PDF_PALETTE.line, PDF_PALETTE.in_range, PDF_PALETTE.low, PDF_PALETTE.high, PDF_PALETTE.morning, PDF_PALETTE.muted),
            ("#3584e4", "#2ec27e", "#e01b24", "#ff7800", "#9141ac", "#77767b"),
        )
        self.assertEqual((PDF_PALETTE.background, PDF_PALETTE.text, PDF_PALETTE.fonts, PDF_PALETTE.grid_below), (None, None, (), False))

    def test_default_figures_keep_matplotlib_defaults(self):
        timeline, _morning, distribution = self.default
        ax = timeline.axes[0]
        self.assertEqual(hex_of(timeline.get_facecolor()), "#FFFFFF")
        self.assertEqual(hex_of(ax.get_yticklabels()[0].get_color()), "#000000")
        self.assertEqual(ax.get_yticklabels()[0].get_fontfamily(), ["sans-serif"])
        self.assertEqual(hex_of(ax.collections[0].get_edgecolor()[0]), "#FFFFFF")
        box_ax = distribution.axes[0]
        self.assertEqual(hex_of(box_ax.lines[4].get_color()), "#000000")

    def test_themed_figures_take_the_palette(self):
        timeline, morning, distribution = self.themed
        for fig in (timeline, morning, distribution):
            self.assertEqual(hex_of(fig.get_facecolor()), THEMED.background)
            ax = fig.axes[0]
            self.assertEqual(hex_of(ax.get_facecolor()), THEMED.background)
            self.assertEqual(hex_of(ax.spines["left"].get_edgecolor()), THEMED.edge)
            label = ax.get_yticklabels()[0]
            self.assertEqual((hex_of(label.get_color()), label.get_fontfamily()), (THEMED.text, list(THEMED.fonts)))
            self.assertEqual(hex_of(ax.yaxis.label.get_color()), THEMED.text)
            self.assertTrue(ax.get_axisbelow())
        points = timeline.axes[0].collections[0]
        self.assertLessEqual({hex_of(c) for c in points.get_facecolor()}, {THEMED.low, THEMED.in_range, THEMED.high})
        self.assertEqual(hex_of(points.get_edgecolor()[0]), THEMED.point_edge)
        legend = timeline.axes[0].get_legend()
        self.assertEqual({hex_of(t.get_color()) for t in legend.get_texts()}, {THEMED.text})
        dose_ax = morning.axes[1]
        self.assertEqual(hex_of(dose_ax.get_yticklabels()[0].get_color()), THEMED.dose)
        box_ax = distribution.axes[0]
        self.assertEqual(hex_of(box_ax.title.get_color()), THEMED.title)
        self.assertEqual(hex_of(box_ax.patches[-1].get_facecolor()), THEMED.box_face)
        # points aberrants : marqueur seul, leur trait (noir par défaut) n'est jamais tracé
        drawn = {hex_of(line.get_markeredgecolor() if line.get_marker() not in ("", "None") else line.get_color())
                 for line in box_ax.lines}
        self.assertEqual(drawn, {THEMED.median})

    def test_themed_empty_figure(self):
        from services.charts.figures import timeline_figure

        fig = timeline_figure([], [], SETTINGS, palette=THEMED)
        text = fig.axes[0].texts[0]
        self.assertEqual((hex_of(fig.get_facecolor()), hex_of(text.get_color())), (THEMED.background, THEMED.muted))

    def test_palette_never_touches_rcparams(self):
        """rcParams est partagé : le PDF, construit dans un thread, ne doit jamais hériter des couleurs de l'écran."""
        self.assertEqual(self.rc_after, self.rc_before)
        self.assertEqual(hex_of(self.default[0].get_facecolor()), "#FFFFFF")


if __name__ == "__main__":
    unittest.main()
