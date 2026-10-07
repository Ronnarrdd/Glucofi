"""Thème de Glucofi PC, repris de Glucofi pour Android (DESIGN.md et ui/Theme.kt du dépôt GlucofiAndroid).

Univers pastel : fond sauge, cartes blanches, tuiles pêche (Matin) et lavande (Soir), pastels de synthèse,
un seul accent bleu canard. Fredoka pour les titres et les chiffres, Nunito pour le texte. Schéma statique
écrit à la main en clair et en sombre : la dose et les niveaux de glycémie gardent les mêmes couleurs partout.

Ce module est la seule source des couleurs. app/style.css n'emploie que var(--glucofi-*), déclarées ici avec
les variables de libadwaita qu'elles remplacent, et les graphiques de l'écran lisent chart_palette().
Toute nouvelle paire texte sur fond entre dans CONTRAST_PAIRS (app/tests/test_theme.py).
"""

from __future__ import annotations

import logging
from pathlib import Path

from services.charts.palette import ChartPalette

log = logging.getLogger("glucofi.ui")

APP_DIR = Path(__file__).resolve().parent
FONT_DIR = APP_DIR / "fonts"
STYLE_CSS = APP_DIR / "style.css"
ICONS_DIR = APP_DIR / "icons"
DISPLAY_FONT = "Fredoka"
TEXT_FONT = "Nunito"
# caractères affichés que Fredoka et Nunito n'ont pas : la police du système les dessine (comme sur la tablette)
FALLBACK = {
    "→": "lignes de l'historique du protocole (app/protocol.py), « 0,80 → 0,90 g/L »",
}

LIGHT = {
    "primary": "#0B4A52",
    "on-primary": "#FFFFFF",
    "primary-container": "#CBE7E2",
    "on-primary-container": "#00363C",
    "inverse-primary": "#8ED3CB",
    "secondary-container": "#DCEAE5",
    "on-secondary-container": "#0E2A28",
    "tertiary": "#9A4558",
    "background": "#E8EEE6",
    "on-surface": "#1D2B2C",
    "on-surface-variant": "#4F5E5D",
    "card": "#FFFFFF",
    "surface-container-high": "#FDFDFB",
    "surface-container-highest": "#DDE5DB",
    "outline": "#7A8987",
    "outline-variant": "#D3DCD6",
    "inverse-surface": "#233233",
    "inverse-on-surface": "#EDF3EF",
    "morning-tile": "#FEDCC0",
    "morning-ink": "#3B2414",
    "morning-muted": "#6B4A33",
    "morning-dose": "#0B4A52",
    "evening-tile": "#E0D7F4",
    "evening-ink": "#241D3D",
    "evening-muted": "#4E4568",
    "evening-dose": "#0B4A52",
    "pastel-sky": "#DCEDF5",
    "pastel-mint": "#D9EFE2",
    "pastel-lavender": "#E6DFF6",
    "pastel-peach": "#FEE4CF",
    "on-pastel": "#1D2B2C",
    "level-low": "#B3261E",
    "level-low-container": "#FCDDDA",
    "level-low-on-container": "#5F1410",
    "level-in": "#1F6A45",
    "level-in-container": "#D4EFDF",
    "level-in-on-container": "#0C3B24",
    "level-high": "#8A4B00",
    "level-high-container": "#FDE3CC",
    "level-high-on-container": "#4A2800",
    # texte posé sur une couleur de niveau pleine (boutons d'erreur, barres du graphique de répartition)
    "on-level": "#FFFFFF",
}

DARK = {
    "primary": "#8ED3CB",
    "on-primary": "#003733",
    "primary-container": "#0E4E54",
    "on-primary-container": "#C6EEE8",
    "inverse-primary": "#0B4A52",
    "secondary-container": "#2C3D3B",
    "on-secondary-container": "#D3E6E1",
    "tertiary": "#FFB1C0",
    "background": "#101819",
    "on-surface": "#E3ECE9",
    "on-surface-variant": "#B5C4C1",
    "card": "#1A2423",
    "surface-container-high": "#222D2C",
    "surface-container-highest": "#2F3A39",
    "outline": "#8A9A97",
    "outline-variant": "#34403F",
    "inverse-surface": "#E3ECE9",
    "inverse-on-surface": "#233233",
    "morning-tile": "#4A3324",
    "morning-ink": "#FFE3CF",
    "morning-muted": "#E9C3A8",
    "morning-dose": "#FFCFA8",
    "evening-tile": "#342D4D",
    "evening-ink": "#E6DEFF",
    "evening-muted": "#C9BFEA",
    "evening-dose": "#D3C6FF",
    "pastel-sky": "#1F3A47",
    "pastel-mint": "#1E3E30",
    "pastel-lavender": "#332C4A",
    "pastel-peach": "#45301F",
    "on-pastel": "#E3ECE9",
    "level-low": "#FFB4AB",
    "level-low-container": "#6B1C17",
    "level-low-on-container": "#FFDAD6",
    "level-in": "#8ED6AE",
    "level-in-container": "#164A31",
    "level-in-on-container": "#CBF3DC",
    "level-high": "#FFBE7A",
    "level-high-container": "#5A3500",
    "level-high-on-container": "#FFDDBF",
    "on-level": "#101819",
}

