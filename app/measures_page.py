"""Onglet Mesures : filtres, résumé avec répartition, mesures groupées par jour.

Toute la mise en forme (textes, niveaux, icônes, parts de la barre) vient de
app.measures ; ce module ne fait qu'assembler les widgets.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gtk, Pango

from app.measures import (
    MARKER_ICONS,
    MEAL_FILTERS,
    RETAINED_ICON,
    RETAINED_TOOLTIP,
    MeasureDay,
    MeasureLine,
    MeasureSummary,
    measure_view,
)
from app.state import AppState
from app.widgets import clear, label, page, toggle_group
from services.charts import PERIODS

DAY_FILTERS = (("30", "30 jours"), ("90", "90 jours"), ("all", "Tout"))
PERIOD_FILTERS = (("all", "Tous moments"),) + tuple((p, p) for p in PERIODS)
# libellés courts des moments en fenêtre étroite, pour tenir dans 360 px
PERIOD_SHORT_LABELS = {"all": "Tous", PERIODS[2]: "Soir"}


class MeasuresPage:
    def __init__(self, state: AppState, on_fetch: Callable[[], None]):
        self.state = state
        self.on_fetch = on_fetch
        self.days = "90"
        self.period = "all"
        self.meal = "all"
        self._resetting = False

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        outer.append(self._build_filters())
        self.summary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(self.summary_box)
        self.days_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        outer.append(self.days_box)
        self.widget = page(outer, max_width=900)

    # Filtres

    def _build_filters(self) -> Gtk.Widget:
        bar = Adw.WrapBox(child_spacing=12, line_spacing=8, halign=Gtk.Align.CENTER, align=0.5)
        self.days_toggle = toggle_group(DAY_FILTERS, self.days, self._on_days)
        self.period_toggle = toggle_group(PERIOD_FILTERS, self.period, self._on_period)
        bar.append(self.days_toggle)
        bar.append(self.period_toggle)

        model = Gtk.StringList()
        for _key, text in MEAL_FILTERS:
            model.append(text)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._marker_item_setup)
        factory.connect("bind", self._marker_item_bind)
        self.meal_dropdown = Gtk.DropDown(model=model, factory=factory, selected=0, valign=Gtk.Align.CENTER)
        self.meal_dropdown.set_tooltip_text("Filtrer par le marqueur saisi sur le lecteur")
        self.meal_dropdown.update_property([Gtk.AccessibleProperty.LABEL], ["Marqueur"])
        self.meal_dropdown.connect("notify::selected", self._on_meal)
        bar.append(self.meal_dropdown)
        return bar

    def add_narrow_setters(self, breakpoint: Adw.Breakpoint) -> None:
        for name, short in PERIOD_SHORT_LABELS.items():
            toggle = self.period_toggle.get_toggle_by_name(name)
            toggle.set_tooltip(toggle.get_label())
            breakpoint.add_setter(toggle, "label", short)

    @staticmethod
    def _marker_item_setup(_factory, item: Gtk.ListItem) -> None:
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Image())
        row.append(Gtk.Label(xalign=0))
        item.set_child(row)

    @staticmethod
    def _marker_item_bind(_factory, item: Gtk.ListItem) -> None:
        key, text = MEAL_FILTERS[item.get_position()]
        icon = item.get_child().get_first_child()
        icon.set_from_icon_name(MARKER_ICONS[key])
        icon.get_next_sibling().set_label(text)

    def _on_days(self, name: str | None) -> None:
        if name and name != self.days:
            self.days = name
            self._changed()

    def _on_period(self, name: str | None) -> None:
        if name and name != self.period:
            self.period = name
            self._changed()

    def _on_meal(self, dropdown: Gtk.DropDown, _pspec) -> None:
        selected = dropdown.get_selected()
        if selected >= len(MEAL_FILTERS) or MEAL_FILTERS[selected][0] == self.meal:
            return
        self.meal = MEAL_FILTERS[selected][0]
        self._changed()

    def _changed(self) -> None:
        if not self._resetting:
            self.refresh()

    def reset_filters(self) -> None:
        self._resetting = True
        self.days_toggle.set_active_name("all")
        self.period_toggle.set_active_name("all")
        self.meal_dropdown.set_selected(0)
        self._resetting = False
        self.refresh()

    # Contenu

    def refresh(self) -> None:
        clear(self.summary_box)
        clear(self.days_box)
        settings = self.state.settings
        if settings is None:
            self.days_box.append(Adw.StatusPage(
                icon_name="preferences-system-symbolic",
                title="Protocole à saisir",
                description="Recopiez d'abord le protocole de l'ordonnance.",
            ))
            return
        days = None if self.days == "all" else int(self.days)
        view = measure_view(self.state.readings(days=days), settings, self.period, self.meal, self.state.meters())
        if not view.days:
            self.days_box.append(self._empty_state())
            return
        self.summary_box.append(self._summary_card(view.summary))
        for day in view.days:
            self.days_box.append(self._day_group(day))

    def _empty_state(self) -> Gtk.Widget:
        if self.state.store.count_readings() == 0:
            button = Gtk.Button(label="Récupérer les mesures", halign=Gtk.Align.CENTER)
            button.add_css_class("pill")
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda _b: self.on_fetch())
            return Adw.StatusPage(
                icon_name="drive-removable-media-symbolic",
                title="Aucune mesure",
                description="Branchez le lecteur Accu-Chek en USB puis récupérez les mesures.",
                child=button,
            )
        button = Gtk.Button(label="Afficher toutes les mesures", halign=Gtk.Align.CENTER)
        button.add_css_class("pill")
        button.connect("clicked", lambda _b: self.reset_filters())
        return Adw.StatusPage(
            icon_name="edit-find-symbolic",
            title="Aucune mesure pour ce filtre",
            description="Essayez une autre période, un autre moment ou un autre marqueur.",
            child=button,
        )

    def _summary_card(self, summary: MeasureSummary) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        card.add_css_class("card")
        card.add_css_class("summary-card")

        tiles = Gtk.FlowBox(
            homogeneous=True, min_children_per_line=2, max_children_per_line=4,
            selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12,
        )
        for tile in summary.tiles:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(label(tile.label, "stat-label", xalign=0))
            box.append(label(tile.value, "stat-value", "numeric", xalign=0))
            box.append(label(tile.detail, "caption", "dim-label", "numeric", xalign=0))
            child = Gtk.FlowBoxChild(child=box, focusable=False)
            tiles.append(child)
        card.append(tiles)

        bar = Gtk.Grid(column_homogeneous=True, overflow=Gtk.Overflow.HIDDEN)
        bar.add_css_class("range-bar")
        column = 0
        for segment in summary.segments:
            if not segment.span:
                continue
            piece = Gtk.Box(hexpand=True, tooltip_text=f"{segment.label} : {segment.count} ({segment.pct})")
            piece.add_css_class("range-segment")
            piece.add_css_class(f"level-{segment.level}")
            bar.attach(piece, column, 0, segment.span, 1)
            column += segment.span
        card.append(bar)

        legend = Adw.WrapBox(child_spacing=20, line_spacing=6)
        for segment in summary.segments:
            item = Gtk.Box(spacing=6)
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("legend-dot")
            dot.add_css_class(f"level-{segment.level}")
            item.append(dot)
            item.append(label(segment.label))
            item.append(label(segment.pct, "heading", "numeric"))
            item.append(label(segment.bounds, "caption", "dim-label", "numeric", valign=Gtk.Align.BASELINE_CENTER))
            legend.append(item)
        card.append(legend)
        return card

    def _day_group(self, day: MeasureDay) -> Gtk.Widget:
        group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header = Gtk.Box(spacing=12, margin_start=6, margin_end=6)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        titles.append(label(day.title, "measure-day-title", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        details = f"{day.detail} · {day.count_text}" if day.detail else day.count_text
        titles.append(label(details, "caption", "dim-label", "numeric", xalign=0, ellipsize=Pango.EllipsizeMode.END))
        header.append(titles)
        if day.morning is not None:
            chip = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, tooltip_text=f"{RETAINED_TOOLTIP}. {day.morning.level_label}")
            chip.add_css_class("morning-chip")
            chip.append(Gtk.Image(icon_name=RETAINED_ICON))
            chip.append(label("Matin", "caption"))
            chip.append(label(day.morning.value, "caption-heading", "numeric", f"level-text-{day.morning.level}"))
            header.append(chip)
        group.append(header)

        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        for line in day.lines:
            rows.append(self._measure_row(line))
        group.append(rows)
        return group

    @staticmethod
    def _measure_row(line: MeasureLine) -> Gtk.Widget:
        row = Adw.ActionRow(title=line.title, subtitle=line.subtitle, use_markup=False)
        row.add_css_class("measure-row")

        badge = Gtk.Image(icon_name=line.marker.icon, valign=Gtk.Align.CENTER, tooltip_text=line.marker.label)
        badge.add_css_class("marker-badge")
        badge.add_css_class(f"marker-{line.marker.key}")
        row.add_prefix(badge)

        if line.retained:
            chip = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER, tooltip_text=RETAINED_TOOLTIP)
            chip.add_css_class("retained-chip")
            chip.append(Gtk.Image(icon_name=RETAINED_ICON))
            chip.append(label("Retenue", "caption-heading"))
            row.add_suffix(chip)

        value = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        pill = Gtk.Box(spacing=4, halign=Gtk.Align.END, tooltip_text=line.level_label)
        pill.add_css_class("value-pill")
        pill.add_css_class(f"level-{line.level}")
        if line.level_icon:
            pill.append(Gtk.Image(icon_name=line.level_icon))
        pill.append(label(line.value, "numeric"))
        value.append(pill)
        value.append(label(line.mg, "caption", "dim-label", "numeric", xalign=1, margin_end=10))
        row.add_suffix(value)
        row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [f"{line.value}, {line.level_label}"])
        return row
