"""Boîtes de dialogue : premier lancement, préférences, modification manuelle de la dose."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Callable

from gi.repository import Adw, Gtk

from app.state import AppState, FormError, fmt_form_g_l, parse_count, protocol_from_form

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
    dialog = Adw.Dialog(title=title, content_width=460)
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


def parse_hhmm(text: str) -> time:
    try:
        return datetime.strptime(text.strip(), "%H:%M").time()
    except ValueError:
        raise ValueError(f"heure invalide « {text} », format HH:MM attendu") from None


def _protocol_rows(draft: dict) -> dict[str, Adw.EntryRow]:
    """Champs de l'ordonnance, vides tant que l'utilisateur ne les a pas saisis."""
    step = draft.get("step_ui")
    streak = draft.get("high_streak_days")
    return {
        "insulin": _entry("Insuline du soir (nom sur l'ordonnance)", draft.get("insulin", "")),
        "low_g_l": _entry("Seuil bas du matin (g/L)", fmt_form_g_l(draft.get("low_g_l"))),
        "high_g_l": _entry("Seuil haut du matin (g/L)", fmt_form_g_l(draft.get("high_g_l"))),
        "step_ui": _entry("Pas d'ajustement (UI)", "" if step is None else str(step)),
        "high_streak_days": _entry("Jours consécutifs au-dessus pour une hausse", "" if streak is None else str(streak)),
    }


def _protocol_group(rows: dict[str, Adw.EntryRow]) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(
        title="Protocole du médecin",
        description=(
            "Recopiez les valeurs de l'ordonnance. Glycémie du matin sous le seuil bas : la dose du soir baisse "
            "du pas. Au-dessus du seuil haut le nombre de jours indiqué : elle augmente du pas. "
            "Glucofi ne fait que proposer ; rien n'est appliqué sans votre validation."
        ),
    )
    for row in rows.values():
        group.add(row)
    return group


def _read_protocol(rows: dict[str, Adw.EntryRow], base: dict):
    try:
        return protocol_from_form({k: r.get_text() for k, r in rows.items()}, base), None
    except FormError as exc:
        rows[exc.field].add_css_class("error")
        return None, str(exc)
    except ValueError as exc:
        return None, str(exc)


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

    rows = _protocol_rows(state.store.dosing_draft())
    page.add(_protocol_group(rows))
    if with_dose:
        doses = Adw.PreferencesGroup(
            title="Dose de départ",
            description="Doses prescrites au début du protocole. Seules les glycémies du matin à partir de la date de début comptent.",
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
        settings, problem = _read_protocol(rows, state.store.dosing_draft())
        if settings is None:
            fail(problem)
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
        description="À utiliser uniquement sur consigne du médecin. La nouvelle dose s'applique dès maintenant "
        f"et remet à zéro le suivi des {settings.high_streak_days} jours.",
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


def preferences_dialog(parent: Gtk.Widget, state: AppState, on_saved: Callable[[str | None], None]) -> None:
    s = state.settings
    if s is None:
        onboarding_dialog(parent, state, lambda: on_saved(None))
        return
    dialog = Adw.PreferencesDialog(title="Préférences")
    page = Adw.PreferencesPage(title="Protocole", icon_name="preferences-system-symbolic")

    patient = Adw.PreferencesGroup(title="Patient")
    name = _entry("Nom", state.patient_name)
    patient.add(name)

    morning = Adw.PreferencesGroup(
        title="Glycémie du matin",
        description="La glycémie du matin est prise dans cette plage horaire (heure du lecteur) : première mesure « à jeun », sinon « avant repas » ou sans marqueur.",
    )
    start = _entry("Début (HH:MM)", s.morning_start.strftime("%H:%M"))
    end = _entry("Fin (HH:MM)", s.morning_end.strftime("%H:%M"))
    morning.add(start)
    morning.add(end)

    rows = _protocol_rows(state.store.dosing_draft())
    for group in (patient, morning, _protocol_group(rows)):
        page.add(group)
    dialog.add(page)

    def on_closed(_dialog):
        state.set_patient_name(name.get_text())
        try:
            base = dict(state.store.dosing_draft(), morning_start=parse_hhmm(start.get_text()), morning_end=parse_hhmm(end.get_text()))
            new, problem = _read_protocol(rows, base)
        except ValueError as exc:
            new, problem = None, str(exc)
        if new is None:
            on_saved(f"Réglages non enregistrés : {problem}")
            return
        state.configure_protocol(new)
        on_saved(None)

    dialog.connect("closed", on_closed)
    dialog.present(parent)
