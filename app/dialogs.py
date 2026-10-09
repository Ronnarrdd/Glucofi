"""Boîtes de dialogue : premier lancement, préférences, modification manuelle de la dose, note sur une mesure.

Même style que les formulaires de la tablette : fond sauge, sections en cartes blanches (classe form-section),
champs à contour aux coins de 16 px avec leur unité, bouton plein en pilule pleine largeur en bas. Le premier
lancement s'ouvre sur la bannière du renard « Bienvenue dans Glucofi ».
"""

from __future__ import annotations

import threading
from datetime import date, datetime, time
from typing import Callable

from gi.repository import Adw, GLib, Gtk

from app.art import FoxBanner
from app.components import button, describe, label
from app.meals import MealCell, baseline_of, entry_from_form, fmt_carbs, fmt_kcal
from app.measures import marker_of
from app.protocol import protocol_to_form
from app.protocol_editor import ProtocolEditor, entry_row, form_group
from app.state import AppState, FormError, exclusion_option, parse_count
from contracts import MEAL_TEXT_MAX_CHARS, NOTE_MAX_CHARS, NOTE_TAG_LABELS_FR, NoteTag, Reading, ReadingNote
from services.meals import EstimateError, MealEstimate
from services.dosing import fmt_g_l

MAX_UI = 80
FORM_WIDTH = 600
ONBOARDING_WIDTH = 840
BANNER_COMPACT = "max-width: 559px"
WELCOME = (
    "Glucofi propose d'ajuster la dose d'insuline du soir d'après les glycémies du matin, en suivant le protocole "
    "prescrit par votre médecin. Ne l'utilisez pas sans protocole écrit."
)
WELCOME_BACK = (
    "Glucofi ne contient plus de protocole par défaut : recopiez celui de votre ordonnance pour retrouver les "
    "propositions de dose."
)


def _error_label() -> Gtk.Label:
    widget = Gtk.Label(wrap=True, visible=False, xalign=0)
    widget.add_css_class("error")
    widget.add_css_class("body-medium")
    return widget


def _form_dialog(
    title: str, children: list[Gtk.Widget], action: Gtk.Button, closable: bool = True, width: int = FORM_WIDTH,
) -> tuple[Adw.Dialog, Gtk.Box]:
    """Dialogue à fond sauge : `children` empilés dans une colonne qui défile, `action` en pilule pleine largeur."""
    dialog = Adw.Dialog(title=title, content_width=width)
    dialog.add_css_class("glucofi-form")
    dialog.set_can_close(closable)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar(show_end_title_buttons=closable))
    column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_start=24, margin_end=24, margin_top=8, margin_bottom=24)
    for child in children:
        column.append(child)
    action.set_halign(Gtk.Align.FILL)
    column.append(action)
    clamp = Adw.Clamp(maximum_size=width - 48, tightening_threshold=width - 48, child=column)
    toolbar.set_content(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True))
    dialog.set_child(toolbar)
    return dialog, column


def parse_date_fr(text: str) -> date:
    return datetime.strptime(text.strip(), "%d/%m/%Y").date()


def _sentence(message: str) -> str:
    message = str(message)
    return f"{message[0].upper()}{message[1:]}" + ("" if message.endswith(".") else ".")


def onboarding_dialog(parent: Gtk.Widget, state: AppState, on_done: Callable[[], None]) -> None:
    """Premier lancement : protocole, et dose de départ s'il n'y en a pas encore."""
    with_dose = state.needs_start_dose
    banner = FoxBanner("Bienvenue dans Glucofi", WELCOME if with_dose else WELCOME_BACK)
    patient = form_group("Patient")
    name = entry_row("Nom du patient", state.patient_name)
    patient.add(name)
    start = entry_row("Début du protocole", f"{date.today():%d/%m/%Y}", suffix="JJ/MM/AAAA")
    if with_dose:
        patient.add(start)
    editor = ProtocolEditor(protocol_to_form(state.settings, state.store.dosing_draft()))
    children: list[Gtk.Widget] = [banner, patient, *editor.groups]
    morning = entry_row("Dose de départ du matin", suffix="UI")
    evening = entry_row("Dose de départ du soir", suffix="UI")
    if with_dose:
        doses = form_group(
            "Dose de départ",
            "Doses prescrites au début du protocole. Seules les glycémies à partir de la date de début comptent.",
        )
        doses.add(morning)
        doses.add(evening)
        children.append(doses)
    error = _error_label()
    children.append(error)
    action = button("Commencer le suivi" if with_dose else "Enregistrer le protocole", tall=True)
    dialog, _column = _form_dialog(
        "Premier lancement" if with_dose else "Protocole", children, action, closable=False, width=ONBOARDING_WIDTH,
    )
    narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(BANNER_COMPACT))
    narrow.add_setter(banner, "compact", True)
    # un dialogue à points de rupture doit déclarer sa taille minimale
    dialog.set_size_request(360, 320)
    dialog.add_breakpoint(narrow)

    def fail(message: str) -> None:
        error.set_label(message)
        error.set_visible(True)

    def on_click(_btn):
        settings, problem = editor.read(state.store.dosing_draft())
        if settings is None:
            fail(_sentence(problem))
            return
        if not with_dose:
            state.set_patient_name(name.get_text())
            state.configure_protocol(settings)
            dialog.force_close()
            on_done()
            return
        try:
            start_date = parse_date_fr(start.get_text())
        except ValueError:
            start.add_css_class("error")
            fail("Date de début invalide, format JJ/MM/AAAA attendu.")
            return
        if start_date > date.today():
            start.add_css_class("error")
            fail("La date de début ne peut pas être dans le futur.")
            return
        try:
            morning_ui = parse_count("morning", morning.get_text(), 0, MAX_UI)
        except FormError as exc:
            morning.add_css_class("error")
            fail(f"Dose du matin : {exc}")
            return
        try:
            evening_ui = parse_count("evening", evening.get_text(), 0, MAX_UI)
        except FormError as exc:
            evening.add_css_class("error")
            fail(f"Dose du soir : {exc}")
            return
        state.start_protocol(name.get_text(), datetime.combine(start_date, time(0, 0)), morning_ui, evening_ui, settings)
        dialog.force_close()
        on_done()

    action.connect("clicked", on_click)
    dialog.present(parent)


