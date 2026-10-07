"""Eval du thème de la tablette : à l'écran, chaque texte est lisible, dans ses polices, sans débordement.

Rend l'application de démonstration (scripts.screenshots, scénario « titration » : deux propositions de
dose, notes, historique du protocole) dans un compositeur sans écran, en clair et en sombre, à 1000 et
360 px de large : onglets Aujourd'hui, Graphiques, Mesures, Doses et dialogue Préférences.

Pour chaque libellé visible :
- contraste WCAG entre sa couleur (CSS, opacité des widgets comprise) et le fond réellement peint derrière
  lui (couleur majoritaire des pixels de son cadre) : 4,5 pour le texte courant, 3 pour le grand texte
  (24 px, ou 18,66 px en gras) ;
- encre : le meilleur pixel du texte atteint au moins ENCRE_MIN de ce contraste, ce qui attrape une
  transparence CSS (opacity) que Gtk.Widget.get_color() ne montre pas ;
- polices résolues par Pango, segment par segment : Fredoka ou Nunito (sauf les caractères de
  app.theme.FALLBACK), aucun glyphe inconnu ;
- débordement : le libellé et chacun de ses parents tiennent dans la largeur de la fenêtre.
Pour chaque onglet, le fond de la fenêtre est celui du thème (sauge en clair).

Seuil de réussite : 100 %. Détail de chaque vérification dans /tmp/glucofi-eval/theme_render.csv.

Usage :
    python3 -m evals.theme_render             # relancé dans scripts/headless.sh (aucune fenêtre sur le bureau)
    python3 -m evals.theme_render --display   # sur l'écran courant
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app import theme
from scripts import headless

OUT_DIR = Path("/tmp/glucofi-eval")
SCHEMES = (("clair", False), ("sombre", True))
WIDTHS = (1000, 360)
VIEWS = ("today", "charts", "measures", "doses", "preferences")
SCENARIO = "titration"
FONTS = {theme.DISPLAY_FONT, theme.TEXT_FONT}
TEXT, LARGE = theme.TEXT, theme.SHAPE
# part du contraste annoncé que les pixels du texte doivent atteindre ; le rendu du thème atteint 0,96 au
# plus bas (anticrénelage compris), l'opacité 0,55 d'Adwaita sur .dim-label tombe à 0,36-0,39
ENCRE_MIN = 0.85
# libellés vérifiés au minimum par vue : en dessous, l'eval n'a pas vu l'écran qu'elle croit voir
MIN_LABELS = 5
# la barre d'en-tête d'Adwaita déborde d'1 px par construction (marges négatives de sa CenterBox)
OVERFLOW_TOLERANCE = 2
# sous cette opacité, un libellé est caché (fondu d'Adw.EntryRow, qui superpose deux titres)
HIDDEN_ALPHA = 0.05

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class LabelSample:
    """Ce que l'eval relève d'un libellé rendu (séparé de GTK pour les tests)."""

    view: str
    text: str
    css: str
    color: tuple[float, float, float, float]  # couleur CSS (0..1), alpha multiplié par l'opacité des widgets
    background: RGB  # couleur majoritaire des pixels du cadre du libellé
    ink: float  # meilleur contraste atteint par un pixel du cadre contre ce fond
    size_px: float
    weight: int
    runs: tuple[tuple[str, str], ...]  # (famille résolue, texte du segment)
    unknown_glyphs: int
    overflow: str  # widget (le libellé ou un parent) qui sort de la fenêtre, "" sinon


@dataclass(frozen=True)
class Check:
    view: str
    subject: str
    check: str
    value: str
    threshold: str
    ok: bool


def hex_color(rgb: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def over(color: tuple[float, float, float, float], background: RGB) -> RGB:
    """Couleur `color` (alpha compris) posée sur `background`."""
    r, g, b, a = color
    return tuple(round(c * 255 * a + bg * (1 - a)) for c, bg in zip((r, g, b), background))


def is_large(size_px: float, weight: int) -> bool:
    return size_px >= 24 or (size_px >= 18.66 and weight >= 700)


def assess_label(s: LabelSample) -> list[Check]:
    subject = f"{s.css or 'label'} « {s.text[:40]} »"
    need = LARGE if is_large(s.size_px, s.weight) else TEXT
    shown = over(s.color, s.background)
    ratio = theme.contrast(hex_color(shown), hex_color(s.background))
    checks = [Check(s.view, subject, "contraste", f"{ratio:.2f} ({hex_color(shown)} sur {hex_color(s.background)})",
                    f">= {need}", ratio >= need)]
    floor = ratio * ENCRE_MIN
    checks.append(Check(s.view, subject, "encre", f"{s.ink:.2f}", f">= {floor:.2f}", s.ink >= floor))
    foreign = sorted({family for family, text in s.runs
                      if family not in FONTS and any(c not in theme.FALLBACK and not c.isspace() for c in text)})
    fonts_ok = bool(s.runs) and not foreign and s.unknown_glyphs == 0
    found = ", ".join(sorted({family for family, _ in s.runs}))
    detail = found + (f" ; {s.unknown_glyphs} glyphe(s) inconnu(s)" if s.unknown_glyphs else "")
    checks.append(Check(s.view, subject, "police", detail, "Fredoka ou Nunito", fonts_ok))
    checks.append(Check(s.view, subject, "débordement", s.overflow or "aucun", "aucun", not s.overflow))
    return checks


def assess_background(view: str, pixel: RGB, dark: bool) -> Check:
    want = theme.palette(dark)["background"].upper()
    got = hex_color(pixel)
    ok = all(abs(a - b) <= 2 for a, b in zip(pixel, (int(want[i:i + 2], 16) for i in (1, 3, 5))))
    return Check(view, "fenêtre", "fond", got, want, ok)


def score(checks: list[Check], labels_per_view: dict[str, int]) -> tuple[float, list[str]]:
    """Part des vérifications réussies, et les vues où trop peu de libellés ont été vus."""
    thin = [view for view, n in labels_per_view.items() if n < MIN_LABELS]
    passed = sum(c.ok for c in checks)
    return (passed / len(checks) if checks else 0.0), thin


# Pixels

_LINEAR = [((v / 255) / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4) for v in range(256)]


def _lum(px) -> float:
    return 0.2126 * _LINEAR[px[0]] + 0.7152 * _LINEAR[px[1]] + 0.0722 * _LINEAR[px[2]]


class Pixels:
    """Image RGBA 8 bits d'une fenêtre rendue."""

    def __init__(self, data: bytes, width: int, height: int, stride: int):
        self.data, self.width, self.height, self.stride = data, width, height, stride

    def at(self, x: int, y: int) -> RGB:
        i = y * self.stride + x * 4
        return tuple(self.data[i:i + 3])

    def box(self, x: float, y: float, w: float, h: float) -> list[RGB]:
        x0, y0 = max(0, int(x)), max(0, int(y))
        x1, y1 = min(self.width, int(x + w + 0.999)), min(self.height, int(y + h + 0.999))
        out = []
        for row in range(y0, y1):
            base = row * self.stride
            line = self.data[base + x0 * 4: base + x1 * 4]
            out += [tuple(line[i:i + 3]) for i in range(0, len(line), 4)]
        return out