# variable de libadwaita -> jeton : les widgets standard (fenêtre, cartes, listes, dialogues, accent,
# couleurs d'état) prennent le thème sans règle propre. Les niveaux de glycémie remplacent erreur,
# réussite et avertissement : une valeur marquée .error, .success ou .warning a la couleur de son niveau.
ADWAITA = {
    "window-bg-color": "background",
    "window-fg-color": "on-surface",
    "view-bg-color": "card",
    "view-fg-color": "on-surface",
    "headerbar-bg-color": "background",
    "headerbar-fg-color": "on-surface",
    "headerbar-border-color": "on-surface",
    "headerbar-backdrop-color": "background",
    "card-bg-color": "card",
    "card-fg-color": "on-surface",
    "card-shade-color": "outline-variant",
    "dialog-bg-color": "surface-container-high",
    "dialog-fg-color": "on-surface",
    "popover-bg-color": "surface-container-high",
    "popover-fg-color": "on-surface",
    "accent-bg-color": "primary",
    "accent-fg-color": "on-primary",
    "accent-color": "primary",
    "active-toggle-bg-color": "primary",
    "active-toggle-fg-color": "on-primary",
    "destructive-bg-color": "level-low",
    "destructive-fg-color": "on-level",
    "destructive-color": "level-low",
    "error-bg-color": "level-low",
    "error-fg-color": "on-level",
    "error-color": "level-low",
    "success-bg-color": "level-in",
    "success-fg-color": "on-level",
    "success-color": "level-in",
    "warning-bg-color": "level-high",
    "warning-fg-color": "on-level",
    "warning-color": "level-high",
}
# plat comme la tablette : la barre du haut se fond dans le fond sauge, sans filet ni ombre
ADWAITA_FIXED = {"headerbar-shade-color": "transparent"}

# tuiles de synthèse de l'onglet Mesures, dans l'ordre de la tablette : Moyenne, Dans l'objectif,
# Hypoglycémies, Avec marqueur
SUMMARY_PASTELS = ("pastel-sky", "pastel-mint", "pastel-lavender", "pastel-peach")
LEVEL_KEYS = ("low", "in", "high")

TEXT = 4.5
SHAPE = 3.0

# (texte ou forme, fond, seuil WCAG) vérifiés en clair et en sombre par app/tests/test_theme.py
CONTRAST_PAIRS = [
    *[("on-surface", bg, TEXT) for bg in ("background", "card", "surface-container-high")],
    *[("on-surface-variant", bg, TEXT) for bg in ("background", "card", "surface-container-high", *SUMMARY_PASTELS)],
    ("on-primary", "primary", TEXT),
    *[("primary", bg, TEXT) for bg in ("background", "card", "surface-container-high")],
    ("on-primary-container", "primary-container", TEXT),
    ("on-secondary-container", "secondary-container", TEXT),
    ("inverse-on-surface", "inverse-surface", TEXT),
    ("inverse-primary", "inverse-surface", TEXT),
    *[(f"{moment}-{ink}", f"{moment}-tile", TEXT) for moment in ("morning", "evening") for ink in ("ink", "muted", "dose")],
    *[("on-surface", f"{moment}-tile", TEXT) for moment in ("morning", "evening")],
    *[("on-pastel", bg, TEXT) for bg in SUMMARY_PASTELS],
    *[(f"level-{level}", bg, TEXT) for level in LEVEL_KEYS for bg in ("background", "card", "surface-container-high")],
    *[(f"level-{level}-on-container", f"level-{level}-container", TEXT) for level in LEVEL_KEYS],
    *[(f"level-{level}", f"level-{level}-container", TEXT) for level in LEVEL_KEYS],
    *[("on-level", f"level-{level}", TEXT) for level in LEVEL_KEYS],
    # formes : bouton plein, contour des champs et de l'icône de note, points et segments des graphiques
    *[("primary", bg, SHAPE) for bg in ("morning-tile", "evening-tile")],
    *[("outline", bg, SHAPE) for bg in ("background", "card", "surface-container-high")],
    ("tertiary", "card", SHAPE),
    *[(f"level-{level}", "surface-container-highest", SHAPE) for level in LEVEL_KEYS],
]


