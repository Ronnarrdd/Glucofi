"""Onglet Graphiques (propre au PC) : titre, puces de durée, cinq tuiles pastel, puis chaque graphique dans sa carte.

Les graphiques sont des images matplotlib aux couleurs du thème (theme.chart_palette), refaites au passage
clair/sombre et seulement quand l'onglet est visible : construire trois figures coûte une demi-seconde.
"""

from __future__ import annotations

import importlib.util
import logging

from gi.repository import Adw, Gdk, GLib, Gtk

from app import theme
from app.components import FilterChips, Notice, Page, Section, clear, label, screen_title
from app.state import AppState

log = logging.getLogger("glucofi.ui")

HAS_MPL = importlib.util.find_spec("matplotlib") is not None
CHART_DPI = 200
CHART_WIDTH_PX = 860
CHART_PERIODS = (("14", "14 jours"), ("30", "30 jours"), ("90", "90 jours"))
STAT_PASTELS = ("pastel-sage", "pastel-sky", "pastel-mint", "pastel-lavender", "pastel-peach")


def stat_tile(title: str, value: str, tone: str) -> Gtk.Widget:
    tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    tile.add_css_class("stat-tile")
    tile.add_css_class(tone)
    tile.append(label(title, "title-small", xalign=0, wrap=True))
    tile.append(label(value, "headline-large", xalign=0))
    return tile


class ChartsPage:
    def __init__(self, state: AppState, days: str = "30"):
        self.state = state
        self.days = days
        self.dirty = True
        self.page = Page()
        self.widget = self.page
        self.page.append(screen_title("Graphiques"))
        self.chips = FilterChips(CHART_PERIODS, days, self._on_period, name="Période")
        self.page.append(self.chips)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.page.append(self.body)

    def narrow_setters(self, breakpoint: Adw.Breakpoint) -> None:
        self.page.narrow_setters(breakpoint)

    def _on_period(self, days: str) -> None:
        self.days = days
        self.build()

    def build(self) -> None:
        self.dirty = False
        clear(self.body)
        if not HAS_MPL:
            self.body.append(Notice("Graphiques indisponibles : installez matplotlib (paquet python3-matplotlib).", "warning"))
            return
        try:
            self._fill()
        except Exception as exc:  # noqa: BLE001 - un graphique cassé ne doit pas rester silencieux
            log.exception("échec de l'affichage des graphiques")
            clear(self.body)
            self.body.append(Notice(f"Graphiques indisponibles : {exc}", "danger"))

    def _fill(self) -> None:
        from app.charts_data import chart_data, chart_figures
        from services.charts.figures import figure_png

        data = chart_data(self.state, int(self.days))
        if data is None:
            self.body.append(Notice("Saisissez le protocole pour voir les graphiques par rapport à l'objectif.", "info"))
            return
        tiles = Gtk.FlowBox(
            homogeneous=True, min_children_per_line=2, max_children_per_line=5,
            selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12,
        )
        tiles.add_css_class("summary-tiles")
        for (title, value), tone in zip(data.tiles, STAT_PASTELS):
            tiles.append(Gtk.FlowBoxChild(child=stat_tile(title, value, tone), focusable=False))
        self.body.append(tiles)

        theme.register_chart_fonts()
        palette = theme.chart_palette(Adw.StyleManager.get_default().get_dark())
        for title, fig in chart_figures(data, palette):
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(figure_png(fig, dpi=CHART_DPI)))
            picture = Gtk.Picture(paintable=texture, content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True)
            picture.set_alternative_text(title)
            fig_w, fig_h = fig.get_size_inches()
            picture.set_size_request(-1, round(CHART_WIDTH_PX * fig_h / fig_w))
            section = Section(title)
            section.append(picture)
            self.body.append(section)
