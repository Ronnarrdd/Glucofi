"""Onglet Repas : une carte par jour (aujourd'hui et les treize précédents), trois repas par carte.

Un clic sur un repas ouvre son formulaire (texte, estimation Gemini, chiffres modifiables). Toute la mise en forme
(statuts, totaux, textes) vient de app/meals.py ; ce module ne fait qu'assembler les widgets.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gtk

from app.components import Notice, Page, Section, clear, describe, divider, label, screen_title, tag
from app.dialogs import meal_dialog
from app.meals import DISCLAIMER, INTRO, MealCell, MealDay, meals_view
from app.state import AppState
from services.meals import load_api_key


class MealsPage:
    def __init__(self, state: AppState, on_changed: Callable[[], None] | None = None):
        self.state = state
        self.on_changed = on_changed or self.refresh
        self.page = Page()
        self.widget = self.page
        self.page.append(screen_title("Repas"))
        self.page.append(label(INTRO, "body-large", "muted", xalign=0, wrap=True))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.page.append(self.body)
        self.refresh()

    def narrow_setters(self, breakpoint: Adw.Breakpoint) -> None:
        self.page.narrow_setters(breakpoint)

    def refresh(self) -> None:
        clear(self.body)
        if not load_api_key():
            self.body.append(Notice(
                "Pas de clé Gemini : les repas s'enregistrent, mais l'estimation est impossible. Ajoutez "
                "GEMINI_API_KEY=... dans le fichier .env (clé gratuite sur aistudio.google.com/apikey).", "warning",
            ))
        for day in meals_view(self.state).days:
            self.body.append(self._day(day))
        self.body.append(label(DISCLAIMER, "body-medium", "muted", xalign=0, wrap=True))

    def _day(self, day: MealDay) -> Gtk.Widget:
        subtitle = day.detail
        total = tag(day.total) if day.total else None
        section = Section(day.title, subtitle, action=total)
        for i, cell in enumerate(day.cells):
            if i:
                section.append(divider())
            section.append(self._row(cell))
        return section

    def _row(self, cell: MealCell) -> Gtk.Widget:
        row = Gtk.Button()
        row.add_css_class("flat")
        row.add_css_class("meal-row")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6)
        head = Gtk.Box(spacing=12)
        head.append(label(cell.label, "title-medium", xalign=0, hexpand=True))
        head.append(label(cell.status, "body-medium", "muted", xalign=1))
        box.append(head)
        if cell.entry is not None:
            box.append(label(cell.text, "body-large", xalign=0, wrap=True))
        if cell.summary:
            box.append(label(cell.summary + (f" ({cell.range})" if cell.range else ""), "body-medium", xalign=0, wrap=True))
        row.set_child(box)
        describe(row, cell.description)
        row.connect("clicked", lambda _b, c=cell: meal_dialog(row, self.state, c, self.on_changed))
        return row
