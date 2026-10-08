"""Onglet Mesures, composé comme sur la tablette (MeasuresScreen.kt) : titre, trois rangées de filtres en pilules,
tuiles de synthèse pastel, carte de répartition, puis une carte blanche par jour.

Toute la mise en forme (textes, niveaux, parts de la barre) vient de app.measures ; ce module ne fait
qu'assembler les widgets. Une ligne entière ouvre la note de sa mesure.
"""

from __future__ import annotations

import itertools
from typing import Callable

from gi.repository import Adw, GLib, Gtk

from app.components import (
    ARROWS,
    FilterChips,
    LevelChip,
    LevelValue,
    Notice,
    Page,
    Section,
    button,
    clear,
    divider,
    heading,
    label,
    screen_title,
    tag,
)
from app.dialogs import note_dialog
from app.measures import (
    EXCLUDED_TOOLTIP,
    MEAL_FILTERS,
    REFUSED_TOOLTIP,
    MeasureDay,
    MeasureLine,
    MeasureSummary,
    measure_view,
)
from app.state import AppState
from app.theme import SUMMARY_PASTELS
from contracts import Reading
from services.charts import PERIODS

DAY_FILTERS = (("7", "7 jours"), ("14", "14 jours"), ("30", "30 jours"), ("90", "90 jours"), ("all", "Tout"))
PERIOD_FILTERS = (("all", "Tous moments"),) + tuple((p, p) for p in PERIODS)


