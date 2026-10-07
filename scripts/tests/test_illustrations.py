"""Illustrations de la tablette en PNG (scripts/illustrations.py) : conversion sans perte, source absente, fichiers versionnés."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

from scripts import illustrations

SIZES = {"fox-banner.png": ((603, 720), "RGBA"), "brush.png": ((798, 600), "LA")}


def fake_sources(folder: Path) -> None:
    Image.new("RGBA", (6, 4), (200, 100, 50, 128)).save(folder / "illus_fox_banner.webp", lossless=True)
    Image.new("RGBA", (5, 3), (255, 255, 255, 90)).save(folder / "illus_brush.webp", lossless=True)


class ConvertTest(unittest.TestCase):
    def test_converts_both_images_to_png_in_their_mode(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            fake_sources(Path(src))
            written = illustrations.generate(Path(src), Path(dst))
            self.assertEqual(sorted(p.name for p in written), sorted(illustrations.ILLUSTRATIONS))
            with Image.open(Path(dst, "fox-banner.png")) as fox, Image.open(Path(dst, "brush.png")) as brush:
                self.assertEqual((fox.format, fox.mode, fox.getpixel((0, 0))), ("PNG", "RGBA", (200, 100, 50, 128)))
                self.assertEqual((brush.format, brush.mode, brush.getpixel((0, 0))), ("PNG", "LA", (255, 90)))

    def test_missing_source_is_reported_by_name(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dst:
            with self.assertRaisesRegex(FileNotFoundError, "illus_fox_banner.webp"):
                illustrations.generate(Path(src), Path(dst))
            self.assertEqual(list(Path(dst).iterdir()), [])

    def test_main_returns_an_error_code_without_sources(self):
        with tempfile.TemporaryDirectory() as src:
            self.assertEqual(illustrations.main(["--from", src]), 1)


class CommittedIllustrationsTest(unittest.TestCase):
    def test_committed_pngs_have_the_expected_size_and_mode(self):
        for name, (size, mode) in SIZES.items():
            with self.subTest(name=name), Image.open(illustrations.TARGET_DIR / name) as image:
                self.assertEqual((image.format, image.size, image.mode), ("PNG", size, mode))

    @unittest.skipUnless(illustrations.DEFAULT_SOURCE.is_dir(), "dépôt GlucofiAndroid absent à côté de ce dépôt")
    def test_committed_pngs_match_the_tablet_pixels(self):
        for png, (webp, mode) in illustrations.ILLUSTRATIONS.items():
            with self.subTest(png=png), Image.open(illustrations.DEFAULT_SOURCE / webp) as source, \
                    Image.open(illustrations.TARGET_DIR / png) as committed:
                expected = source.convert(mode)
                self.assertIsNone(ImageChops.difference(expected, committed.convert(mode)).getbbox())


if __name__ == "__main__":
    unittest.main()


class FoxPlacementTest(unittest.TestCase):
    """Comme sur la tablette, le renard n'apparaît que dans la bannière d'Aujourd'hui et du premier lancement."""

    def test_fox_is_only_built_by_the_banner_of_today_and_onboarding(self):
        app_dir = illustrations.TARGET_DIR.parent
        calls: dict[str, list[str]] = {"Fox": [], "FoxBanner": []}
        for path in sorted(app_dir.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in calls:
                    calls[node.func.id].append(path.name)
        self.assertEqual(calls["Fox"], ["art.py"], "seule la bannière crée le renard")
        self.assertEqual(sorted(calls["FoxBanner"]), ["dialogs.py", "today_page.py"])
