"""Composants façon tablette (app/components.py, app/art.py) et écran Aujourd'hui assemblés en GTK (ignoré sans affichage).

Aucune fenêtre n'est présentée : les widgets sont construits, mesurés et pilotés sans apparaître sur le bureau.
"""

import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

HAS_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
PAGES = (("a", "A", "glucofi-home-symbolic", "glucofi-home-fill-symbolic"), ("b", "B", "glucofi-doses-symbolic", "glucofi-doses-fill-symbolic"))


def descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


def settle():
    while GLib.MainContext.default().iteration(False):
        pass


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class ComponentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        from app.main import install_style

        install_style(Gdk.Display.get_default())

    def stack(self):
        stack = Adw.ViewStack()
        for name, *_rest in PAGES:
            stack.add_named(Gtk.Label(label=name), name)
        return stack

    def test_navbars_follow_the_stack_and_drive_it(self):
        from app.components import NavBar

        stack = self.stack()
        top, bottom = NavBar(stack, PAGES), NavBar(stack, PAGES, compact=True)
        stack.set_visible_child_name("b")
        for nav in (top, bottom):
            self.assertEqual([n for n, b in nav.buttons.items() if b.get_active()], ["b"])
            self.assertEqual(nav._icons["b"][0].get_icon_name(), "glucofi-doses-fill-symbolic")
            self.assertEqual(nav._icons["a"][0].get_icon_name(), "glucofi-home-symbolic")
        bottom.buttons["a"].set_active(True)
        self.assertEqual(stack.get_visible_child_name(), "a")
        self.assertTrue(top.buttons["a"].get_active())
        self.assertTrue(bottom.has_css_class("compact"))
        self.assertEqual(bottom.buttons["a"].get_child().get_orientation(), Gtk.Orientation.VERTICAL)

    def test_filter_chips_are_exclusive_with_a_checkmark(self):
        from app.components import FilterChips

        changes = []
        chips = FilterChips((("7", "7 jours"), ("30", "30 jours")), "7", changes.append)
        chips.chips["30"].set_active(True)
        self.assertEqual(changes, ["30"])
        self.assertEqual(chips.active, "30")
        self.assertFalse(chips.chips["7"].get_active())
        self.assertEqual([k for k, c in chips.chips.items() if c.check.get_visible()], ["30"])
        chips.set_active("30")
        self.assertEqual(changes, ["30"], "rechoisir le filtre actif ne recalcule rien")

    def test_buttons_keep_their_icon_and_label(self):
        from app.components import button

        widget = button("Récupérer les mesures", icon="glucofi-sync-symbolic", tall=True)
        self.assertIsInstance(widget.get_child(), Adw.ButtonContent)
        self.assertEqual(widget.get_child().get_label(), "Récupérer les mesures")
        self.assertGreater(widget.measure(Gtk.Orientation.HORIZONTAL, -1)[1], 150)
        for css in ("pill-button", "primary", "suggested-action", "tall"):
            self.assertTrue(widget.has_css_class(css), css)
        with self.assertRaises(ValueError):
            button("x", kind="fluo")

    def test_moment_tile_offers_validate_only_for_a_proposal(self):
        from app.components import MomentTile

        tile = MomentTile("evening", "Soir", lambda: None)
        tile.update(6, 8, "au lieu de 6 UI", "Soir : nouvelle dose proposée 8 UI")
        self.assertTrue(tile.revealer.get_reveal_child())
        self.assertEqual(tile.validate.get_label(), "Valider 8 UI")
        self.assertTrue(tile.status.has_css_class("proposed"))
        self.assertEqual(tile.dose.text, "8 UI")
        tile.update(8, None, "inchangée", "Soir : 8 UI, inchangée")
        self.assertFalse(tile.revealer.get_reveal_child())
        self.assertFalse(tile.status.has_css_class("proposed"))

    def test_dose_slides_up_when_it_rises_and_down_when_it_falls(self):
        from app.components import DoseStack

        dose = DoseStack()
        dose.set_value(6)
        dose.set_value(8)
        self.assertEqual(dose.get_transition_type(), Gtk.StackTransitionType.SLIDE_UP)
        dose.set_value(7)
        self.assertEqual(dose.get_transition_type(), Gtk.StackTransitionType.SLIDE_DOWN)
        self.assertEqual(dose.text, "7 UI")

    def test_notice_kinds(self):
        from app.components import Notice

        for kind in ("info", "success", "warning", "danger"):
            self.assertTrue(Notice("x", kind).has_css_class(f"notice-{kind}"))
        with self.assertRaises(ValueError):
            Notice("x", "rose")

    def test_banner_switches_type_scale_when_compact(self):
        from app.art import BANNER_MIN_HEIGHT, FoxBanner

        banner = FoxBanner("Bonjour", "mercredi 7 octobre")
        # dans une fenêtre comme dans l'application : sa destruction libère les enfants de la bannière
        window = Gtk.Window(child=banner)
        self.addCleanup(window.destroy)
        self.assertTrue(banner.title.has_css_class("display-medium"))
        self.assertGreaterEqual(banner.measure(Gtk.Orientation.VERTICAL, 1000)[1], BANNER_MIN_HEIGHT)
        banner.set_property("compact", True)
        self.assertTrue(banner.title.has_css_class("headline-large"))
        self.assertFalse(banner.title.has_css_class("display-medium"))
        self.assertTrue(banner.has_css_class("compact"))
        self.assertLess(banner.measure(Gtk.Orientation.HORIZONTAL, -1)[0], 360, "tient dans une fenêtre de 360 px")

    def test_custom_layouts_release_their_children_when_destroyed(self):
        from app.art import FoxBanner
        from app.today_page import DoseLayout

        banner = FoxBanner("Bonjour")
        layout = DoseLayout([Gtk.Label(label="matin"), Gtk.Label(label="soir")], Gtk.Label(label="pourquoi"))
        for widget, child in ((banner, banner.fox), (layout, layout.why)):
            self.assertIs(child.get_parent(), widget)
            widget.run_dispose()
            self.assertIsNone(child.get_parent(), type(widget).__name__)

    def test_onboarding_banner_goes_compact_below_560(self):
        from app import art, dialogs

        self.assertEqual(dialogs.BANNER_COMPACT, f"max-width: {art.BANNER_COMPACT_BELOW - 1}px")

    def test_dose_layout_modes(self):
        from app.today_page import DoseLayout

        self.assertEqual(DoseLayout.mode(1000, True), "beside")
        self.assertEqual(DoseLayout.mode(1000, False), "row")
        self.assertEqual(DoseLayout.mode(700, True), "row")
        self.assertEqual(DoseLayout.mode(400, True), "stack")


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class TodayPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        from app.main import install_style
        from scripts.screenshots import demo_export_file

        install_style(Gdk.Display.get_default())
        cls.export = demo_export_file(30)

    def make(self, scenario="titration", plugged=None):
        from app.today_page import TodayPage
        from scripts.screenshots import demo_state

        self.window = Adw.ApplicationWindow()
        self.addCleanup(self.window.destroy)
        self.plugged = plugged if plugged is not None else [False]
        self.toasts = []
        self.refreshes = 0

        def refresh():
            self.refreshes += 1
            page.refresh()

        page = TodayPage(self.window, demo_state(self.export, scenario), refresh, self.toasts.append,
                         is_connected=lambda: self.plugged[0])
        self.window.set_content(page.widget)
        page.refresh()
        return page

    def test_both_tiles_offer_validation_in_titration(self):
        page = self.make("titration")
        for tile in page.tiles.values():
            self.assertTrue(tile.revealer.get_reveal_child())
            self.assertTrue(tile.validate.get_label().startswith("Valider "))
        self.assertTrue(page.why.get_visible())

    def test_no_validation_without_a_change(self):
        page = self.make("default")
        self.assertFalse(any(t.revealer.get_reveal_child() for t in page.tiles.values()))

    def test_onboarding_shows_the_setup_notice_instead_of_tiles(self):
        page = self.make("onboarding")
        self.assertFalse(page.doses.get_visible())
        self.assertTrue(page.setup_box.get_visible())
        actions = [w.get_action_name() for w in descendants(page.setup_box) if isinstance(w, Gtk.Button)]
        self.assertEqual(actions, ["win.onboard"])

    def test_meter_chip_follows_the_device(self):
        page = self.make()
        page._poll_meter()
        self.assertEqual(page.meter.text.get_label(), "Lecteur non branché")
        self.plugged[0] = True
        page._poll_meter()
        self.assertEqual(page.meter.text.get_label(), "Lecteur branché")

        def broken():
            raise OSError("udev")

        page._is_connected = broken
        page._poll_meter()
        self.assertFalse(page.meter.plugged)

    def test_read_notices(self):
        from app.components import Notice

        page = self.make()

        def kinds():
            return [w.kind for w in descendants(page.read_box) if isinstance(w, Notice)]

        page.set_read("reading")
        self.assertEqual(kinds(), ["info"])
        page.set_read("done", "12 nouvelles mesures", ("horloge décalée",))
        self.assertEqual(kinds(), ["success", "warning"])
        page.set_read("failed", "Lecteur introuvable")
        self.assertEqual(kinds(), ["danger"])
        page.set_read(None)
        self.assertFalse(page.read_box.get_visible())

    def test_busy_disables_validation(self):
        page = self.make()
        page.set_busy(True)
        self.assertFalse(any(t.validate.get_sensitive() for t in page.tiles.values()))
        page.set_busy(False)
        self.assertTrue(all(t.validate.get_sensitive() for t in page.tiles.values()))

    def test_detail_opens_with_the_meter_section_and_closes(self):
        page = self.make()
        page.open_detail()
        dialog = self.window.get_visible_dialog()
        self.assertIs(dialog, page.detail)
        self.assertTrue(dialog.has_css_class("detail-sheet"))
        texts = [w.get_label() for w in descendants(dialog) if isinstance(w, Gtk.Label)]
        self.assertIn("Lecteur", texts)
        self.assertTrue(any(t.startswith("Pourquoi ") for t in texts))
        page.refresh()
        self.assertIs(self.window.get_visible_dialog(), dialog, "le détail se met à jour sans se fermer")
        dialog.force_close()
        settle()
        self.assertIsNone(page.detail)
        self.assertIsNone(self.window.get_visible_dialog())


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        from app.main import install_style
        from app.window import MainWindow
        from scripts.screenshots import demo_export_file, demo_state

        install_style(Gdk.Display.get_default())
        from app.tests.gtk_app import application

        cls.app = application()
        cls.window = MainWindow(application=cls.app, state=demo_state(demo_export_file(30), "titration"))

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()

    def test_fetch_is_on_ctrl_r_and_in_the_menu(self):
        self.assertEqual(self.app.get_accels_for_action("win.fetch"), ["<Control>r"])
        self.assertEqual(self.window.today.fetch_button.get_action_name(), "win.fetch")

    def test_four_destinations_in_the_tablet_order(self):
        self.assertEqual(list(self.window.nav.buttons), ["today", "measures", "charts", "doses"])
        self.assertEqual(list(self.window.bottom_nav.buttons), list(self.window.nav.buttons))
        self.window.stack.set_visible_child_name("doses")
        self.assertTrue(self.window.bottom_nav.buttons["doses"].get_active())
        self.window.stack.set_visible_child_name("today")

    def test_failed_read_lands_on_today_and_in_a_dialog_elsewhere(self):
        errors = []
        self.window.error = lambda heading, body: errors.append(heading)
        self.window._fetch_done(None, "lecteur introuvable")
        self.assertEqual(self.window.today.read[0], "failed")
        self.assertIn("lecteur introuvable", self.window.today.read[1])
        self.assertEqual(errors, ["Lecture impossible"], "fenêtre non affichée : Aujourd'hui n'est pas à l'écran")
        self.assertFalse(self.window.busy)

    def test_busy_disables_fetch_and_sync(self):
        self.window.set_busy(True)
        self.assertFalse(self.window.lookup_action("fetch").get_enabled())
        self.assertFalse(self.window.lookup_action("sync-tablet").get_enabled())
        self.window.set_busy(False)
        self.assertTrue(self.window.lookup_action("fetch").get_enabled())


if __name__ == "__main__":
    unittest.main()