def background_and_ink(pixels: list[RGB]) -> tuple[RGB, float]:
    background = Counter(pixels).most_common(1)[0][0]
    lb = _lum(background)
    best = 1.0
    for px in set(pixels):
        lp = _lum(px)
        hi, lo = (lp, lb) if lp > lb else (lb, lp)
        best = max(best, (hi + 0.05) / (lo + 0.05))
    return background, best


# GTK

def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _ancestors(widget, root):
    parent = widget.get_parent()
    while parent is not None and parent is not root:
        yield parent
        parent = parent.get_parent()


def _bounds(widget, window):
    ok, rect = widget.compute_bounds(window)
    if not ok:
        return None
    return rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()


def _visible(label, root, window) -> bool:
    """Entièrement à l'écran : dans la fenêtre et dans la zone visible (verticale et horizontale) de chaque fenêtre
    défilante parente."""
    from gi.repository import Gtk

    box = _bounds(label, window)
    if box is None or box[2] < 1 or box[3] < 1:
        return False
    x, y, w, h = box
    if y < 0 or y + h > window.get_height() + 0.5:
        return False
    for parent in _ancestors(label, None):
        if isinstance(parent, Gtk.ScrolledWindow):
            clip = _bounds(parent, window)
            if clip is None or y < clip[1] - 0.5 or y + h > clip[1] + clip[3] + 0.5:
                return False
            # rangée de filtres qui défile à l'horizontale : les pilules hors de la zone visible ne sont pas peintes
            if x < clip[0] - 0.5 or x + w > clip[0] + clip[2] + 0.5:
                return False
    return True


