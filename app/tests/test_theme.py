"""Thème de la tablette sur le PC (app/theme.py, app/style.css) : couleurs, contrastes, CSS, polices, classes."""

import re
import unittest
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from app import theme  # noqa: E402

HAS_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None
ROOT = Path(__file__).resolve().parents[2]
ANDROID_THEME = Path("app/src/main/kotlin/fr/librenard/glucofi/ui/Theme.kt")
# le dépôt Android voisin, ou le dépôt Android qui contient ce code en sous-arbre glucofi/
ANDROID_CANDIDATES = (ROOT.parent / "GlucofiAndroid" / ANDROID_THEME, ROOT.parent / ANDROID_THEME)
CSS = theme.STYLE_CSS.read_text(encoding="utf-8")
CSS_CODE = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
NAMED_COLORS = r"\b(?:white|black|red|green|blue|orange|purple|yellow|gray|grey|pink|brown|cyan|magenta)\b"


def descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from descendants(child)
        child = child.get_next_sibling()


class PaletteTest(unittest.TestCase):
    def test_light_and_dark_define_the_same_tokens(self):
        self.assertEqual(set(theme.LIGHT), set(theme.DARK))

    def test_tokens_are_opaque_hex_colors(self):
        for name, scheme in (("clair", theme.LIGHT), ("sombre", theme.DARK)):
            for token, value in scheme.items():
                with self.subTest(scheme=name, token=token):
                    self.assertRegex(value, r"^#[0-9A-F]{6}$")

    def test_every_text_and_shape_pair_is_legible_in_both_schemes(self):
        failures = []
        for dark in (False, True):
            colors = theme.palette(dark)
            for fg, bg, need in theme.CONTRAST_PAIRS:
                ratio = theme.contrast(colors[fg], colors[bg])
                if ratio < need:
                    failures.append(f"{'sombre' if dark else 'clair'} {fg} sur {bg} : {ratio:.2f} < {need}")
        self.assertEqual(failures, [])

    def test_contrast_pairs_and_adwaita_map_name_real_tokens(self):
        names = {name for fg, bg, _ in theme.CONTRAST_PAIRS for name in (fg, bg)} | set(theme.ADWAITA.values())
        self.assertEqual(names - set(theme.LIGHT), set())

    def test_contrast_matches_wcag_reference_values(self):
        self.assertAlmostEqual(theme.contrast("#000000", "#FFFFFF"), 21.0, places=2)
        self.assertAlmostEqual(theme.contrast("#777777", "#FFFFFF"), 4.48, places=2)
        self.assertEqual(theme.contrast("#123456", "#123456"), 1.0)

    def test_levels_replace_adwaita_error_success_warning(self):
        """Une valeur marquée .error, .success ou .warning prend la couleur de son niveau de glycémie."""
        for adw, level in (("error", "low"), ("success", "in"), ("warning", "high")):
            self.assertEqual(theme.ADWAITA[f"{adw}-color"], f"level-{level}")
            self.assertEqual(theme.ADWAITA[f"{adw}-bg-color"], f"level-{level}")

    def test_tokens_css_declares_theme_and_adwaita_variables(self):
        for dark in (False, True):
            css = theme.tokens_css(dark)
            colors = theme.palette(dark)
            self.assertIn(f"--glucofi-background: {colors['background']};", css)
            self.assertIn(f"--window-bg-color: {colors['background']};", css)
            self.assertIn(f"--accent-bg-color: {colors['primary']};", css)
            self.assertIn("--headerbar-shade-color: transparent;", css)

    def test_chart_palette_follows_the_theme(self):
        for dark in (False, True):
            colors, chart = theme.palette(dark), theme.chart_palette(dark)
            self.assertEqual((chart.low, chart.in_range, chart.high), (colors["level-low"], colors["level-in"], colors["level-high"]))
            self.assertEqual((chart.background, chart.text), (colors["card"], colors["on-surface-variant"]))
            self.assertEqual(chart.fonts[0], theme.TEXT_FONT)

    def test_chart_inks_are_legible_on_the_card(self):
        failures = []
        for dark in (False, True):
            c = theme.chart_palette(dark)
            pairs = [(c.text, c.background, theme.TEXT, "texte"), (c.title, c.background, theme.TEXT, "titre"),
                     (c.muted, c.background, theme.TEXT, "annotation")]
            pairs += [(color, c.background, theme.SHAPE, name) for name, color in
                      (("bas", c.low), ("objectif", c.in_range), ("haut", c.high), ("courbe", c.line),
                       ("matin", c.morning), ("dose", c.dose), ("médiane", c.median), ("hypo", c.hypo))]
            pairs += [(c.bar_text, bar, theme.TEXT, f"pourcentage sur {name}") for name, bar in
                      (("bas", c.low), ("objectif", c.in_range), ("haut", c.high))]
            for fg, bg, need, name in pairs:
                if theme.contrast(fg, bg) < need:
                    failures.append(f"{'sombre' if dark else 'clair'} {name} : {theme.contrast(fg, bg):.2f} < {need}")
        self.assertEqual(failures, [])

    def test_matplotlib_draws_charts_with_the_bundled_nunito(self):
        from matplotlib import font_manager

        theme.register_chart_fonts()
        for weight, face in (("medium", "nunito_medium.ttf"), ("bold", "nunito_bold.ttf")):
            prop = font_manager.FontProperties(family=list(theme.chart_palette(False).fonts), weight=weight)
            path = Path(font_manager.findfont(prop, fallback_to_default=False))
            self.assertEqual((path.parent, path.name), (theme.FONT_DIR, face))


