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

from app.measures import MARKER_ICONS, measure_view  # noqa: E402
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


def texts(widget: Gtk.Widget) -> list[str]:
    return [w.get_label() for w in descendants(widget) if isinstance(w, Gtk.Label)]


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
        self.changes = 0
        page = MeasuresPage(state, lambda: setattr(self, "changes", self.changes + 1))
        page.refresh()
        return page, state

    def rows(self, page):
        return [w for w in descendants(page.widget) if isinstance(w, Gtk.Button) and w.has_css_class("list-row")]

    def notices(self, page):
        from app.components import Notice

        return [w for w in descendants(page.widget) if isinstance(w, Notice)]

    def test_one_clickable_row_per_reading_with_its_marker_icon(self):
        page, state = self.make_page()
        expected = measure_view(state.readings(days=90), state.settings, "all", "all", state.meters()).lines
        rows = self.rows(page)
        self.assertEqual(len(rows), len(expected))
        self.assertEqual([texts(r)[:2] for r in rows], [[line.time, line.subtitle] for line in expected])
        icons = [next(w for w in descendants(r) if isinstance(w, Gtk.Image)).get_icon_name() for r in rows]
        self.assertEqual(icons, [line.marker.icon for line in expected])
        self.assertTrue(all(r.has_css_class("list-row") for r in rows))

    def test_app_icons_resolve_in_the_icon_theme(self):
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        for name in MARKER_ICONS.values():
            self.assertTrue(theme.has_icon(name), name)

    def test_filter_chips_are_exclusive_and_drive_the_list(self):
        page, _state = self.make_page()
        total = len(self.rows(page))
        page.meal_chips.chips["fasting"].set_active(True)
        self.assertEqual([k for k, c in page.meal_chips.chips.items() if c.get_active()], ["fasting"])
        self.assertEqual(page.meal, "fasting")
        fasting = self.rows(page)
        self.assertTrue(0 < len(fasting) < total)
        self.assertTrue(all(texts(r)[1].startswith("À jeun") for r in fasting))
        page.meal_chips.set_active("other")
        self.assertEqual(self.rows(page), [])
        self.assertEqual([n.text.get_label() for n in self.notices(page)], ["Aucune mesure pour ces filtres."])
        page.reset_filters()
        self.assertEqual((page.days, page.period, page.meal), ("all", "all", "all"))
        self.assertEqual(len(self.rows(page)), total)

    def test_checkmark_only_on_the_active_chip(self):
        page, _state = self.make_page()
        page.days_chips.set_active("7")
        shown = [k for k, c in page.days_chips.chips.items() if c.check.get_visible()]
        self.assertEqual(shown, ["7"])

    def test_empty_store_offers_to_fetch(self):
        page, _state = self.make_page(readings=False)
        notices = self.notices(page)
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0].text.get_label().startswith("Aucune mesure"))
        actions = [w for w in descendants(notices[0]) if isinstance(w, Gtk.Button)]
        self.assertEqual([b.get_action_name() for b in actions], ["win.fetch"])

    def morning_with_note(self, state, mg: int = 200):
        """Mesure du matin ajoutée à la démo, sur un jour sans autre mesure."""
        from contracts import Meal, Reading

        t = datetime(2026, 1, 2, 7, 0)
        state.store.import_readings([Reading(t, mg, int(t.timestamp()), meal=Meal.FASTING)], "test")
        return next(x for x in state.store.readings() if x.device_time == t)

    def test_note_form_builds_the_note_and_explains_the_exclusion(self):
        from app.dialogs import NoteForm
        from contracts import NoteTag, ReadingNote

        _page, state = self.make_page()
        form = NoteForm(self.morning_with_note(state), state)
        self.assertTrue(form.exclude.get_sensitive())
        form.tags[NoteTag.LARGE_MEAL].set_active(True)
        form.text.set_text("  anniversaire ")
        form.exclude.set_active(True)
        self.assertEqual(form.note(), ReadingNote((NoteTag.LARGE_MEAL,), "anniversaire", True))
        form.tags[NoteTag.LARGE_MEAL].set_active(False)
        form.text.set_text("")
        with self.assertRaises(ValueError):
            form.note()

    def test_low_reading_cannot_be_excluded_in_the_form(self):
        from app.dialogs import NoteForm

        _page, state = self.make_page()
        form = NoteForm(self.morning_with_note(state, mg=60), state)
        self.assertFalse(form.exclude.get_sensitive())
        form.exclude.set_active(True)
        form.text.set_text("bandelette abîmée ?")
        self.assertFalse(form.note().exclude_from_dosing)

    def test_saved_note_shows_on_its_row_with_the_excluded_tag(self):
        from contracts import NoteTag, ReadingNote

        page, state = self.make_page()
        reading = self.morning_with_note(state)
        state.set_note(reading, ReadingNote((NoteTag.ILLNESS,), "fièvre", True))
        page.days = "all"
        page.refresh()
        row = next(r for r in self.rows(page) if "Malade · fièvre" in texts(r))
        tags = [w for w in descendants(row) if isinstance(w, Gtk.Label) and w.has_css_class("tag")]
        self.assertEqual([t.get_label() for t in tags], ["Écartée"])
        value = next(w for w in descendants(row) if w.has_css_class("level-value"))
        self.assertTrue(value.has_css_class("dimmed"), "une mesure écartée est grisée")

    def test_no_protocol(self):
        page, _state = self.make_page(protocol=False)
        self.assertEqual(len(self.notices(page)), 1)
        self.assertIn("protocole", self.notices(page)[0].text.get_label())

    def test_narrow_puts_two_tiles_per_line(self):
        page, _state = self.make_page()

        def per_line():
            tiles = [w for w in descendants(page.summary_box) if w.has_css_class("stat-tile")]
            return len([t for t in tiles if t.get_parent() is tiles[0].get_parent()])

        self.assertEqual(per_line(), 4)
        page._set_narrow(True)
        self.assertEqual(per_line(), 2)


if __name__ == "__main__":
    unittest.main()
