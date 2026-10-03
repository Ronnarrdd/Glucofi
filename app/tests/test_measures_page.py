"""Onglet Mesures assemblé en widgets GTK (ignoré sans affichage)."""

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from app.measures import MARKER_ICONS, MEAL_FILTERS, measure_view  # noqa: E402
from contracts import DosingSettings  # noqa: E402
from scripts.demo_export import build  # noqa: E402

HAS_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)


def descendants(widget: Gtk.Widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class MeasuresPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        from app.main import install_style

        install_style(Gdk.Display.get_default())

    def make_page(self, protocol: bool = True, readings: bool = True):
        from app.measures_page import MeasuresPage
        from app.state import AppState
        from services.store import Store

        tmp = Path(tempfile.mkdtemp(prefix="glucofi-test-"))
        state = AppState(Store(tmp / "glucofi.db"), tmp)
        if readings:
            export = tmp / "demo.json"
            export.write_text(json.dumps(build(20, date.today())))
            state.import_file(export)
        if protocol:
            state.start_protocol("Test", datetime(2026, 1, 1), 10, 6, SETTINGS)
        self.fetches = 0
        page = MeasuresPage(state, lambda: setattr(self, "fetches", self.fetches + 1))
        page.refresh()
        return page, state

    def rows(self, page):
        return [w for w in descendants(page.widget) if isinstance(w, Adw.ActionRow) and w.has_css_class("measure-row")]

    def test_one_row_per_reading_with_its_marker_badge(self):
        page, state = self.make_page()
        expected = measure_view(state.readings(days=90), state.settings, "all", "all", state.meters()).lines
        rows = self.rows(page)
        self.assertEqual(len(rows), len(expected))
        self.assertEqual([r.get_title() for r in rows], [line.title for line in expected])
        badges = [w for w in descendants(page.widget) if isinstance(w, Gtk.Image) and w.has_css_class("marker-badge")]
        self.assertEqual([b.get_icon_name() for b in badges], [line.marker.icon for line in expected])

    def test_app_icons_resolve_in_the_icon_theme(self):
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for name in MARKER_ICONS.values():
            self.assertTrue(theme.has_icon(name), name)

    def test_marker_filter_then_reset(self):
        page, _state = self.make_page()
        total = len(self.rows(page))
        page.meal_dropdown.set_selected([key for key, _ in MEAL_FILTERS].index("fasting"))
        fasting = self.rows(page)
        self.assertTrue(0 < len(fasting) < total)
        self.assertTrue(all(r.get_subtitle().startswith("À jeun") for r in fasting))
        page.meal_dropdown.set_selected([key for key, _ in MEAL_FILTERS].index("other"))
        self.assertEqual(self.rows(page), [])
        status = [w for w in descendants(page.widget) if isinstance(w, Adw.StatusPage)]
        self.assertEqual([s.get_title() for s in status], ["Aucune mesure pour ce filtre"])
        page.reset_filters()
        self.assertEqual((page.days, page.period, page.meal), ("all", "all", "all"))
        self.assertEqual(len(self.rows(page)), total)

    def test_empty_store_offers_to_fetch(self):
        page, _state = self.make_page(readings=False)
        status = [w for w in descendants(page.widget) if isinstance(w, Adw.StatusPage)]
        self.assertEqual([s.get_title() for s in status], ["Aucune mesure"])
        status[0].get_child().emit("clicked")
        self.assertEqual(self.fetches, 1)

    def test_no_protocol(self):
        page, _state = self.make_page(protocol=False)
        status = [w for w in descendants(page.widget) if isinstance(w, Adw.StatusPage)]
        self.assertEqual([s.get_title() for s in status], ["Protocole à saisir"])


if __name__ == "__main__":
    unittest.main()