class Stepper(Gtk.Box):
    """Dose d'un moment : libellé, état (« au lieu de 10 UI » ou « inchangée »), boutons moins et plus en cercles tonals
    de 56 px autour de la valeur."""

    __gtype_name__ = "GlucofiStepper"

    def __init__(self, title: str, current: int):
        super().__init__(spacing=12)
        self.add_css_class("stepper")
        self.title = title
        self.current = current
        self.value = current
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(label(title, "title-medium", xalign=0))
        self.state_label = label("", "body-medium", "muted", xalign=0, wrap=True)
        texts.append(self.state_label)
        self.append(texts)
        self.minus = self._round("glucofi-remove-symbolic", f"{title} : une unité de moins", -1)
        self.append(self.minus)
        self.value_label = label("", "headline-small", "stepper-value", width_chars=5)
        self.append(self.value_label)
        self.plus = self._round("glucofi-add-symbolic", f"{title} : une unité de plus", +1)
        self.append(self.plus)
        self._sync()

    def _round(self, icon: str, description: str, step: int) -> Gtk.Button:
        widget = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=description)
        widget.add_css_class("stepper-button")
        describe(widget, description)
        widget.connect("clicked", lambda _b: self.set_value(self.value + step))
        return widget

    def set_value(self, value: int) -> None:
        self.value = min(max(value, 0), MAX_UI)
        self._sync()

    def _sync(self) -> None:
        self.value_label.set_label(f"{self.value} UI")
        changed = self.value != self.current
        self.state_label.set_label(f"au lieu de {self.current} UI" if changed else "inchangée")
        self.minus.set_sensitive(self.value > 0)
        self.plus.set_sensitive(self.value < MAX_UI)
        self.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [f"{self.value} UI, " + (f"modifiée, actuellement {self.current} UI" if changed else "inchangée")],
        )


def manual_dose_dialog(parent: Gtk.Widget, state: AppState, on_done: Callable[[], None]) -> None:
    current = state.store.current_dose()
    settings = state.settings
    if current is None or settings is None:
        onboarding_dialog(parent, state, on_done)
        return
    group = form_group(
        "Modifier la dose",
        "Uniquement sur consigne du médecin. La nouvelle dose s'applique dès maintenant ; le suivi repart de zéro "
        f"pour la dose qui change, l'autre garde ses jours déjà comptés. Unités (UI) de {settings.insulin}.",
    )
    morning = Stepper("Matin", current.morning_ui)
    evening = Stepper("Soir", current.evening_ui)
    group.add(morning)
    group.add(evening)
    fields = form_group()
    note = entry_row("Motif (ex. consigne du Dr …)")
    fields.add(note)
    error = _error_label()
    action = button("Enregistrer", tall=True)
    dialog, _column = _form_dialog("Dose", [group, fields, error], action)

    def on_click(_btn):
        if not note.get_text().strip():
            note.add_css_class("error")
            error.set_label("Indiquez le motif du changement (consigne du médecin).")
            error.set_visible(True)
            return
        state.manual_change(morning.value, evening.value, note.get_text().strip())
        dialog.close()
        on_done()

    action.connect("clicked", on_click)
    dialog.present(parent)


