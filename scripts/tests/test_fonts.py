"""Polices de l'interface (scripts/fonts.py, app/fonts) : fichiers, noms, graisses, licences, glyphes des textes."""

from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path

from app import theme

HAS_FONTTOOLS = importlib.util.find_spec("fontTools") is not None
ROOT = Path(__file__).resolve().parents[2]
TEXT_SOURCES = ("app", "contracts", "services")


def python_texts() -> set[str]:
    """Caractères des chaînes qui peuvent finir à l'écran ou dans un graphique (docstrings et tests exclus)."""
    chars: set[str] = set()
    for top in TEXT_SOURCES:
        for path in (ROOT / top).rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = {
                id(node.value) for node in ast.walk(tree)
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                    chars |= set(node.value)
    return {c for c in chars if c.isprintable() and not c.isspace()}


@unittest.skipUnless(HAS_FONTTOOLS, "fontTools absent (urpmi python3-fonttools)")
class FontFilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fontTools.ttLib import TTFont

        from scripts import fonts

        cls.fonts = fonts
        cls.ttf = {name: TTFont(fonts.FONT_DIR / f"{name}.ttf") for name in fonts.FACES}

    def test_one_file_per_face_and_the_licences(self):
        files = {p.name for p in self.fonts.FONT_DIR.iterdir() if p.is_file()}
        self.assertEqual(files, {f"{name}.ttf" for name in self.fonts.FACES})
        self.assertEqual({p.name for p in theme.font_files()}, files)
        for name in files:
            size = (self.fonts.FONT_DIR / name).stat().st_size
            self.assertLess(size, 80_000, f"{name} : {size} octets, le sous-ensemble latin a-t-il sauté ?")

    def test_static_instances_at_the_pinned_weights(self):
        for name, (_family, axes, _style) in self.fonts.FACES.items():
            with self.subTest(name=name):
                self.assertNotIn("fvar", self.ttf[name])
                self.assertEqual(self.ttf[name]["OS/2"].usWeightClass, axes["wght"])

    def test_names_follow_the_weight(self):
        """Famille et style typographiques (ID 16/17, sinon 1/2) : fontconfig et Pango rangent les faces ainsi."""
        for name, (family, _axes, style) in self.fonts.FACES.items():
            table = self.ttf[name]["name"]
            with self.subTest(name=name):
                self.assertEqual(table.getDebugName(16) or table.getDebugName(1), family)
                self.assertEqual(table.getDebugName(17) or table.getDebugName(2), style)

    def test_licences_ship_with_the_app(self):
        for family, source in self.fonts.SOURCES.items():
            text = (self.fonts.LICENCES / source.licence).read_text(encoding="utf-8")
            self.assertIn("SIL OPEN FONT LICENSE", text.upper())
            self.assertIn(family, text)

    def test_sources_are_pinned(self):
        for family, source in self.fonts.SOURCES.items():
            with self.subTest(family=family):
                self.assertRegex(source.commit, r"^[0-9a-f]{40}$")
                self.assertRegex(source.sha256, r"^[0-9a-f]{64}$")

    def test_checksum_mismatch_is_refused(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "Nunito[wght].ttf"
            fake.write_bytes(b"pas une police")
            with self.assertRaises(self.fonts.ChecksumMismatch):
                self.fonts.verify("Nunito", fake)

    def test_every_displayed_character_is_drawn_by_each_face(self):
        chars = python_texts()
        missing = {}
        for name, font in self.ttf.items():
            cmap = font.getBestCmap()
            lacking = sorted(c for c in chars if ord(c) not in cmap and c not in theme.FALLBACK)
            if lacking:
                missing[name] = " ".join(f"{c} U+{ord(c):04X}" for c in lacking)
        self.assertEqual(missing, {}, "caractères affichés sans glyphe (ajouter à UNICODES ou à theme.FALLBACK, justifié)")

    def test_fallback_list_stays_minimal(self):
        """Un caractère de FALLBACK qui n'est plus affiché, ou que les polices ont gagné, sort de la liste."""
        chars = python_texts()
        for char, why in theme.FALLBACK.items():
            with self.subTest(char=char, why=why):
                self.assertIn(char, chars)
                for font in self.ttf.values():
                    self.assertNotIn(ord(char), font.getBestCmap())


class PangoFacesTest(unittest.TestCase):
    def test_pango_sees_each_weight_as_its_own_face(self):
        """Sans le renommage de scripts/fonts.py, Pango ne voyait qu'une face par famille."""
        import gi

        gi.require_version("PangoCairo", "1.0")
        from gi.repository import PangoCairo

        if not theme.load_fonts():
            self.skipTest("Pango antérieur à 1.56 : polices non chargeables par fichier")
        families = {f.get_name(): f for f in PangoCairo.FontMap.get_default().list_families()}
        expected = {theme.DISPLAY_FONT: {"Medium", "SemiBold"}, theme.TEXT_FONT: {"Medium", "Bold"}}
        for family, styles in expected.items():
            with self.subTest(family=family):
                self.assertIn(family, families)
                self.assertLessEqual(styles, {face.get_face_name() for face in families[family].list_faces()})


if __name__ == "__main__":
    unittest.main()