def _overflow(label, window) -> str:
    from gi.repository import Gtk

    width = window.get_width()
    chain = [label, *_ancestors(label, window)]
    # contenu d'une rangée qui défile à l'horizontale (filtres) : seule la fenêtre défilante doit tenir
    for index, widget in enumerate(chain):
        if isinstance(widget, Gtk.ScrolledWindow) and widget.get_policy()[0] != Gtk.PolicyType.NEVER:
            chain = chain[index:]
            break
    for widget in chain:
        box = _bounds(widget, window)
        if box is not None and box[2] > 0 and (
            box[0] < -OVERFLOW_TOLERANCE or box[0] + box[2] > width + OVERFLOW_TOLERANCE
        ):
            css = " ".join(widget.get_css_classes())
            return f"{type(widget).__name__} {css} x={box[0]:.0f} largeur={box[2]:.0f} (fenêtre {width})".strip()
    return ""


def _opacity(label, window) -> float:
    alpha = label.get_opacity()
    for parent in _ancestors(label, window):
        alpha *= parent.get_opacity()
    return alpha


def _runs(label) -> tuple[tuple[tuple[str, str], ...], float, int]:
    from gi.repository import Pango

    layout = label.get_layout()
    text = layout.get_text().encode("utf-8")
    it = layout.get_iter()
    runs, size, weight = [], 0.0, 400
    while True:
        run = it.get_run_readonly()
        if run is not None:
            desc = run.item.analysis.font.describe_with_absolute_size()
            chunk = text[run.item.offset: run.item.offset + run.item.length].decode("utf-8", "replace")
            runs.append((desc.get_family(), chunk))
            size = max(size, desc.get_size() / Pango.SCALE)
            weight = max(weight, int(desc.get_weight()))
        if not it.next_run():
            break
    return tuple(runs), size, weight


def sample_labels(view: str, window, root, pixels: Pixels) -> list[LabelSample]:
    from gi.repository import Gtk

    samples = []
    for widget in _walk(root):
        if not isinstance(widget, Gtk.Label) or not widget.is_drawable() or not widget.get_text().strip():
            continue
        alpha = _opacity(widget, window)
        if alpha < HIDDEN_ALPHA or not _visible(widget, root, window):
            continue
        x, y, w, h = _bounds(widget, window)
        background, ink = background_and_ink(pixels.box(x, y, w, h))
        rgba = widget.get_color()
        runs, size, weight = _runs(widget)
        samples.append(LabelSample(
            view=view,
            text=" ".join(widget.get_text().split()),
            css=" ".join(widget.get_css_classes()),
            color=(rgba.red, rgba.green, rgba.blue, rgba.alpha * alpha),
            background=background,
            ink=ink,
            size_px=size,
            weight=weight,
            runs=runs,
            unknown_glyphs=widget.get_layout().get_unknown_glyphs_count(),
            overflow=_overflow(widget, window),
        ))
    return samples


def capture(window) -> Pixels | None:
    from gi.repository import Gdk

    from scripts.screenshots import render

    texture = render(window)
    if texture is None:
        return None
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    return Pixels(data.get_data(), texture.get_width(), texture.get_height(), stride)