class NoteForm:
    """Formulaire d'une note : étiquettes, texte libre, interrupteur « écarter de l'ajustement »."""

    def __init__(self, reading: Reading, state: AppState):
        self.reading = reading
        note = reading.note or ReadingNote()

        self.what = form_group(
            f"{reading.device_time:%d/%m/%Y à %H:%M} · {fmt_g_l(reading.mg_dl)}",
            f"{marker_of(reading).label}. La note reste dans Glucofi : le lecteur ne la voit pas.",
        )
        tags = Adw.WrapBox(child_spacing=8, line_spacing=8)
        self.tags: dict[NoteTag, Gtk.ToggleButton] = {}
        for tag in NoteTag:
            toggle = Gtk.ToggleButton(label=NOTE_TAG_LABELS_FR[tag], active=tag in note.tags)
            toggle.add_css_class("note-tag")
            toggle.connect("toggled", lambda _t: self._sync())
            tags.append(toggle)
            self.tags[tag] = toggle
        self.what.add(tags)
        self.text = Adw.EntryRow(title="Texte libre (facultatif)", max_length=NOTE_MAX_CHARS)
        self.text.set_text(note.text)
        self.text.connect("changed", lambda _r: self._sync())
        self.what.add(self.text)

        self.dosing = form_group("Ajustement de la dose")
        self.allowed, explanation = exclusion_option(reading, state.settings)
        self.exclude = Adw.SwitchRow(
            title="Écarter de l'ajustement de la dose",
            subtitle=explanation,
            subtitle_lines=0,
            active=self.allowed and note.exclude_from_dosing,
            sensitive=self.allowed,
        )
        self.exclude.connect("notify::active", lambda *_a: self._sync())
        self.dosing.add(self.exclude)
        self.error = _error_label()

    @property
    def widgets(self) -> list[Gtk.Widget]:
        return [self.what, self.dosing, self.error]

    def note(self) -> ReadingNote:
        """Raises ValueError si la note est invalide (texte trop long, mesure écartée sans motif)."""
        return ReadingNote(
            tuple(tag for tag, toggle in self.tags.items() if toggle.get_active()),
            self.text.get_text(),
            self.allowed and self.exclude.get_active(),
        )

    def _sync(self) -> None:
        self.error.set_visible(False)


def note_dialog(parent: Gtk.Widget, state: AppState, reading: Reading, on_done: Callable[[], None]) -> None:
    form = NoteForm(reading, state)
    action = button("Enregistrer", tall=True)
    dialog, column = _form_dialog("Note sur la mesure", form.widgets, action)

    def save(note: ReadingNote | None) -> None:
        state.set_note(reading, note)
        dialog.close()
        on_done()

    def on_save(_btn):
        try:
            note = form.note()
        except ValueError as exc:
            form.error.set_label(f"{str(exc)[0].upper()}{str(exc)[1:]}.")
            form.error.set_visible(True)
            return
        save(note)

    action.connect("clicked", on_save)
    if reading.note is not None:
        delete = button("Supprimer la note", icon="glucofi-close-symbolic", kind="text", on_click=lambda: save(None), halign=Gtk.Align.CENTER)
        delete.add_css_class("destructive")
        column.append(delete)
    dialog.present(parent)


