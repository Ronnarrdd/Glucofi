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
ANDROID_COMMON = ANDROID_THEME.with_name("Common.kt")
ANDROID_DRAWABLE = Path("app/src/main/res/drawable")
# classes de libadwaita et de GTK que l'application pose sans les redéfinir
ADWAITA_CLASSES = {
    "suggested-action", "destructive-action", "flat", "boxed-list", "heading", "numeric", "dimmed", "circular",
    "error", "title-1", "title-2", "title-3", "title-4", "caption", "pill", "card", "property", "toolbar",
    "start", "end",
}
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
        self.assertEqual(names - set(theme.palette(False)), set())

    def test_every_notice_ink_is_checked_on_its_background_and_icon_circle(self):
        pairs = {(fg, bg) for fg, bg, _ in theme.CONTRAST_PAIRS}
        for kind, (bg, ink) in theme.NOTICE_KINDS.items():
            self.assertIn((ink, bg), pairs, kind)
            self.assertIn((ink, f"notice-{kind}-circle"), pairs, kind)

    def test_notice_circle_is_the_ink_at_ten_percent_on_the_background(self):
        self.assertEqual(theme.blend("#000000", "#FFFFFF", 0.10), "#E6E6E6")
        for dark in (False, True):
            colors = theme.palette(dark)
            for kind, (bg, ink) in theme.NOTICE_KINDS.items():
                self.assertEqual(colors[f"notice-{kind}-circle"], theme.blend(colors[ink], colors[bg], theme.NOTICE_CIRCLE_ALPHA))

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
        for key, value in re.findall(r"^\s*(\w+) = Color\(0xFF(\w{6})\),\s*$", glucofi, re.M):
            colors[re.sub(r"([A-Z])", r"-\1", key).lower()] = f"#{value.upper()}"
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
            self.assertGreaterEqual(len(shared), 48)
            self.assertLessEqual(
                {"card", "on-pastel", "banner", "banner-blob", "banner-ink", "banner-muted", "meter", "on-meter",
                 "error-container", "on-error-container"},
                shared,
            )
            diff = {token: (pc[token], android[token]) for token in shared if pc[token] != android[token]}
            self.assertEqual(diff, {}, "sombre" if dark else "clair")

    def android_file(self, relative: Path) -> str:
        for theme_path in ANDROID_CANDIDATES:
            root = Path(str(theme_path)[: -len(str(ANDROID_THEME))])
            if (root / relative).is_file():
                return (root / relative).read_text(encoding="utf-8")
        self.skipTest(f"{relative} introuvable")

    def test_logo_paths_are_the_tablet_ones(self):
        from app.art import MarkPaths

        common = self.android_file(ANDROID_COMMON)
        for name in ("TILE", "BAND", "CURVE", "DROP"):
            match = re.search(rf'const val {name} = "([^"]+)"', common)
            self.assertIsNotNone(match, name)
            self.assertEqual(getattr(MarkPaths, name), match.group(1), name)

    def test_sun_and_moon_are_the_tablet_doodles(self):
        from app.art import DoodlePaths

        def drawable(name):
            xml = self.android_file(ANDROID_DRAWABLE / name)
            paths = re.findall(r'android:pathData="([^"]+)"', xml)
            colors = re.findall(r'android:(?:fill|stroke)Color="#FF(\w{6})"', xml)
            return paths, [f"#{c.upper()}" for c in colors]

        sun, sun_colors = drawable("ic_doodle_sun.xml")
        self.assertEqual(sun, [DoodlePaths.SUN_DISC, DoodlePaths.SUN_RAYS])
        self.assertEqual(sun_colors, [theme.DOODLE["sun"], theme.DOODLE["sun-ray"]])
        moon, moon_colors = drawable("ic_doodle_moon.xml")
        self.assertEqual(moon, list(DoodlePaths.MOON))
        self.assertEqual(set(moon_colors), {theme.DOODLE["moon"]})