def run_app(views: list[tuple[str, bool, int, str]]) -> tuple[list[Check], dict[str, int]]:
    """Rend chaque (thème, sombre, largeur, vue) et relève ses vérifications."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, GLib

    from app.main import APP_ID, install_style
    from scripts.screenshots import HEIGHT, demo_export_file, demo_state, set_dark, show

    state = demo_state(demo_export_file(), SCENARIO)
    checks: list[Check] = []
    labels: dict[str, int] = {}
    queue = list(views)
    app = Adw.Application(application_id=APP_ID + ".ThemeEval")
    # pendant un redimensionnement, avant que le point de rupture n'applique les libellés courts, Adwaita
    # signale un débordement passager : l'état stable est vérifié par libellé, ces avertissements sont comptés
    transient: list[str] = []
    GLib.log_set_handler("Adwaita", GLib.LogLevelFlags.LEVEL_WARNING, lambda _d, _l, message, *_: transient.append(message))

    def on_activate(application):
        from app.window import MainWindow

        install_style(Gdk.Display.get_default())
        application.hold()
        window = MainWindow(application=application, state=state)
        window.set_default_size(WIDTHS[0], HEIGHT)
        window.present()

        def measure(name, dark, view, attempt=0):
            pixels = capture(window)
            if pixels is None and attempt < 20:
                GLib.timeout_add(300, measure, name, dark, view, attempt + 1)
                return False
            dialog = window.get_visible_dialog()
            if pixels is None:
                checks.append(Check(name, "fenêtre", "rendu", "vide après 6 s", "image", False))
            else:
                # une exception dans ce rappel GLib laisserait l'application tenue : l'eval attendrait sans fin
                try:
                    root = dialog if view == "preferences" and dialog is not None else window
                    samples = sample_labels(name, window, root, pixels)
                    labels[name] = len(samples)
                    for sample in samples:
                        checks.extend(assess_label(sample))
                    if view != "preferences":
                        checks.append(assess_background(name, pixels.at(4, window.get_height() // 2), dark))
                except Exception as exc:  # noqa: BLE001
                    checks.append(Check(name, "eval", "exception", repr(exc), "aucune", False))
            if dialog is not None:
                dialog.force_close()
            if queue:
                GLib.timeout_add(300, step)
            else:
                application.release()
                application.quit()
            return False

        def step():
            scheme, dark, width, view = queue.pop(0)
            name = f"{scheme}-{width}-{view}"
            set_dark(dark)
            window.set_default_size(width, HEIGHT)
            show(window, view)
            GLib.timeout_add(1200, measure, name, dark, view)
            return False

        GLib.timeout_add(1200, step)

    app.connect("activate", on_activate)
    app.run([])
    if transient:
        print(f"{len(transient)} avertissement(s) Adwaita pendant les redimensionnements, ex. : {transient[0]}")
    return checks, labels


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--display", action="store_true", help="rendre sur l'écran courant plutôt que sans écran")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    if not args.display and not headless.in_headless():
        headless.reexec("evals.theme_render", argv)
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/glucofi-mpl")

    views = [(scheme, dark, width, view) for scheme, dark in SCHEMES for width in WIDTHS for view in VIEWS]
    checks, labels = run_app(views)
    ratio, thin = score(checks, labels)
    failures = [c for c in checks if not c.ok]

    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / "theme_render.csv"
    with report.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["vue", "element", "verification", "mesure", "seuil", "ok"])
        for c in checks:
            writer.writerow([c.view, c.subject, c.check, c.value, c.threshold, "oui" if c.ok else "NON"])

    print(f"{len(labels)} vues, {sum(labels.values())} libellés, {len(checks)} vérifications, "
          f"{len(failures)} échec(s), score {ratio:.1%} (seuil 100 %)")
    print(f"détail : {report}")
    for view in thin:
        print(f"  {view} : {labels[view]} libellé(s) vu(s), moins de {MIN_LABELS}", file=sys.stderr)
    for c in failures:
        print(f"  {c.view} {c.subject} : {c.check} {c.value} (attendu {c.threshold})", file=sys.stderr)
    return 0 if checks and not failures and not thin and len(labels) == len(views) else 1


if __name__ == "__main__":
    sys.exit(main())
