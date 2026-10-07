import unittest

from app import theme
from evals.theme_render import (
    ENCRE_MIN,
    LARGE,
    Check,
    LabelSample,
    Pixels,
    assess_background,
    assess_label,
    background_and_ink,
    hex_color,
    is_large,
    over,
    score,
)


def rgb(hex_value: str) -> tuple[int, int, int]:
    return tuple(int(hex_value[i:i + 2], 16) for i in (1, 3, 5))


def label(fg: str, bg: str, alpha: float = 1.0, **changes) -> LabelSample:
    """Libellé de 14 px en Nunito, peint de la couleur annoncée (encre = contraste annoncé)."""
    color = (*(c / 255 for c in rgb(fg)), alpha)
    fields = {
        "view": "clair 1000 px today", "text": "Dose du soir", "css": "dim-label", "color": color,
        "background": rgb(bg), "ink": theme.contrast(hex_color(over(color, rgb(bg))), bg),
        "size_px": 14.0, "weight": 500, "runs": (("Nunito", "Dose du soir"),), "unknown_glyphs": 0, "overflow": "",
    }
    return LabelSample(**{**fields, **changes})


def failed(sample: LabelSample) -> set[str]:
    return {c.check for c in assess_label(sample) if not c.ok}


class ThemeRenderSmokeTest(unittest.TestCase):
    """Partie pure de l'eval du thème (sans GTK), lancée à chaque commit."""

    def test_every_theme_text_pair_passes(self):
        for dark in (False, True):
            colors = theme.palette(dark)
            for fg, bg, need in theme.CONTRAST_PAIRS:
                if need == theme.TEXT:
                    with self.subTest(dark=dark, fg=fg, bg=bg):
                        self.assertEqual(failed(label(colors[fg], colors[bg])), set())

    def test_eval_catches_adwaita_dimmed_text(self):
        """L'opacité 0,55 d'Adwaita sur .dim-label, que app/style.css remet à 1."""
        for dark in (False, True):
            colors = theme.palette(dark)
            for bg in ("background", "card"):
                with self.subTest(dark=dark, bg=bg):
                    self.assertIn("contraste", failed(label(colors["on-surface-variant"], colors[bg], alpha=0.55)))

    def test_eval_catches_ink_fainter_than_the_declared_color(self):
        """Couleur annoncée lisible, pixels pâles : une opacité CSS que Gtk.Widget.get_color() ne montre pas."""
        for dark in (False, True):
            colors = theme.palette(dark)
            sample = label(colors["on-surface-variant"], colors["background"])
            self.assertEqual(failed(sample), set())
            floor = sample.ink * ENCRE_MIN
            self.assertEqual(failed(LabelSample(**{**sample.__dict__, "ink": floor + 0.01})), set())
            self.assertEqual(failed(LabelSample(**{**sample.__dict__, "ink": floor - 0.01})), {"encre"})
            # même un texte très contrasté, estompé à 0,55, reste sous le seuil même s'il dépasse 3:1
            faded = label(colors["on-surface-variant"], colors["background"], alpha=0.55).ink
            self.assertEqual(failed(LabelSample(**{**sample.__dict__, "ink": faded})), {"encre"})

    def test_large_text_needs_three_to_one(self):
        self.assertTrue(is_large(24, 400))
        self.assertTrue(is_large(18.66, 700))
        self.assertFalse(is_large(18.66, 600))
        self.assertFalse(is_large(23.9, 400))
        grey = "#8A8A8A"  # 3,45:1 sur blanc
        self.assertIn("contraste", failed(label(grey, "#FFFFFF")))
        self.assertEqual(failed(label(grey, "#FFFFFF", size_px=28.0)), set())
        self.assertEqual(LARGE, theme.SHAPE)

    def test_font_check(self):
        colors = theme.palette(False)
        ok = label(colors["on-surface"], colors["background"])
        cases = {
            "Fredoka et Nunito": (("Fredoka", "8 "), ("Nunito", "UI")),
            "repli sur un caractère de FALLBACK": (("Nunito", "0,80 "), ("DejaVu Sans", "→"), ("Nunito", " 0,90")),
            "repli sur une espace": (("Nunito", "8"), ("DejaVu Sans", " "), ("Nunito", "UI")),
        }
        for name, runs in cases.items():
            with self.subTest(name):
                self.assertEqual(failed(LabelSample(**{**ok.__dict__, "runs": runs})), set())
        refused = {
            "police du système": (("Cantarell", "Dose du soir"),),
            "repli hors FALLBACK": (("Nunito", "8 "), ("DejaVu Sans", "€")),
            "aucun segment": (),
        }
        for name, runs in refused.items():
            with self.subTest(name):
                self.assertEqual(failed(LabelSample(**{**ok.__dict__, "runs": runs})), {"police"})
        self.assertEqual(failed(LabelSample(**{**ok.__dict__, "unknown_glyphs": 1})), {"police"})

    def test_overflow_check(self):
        colors = theme.palette(False)
        sample = label(colors["on-surface"], colors["background"], overflow="AdwPreferencesRow 376 px > 360")
        self.assertEqual(failed(sample), {"débordement"})

    def test_window_background_is_the_theme_background(self):
        for dark in (False, True):
            want = rgb(theme.palette(dark)["background"])
            self.assertTrue(assess_background("v", want, dark).ok)
            self.assertTrue(assess_background("v", tuple(c - 2 if c >= 2 else c + 2 for c in want), dark).ok)
            self.assertFalse(assess_background("v", tuple(c - 3 if c >= 3 else c + 3 for c in want), dark).ok)
        self.assertFalse(assess_background("v", (0xFA, 0xFA, 0xFA), False).ok, "fond d'Adwaita, thème non chargé")

    def test_score_and_thin_views(self):
        checks = [Check("a", "s", "contraste", "5", ">= 4.5", True), Check("a", "s", "encre", "1", ">= 3", False)]
        self.assertEqual(score(checks, {"a": 10, "b": 4}), (0.5, ["b"]))
        self.assertEqual(score([], {}), (0.0, []))

    def test_pixels_reads_rows_with_padding(self):
        width, height, stride = 3, 2, 16  # 4 octets de remplissage par ligne
        data = bytearray(stride * height)
        for y in range(height):
            for x in range(width):
                data[y * stride + x * 4: y * stride + x * 4 + 4] = bytes((x, y, 7, 255))
        px = Pixels(bytes(data), width, height, stride)
        self.assertEqual(px.at(2, 1), (2, 1, 7))
        self.assertEqual(px.box(1, 0, 2, 2), [(1, 0, 7), (2, 0, 7), (1, 1, 7), (2, 1, 7)])
        self.assertEqual(len(px.box(-5, -5, 100, 100)), width * height)
        self.assertEqual(px.box(0.5, 0.5, 0.2, 0.2), [(0, 0, 7)])

    def test_background_and_ink(self):
        white, black = (255, 255, 255), (0, 0, 0)
        background, ink = background_and_ink([white] * 8 + [black] * 2)
        self.assertEqual(background, white)
        self.assertAlmostEqual(ink, 21.0, places=2)
        self.assertEqual(background_and_ink([white] * 4), (white, 1.0))


if __name__ == "__main__":
    unittest.main()
