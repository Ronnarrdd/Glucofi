"""Icônes Material Symbols (scripts/symbols.py) : réécriture du tracé, format refusé, icônes versionnées que GTK dessine."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, Graphene, Gsk, Gtk  # noqa: E402

from scripts import symbols  # noqa: E402

HAS_DISPLAY = Gtk.init_check() and Gdk.Display.get_default() is not None


def material(d: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" height="24" viewBox="0 -960 960 960" width="24">'
        f'<path d="{d}"/></svg>'
    )


class TransformTest(unittest.TestCase):
    def test_absolute_ordinates_move_relative_ones_follow(self):
        self.assertEqual(symbols.transform("M10-20L30-40H50V-60Z", 960), "M10 940 L30 920 H50 V900 Z")
        self.assertEqual(symbols.transform("M10-20l30-40h50v-60z", 960), "M10 940 l30 -40 h50 v-60 z")

    def test_leading_relative_move_starts_from_the_origin(self):
        """Le « m » de tête est absolu (premier point), ses paires implicites suivantes sont des « l » relatifs."""
        self.assertEqual(symbols.transform("m40-120 440-760Zm138-80h604", 960), "m40 840 440 -760 Z m138 -80 h604")

    def test_curves_shift_every_control_point(self):
        self.assertEqual(symbols.transform("C1-2 3-4 5-6S7-8 9-10Q1-1 2-2T3-3", 10), "C1 8 3 6 5 4 S7 2 9 0 Q1 9 2 8 T3 7")

    def test_arc_scales_lengths_not_angle_or_flags(self):
        self.assertEqual(symbols.transform("A60 60 90 1 0 120-60", 960, 0.5), "A30 30 90 1 0 60 450")

    def test_scale_to_sixteen_units(self):
        self.assertEqual(symbols.transform("M480-480h240", 960, 16 / 960), "M8 8 h4")

    def test_compact_numbers_are_split(self):
        self.assertEqual(symbols.transform("m0-.5.5.5", 0), "m0 -0.5 0.5 0.5")

    def test_unreadable_path_is_refused(self):
        for d in ("M10 20 30", "M10 20 X5", "M10 20 #"):
            with self.subTest(d=d), self.assertRaises(symbols.UnsupportedSvg):
                symbols.transform(d, 960)


class ToSymbolicTest(unittest.TestCase):
    def test_writes_a_sixteen_pixel_grey_path(self):
        svg = symbols.to_symbolic(material("M480-480h240v240H480Z"), "demo")
        self.assertIn('width="16" height="16" viewBox="0 0 16 16"', svg)
        self.assertIn('<path fill="#2e3436" d="M8 8 h4 v4 H8 Z"/>', svg)
        self.assertIn("Apache 2.0", svg)

    def test_other_layouts_are_refused(self):
        for svg in (
            material("M0 0Z").replace("0 -960 960 960", "0 0 24 24"),
            material("M0 0Z").replace("</svg>", '<path d="M1 1Z"/></svg>'),
            "<svg/>",
        ):
            with self.subTest(svg=svg[:60]), self.assertRaises(symbols.UnsupportedSvg):
                symbols.to_symbolic(svg, "demo")

    def test_generate_reads_a_local_folder(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            for name in symbols.SYMBOLS.values():
                Path(source, f"{name}.svg").write_text(material("M480-480h240v240H480Z"), encoding="utf-8")
            with mock.patch.object(symbols, "ICON_DIR", Path(target)):
                written = symbols.generate(Path(source))
            self.assertEqual(sorted(p.name for p in written), sorted(f"{symbols.icon_name(k)}.svg" for k in symbols.SYMBOLS))

    def test_url_points_to_the_pinned_commit(self):
        url = symbols.URL.format(commit=symbols.COMMIT, base="home", name="home_fill1")
        self.assertIn(f"/{symbols.COMMIT}/symbols/web/home/materialsymbolsoutlined/home_fill1_24px.svg", url)
        self.assertRegex(symbols.COMMIT, r"^[0-9a-f]{40}$")


class CommittedIconsTest(unittest.TestCase):
    """Les icônes de app/icons sont celles que produit le script, dans le cadre 16 x 16."""

    def test_every_symbol_is_committed_inside_the_frame(self):
        for key, name in symbols.SYMBOLS.items():
            path = symbols.ICON_DIR / f"{symbols.icon_name(key)}.svg"
            with self.subTest(icon=path.name):
                svg = path.read_text(encoding="utf-8")
                self.assertIn(f"« {name} »", svg)
                self.assertIn('viewBox="0 0 16 16"', svg)
                d = re.search(r' d="([^"]+)"', svg).group(1)
                numbers = [float(n) for n in re.findall(r"-?\d+\.?\d*", d)]
                self.assertTrue(all(-16.01 <= n <= 16.01 for n in numbers), path.name)

    def test_no_stray_generated_icon(self):
        expected = {f"{symbols.icon_name(key)}.svg" for key in symbols.SYMBOLS}
        generated = {p.name for p in symbols.ICON_DIR.glob("*.svg") if "Material Symbols" in p.read_text(encoding="utf-8")}
        self.assertEqual(generated, expected)

    def test_every_icon_named_in_the_app_exists_and_none_is_unused(self):
        """Les noms d'icônes sont écrits en entier dans app/*.py : une faute de frappe afficherait une icône vide."""
        app_dir = symbols.ICON_DIR.parents[3]
        source = "".join(p.read_text(encoding="utf-8") for p in app_dir.glob("*.py"))
        named = set(re.findall(r'"(glucofi-[a-z0-9-]+-symbolic)"', source))
        committed = {p.stem for p in symbols.ICON_DIR.glob("*.svg")}
        self.assertEqual(named - committed, set(), "icône citée mais absente")
        self.assertEqual(committed - named, set(), "icône versionnée que l'application n'emploie plus")


@unittest.skipUnless(HAS_DISPLAY, "pas d'affichage")
class GtkDrawsTheIconsTest(unittest.TestCase):
    """GTK 4.20 ignore le viewBox : une icône hors du cadre 16 x 16 s'affiche vide. Chaque icône doit se dessiner."""

    def test_every_icon_draws_in_the_requested_color(self):
        renderer = Gsk.CairoRenderer()
        renderer.realize_for_display(Gdk.Display.get_default())
        self.addCleanup(renderer.unrealize)
        red = Gdk.RGBA(red=1, green=0, blue=0, alpha=1)
        for key in symbols.SYMBOLS:
            path = symbols.ICON_DIR / f"{symbols.icon_name(key)}.svg"
            with self.subTest(icon=path.name):
                paintable = Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(str(path)), 48, 1)
                snapshot = Gtk.Snapshot()
                paintable.snapshot_symbolic(snapshot, 48, 48, [red] * 4)
                texture = renderer.render_texture(snapshot.to_node(), Graphene.Rect().init(0, 0, 48, 48))
                downloader = Gdk.TextureDownloader.new(texture)
                downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
                data = downloader.download_bytes()[0].get_data()
                reds = sum(1 for i in range(0, len(data), 4) if data[i + 3] > 128 and data[i] > 200 and data[i + 1] < 60)
                self.assertGreater(reds, 48 * 48 // 25, "icône vide ou presque à 48 px")


if __name__ == "__main__":
    unittest.main()
