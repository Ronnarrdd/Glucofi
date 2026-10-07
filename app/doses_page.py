"""Onglet Doses, composé comme sur la tablette (DosesScreen.kt) : dose en cours en petites tuiles Matin et Soir,
consigne, historique des doses, puis le protocole en cours et son historique (le PC garde le protocole ici)."""

from __future__ import annotations

from gi.repository import Adw, Gtk

from app.components import MOMENT_ICONS, Notice, Page, Section, button, clear, divider, heading, label, screen_title, tag
from app.protocol import protocol_history, protocol_sections
from app.state import AppState
from contracts import RULE_LABELS_FR

ADVICE = "À modifier uniquement sur consigne du médecin. Une proposition se valide sur l'écran Aujourd'hui."


def dose_number(title: str, value: int | None, target: str) -> Gtk.Widget:
    """Petite tuile pastel du moment, dose en Fredoka 45 px."""
    tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
    tile.add_css_class("moment-tile")
    tile.add_css_class(f"moment-{target}")
    tile.add_css_class("small")
    head = Gtk.Box(spacing=10)
    head.append(Gtk.Image(icon_name=MOMENT_ICONS[target], pixel_size=28, accessible_role=Gtk.AccessibleRole.PRESENTATION))
    head.append(label(title, "headline-small"))
    tile.append(head)
    tile.append(label(f"{'?' if value is None else value} UI", "display-medium", "dose", xalign=0))
    return tile


def history_row(title: str, subtitle: str, details: list[str], current: bool, muted: list[str] | None = None) -> Gtk.Widget:
    row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=8, margin_bottom=8)
    top = Gtk.Box(spacing=12)
    top.append(label(title, "title-medium", xalign=0, wrap=True, hexpand=True))
    if current:
        top.append(tag("En cours", current=True))
    row.append(top)
    row.append(label(subtitle, "body-medium", "muted", xalign=0, wrap=True))
    for line in details:
        row.append(label(line, "body-medium", xalign=0, wrap=True))
    for line in muted or ():
        row.append(label(line, "body-medium", "muted", xalign=0, wrap=True))
    return row


class DosesPage:
    def __init__(self, state: AppState):
        self.state = state
        self.page = Page()
        self.widget = self.page
        self.page.append(screen_title("Doses"))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.page.append(self.body)

    def narrow_setters(self, breakpoint: Adw.Breakpoint) -> None:
        self.page.narrow_setters(breakpoint)

    def refresh(self) -> None:
        clear(self.body)
        changes = self.state.dose_changes()
        settings = self.state.settings
        if changes:
            current = changes[-1]
            block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            head = Gtk.Box(spacing=12)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            texts.append(heading("Dose en cours", "title-large"))
            if settings is not None and settings.insulin:
                texts.append(label(settings.insulin, "body-medium", "muted", xalign=0))
            head.append(texts)
            head.append(button("Modifier la dose", icon="glucofi-edit-symbolic", kind="outlined", action_name="win.manual-dose", valign=Gtk.Align.CENTER))
            block.append(head)
            tiles = Gtk.Box(spacing=16, homogeneous=True)
            tiles.append(dose_number("Matin", current.morning_ui, "morning"))
            tiles.append(dose_number("Soir", current.evening_ui, "evening"))
            block.append(tiles)
            block.append(label(ADVICE, "body-medium", "muted", xalign=0, wrap=True))
            self.body.append(block)
        else:
            self.body.append(Notice("Aucune dose enregistrée : saisissez le protocole et la dose de départ.", "info"))

        history = Section("Historique des doses", "La plus récente est la dose en cours.")
        if not changes:
            history.append(label("Aucune dose enregistrée.", "body-large", xalign=0))
        for i, change in enumerate(reversed(changes)):
            if i:
                history.append(divider())
            history.append(history_row(
                f"Matin {change.morning_ui} UI · Soir {change.evening_ui} UI",
                f"Depuis le {change.effective:%d/%m/%Y %H:%M} · {RULE_LABELS_FR[change.rule]}",
                [*change.evidence, *([change.note] if change.note else [])],
                current=i == 0,
                muted=["Écartées : " + " ; ".join(change.excluded)] if change.excluded else None,
            ))
        self.body.append(history)
        if settings is not None:
            self._protocol(settings)

    def _protocol(self, settings) -> None:
        edit = button("Modifier", icon="glucofi-edit-symbolic", kind="primary", action_name="win.preferences")
        section = Section("Protocole en cours", "Recopié de l'ordonnance.", action=edit)
        for i, (title, lines) in enumerate(protocol_sections(settings)):
            if i:
                section.append(divider())
            block = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4, margin_bottom=4)
            block.append(label(title, "title-medium", xalign=0, wrap=True))
            for line in lines:
                block.append(label(line, "body-medium", xalign=0, wrap=True))
            section.append(block)
        self.body.append(section)

        history = Section("Historique du protocole", "La version la plus récente est celle en cours.")
        entries = protocol_history(self.state.protocol_changes())
        if not entries:
            history.append(label("Aucune version enregistrée.", "body-large", xalign=0))
        for i, (change, lines) in enumerate(entries):
            if i:
                history.append(divider())
            title = f"Depuis le {change.effective:%d/%m/%Y %H:%M}"
            history.append(history_row(title, change.note or "Ordonnance", list(lines) or ["Aucun changement"], current=i == 0))
        self.body.append(history)