def palette(dark: bool) -> dict[str, str]:
    return DARK if dark else LIGHT


def tokens_css(dark: bool) -> str:
    """Bloc :root des couleurs du thème : les --glucofi-* et les variables de libadwaita qu'elles remplacent."""
    colors = palette(dark)
    lines = [f"  --glucofi-{name}: {value};" for name, value in colors.items()]
    lines += [f"  --{name}: {colors[token]};" for name, token in ADWAITA.items()]
    lines += [f"  --{name}: {value};" for name, value in ADWAITA_FIXED.items()]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


# Contraste WCAG 2.x

def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    h = color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# Graphiques de l'écran (le PDF garde services.charts.palette.PDF_PALETTE)

def chart_palette(dark: bool) -> ChartPalette:
    c = palette(dark)
    return ChartPalette(
        low=c["level-low"],
        in_range=c["level-in"],
        high=c["level-high"],
        band=c["level-in"],
        band_alpha=0.14 if not dark else 0.12,
        hypo=c["level-low"],
        line=c["primary"],
        morning=c["tertiary"],
        dose=c["primary"],
        muted=c["on-surface-variant"],
        point_edge=c["card"],
        annotation_bg=c["card"],
        bar_text=c["on-level"],
        box_face=c["primary-container"],
        box_alpha=1.0,
        median=c["on-surface"],
        background=c["card"],
        text=c["on-surface-variant"],
        title=c["on-surface"],
        edge=c["outline-variant"],
        grid=c["outline-variant"],
        grid_alpha=1.0,
        grid_below=True,
        # DejaVu Sans, livrée avec matplotlib, pour un signe absent du sous-ensemble de Nunito
        fonts=(TEXT_FONT, "DejaVu Sans"),
    )


# Polices et installation sur l'écran

def font_files() -> list[Path]:
    return sorted(FONT_DIR.glob("*.ttf"))


_fonts_loaded: bool | None = None


def load_fonts() -> bool:
    """Fredoka et Nunito pour Pango, donc pour GTK, sans les installer dans le système. Une fois par processus."""
    global _fonts_loaded
    if _fonts_loaded is not None:
        return _fonts_loaded
    import gi

    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo

    font_map = PangoCairo.FontMap.get_default()
    if not hasattr(font_map, "add_font_file"):
        log.warning("Pango antérieur à 1.56 : Fredoka et Nunito non chargées, police du système à la place")
        _fonts_loaded = False
        return False
    files = font_files()
    loaded = [path for path in files if font_map.add_font_file(str(path))]
    for path in sorted(set(files) - set(loaded)):
        log.warning("police illisible, police du système à la place : %s", path)
    _fonts_loaded = bool(files) and len(loaded) == len(files)
    if not files:
        log.warning("aucune police dans %s : police du système à la place", FONT_DIR)
    return _fonts_loaded


def register_chart_fonts() -> None:
    """Nunito pour matplotlib (graphiques de l'écran). Sans effet si déjà fait."""
    from matplotlib import font_manager

    known = {entry.fname for entry in font_manager.fontManager.ttflist}
    for path in font_files():
        if str(path) not in known:
            font_manager.fontManager.addfont(str(path))


_installed: dict[int, object] = {}


def install(display) -> None:
    """Polices, feuille de style et couleurs du thème sur `display` ; les couleurs suivent le passage clair/sombre.

    Les couleurs sont rechargées sur Adw.StyleManager:dark plutôt que par @media (prefers-color-scheme) :
    même rendu sur GTK 4.18 (Debian 13), où libadwaita 1.7 suffit à Glucofi.
    """
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    if hash(display) in _installed:
        return
    load_fonts()
    manager = Adw.StyleManager.get_for_display(display)
    tokens = Gtk.CssProvider()
    tokens.load_from_string(tokens_css(manager.get_dark()))
    style = Gtk.CssProvider()
    style.load_from_path(str(STYLE_CSS))
    for provider in (tokens, style):
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    manager.connect("notify::dark", lambda m, _p: tokens.load_from_string(tokens_css(m.get_dark())))
    Gtk.IconTheme.get_for_display(display).add_search_path(str(ICONS_DIR))
    _installed[hash(display)] = (tokens, style)


def installed_providers(display) -> tuple | None:
    """(couleurs, feuille de style) installés sur `display`, pour les tests."""
    return _installed.get(hash(display))