class AndroidParityTest(unittest.TestCase):
    """Mêmes couleurs que la tablette : Theme.kt lu quand le dépôt Android est là."""

    @classmethod
    def setUpClass(cls):
        found = [path for path in ANDROID_CANDIDATES if path.is_file()]
        if not found:
            raise unittest.SkipTest("Theme.kt de Glucofi pour Android introuvable à côté de ce dépôt")
        cls.kotlin = found[0].read_text(encoding="utf-8")

    def block(self, name: str) -> str:
        match = re.search(rf"val {name} = \w+\((.*?)\n\)", self.kotlin, re.S)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def android(self, dark: bool) -> dict[str, str]:
        prefix = "Dark" if dark else "Light"
        colors = {}
        for key, value in re.findall(r"(\w+) = Color\(0xFF([0-9A-Fa-f]{6})\)", self.block(f"{prefix}Scheme")):
            colors[re.sub(r"([A-Z])", r"-\1", key).lower()] = f"#{value.upper()}"
        for key, level in (("low", "low"), ("inRange", "in"), ("high", "high")):
            tone = re.search(rf"{key} = LevelTone\(Color\(0xFF(\w{{6}})\), Color\(0xFF(\w{{6}})\), Color\(0xFF(\w{{6}})\)\)",
                             self.block(f"{prefix}Levels"))
            for suffix, value in zip(("", "-container", "-on-container"), tone.groups()):
                colors[f"level-{level}{suffix}"] = f"#{value.upper()}"
        glucofi = self.block(f"{prefix}Glucofi")
        colors["card"] = "#" + re.search(r"card = Color\(0xFF(\w{6})\)", glucofi).group(1).upper()
        colors["on-pastel"] = "#" + re.search(r"onPastel = Color\(0xFF(\w{6})\)", glucofi).group(1).upper()
        for moment in ("morning", "evening"):
            tone = re.search(rf"^\s*{moment} = MomentTone\((.*)\),\s*$", glucofi, re.M).group(1)
            for part, value in re.findall(r"(\w+) = Color\(0xFF(\w{6})\)", tone):
                colors[f"{moment}-{part}"] = f"#{value.upper()}"
        pastels = re.findall(r"Color\(0xFF(\w{6})\)", re.search(r"^\s*pastels = listOf\((.*)\),\s*$", glucofi, re.M).group(1))
        for name, value in zip(theme.SUMMARY_PASTELS, pastels):
            colors[name] = f"#{value.upper()}"
        return colors

    def test_shared_tokens_have_the_tablet_values(self):
        for dark in (False, True):
            android = self.android(dark)
            pc = theme.palette(dark)
            shared = set(android) & set(pc)
            self.assertGreaterEqual(len(shared), 40)
            diff = {token: (pc[token], android[token]) for token in shared if pc[token] != android[token]}
            self.assertEqual(diff, {}, "sombre" if dark else "clair")


