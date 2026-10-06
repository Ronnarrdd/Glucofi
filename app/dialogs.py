"""Boîtes de dialogue : premier lancement, préférences, modification manuelle de la dose, note sur une mesure."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Callable

from gi.repository import Adw, Gtk

from app.measures import marker_of
from app.protocol import protocol_to_form
from app.protocol_editor import ProtocolEditor
from app.state import AppState, FormError, exclusion_option, parse_count
from contracts import NOTE_MAX_CHARS, NOTE_TAG_LABELS_FR, NoteTag, Reading, ReadingNote
from services.dosing import fmt_g_l

MAX_UI = 80


def _spin(title: str, value: float, lower: float, upper: float, step: float, digits: int = 0, subtitle: str = "") -> Adw.SpinRow:
    row = Adw.SpinRow.new_with_range(lower, upper, step)
    row.set_title(title)
    row.set_digits(digits)
    row.set_value(value)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def _entry(title: str, text: str = "") -> Adw.EntryRow:
    row = Adw.EntryRow(title=title)
    row.set_text(text)
    row.connect("changed", lambda r: r.remove_css_class("error"))
    return row


def _dialog_shell(title: str, page: Adw.PreferencesPage, button: Gtk.Button, closable: bool = True) -> Adw.Dialog:
    dialog = Adw.Dialog(title=title, content_width=560)
    dialog.set_can_close(closable)
    toolbar = Adw.ToolbarView()
    header = Adw.HeaderBar(show_end_title_buttons=closable)
    toolbar.add_top_bar(header)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.append(page)
    button.set_margin_start(24)
    button.set_margin_end(24)
    button.set_margin_bottom(24)
    button.add_css_class("suggested-action")
    button.add_css_class("pill")
    button.set_halign(Gtk.Align.CENTER)
    box.append(button)
    toolbar.set_content(box)
    dialog.set_child(toolbar)
    return dialog


def parse_date_fr(text: str) -> date:
    return datetime.strptime(text.strip(), "%d/%m/%Y").date()


def _sentence(message: str) -> str:
    message = str(message)
    return f"{message[0].upper()}{message[1:]}" + ("" if message.endswith(".") else ".")


def onboarding_dialog(parent: Gtk.Widget, state: AppState, on_done: Callable[[], None]) -> None:
    """Premier lancement : protocole, et dose de départ s'il n'y en a pas encore."""
    page = Adw.PreferencesPage()
    with_dose = state.needs_start_dose
    intro = Adw.PreferencesGroup(
        title="Bienvenue dans Glucofi",
        description=(
            "Glucofi propose d'ajuster la dose d'insuline du soir d'après les glycémies du matin, "
            "en suivant le protocole prescrit par votre médecin. Ne l'utilisez pas sans protocole écrit."
            if with_dose else
            "Glucofi ne contient plus de protocole par défaut : recopiez celui de votre ordonnance "
            "pour retrouver les propositions de dose."
        ),
    )
    name = _entry("Nom du patient", state.patient_name)
    intro.add(name)
    start = _entry("Début du protocole (JJ/MM/AAAA)", f"{date.today():%d/%m/%Y}")
    morning = _entry("Dose de départ du matin (UI)")
    evening = _entry("Dose de départ du soir (UI)")
    if with_dose:
        intro.add(start)
    page.add(intro)

    editor = ProtocolEditor(protocol_to_form(state.settings, state.store.dosing_draft()))
    for group in editor.groups:
        page.add(group)
    if with_dose:
        doses = Adw.PreferencesGroup(
            title="Dose de départ",
            description="Doses prescrites au début du protocole. Seules les glycémies à partir de la date de début comptent.",
        )
        doses.add(morning)
        doses.add(evening)
        page.add(doses)

    error = Gtk.Label(wrap=True, visible=False, margin_start=24, margin_end=24, margin_bottom=12)
    error.add_css_class("error")
    button = Gtk.Button(label="Commencer le suivi" if with_dose else "Enregistrer le protocole")
    dialog = _dialog_shell("Premier lancement" if with_dose else "Protocole", page, button, closable=False)
    button.get_parent().insert_child_after(error, page)

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

    button.connect("clicked", on_click)
    dialog.present(parent)


def manual_dose_dialog(parent: Gtk.Widget, state: AppState, on_done: Callable[[], None]) -> None:
    current = state.store.current_dose()
    settings = state.settings
    if current is None or settings is None:
        onboarding_dialog(parent, state, on_done)
        return
    page = Adw.PreferencesPage()
    group = Adw.PreferencesGroup(
        title="Modifier la dose",
        description="À utiliser uniquement sur consigne du médecin. La nouvelle dose s'applique dès maintenant ; "
        "le suivi repart de zéro pour la dose qui change, l'autre garde ses jours déjà comptés.",
    )
    morning = _spin("Matin", current.morning_ui, 0, MAX_UI, 1, subtitle=f"unités (UI) de {settings.insulin}")
    evening = _spin("Soir", current.evening_ui, 0, MAX_UI, 1, subtitle=f"unités (UI) de {settings.insulin}")
    note = _entry("Motif (ex. consigne du Dr ...)")
    for row in (morning, evening, note):
        group.add(row)
    page.add(group)
    button = Gtk.Button(label="Enregistrer")
    dialog = _dialog_shell("Dose", page, button)

    def on_click(_btn):
        if not note.get_text().strip():
            note.add_css_class("error")
            return
        state.manual_change(int(morning.get_value()), int(evening.get_value()), note.get_text().strip())
        dialog.close()
        on_done()

    button.connect("clicked", on_click)
    dialog.present(parent)


class NoteForm:
    """Formulaire d'une note : étiquettes, texte libre, interrupteur « écarter de l'ajustement »."""

    def __init__(self, reading: Reading, state: AppState):
        self.reading = reading
        note = reading.note or ReadingNote()
        self.page = Adw.PreferencesPage()

        what = Adw.PreferencesGroup(
            title=f"{reading.device_time:%d/%m/%Y à %H:%M} · {fmt_g_l(reading.mg_dl)}",
            description=f"{marker_of(reading).label}. La note reste dans Glucofi : le lecteur ne la voit pas.",
        )
        tags = Adw.WrapBox(child_spacing=8, line_spacing=8)
        self.tags: dict[NoteTag, Gtk.ToggleButton] = {}
        for tag in NoteTag:
            toggle = Gtk.ToggleButton(label=NOTE_TAG_LABELS_FR[tag], active=tag in note.tags)
            toggle.add_css_class("note-tag")
            toggle.connect("toggled", lambda _t: self._sync())
            tags.append(toggle)
            self.tags[tag] = toggle
        what.add(tags)
        self.page.add(what)

        free = Adw.PreferencesGroup()
        self.text = Adw.EntryRow(title="Texte libre (facultatif)", max_length=NOTE_MAX_CHARS)
        self.text.set_text(note.text)
        self.text.connect("changed", lambda _r: self._sync())
        free.add(self.text)
        self.page.add(free)

        dosing = Adw.PreferencesGroup(title="Ajustement de la dose")
        self.allowed, explanation = exclusion_option(reading, state.settings)
        self.exclude = Adw.SwitchRow(
            title="Écarter de l'ajustement de la dose",
            subtitle=explanation,
            subtitle_lines=0,
            active=self.allowed and note.exclude_from_dosing,
            sensitive=self.allowed,
        )
        self.exclude.connect("notify::active", lambda *_a: self._sync())
        dosing.add(self.exclude)
        self.page.add(dosing)

        self.error = Gtk.Label(wrap=True, visible=False, margin_start=24, margin_end=24, margin_bottom=12)
        self.error.add_css_class("error")

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
    button = Gtk.Button(label="Enregistrer")
    dialog = _dialog_shell("Note sur la mesure", form.page, button)
    box = button.get_parent()
    box.insert_child_after(form.error, form.page)

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

    button.connect("clicked", on_save)
    if reading.note is not None:
        delete = Gtk.Button(label="Supprimer la note", halign=Gtk.Align.CENTER, margin_bottom=24)
        delete.add_css_class("destructive-action")
        delete.add_css_class("flat")
        delete.connect("clicked", lambda _b: save(None))
        button.set_margin_bottom(8)
        box.append(delete)
    dialog.present(parent)


def preferences_dialog(parent: Gtk.Widget, state: AppState, on_saved: Callable[[str | None], None]) -> None:
    """Patient et protocole ; enregistré à la fermeture, avec une nouvelle version dans l'historique."""
    if state.settings is None:
        onboarding_dialog(parent, state, lambda: on_saved(None))
        return
    dialog = Adw.PreferencesDialog(title="Préférences", content_width=640)
    page = Adw.PreferencesPage(title="Protocole", icon_name="preferences-system-symbolic")

    patient = Adw.PreferencesGroup(title="Patient")
    name = _entry("Nom", state.patient_name)
    patient.add(name)
    page.add(patient)

    editor = ProtocolEditor(protocol_to_form(state.settings))
    for group in editor.groups:
        page.add(group)
    history = Adw.PreferencesGroup(
        title="Historique",
        description="Si vous changez le protocole, la nouvelle version entre dans l'historique (onglet Doses).",
    )
    motive = _entry("Motif du changement (ex. consultation du Dr ...)")
    history.add(motive)
    page.add(history)
    dialog.add(page)

    def on_close_attempt(_dialog):
        new, problem = editor.read(state.store.dosing_draft())
        if new is None:
            dialog.add_toast(Adw.Toast(title=f"À corriger : {problem}", timeout=6))
            return
        state.set_patient_name(name.get_text())
        change = state.configure_protocol(new, motive.get_text().strip())
        dialog.force_close()
        on_saved("Nouveau protocole enregistré dans l'historique" if change is not None else None)

    dialog.set_can_close(False)
    dialog.connect("close-attempt", on_close_attempt)
    dialog.present(parent)