def meal_dialog(parent: Gtk.Widget, state: AppState, cell: MealCell, on_done: Callable[[], None]) -> None:
    """Un repas : ce qui a été mangé, estimation par Gemini (en tâche de fond), chiffres modifiables à la main."""
    entry = cell.entry
    estimate: list[MealEstimate | None] = [baseline_of(entry)]  # l'estimation dont les chiffres affichés viennent
    busy = [False]
    day_text = f"{cell.day:%d/%m/%Y}"

    what = form_group(f"{cell.label} du {day_text}", "Écrivez ce que vous avez mangé et bu, avec les quantités si vous les connaissez.")
    text = Adw.EntryRow(title="Ce que vous avez mangé", max_length=MEAL_TEXT_MAX_CHARS)
    text.set_text(cell.text)
    what.add(text)

    estimate_button = button("Estimer avec Gemini", icon="glucofi-estimate-symbolic", kind="tonal", halign=Gtk.Align.START)
    spinner = Adw.Spinner(visible=False)
    bar = Gtk.Box(spacing=12)
    bar.append(estimate_button)
    bar.append(spinner)
    result = Gtk.Label(wrap=True, xalign=0, visible=False)
    result.add_css_class("body-medium")
    result.add_css_class("muted")
    numbers = form_group("Estimation", "Corrigez les chiffres si vous les connaissez mieux que Gemini.")
    carbs = entry_row("Glucides", "" if entry is None or entry.carbs_g is None else f"{entry.carbs_g:g}".replace(".", ","), "g")
    kcal = entry_row("Calories", "" if entry is None or entry.calories_kcal is None else str(entry.calories_kcal), "kcal")
    numbers.add(carbs)
    numbers.add(kcal)
    error = _error_label()
    save_button = button("Enregistrer", tall=True)
    dialog, column = _form_dialog(f"Repas · {cell.label}", [what, bar, result, numbers, error], save_button)

    def show_range() -> None:
        current = estimate[0]
        if current is None or current.carbs_low_g == current.carbs_high_g:
            result.set_visible(False)
            return
        result.set_label(f"Fourchette de Gemini : {current.carbs_low_g:g} à {fmt_carbs(current.carbs_high_g)} de glucides.".replace(".", ","))
        result.set_visible(True)

    show_range()

    def finished(outcome: MealEstimate | EstimateError) -> None:
        busy[0] = False
        spinner.set_visible(False)
        estimate_button.set_sensitive(True)
        if isinstance(outcome, EstimateError):
            error.set_label(str(outcome))
            error.set_visible(True)
            return
        estimate[0] = outcome
        carbs.set_text(f"{outcome.carbs_g:g}".replace(".", ","))
        kcal.set_text(str(outcome.calories_kcal))
        detail = " · ".join(f"{i.name} : {fmt_carbs(i.carbs_g)}" for i in outcome.items)
        show_range()
        if detail:
            result.set_label(f"{result.get_label()}\n{detail}".strip())
            result.set_visible(True)

    def on_estimate(_btn):
        if busy[0]:
            return
        error.set_visible(False)
        wanted = text.get_text().strip()
        if not wanted:
            error.set_label("Décrivez d'abord ce que vous avez mangé.")
            error.set_visible(True)
            return
        busy[0] = True
        spinner.set_visible(True)
        estimate_button.set_sensitive(False)

        def work():
            try:
                outcome: MealEstimate | EstimateError = state.estimate_meal(wanted)
            except EstimateError as exc:
                outcome = exc
            except Exception as exc:  # noqa: BLE001 : jamais de fil qui meurt en silence, le bouton doit se rendre
                outcome = EstimateError(f"L'estimation a échoué ({type(exc).__name__}).")
            GLib.idle_add(finished, outcome)

        threading.Thread(target=work, daemon=True).start()

    def on_save(_btn):
        try:
            state.save_meal(entry_from_form(cell.day, cell.slot, text.get_text(), kcal.get_text(), carbs.get_text(), estimate[0]))
        except ValueError as exc:
            error.set_label(_sentence(exc))
            error.set_visible(True)
            return
        dialog.close()
        on_done()

    def erase():
        state.clear_meal(cell.day, cell.slot)
        dialog.close()
        on_done()

    estimate_button.connect("clicked", on_estimate)
    save_button.connect("clicked", on_save)
    if entry is not None:
        delete = button("Effacer ce repas", icon="glucofi-close-symbolic", kind="text", on_click=erase, halign=Gtk.Align.CENTER)
        delete.add_css_class("destructive")
        column.append(delete)
    dialog.present(parent)


def preferences_dialog(parent: Gtk.Widget, state: AppState, on_saved: Callable[[str | None], None]) -> None:
    """Patient et protocole ; enregistré par « Enregistrer » ou à la fermeture, avec une nouvelle version dans
    l'historique."""
    if state.settings is None:
        onboarding_dialog(parent, state, lambda: on_saved(None))
        return
    dialog = Adw.PreferencesDialog(title="Préférences", content_width=680)
    dialog.add_css_class("glucofi-form")
    page = Adw.PreferencesPage(title="Protocole", icon_name="glucofi-doses-symbolic")

    patient = form_group("Patient")
    name = entry_row("Nom", state.patient_name)
    patient.add(name)
    page.add(patient)

    editor = ProtocolEditor(protocol_to_form(state.settings))
    for group in editor.groups:
        page.add(group)
    history = form_group(
        "Historique",
        "Si vous changez le protocole, la nouvelle version entre dans l'historique (onglet Doses).",
    )
    motive = entry_row("Motif du changement (ex. consultation du Dr …)")
    history.add(motive)
    page.add(history)
    actions = Adw.PreferencesGroup()
    save = button("Enregistrer", tall=True, halign=Gtk.Align.FILL)
    actions.add(save)
    page.add(actions)
    dialog.add(page)

    def attempt(*_args):
        new, problem = editor.read(state.store.dosing_draft())
        if new is None:
            dialog.add_toast(Adw.Toast(title=f"À corriger : {problem}", timeout=6))
            return
        state.set_patient_name(name.get_text())
        change = state.configure_protocol(new, motive.get_text().strip())
        dialog.force_close()
        on_saved("Nouveau protocole enregistré dans l'historique" if change is not None else None)

    dialog.set_can_close(False)
    dialog.connect("close-attempt", attempt)
    save.connect("clicked", attempt)
    dialog.present(parent)
