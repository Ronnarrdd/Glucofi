"""Onglet Aujourd'hui, composé comme sur la tablette (TodayScreen.kt).

De haut en bas : bannière « Bonjour » du renard avec « Récupérer les mesures » et la pastille du lecteur, bandeaux de
lecture, alertes urgentes, tuiles Matin et Soir avec la colonne « Pourquoi ? », « Dernière mesure », informations,
puis « Glucofi propose, vous validez. ». Le détail des règles, des glycémies de référence et du lecteur passe dans
le dialogue « Voir le détail ». Les textes viennent de app/today.py.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, GLib, Graphene, Gsk, Gtk

from app.art import FoxBanner, release_children_on_destroy
from app.components import (
    MOMENT_ICONS,
    LevelChip,
    LevelValue,
    MeterChip,
    MomentTile,
    Notice,
    Page,
    Section,
    button,
    clear,
    divider,
    heading,
    icon_button,
    label,
)
from app.dialogs import note_dialog
from app.state import AppState, StaleProposal
from app.today import DISCLAIMER, TAGLINE, AdjustmentView, DoseCard, TodayView, chips, delta, today_view
from contracts import DoseTarget
from services import device

WHY_BESIDE = 840
TILES_SIDE_BY_SIDE = 560
TILE_PER_WHY = 1.4
GAP = 16
METER_POLL_S = 2
READ_TEXTS = {"reading": "Lecture en cours, ne débranchez pas le lecteur…"}


class DoseLayout(Gtk.Widget):
    """Tuiles Matin et Soir et colonne « Pourquoi ? » : la colonne à droite des tuiles, à la même hauteur, à partir de
    840 px ; dessous, tuiles côte à côte, de 560 à 840 px ; tout empilé en dessous de 560 px."""

    __gtype_name__ = "GlucofiDoseLayout"

    def __init__(self, tiles: list[Gtk.Widget], why: Gtk.Widget):
        super().__init__()
        self.tiles = tiles
        self.why = why
        for child in (*tiles, why):
            child.set_parent(self)
        release_children_on_destroy(self)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    @staticmethod
    def mode(width: int, why_shown: bool) -> str:
        if width >= WHY_BESIDE and why_shown:
            return "beside"
        if width >= TILES_SIDE_BY_SIDE:
            return "row"
        return "stack"

    def _why_shown(self) -> bool:
        return self.why.get_visible()

    @staticmethod
    def _h(widget: Gtk.Widget, width: float) -> int:
        return widget.measure(Gtk.Orientation.VERTICAL, max(int(width), 0))[1]

    def _plan(self, width: int) -> tuple[str, float, float, int, int]:
        """(mode, largeur d'une tuile, largeur de la colonne, hauteur des tuiles, hauteur totale)."""
        n = len(self.tiles)
        mode = self.mode(width, self._why_shown())
        why_w = (width - GAP * n) / (n * TILE_PER_WHY + 1)
        if mode == "beside" and why_w < self.why.measure(Gtk.Orientation.HORIZONTAL, -1)[0]:
            mode = "row"
        if mode == "beside":
            tile_w = (width - why_w - GAP - GAP * (n - 1)) / n
            tiles_h = max(self._h(t, tile_w) for t in self.tiles)
            height = max(tiles_h, self._h(self.why, why_w))
            return mode, tile_w, why_w, height, height
        why_h = self._h(self.why, width) + GAP if self._why_shown() else 0
        if mode == "row":
            tile_w = (width - GAP * (n - 1)) / n
            tiles_h = max(self._h(t, tile_w) for t in self.tiles)
            return mode, tile_w, width, tiles_h, tiles_h + why_h
        heights = [self._h(t, width) for t in self.tiles]
        return mode, width, width, 0, sum(heights) + GAP * (n - 1) + why_h

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            widths = [c.measure(Gtk.Orientation.HORIZONTAL, -1)[0] for c in (*self.tiles, self.why) if c.get_visible()]
            minimum = max(widths) if widths else 0
            return minimum, max(minimum, 1040), -1, -1
        height = self._plan(for_size if for_size >= 0 else 1040)[4]
        return height, height, -1, -1

    @staticmethod
    def _place(widget: Gtk.Widget, x: float, y: float, w: float, h: float) -> None:
        widget.allocate(int(w), int(h), -1, Gsk.Transform().translate(Graphene.Point().init(x, y)))

    def do_size_allocate(self, width, height, baseline):
        mode, tile_w, why_w, tiles_h, _total = self._plan(width)
        if mode == "beside":
            for i, tile in enumerate(self.tiles):
                self._place(tile, i * (tile_w + GAP), 0, tile_w, height)
            self._place(self.why, width - why_w, 0, why_w, height)
            return
        y = 0.0
        if mode == "row":
            for i, tile in enumerate(self.tiles):
                self._place(tile, i * (tile_w + GAP), 0, tile_w, tiles_h)
            y = tiles_h + GAP
        else:
            for tile in self.tiles:
                h = self._h(tile, width)
                self._place(tile, 0, y, width, h)
                y += h + GAP
        if self._why_shown():
            self._place(self.why, 0, y, width, self._h(self.why, width))


def _reference_row(item, on_note: Callable) -> Gtk.Widget:
    """Glycémie de référence : marqueur, jour et heure, note ou exclusion, valeur fléchée. Toute la ligne ouvre la note."""
    row = Gtk.Box(spacing=12)
    row.append(Gtk.Image(icon_name=item.marker.icon, pixel_size=22, accessible_role=Gtk.AccessibleRole.PRESENTATION))
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    texts.append(label(f"{item.day} · {item.hour}", "body-large", "muted" if item.excluded else "ink", xalign=0))
    texts.append(label(item.marker.label, "body-medium", "muted", xalign=0, wrap=True))
    if item.excluded:
        texts.append(label(f"Écartée de l'ajustement : {item.note or ''}".rstrip(" :"), "body-medium", "muted", xalign=0, wrap=True))
    elif item.note:
        texts.append(label(f"Note : {item.note}", "body-medium", "muted", xalign=0, wrap=True))
    row.append(texts)
    row.append(LevelValue(item.g_l, item.level, item.level_label, dimmed=item.excluded))
    widget = Gtk.Button(child=row, tooltip_text="Ajouter ou modifier une note")
    widget.add_css_class("list-row")
    widget.connect("clicked", lambda _b: on_note(item.reading))
    return widget


class TodayPage:
    def __init__(
        self,
        window: Gtk.Window,
        state: AppState,
        refresh: Callable[[], None],
        toast: Callable[[str], None],
        is_connected: Callable[[], bool] = device.is_device_connected,
    ):
        self.window = window
        self.state = state
        self._refresh_all = refresh
        self._toast = toast
        self._is_connected = is_connected
        self.view: TodayView | None = None
        self.busy = False
        self.read: tuple[str, str, tuple[str, ...]] | None = None
        self._poll_id = 0
        self.detail: Adw.Dialog | None = None

        self.page = Page(top=24)
        self.widget = self.page

        self.meter = MeterChip()
        self.banner = FoxBanner("Bonjour", corner=self.meter)
        self.fetch_button = button(
            "Récupérer les mesures", icon="glucofi-sync-symbolic", tall=True, action_name="win.fetch",
            tooltip_text="Lire les mesures du lecteur Accu-Chek branché en USB (Ctrl+R)",
        )
        self.banner.add_action(self.fetch_button)
        self.page.append(self.banner)

        self.read_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False)
        self.alerts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GAP, visible=False)
        self.setup_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        self.tiles = {
            target: MomentTile(target.value, label_text, lambda t=target: self._ask_validation(t))
            for target, label_text in ((DoseTarget.MORNING, "Matin"), (DoseTarget.EVENING, "Soir"))
        }
        self.why = Section("Pourquoi ?")
        self.why_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.why.append(self.why_body)
        self.detail_button = button("Voir le détail", icon="glucofi-history-symbolic", kind="text", on_click=self.open_detail, halign=Gtk.Align.START)
        self.why.append(self.detail_button)
        self.doses = DoseLayout(list(self.tiles.values()), self.why)
        self.last = Gtk.Box(spacing=12)
        self.last.add_css_class("last-reading")
        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=GAP, visible=False)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=8)
        footer.append(label(TAGLINE, "title-small", "muted", justify=Gtk.Justification.CENTER))
        footer.append(label(DISCLAIMER, "body-small", "muted", wrap=True, justify=Gtk.Justification.CENTER))
        for widget in (self.read_box, self.alerts_box, self.setup_box, self.doses, self.last, self.info_box, footer):
            self.page.append(widget)
        self.page.connect("map", lambda *_a: self._start_polling())
        self.page.connect("unmap", lambda *_a: self._stop_polling())

    # points de rupture de la fenêtre

    def narrow_setters(self, breakpoint: Adw.Breakpoint) -> None:
        """Fenêtre étroite : bannière étroite, marges de 16 px, « Dernière mesure » sur plusieurs lignes."""
        self.page.narrow_setters(breakpoint)
        breakpoint.add_setter(self.banner, "compact", True)
        breakpoint.add_setter(self.last, "orientation", Gtk.Orientation.VERTICAL)

    # lecteur

    def _start_polling(self) -> None:
        self._poll_meter()
        if not self._poll_id:
            self._poll_id = GLib.timeout_add_seconds(METER_POLL_S, self._poll_meter)

    def _stop_polling(self) -> None:
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = 0

    def _poll_meter(self) -> bool:
        try:
            plugged = bool(self._is_connected())
        except OSError:
            plugged = False
        self.meter.set_plugged(plugged)
        return GLib.SOURCE_CONTINUE

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        for tile in self.tiles.values():
            tile.validate.set_sensitive(not busy)

    def set_read(self, kind: str | None, message: str = "", warnings: tuple[str, ...] = ()) -> None:
        """Bandeau de lecture : « reading » (en cours), « done » (réussie), « failed » (échouée), None (aucun)."""
        self.read = None if kind is None else (kind, message, tuple(warnings))
        clear(self.read_box)
        self.read_box.set_visible(kind is not None)
        if kind is None:
            return
        close = icon_button("glucofi-close-symbolic", "Fermer", lambda: self.set_read(None))
        if kind == "reading":
            self.read_box.append(Notice(message or READ_TEXTS["reading"], "info", trailing=Adw.Spinner()))
        elif kind == "done":
            self.read_box.append(Notice(message, "success", trailing=close))
            for warning in warnings:
                self.read_box.append(Notice(warning, "warning"))
        else:
            self.read_box.append(Notice(message, "danger", trailing=close))

    # contenu

    def refresh(self) -> None:
        view = today_view(self.state)
        self.view = view
        self.banner.set_subtitle(view.date)
        clear(self.alerts_box)
        for alert in view.urgent:
            self.alerts_box.append(Notice(alert.message, alert.kind))
        self.alerts_box.set_visible(bool(view.urgent))
        clear(self.info_box)
        for alert in view.info:
            self.info_box.append(Notice(alert.message, "info"))
        self.info_box.set_visible(bool(view.info))
        self._fill_setup(view)
        self._fill_doses(view)
        self._fill_last(view)
        if self.detail is not None:
            self._fill_detail(self.detail.content)

    def _fill_setup(self, view: TodayView) -> None:
        clear(self.setup_box)
        needed = not view.doses
        self.setup_box.set_visible(needed)
        if needed:
            start = button("Saisir le protocole", kind="primary", action_name="win.onboard")
            self.setup_box.append(Notice("Recopiez le protocole de votre ordonnance pour commencer le suivi.", "info", trailing=start))

    def _fill_doses(self, view: TodayView) -> None:
        self.doses.set_visible(bool(view.doses))
        for card in view.doses:
            self.tiles[card.target].update(card.current_ui, card.proposed_ui, card.status, card.description)
        featured, explained = view.why
        clear(self.why_body)
        self.why.set_visible(bool(explained))
        for index, card in enumerate(featured):
            if index:
                self.why_body.append(divider())
            self.why_body.append(self._why_item(card))
        self.doses.queue_resize()

    def _why_item(self, card: DoseCard) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        change = delta(card)
        box.append(heading(change or f"{card.label} : {card.status}", "title-large"))
        if change is None:
            box.append(label(card.adjustment.reason, "body-medium", "muted", xalign=0, wrap=True))
        shown = chips(card.adjustment)
        if shown:
            flow = Gtk.FlowBox(
                selection_mode=Gtk.SelectionMode.NONE, column_spacing=8, row_spacing=8, homogeneous=False,
                max_children_per_line=12, margin_top=2,
            )
            for item in shown:
                flow.append(Gtk.FlowBoxChild(child=LevelChip(item.chip_text, item.level, item.level_label), focusable=False))
            box.append(flow)
        return box

    def _fill_last(self, view: TodayView) -> None:
        clear(self.last)
        last = view.last_reading
        line = Adw.WrapBox(child_spacing=12, line_spacing=4, valign=Gtk.Align.CENTER, align=0.5)
        if last is None:
            line.append(label("Aucune mesure pour l'instant", "body-large"))
        else:
            line.append(label("Dernière mesure", "title-small", "muted"))
            line.append(LevelValue(last.g_l, last.level, last.level_label))
            line.append(label(last.when, "body-large"))
        self.last.append(line)
        if view.footer:
            self.last.append(label(view.footer, "body-medium", "muted", hexpand=True, xalign=1, wrap=True))

    # validation

    def _ask_validation(self, target: DoseTarget) -> None:
        view = self.view
        card = next((c for c in view.doses if c.target is target), None) if view else None
        if card is None or card.proposed_ui is None or view.proposal is None:
            return
        adjustment = card.adjustment
        dialog = Adw.AlertDialog(heading=adjustment.confirm_title, body=adjustment.confirm_body)
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("validate", f"Valider {card.proposed_ui} UI")
        dialog.set_response_appearance("validate", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("validate")
        dialog.set_close_response("cancel")
        proposal = view.proposal
        dialog.connect("response", lambda _d, response: response == "validate" and self._validate(proposal, card, adjustment))
        dialog.present(self.window)

    def _validate(self, proposal, card: DoseCard, adjustment: AdjustmentView) -> None:
        dose = "dose du soir" if card.target is DoseTarget.EVENING else "dose du matin"
        try:
            self.state.validate(proposal, target=card.target)
        except StaleProposal as exc:
            self._toast(str(exc))
        else:
            self._toast(f"{dose.capitalize()} : {card.proposed_ui} UI à partir de {adjustment.when}")
        self._refresh_all()

    # détail

    def open_detail(self) -> None:
        if self.view is None:
            return
        dialog = Adw.Dialog(title="Détail des doses", content_width=680, content_height=720)
        dialog.add_css_class("detail-sheet")
        top = Gtk.Box(halign=Gtk.Align.END, margin_top=16, margin_end=24, margin_start=24)
        top.append(button("Fermer", icon="glucofi-close-symbolic", kind="tonal", on_click=dialog.close))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=24, margin_end=24, margin_top=16, margin_bottom=24)
        scroller = Gtk.ScrolledWindow(child=content, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(top)
        outer.append(scroller)
        dialog.set_child(outer)
        dialog.content = content
        self.detail = dialog
        self._fill_detail(content)
        dialog.connect("closed", lambda *_a: setattr(self, "detail", None))
        dialog.present(self.window)

    def _fill_detail(self, content: Gtk.Box) -> None:
        clear(content)
        _featured, explained = self.view.why
        for index, card in enumerate(explained):
            if index:
                content.append(divider())
            self._why_detail(content, card)
        if explained:
            content.append(divider())
        content.append(heading("Lecteur", "headline-small"))
        for title, value in self.view.meter_rows:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            row.append(label(title, "title-medium", xalign=0))
            row.append(label(value, "body-medium", "muted", xalign=0, wrap=True))
            content.append(row)

    def _why_detail(self, content: Gtk.Box, card: DoseCard) -> None:
        adjustment = card.adjustment
        title = Gtk.Box(spacing=12)
        title.append(Gtk.Image(icon_name=MOMENT_ICONS[card.target.value], pixel_size=28, accessible_role=Gtk.AccessibleRole.PRESENTATION, css_classes=["accent-icon"]))
        title.append(heading(card.detail_title, "headline-small"))
        content.append(title)
        content.append(label(adjustment.rule_label, "title-medium", xalign=0, wrap=True))
        content.append(label(adjustment.reason, "body-large", xalign=0, wrap=True))
        content.append(label(adjustment.references_title, "title-small", xalign=0, wrap=True, margin_top=4))
        content.append(label(adjustment.references_rule, "body-medium", "muted", xalign=0, wrap=True))
        if not adjustment.references:
            content.append(label("Aucune glycémie de référence pour l'instant.", "body-large", xalign=0))
        for index, item in enumerate(adjustment.references):
            if index:
                content.append(divider())
            content.append(_reference_row(item, self._open_note))

    def _open_note(self, reading) -> None:
        note_dialog(self.window, self.state, reading, self._refresh_all)
