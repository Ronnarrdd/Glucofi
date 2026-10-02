import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from contracts import DosingSettings, Reading

HAS_MPL = importlib.util.find_spec("matplotlib") is not None
APP_DIR = Path(__file__).resolve().parents[1]


def sample() -> list[Reading]:
    start = datetime(2026, 9, 1, 8)
    return [Reading(start + timedelta(days=d), 100 + d * 5, d) for d in range(10)]


class NoGtkBackendTest(unittest.TestCase):
    """Sur Mageia, backend_gtk4agg est dans python3-matplotlib-gtk3 : l'appli ne doit pas en dépendre."""

    def test_app_does_not_use_matplotlib_gtk_backends(self):
        for path in APP_DIR.glob("*.py"):
            self.assertFalse("backend_gtk" in path.read_text(), f"{path.name} importe un backend GTK de matplotlib")

    @unittest.skipUnless(HAS_MPL, "matplotlib absent")
    def test_chart_becomes_gdk_texture_without_gtk_backend(self):
        import gi

        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, GLib

        blocked = {name: None for name in ("matplotlib.backends.backend_gtk4agg", "matplotlib.backends.backend_gtk4")}
        with mock.patch.dict(sys.modules, blocked):
            from services.charts.figures import figure_png, timeline_figure

            png = figure_png(timeline_figure(sample(), [], DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3), size=(4, 2)), dpi=50)
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
        self.assertEqual((texture.get_width(), texture.get_height()), (200, 100))


if __name__ == "__main__":
    unittest.main()