class MeasuresPage:
    def __init__(self, state: AppState, on_changed: Callable[[], None] | None = None):
        """`on_changed` après l'enregistrement d'une note (la proposition de dose peut changer), sinon refresh."""
        self.state = state
        self.on_changed = on_changed or self.refresh
        self.days = "90"
        self.period = "all"
        self.meal = "all"
        self.narrow = False
        self._resetting = False

        self.page = Page()
        self.widget = self.page
        self.page.append(screen_title("Mesures"))
        filters = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.days_chips = FilterChips(DAY_FILTERS, self.days, self._on_days, name="Durée")
        self.period_chips = FilterChips(PERIOD_FILTERS, self.period, self._on_period, name="Moment")
        self.meal_chips = FilterChips(MEAL_FILTERS, self.meal, self._on_meal, name="Marqueur")
        for row in (self.days_chips, self.period_chips, self.meal_chips):
            filters.append(row)
        self.page.append(filters)
        self.summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.page.append(self.summary_box)
        self.days_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.page.append(self.days_box)

    # Filtres

    def narrow_setters(self, breakpoint: Adw.Breakpoint) -> None:
        """Fenêtre étroite : deux tuiles par ligne et la pastille Matin sous le titre du jour."""
        self.page.narrow_setters(breakpoint)
        breakpoint.connect("apply", lambda *_a: self._set_narrow(True))
        breakpoint.connect("unapply", lambda *_a: self._set_narrow(False))

    def _set_narrow(self, narrow: bool) -> None:
        if narrow != self.narrow:
            self.narrow = narrow
            self.refresh()

    def _on_days(self, key: str) -> None:
        self.days = key
        self._changed()

    def _on_period(self, key: str) -> None:
        self.period = key
        self._changed()

    def _on_meal(self, key: str) -> None:
        self.meal = key
        self._changed()

    def _changed(self) -> None:
        if not self._resetting:
            self.refresh()

    def reset_filters(self) -> None:
        self._resetting = True
        self.days_chips.set_active("all")
        self.period_chips.set_active("all")
        self.meal_chips.set_active("all")
        self._resetting = False
        self.refresh()

    # Contenu

    def refresh(self) -> None:
        clear(self.summary_box)
        clear(self.days_box)
        settings = self.state.settings
        if settings is None:
            self.days_box.append(Notice("Saisissez le protocole pour voir les mesures par rapport à l'objectif.", "info"))
            return
        days = None if self.days == "all" else int(self.days)
        view = measure_view(
            self.state.readings(days=days), settings, self.period, self.meal, self.state.meters(), missed=self.state.missed()
        )
        if not view.days:
            self.days_box.append(self._empty_state())
            return
        self._summary(view.summary)
        for day in view.days:
            self.days_box.append(self._day_card(day))

    def _empty_state(self) -> Gtk.Widget:
        if self.state.store.count_readings() == 0:
            return Notice(
                "Aucune mesure : branchez le lecteur Accu-Chek en USB, puis récupérez les mesures.", "info",
                trailing=button("Récupérer les mesures", icon="glucofi-sync-symbolic", action_name="win.fetch"),
            )
        return Notice(
            "Aucune mesure pour ces filtres.", "info",
            trailing=button("Afficher toutes les mesures", kind="tonal", on_click=self.reset_filters),
        )

    def _summary(self, summary: MeasureSummary) -> None:
        """Tuiles pastel de même largeur posées sur le fond (quatre par ligne, deux en étroit), puis la répartition."""
        per_line = 2 if self.narrow else 4
        tiles = list(zip(summary.tiles, itertools.cycle(SUMMARY_PASTELS)))
        for start in range(0, len(tiles), per_line):
            row = Gtk.Box(spacing=12, homogeneous=True)
            for tile, tone in tiles[start:start + per_line]:
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
                box.add_css_class("stat-tile")
                box.add_css_class(tone)
                box.append(label(tile.label, "title-small", xalign=0, wrap=True))
                box.append(label(tile.value, "headline-large", xalign=0))
                box.append(label(tile.detail, "body-medium", xalign=0, wrap=True))
                row.append(box)
            self.summary_box.append(row)
        if summary.count:
            self.summary_box.append(self._distribution(summary))

    @staticmethod
    def _distribution(summary: MeasureSummary) -> Gtk.Widget:
        card = Section()
        # écart de 3 px porté par les segments : un column_spacing compterait 99 écarts dans la largeur minimale
        bar = Gtk.Grid(column_homogeneous=True, overflow=Gtk.Overflow.HIDDEN)
        bar.add_css_class("range-bar")
        column = 0
        shown = [segment for segment in summary.segments if segment.span]
        for index, segment in enumerate(shown):
            piece = Gtk.Box(hexpand=True, tooltip_text=segment.legend, margin_end=3 if index < len(shown) - 1 else 0)
            piece.add_css_class("range-segment")
            piece.add_css_class(f"level-{segment.level}")
            bar.attach(piece, column, 0, segment.span, 1)
            column += segment.span
        card.append(bar)
        legend = Adw.WrapBox(child_spacing=20, line_spacing=4)
        for segment in summary.segments:
            item = Gtk.Box(spacing=6)
            if segment.level in ARROWS:
                arrow = Gtk.Image(icon_name=ARROWS[segment.level], pixel_size=18, accessible_role=Gtk.AccessibleRole.PRESENTATION)
                arrow.add_css_class(f"level-{segment.level}")
                arrow.add_css_class("level-ink")
                item.append(arrow)
            else:
                dot = Gtk.Box(valign=Gtk.Align.CENTER)
                dot.add_css_class("legend-dot")
                dot.add_css_class(f"level-{segment.level}")
                item.append(dot)
            item.append(label(segment.legend, "body-medium", wrap=True, xalign=0))
            legend.append(item)
        card.append(legend)
        return card

    def _day_card(self, day: MeasureDay) -> Gtk.Widget:
        card = Section()
        head = Gtk.Box(spacing=12, orientation=Gtk.Orientation.VERTICAL if self.narrow else Gtk.Orientation.HORIZONTAL)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        titles.append(heading(day.title, "title-large"))
        titles.append(label(" · ".join(p for p in (day.detail, day.count_text) if p), "body-medium", "muted", xalign=0, wrap=True))
        head.append(titles)
        if day.morning is not None:
            head.append(LevelChip(f"Matin {day.morning.value}", day.morning.level, day.morning.level_label))
        card.append(head)
        for index, line in enumerate(day.lines):
            if index:
                card.append(divider())
            card.append(self._line_row(line))
        return card

    def edit_note(self, reading: Reading) -> None:
        note_dialog(self.widget, self.state, reading, self._note_saved)

    def _note_saved(self) -> None:
        """Reconstruit les onglets sans perdre la position de défilement de la liste."""
        adjustment = self.widget.get_vadjustment()
        position = adjustment.get_value()
        self.on_changed()
        GLib.idle_add(lambda: adjustment.set_value(position) and False)

    def _line_row(self, line: MeasureLine) -> Gtk.Widget:
        row = Gtk.Box(spacing=12)
        icon = Gtk.Image(icon_name=line.marker.icon, pixel_size=22, valign=Gtk.Align.CENTER, tooltip_text=line.marker.label)
        icon.add_css_class("muted")
        row.append(icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(label(line.time, "body-large", "numeric", xalign=0))
        texts.append(label(line.subtitle, "body-medium", "muted", xalign=0, wrap=True))
        if line.note:
            texts.append(label(line.note, "body-medium", "muted", xalign=0, wrap=True))
        if line.excluded_why:
            texts.append(label(f"Écartée : {line.excluded_why}", "body-medium", "muted", xalign=0, wrap=True))
        row.append(texts)
        if line.excluded:
            row.append(tag("Écartée", tooltip=EXCLUDED_TOOLTIP))
        elif line.exclusion_refused:
            row.append(tag("Comptée", tooltip=REFUSED_TOOLTIP))
        elif line.retained:
            row.append(tag("Retenue", tooltip=line.retained_tooltip))
        row.append(LevelValue(line.value, line.level, f"{line.level_label} · {line.mg}", dimmed=line.excluded))

        widget = Gtk.Button(child=row, tooltip_text=line.note_tooltip)
        widget.add_css_class("list-row")
        description = ", ".join(
            part for part in (
                line.time, line.value, line.marker.label, line.level_label,
                line.retained_tooltip if line.retained else None,
                EXCLUDED_TOOLTIP if line.excluded else None,
                f"Note : {line.note}" if line.note else None,
                f"Écartée : {line.excluded_why}" if line.excluded_why else None,
            ) if part
        )
        widget.update_property([Gtk.AccessibleProperty.LABEL], [description])
        widget.connect("clicked", lambda _b: self.edit_note(line.reading))
        return widget
