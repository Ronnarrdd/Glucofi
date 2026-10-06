"""Éditeur GTK du protocole (ignoré sans affichage)."""

import unittest
from dataclasses import replace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from app.protocol import protocol_to_form  # noqa: E402
from app.tests.test_protocol import FULL, SIMPLE  # noqa: E402
from contracts import LowTier  # noqa: E402

HAS_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class ProtocolEditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def editor(self, settings):
        from app.protocol_editor import ProtocolEditor

        return ProtocolEditor(protocol_to_form(settings))

    def test_reads_back_what_it_shows(self):
        for settings in (SIMPLE, FULL):
            with self.subTest(morning=settings.morning_titration is not None):
                self.assertEqual(self.editor(settings).read({}), (settings, None))

    def test_morning_fields_follow_the_switch(self):
        editor = self.editor(SIMPLE)
        self.assertFalse(editor.morning_rows[0].get_visible())
        self.assertFalse(editor.morning_tiers.get_visible())
        editor.morning_switch.set_active(True)
        self.assertTrue(editor.morning_rows[0].get_visible())
        settings, problem = editor.read({})
        self.assertIsNone(settings)
        self.assertIn("n'est pas une glycémie", problem)
        self.assertIn("error", editor.rows["m_low_g_l"].get_css_classes())

    def test_tiers_can_be_added_removed_and_are_marked_on_error(self):
        editor = self.editor(SIMPLE)
        low = editor.tiers["low_tiers"]
        low.add({"below_g_l": "0,6", "step_ui": "4"})
        low.add({"below_g_l": "0,9", "step_ui": "6"})
        self.assertEqual([row.get_title() for row, _ in low.rows], ["Baisse 1", "Baisse 2"])
        settings, problem = editor.read({})
        self.assertIsNone(settings)
        self.assertIn("sous le seuil bas", problem)
        self.assertIn("error", low.rows[1][1]["below_g_l"].get_css_classes())
        low.remove(low.rows[1][0])
        self.assertEqual([row.get_title() for row, _ in low.rows], ["Baisse 1"])
        self.assertEqual(editor.read({})[0], replace(SIMPLE, low_tiers=(LowTier(0.60, 4),)))


if __name__ == "__main__":
    unittest.main()