class StyleSheetTest(unittest.TestCase):
    def test_no_literal_color_in_style_css(self):
        """Les couleurs ne vivent que dans app/theme.py : la feuille n'emploie que var(--glucofi-*)."""
        self.assertEqual(re.findall(r"#[0-9A-Fa-f]{3,8}\b|\b(?:rgba?|hsla?|alpha|mix)\(", CSS_CODE), [])
        self.assertEqual(re.findall(NAMED_COLORS, CSS_CODE), [])

    def test_every_variable_used_is_declared(self):
        used = set(re.findall(r"var\(--glucofi-([\w-]+)\)", CSS_CODE))
        self.assertTrue(used)
        self.assertEqual(used - set(theme.palette(False)), set())

    def test_every_pastel_notice_and_level_class_has_a_rule(self):
        for name in (*theme.SUMMARY_PASTELS, "pastel-sage", "moment-morning", "moment-evening", "status-pill",
                     "tag-current", *(f"notice-{kind}" for kind in theme.NOTICE_KINDS)):
            self.assertIn(f".{name}", CSS_CODE)
        for kind in theme.NOTICE_KINDS:
            self.assertIn(f".notice-{kind} .notice-icon", CSS_CODE)
        for level in theme.LEVEL_KEYS:
            for owner in ("level-chip", "level-value", "range-segment", "legend-dot"):
                self.assertIn(f".{owner}.level-{level}", CSS_CODE)

    def test_type_scale_is_the_tablet_one_in_points(self):
        """Tailles de DESIGN.md (px) en points (x 0,75) : le texte suit le réglage « grands caractères »."""
        sizes = {"dose-number": 96, "display-medium": 45, "display-small": 36, "headline-large": 32,
                 "headline-small": 24, "title-large": 22, "title-medium": 16, "title-small": 14, "body-large": 16,
                 "body-medium": 14, "body-small": 12, "label-large": 14, "label-medium": 12}
        for name, px in sizes.items():
            sizes_set = {m.group(1) for m in re.finditer(rf"\.{name}\b[^{{]*\{{[^}}]*font-size: ([\d.]+pt)", CSS_CODE)}
            self.assertEqual(sizes_set, {f"{px * 0.75:g}pt"}, name)

    def test_touch_targets_are_at_least_48_px(self):
        """min-height + padding vertical des pilules, filtres et onglets : 48 px au moins, 56 pour les grandes."""
        for selector, need in (("button.pill-button", 48), ("button.pill-button.tall", 56), ("button.filter-chip", 48),
                               (".navbar button.nav-item", 48), (".navbar.compact button.nav-item", 56)):
            rule = re.search(rf"^{re.escape(selector)} \{{([^}}]*)\}}", CSS_CODE, re.M).group(1)
            height = int(re.search(r"min-height: (\d+)px", rule).group(1))
            vertical = int(re.search(r"padding: (\d+)px", rule).group(1))
            self.assertGreaterEqual(height + 2 * vertical, need, selector)

    def test_no_shadow_on_cards(self):
        rule = re.search(r"\.section-card, \.card, list\.boxed-list[^{]*\{([^}]*)\}", CSS_CODE).group(1)
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
    """Les classes que la feuille de style attend sont posées par les écrans, et chaque classe posée a sa règle."""

    @classmethod
    def setUpClass(cls):
        Adw.init()
        theme.install(Gdk.Display.get_default())
        from app.window import MainWindow
        from scripts.screenshots import demo_export_file, demo_state

        from app.tests.gtk_app import application

        cls.app = application()
        cls.window = MainWindow(application=cls.app, state=demo_state(demo_export_file(30), "titration"))
        cls.window.charts.build()

    @classmethod
    def tearDownClass(cls):
        cls.window.destroy()

    def classed(self, css: str, root=None) -> list:
        return [w for w in descendants(root or self.window) if w.has_css_class(css)]

    def test_every_class_set_by_the_app_has_a_rule(self):
        """Une classe écrite dans app/*.py et posée sur un widget est stylée par style.css ou vient de libadwaita."""
        source = "".join(path.read_text(encoding="utf-8") for path in theme.APP_DIR.glob("*.py"))
        literals = set(re.findall(r'"([a-z][a-z0-9-]+)"', source))
        self.window.today.open_detail()
        try:
            widgets = [self.window, *descendants(self.window)]
            dialog = self.window.get_visible_dialog()
            widgets += [dialog, *descendants(dialog)]
            used = {css for w in widgets for css in w.get_css_classes()} & literals
        finally:
            self.window.today.detail.force_close()
        defined = set(re.findall(r"\.([a-z][a-z0-9-]+)", CSS_CODE))
        self.assertEqual(used - defined - ADWAITA_CLASSES - set(theme.palette(False)), set())

    def test_dose_tiles_have_their_moment_and_proposal_pill(self):
        today = self.window.today.widget
        self.assertEqual(len(self.classed("moment-morning", today)), 1)
        self.assertEqual(len(self.classed("moment-evening", today)), 1)
        pills = self.classed("status-pill", today)
        self.assertEqual(len(pills), 2)
        self.assertTrue(all(p.has_css_class("proposed") for p in pills), "titration : une proposition pour chaque dose")

    def test_alerts_are_notices(self):
        from app.components import Notice

        self.assertTrue([w for w in descendants(self.window) if isinstance(w, Notice)])
        self.assertFalse([w for w in descendants(self.window) if isinstance(w, (Adw.ActionRow, Adw.StatusPage))
                          and (w.has_css_class("error") or w.has_css_class("warning"))])

    def test_summary_tiles_are_pastel_and_outside_the_cards(self):
        measures = self.classed("stat-tile", self.window.measures.summary_box)
        self.assertEqual([next(c for c in t.get_css_classes() if c.startswith("pastel-")) for t in measures],
                         list(theme.SUMMARY_PASTELS))
        charts = self.classed("stat-tile", self.window.charts.widget)
        self.assertEqual([next(c for c in t.get_css_classes() if c.startswith("pastel-")) for t in charts],
                         ["pastel-sage", *theme.SUMMARY_PASTELS])
        for tile in measures + charts:
            parent = tile.get_parent()
            while parent is not None:
                self.assertFalse(parent.has_css_class("section-card"))
                parent = parent.get_parent()

    def test_level_chips_show_their_level(self):
        chips = self.classed("level-chip")
        self.assertTrue(chips)
        levels = {f"level-{k}" for k in theme.LEVEL_KEYS} | {"dimmed"}
        for chip in chips:
            self.assertEqual(len([c for c in chip.get_css_classes() if c in levels]), 1)

    def test_current_dose_and_protocol_are_tagged(self):
        tags = self.classed("tag-current")
        self.assertEqual(len(tags), 2)
        self.assertTrue(all(t.get_label() == "En cours" for t in tags))

    def test_filters_are_chips(self):
        from app.components import FilterChips

        rows = [w for w in descendants(self.window) if isinstance(w, FilterChips)]
        self.assertEqual(len(rows), 4, "trois rangées dans Mesures, une dans Graphiques")
        self.assertFalse([w for w in descendants(self.window) if isinstance(w, Adw.ToggleGroup)])

    def test_charts_are_rebuilt_on_dark_switch(self):
        manager = Adw.StyleManager.get_default()
        scheme = manager.get_color_scheme()
        try:
            self.window.charts.dirty = False
            manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK if not manager.get_dark() else Adw.ColorScheme.FORCE_LIGHT)
            self.assertTrue(self.window.charts.dirty)
        finally:
            manager.set_color_scheme(scheme)
            while GLib.MainContext.default().iteration(False):
                pass


if __name__ == "__main__":
    unittest.main()