class StyleSheetTest(unittest.TestCase):
    def test_no_literal_color_in_style_css(self):
        """Les couleurs ne vivent que dans app/theme.py : la feuille n'emploie que var(--glucofi-*)."""
        self.assertEqual(re.findall(r"#[0-9A-Fa-f]{3,8}\b|\b(?:rgba?|hsla?|alpha|mix)\(", CSS_CODE), [])
        self.assertEqual(re.findall(NAMED_COLORS, CSS_CODE), [])

    def test_every_variable_used_is_declared(self):
        used = set(re.findall(r"var\(--glucofi-([\w-]+)\)", CSS_CODE))
        self.assertTrue(used)
        self.assertEqual(used - set(theme.LIGHT), set())

    def test_every_pastel_and_level_class_has_a_rule(self):
        for name in (*theme.SUMMARY_PASTELS, "pastel-sage", "moment-morning", "moment-evening",
                     "notice-danger", "notice-warning", "notice-info", "status-pill", "tag-current"):
            self.assertIn(f".{name}", CSS_CODE)
        for level in theme.LEVEL_KEYS:
            self.assertIn(f".value-pill.level-{level}", CSS_CODE)
            self.assertIn(f".morning-chip.level-{level}", CSS_CODE)

    def test_no_shadow_on_cards(self):
        rule = re.search(r"\.card, list\.boxed-list[^{]*\{([^}]*)\}", CSS_CODE).group(1)
        self.assertIn("box-shadow: none", rule)


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class InstalledThemeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        cls.display = Gdk.Display.get_default()
        theme.install(cls.display)
        cls.manager = Adw.StyleManager.get_default()
        cls.scheme = cls.manager.get_color_scheme()

    def tearDown(self):
        self.manager.set_color_scheme(self.scheme)

    def parse_errors(self, load) -> list[str]:
        provider = Gtk.CssProvider()
        errors = []
        provider.connect("parsing-error", lambda _p, section, error: errors.append(f"{section.to_string()} : {error.message}"))
        load(provider)
        return errors

    def test_style_sheet_and_colors_parse_without_error(self):
        self.assertEqual(self.parse_errors(lambda p: p.load_from_path(str(theme.STYLE_CSS))), [])
        for dark in (False, True):
            self.assertEqual(self.parse_errors(lambda p, d=dark: p.load_from_string(theme.tokens_css(d))), [])

    def test_install_once_per_display(self):
        before = theme.installed_providers(self.display)
        theme.install(self.display)
        self.assertIs(theme.installed_providers(self.display), before)

    def test_colors_follow_the_dark_switch(self):
        tokens, _style = theme.installed_providers(self.display)
        for scheme, dark in ((Adw.ColorScheme.FORCE_DARK, True), (Adw.ColorScheme.FORCE_LIGHT, False)):
            self.manager.set_color_scheme(scheme)
            self.assertIn(theme.palette(dark)["background"].lower(), tokens.to_string().lower())

    def test_fonts_resolve_to_nunito_and_fredoka(self):
        self.assertTrue(theme.load_fonts(), "Pango 1.56 ou plus requis pour charger app/fonts")
        window = Gtk.Window()
        box = Gtk.Box()
        body, title = Gtk.Label(label="Glycémie"), Gtk.Label(label="Matin")
        title.add_css_class("title-1")
        box.append(body)
        box.append(title)
        window.set_child(box)
        for label, family in ((body, theme.TEXT_FONT), (title, theme.DISPLAY_FONT)):
            context = label.get_pango_context()
            font = context.load_font(context.get_font_description())
            self.assertEqual(font.describe().get_family(), family)
        window.destroy()


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage graphique")
class ThemeClassesTest(unittest.TestCase):
    """Les classes que la feuille de style attend sont posées par les écrans."""

    @classmethod
    def setUpClass(cls):
        Adw.init()
        theme.install(Gdk.Display.get_default())
        from app.window import MainWindow
        from scripts.screenshots import demo_export_file, demo_state

        # sans identifiant : enregistrée sans D-Bus, « startup » émis avant d'y ajouter la fenêtre
        cls.app = Adw.Application()
        cls.app.register(None)
        cls.window = MainWindow(application=cls.app, state=demo_state(demo_export_file(30), "titration"))
        cls.window._build_charts()

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()

    def classed(self, css: str) -> list:
        return [w for w in descendants(self.window) if w.has_css_class(css)]

    def test_dose_tiles_have_their_moment_and_proposal_pill(self):
        self.assertEqual(len(self.classed("moment-morning")), 1)
        self.assertEqual(len(self.classed("moment-evening")), 1)
        pills = self.classed("status-pill")
        self.assertEqual(len(pills), 2, "titration : une proposition pour chaque dose")
        self.assertTrue(all(p.get_label().startswith("proposé") for p in pills))

    def test_alerts_carry_a_notice_class(self):
        notices = [w for w in descendants(self.window) if isinstance(w, Gtk.ListBox)
                   and any(w.has_css_class(c) for c in ("notice-danger", "notice-warning", "notice-info"))]
        self.assertTrue(notices)
        self.assertFalse([w for w in descendants(self.window) if isinstance(w, Adw.ActionRow)
                          and (w.has_css_class("error") or w.has_css_class("warning"))])

    def test_summary_tiles_are_pastel_and_outside_the_card(self):
        tiles = self.classed("summary-tile")
        self.assertEqual([next(c for c in t.get_css_classes() if c.startswith("pastel-")) for t in tiles],
                         list(theme.SUMMARY_PASTELS))
        for tile in tiles:
            parent = tile.get_parent()
            while parent is not None:
                self.assertFalse(parent.has_css_class("card"))
                parent = parent.get_parent()

    def test_chart_stat_tiles_use_the_five_pastels(self):
        tones = [next(c for c in t.get_css_classes() if c.startswith("pastel-")) for t in self.classed("stat-tile")]
        self.assertEqual(tones, ["pastel-sage", *theme.SUMMARY_PASTELS])

    def test_morning_chips_show_their_level(self):
        chips = self.classed("morning-chip")
        self.assertTrue(chips)
        for chip in chips:
            self.assertEqual(len([c for c in chip.get_css_classes() if c in {f"level-{k}" for k in theme.LEVEL_KEYS}]), 1)

    def test_current_dose_and_protocol_are_tagged(self):
        tags = self.classed("tag-current")
        self.assertEqual(len(tags), 2)
        self.assertTrue(all(t.get_label() == "En cours" for t in tags))

    def test_filters_are_round(self):
        groups = [w for w in descendants(self.window) if isinstance(w, Adw.ToggleGroup)]
        self.assertTrue(groups)
        self.assertTrue(all(g.has_css_class("round") for g in groups))

    def test_charts_are_rebuilt_on_dark_switch(self):
        manager = Adw.StyleManager.get_default()
        scheme = manager.get_color_scheme()
        try:
            self.window.charts_dirty = False
            manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if not manager.get_dark() else Adw.ColorScheme.FORCE_LIGHT)
            self.assertTrue(self.window.charts_dirty)
        finally:
            manager.set_color_scheme(scheme)
            while GLib.MainContext.default().iteration(False):
                pass


if __name__ == "__main__":
    unittest.main()
