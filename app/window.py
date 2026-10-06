"""Fenêtre principale : Aujourd'hui, Graphiques, Mesures, Doses."""

from __future__ import annotations

import importlib.util
import logging
import threading
from datetime import datetime
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from app.dialogs import manual_dose_dialog, note_dialog, onboarding_dialog, preferences_dialog
from app.measures_page import MeasuresPage
from app.protocol import DOSE_NAMES, evening_rule_text, morning_rule_text, protocol_history, protocol_sections
from app.state import AppState, StaleProposal, clock_text, import_message, merge_message, meter_text
from app.widgets import clear as _clear
from app.widgets import page as _page
from app.widgets import toggle_group as _toggle_group
from contracts import (
    count_fr,
    DOSE_TARGET_LABELS_FR,
    MEAL_LABELS_FR,
    REFERENCE_LABELS_FR,
    RULE_LABELS_FR,
    AlertLevel,
    DoseAdjustment,
    DoseProposal,
    DoseTarget,
    Reading,
)
from services import device
from services.store import MergeRefused
from services.dosing import fmt_g_l, fmt_mg_dl

log = logging.getLogger("glucofi.ui")

HAS_MPL = importlib.util.find_spec("matplotlib") is not None
CHART_DPI = 200
CHART_WIDTH_PX = 860
CHART_PERIODS = (("14", "14 jours"), ("30", "30 jours"), ("90", "90 jours"))
DAYS_FR = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
ALERT_STYLES = {
    AlertLevel.DANGER: ("dialog-error-symbolic", "error"),
    AlertLevel.WARNING: ("dialog-warning-symbolic", "warning"),
    AlertLevel.INFO: ("dialog-information-symbolic", None),
}


def _glycemia_class(reading: Reading, state: AppState, target: DoseTarget = DoseTarget.EVENING) -> str:
    t = state.settings.titration(target)
    if reading.g_l < t.low_g_l:
        return "error"
    if reading.g_l > t.high_g_l:
        return "warning"
    return "success"


def _value_label(reading: Reading, state: AppState, target: DoseTarget = DoseTarget.EVENING) -> Gtk.Label:
    label = Gtk.Label(label=fmt_g_l(reading.mg_dl), valign=Gtk.Align.CENTER)
    label.add_css_class("heading")
    label.add_css_class(_glycemia_class(reading, state, target))
    return label


def _meal_suffix(reading: Reading) -> str:
    return f" · {MEAL_LABELS_FR[reading.meal]}" if reading.meal is not None else ""


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, state: AppState):
        super().__init__(application=application, title="Glucofi", default_width=1000, default_height=780)
        self.state = state
        self.chart_days = "30"
        self.charts_dirty = True
        self.busy = False
        self._proposal: DoseProposal | None = None

        self._install_actions()
        self.toasts = Adw.ToastOverlay()
        self.stack = Adw.ViewStack()
        self.stack.connect("notify::visible-child-name", self._on_page_changed)

        self.today_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.stack.add_titled_with_icon(_page(self.today_box), "today", "Aujourd'hui", "x-office-calendar-symbolic")
        self.stack.add_titled_with_icon(self._build_charts_page(), "charts", "Graphiques", "x-office-spreadsheet-symbolic")
        self.measures = MeasuresPage(state, self.fetch_from_device, self.refresh)
        self.stack.add_titled_with_icon(self.measures.widget, "measures", "Mesures", "view-list-symbolic")
        self.doses_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.stack.add_titled_with_icon(_page(self.doses_box), "doses", "Doses", "document-edit-symbolic")

        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        self.fetch_button = Gtk.Button(
            child=Adw.ButtonContent(icon_name="view-refresh-symbolic", label="Récupérer"),
            tooltip_text="Lire les mesures du lecteur Accu-Chek branché en USB",
        )
        self.fetch_button.add_css_class("suggested-action")
        self.fetch_button.connect("clicked", lambda _b: self.fetch_from_device())
        self.spinner = Adw.Spinner(visible=False)
        header.pack_start(self.fetch_button)
        header.pack_start(self.spinner)

        menu = Gio.Menu()
        menu.append("Importer un fichier JSON…", "win.import")
        menu.append("Exporter en PDF…", "win.export")
        menu.append("Modifier la dose…", "win.manual-dose")
        sync = Gio.Menu()
        sync.append("Exporter la base pour la tablette…", "win.export-db")
        sync.append("Fusionner une base Glucofi…", "win.merge-db")
        menu.append_section(None, sync)
        section = Gio.Menu()
        section.append("Préférences", "win.preferences")
        section.append("À propos de Glucofi", "win.about")
        menu.append_section(None, section)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu principal"))
        pdf_button = Gtk.Button(icon_name="x-office-document-symbolic", tooltip_text="Exporter en PDF", action_name="win.export")
        header.pack_end(pdf_button)

        switcher_bar = Adw.ViewSwitcherBar(stack=self.stack)
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.add_bottom_bar(switcher_bar)
        self.toasts.set_child(self.stack)
        toolbar.set_content(self.toasts)
        self.set_content(toolbar)

        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 600sp"))
        breakpoint.add_setter(switcher_bar, "reveal", True)
        breakpoint.add_setter(switcher, "visible", False)
        self.measures.add_narrow_setters(breakpoint)
        self.add_breakpoint(breakpoint)

        self.refresh()
        if self.state.needs_onboarding:
            GLib.idle_add(self._onboard)

    # Actions

    def _install_actions(self):
        for name, callback in (
            ("import", self.import_file),
            ("export", self.export_pdf),
            ("manual-dose", lambda: manual_dose_dialog(self, self.state, self.refresh)),
            ("export-db", self.export_db),
            ("merge-db", self.merge_db),
            ("preferences", self.show_preferences),
            ("about", self.show_about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)

    def _onboard(self):
        onboarding_dialog(self, self.state, self.refresh)
        return False

    def toast(self, message: str, button: str | None = None, on_button=None, timeout: int = 5) -> None:
        toast = Adw.Toast(title=message, timeout=timeout)
        if button and on_button:
            toast.set_button_label(button)
            toast.connect("button-clicked", lambda _t: on_button())
        self.toasts.add_toast(toast)

    def error(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", "Fermer")
        dialog.present(self)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.spinner.set_visible(busy)
        self.fetch_button.set_sensitive(not busy)

    # Lecteur

    def fetch_from_device(self) -> None:
        if self.busy:
            return
        self.set_busy(True)
        self.toast("Lecture du lecteur en cours… Ne débranchez pas l'appareil.", timeout=3)
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def _fetch_worker(self) -> None:
        try:
            result = device.fetch(raw_dir=self.state.raw_dir)
            GLib.idle_add(self._fetch_done, result, None)
        except device.DeviceError as exc:
            log.warning("lecture impossible : %s", exc)
            GLib.idle_add(self._fetch_done, None, str(exc))
        except Exception as exc:  # noqa: BLE001 - toute erreur doit remonter à l'écran
            log.exception("erreur inattendue pendant la lecture")
            GLib.idle_add(self._fetch_done, None, f"Erreur inattendue : {exc}")

    def _fetch_done(self, result, error: str | None) -> bool:
        self.set_busy(False)
        if error:
            self.error("Lecture impossible", error)
            return False
        summary = self.state.import_fetch(result)
        self.toast(import_message(summary))
        if result.warnings:
            self.error("Lecture terminée, à vérifier", "\n\n".join(result.warnings))
        self.refresh()
        return False

    def import_file(self) -> None:
        dialog = Gtk.FileDialog(title="Importer un export accuchek (JSON)")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        json_filter = Gtk.FileFilter(name="Export JSON")
        json_filter.add_pattern("*.json")
        filters.append(json_filter)
        dialog.set_filters(filters)

        def done(dlg, result):
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            try:
                summary = self.state.import_file(Path(file.get_path()))
            except device.DeviceError as exc:
                self.error("Import impossible", str(exc))
                return
            self.toast(import_message(summary))
            self.refresh()

        dialog.open(self, None, done)

    # Base : export et fusion avec la tablette

    def export_db(self) -> None:
        dialog = Gtk.FileDialog(title="Exporter la base Glucofi", initial_name=f"glucofi-pc-{datetime.now():%Y-%m-%d}.db")

        def done(dlg, result):
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return
            try:
                path = self.state.export_db(Path(file.get_path()))
            except (OSError, RuntimeError) as exc:
                self.error("Export impossible", str(exc))
                return
            self.toast(f"Base exportée : {path.name}. Copiez-la sur la tablette, puis « Fusionner » sur la tablette.", timeout=8)

        dialog.save(self, None, done)

    def merge_db(self) -> None:
        dialog = Gtk.FileDialog(title="Fusionner une base Glucofi (tablette ou autre PC)")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        db_filter = Gtk.FileFilter(name="Base Glucofi")
        db_filter.add_pattern("*.db")
        filters.append(db_filter)
        dialog.set_filters(filters)

        def done(dlg, result):
            try:
                file = dlg.open_finish(result)
            except GLib.Error:
                return
            try:
                summary = self.state.merge_db(Path(file.get_path()))
            except MergeRefused as exc:
                self.error("Fusion impossible", str(exc))
                return
            self.toast(merge_message(summary), timeout=8)
            if summary.warnings:
                self.error("Fusion terminée, à vérifier", "\n\n".join(summary.warnings))
            self.refresh()

        dialog.open(self, None, done)

    # Export PDF

    def export_pdf(self) -> None:
        if not HAS_MPL:
            self.error("Export impossible", "Installez matplotlib (paquet python3-matplotlib)")
            return
        if self.state.needs_onboarding:
            return
        chooser = Adw.AlertDialog(heading="Exporter en PDF", body="Période couverte par le rapport :")
        chooser.add_response("cancel", "Annuler")
        for days, label in CHART_PERIODS:
            chooser.add_response(days, label)
        chooser.set_response_appearance(self.chart_days, Adw.ResponseAppearance.SUGGESTED)
        chooser.set_default_response(self.chart_days)
        chooser.set_close_response("cancel")
        chooser.connect("response", lambda _d, resp: resp != "cancel" and self._choose_pdf_path(int(resp)))
        chooser.present(self)

    def _choose_pdf_path(self, days: int) -> None:
        name = self.state.patient_name.replace(" ", "-").lower() or "patient"
        dialog = Gtk.FileDialog(title="Enregistrer le rapport", initial_name=f"glycemie-{name}-{datetime.now():%Y-%m-%d}.pdf")

        def done(dlg, result):
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return
            self._build_pdf(Path(file.get_path()), days)

        dialog.save(self, None, done)

    def _build_pdf(self, path: Path, days: int) -> None:
        from services.report.pdf import ReportInput, build_report

        since, until = self.state.period_bounds(days)
        data = ReportInput(
            readings=self.state.readings(),
            changes=self.state.dose_changes(),
            settings=self.state.settings,
            since=since,
            until=until,
            patient_name=self.state.patient_name,
            proposal=self.state.proposal(),
            protocol=protocol_sections(self.state.settings),
            protocol_history=[
                (f"{change.effective:%d/%m/%Y %H:%M}" + (f" · {change.note}" if change.note else ""), lines)
                for change, lines in protocol_history(self.state.protocol_changes())
            ],
        )
        self.set_busy(True)

        def worker():
            try:
                build_report(data, path)
                GLib.idle_add(self._pdf_done, path, None)
            except Exception as exc:  # noqa: BLE001
                log.exception("échec de l'export PDF")
                GLib.idle_add(self._pdf_done, path, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _pdf_done(self, path: Path, error: str | None) -> bool:
        self.set_busy(False)
        if error:
            self.error("Export PDF impossible", error)
            return False
        log.info("PDF exporté : %s", path)
        self.toast(f"PDF enregistré : {path.name}", "Ouvrir", lambda: Gtk.FileLauncher.new(Gio.File.new_for_path(str(path))).launch(self, None, None))
        return False

    # Préférences / À propos

    def show_preferences(self) -> None:
        def saved(error: str | None):
            if error:
                self.toast(error)
            self.charts_dirty = True
            self.refresh()

        preferences_dialog(self, self.state, saved)

    def show_about(self) -> None:
        from app.main import APP_ID, VERSION

        about = Adw.AboutDialog(
            application_name="Glucofi",
            application_icon=APP_ID,
            version=VERSION,
            developer_name="Ronnarrdd",
            comments="Suivi glycémique avec un lecteur Accu-Chek Guide et proposition d'ajustement de la dose d'insuline du soir selon le protocole du médecin.",
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/Ronnarrdd/Glucofi",
            issue_url="https://github.com/Ronnarrdd/Glucofi/issues",
        )
        about.present(self)

    # Rafraîchissement

    def refresh(self) -> None:
        self._proposal = self.state.proposal()
        self._build_today()
        self._build_doses()
        self.measures.refresh()
        self.charts_dirty = True
        if self.stack.get_visible_child_name() == "charts":
            self._build_charts()

    def _on_page_changed(self, *_args) -> None:
        if self.stack.get_visible_child_name() == "charts" and self.charts_dirty:
            self._build_charts()

    # Aujourd'hui

    def _build_today(self) -> None:
        box = self.today_box
        _clear(box)
        proposal = self._proposal
        if proposal is None:
            box.append(Adw.StatusPage(
                icon_name="x-office-calendar-symbolic",
                title="Bienvenue",
                description="Recopiez le protocole de votre ordonnance pour commencer le suivi.",
            ))
            return

        for alert in proposal.alerts:
            banner = Adw.ActionRow(title=alert.message, title_lines=0)
            banner.set_use_markup(False)
            icon, css = ALERT_STYLES[alert.level]
            banner.add_prefix(Gtk.Image(icon_name=icon))
            if css:
                banner.add_css_class(css)
            frame = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            frame.add_css_class("boxed-list")
            frame.append(banner)
            box.append(frame)

        current = proposal.current
        insulin = self.state.settings.insulin
        doses = Gtk.Box(spacing=12, homogeneous=True)
        for target, ui in ((DoseTarget.MORNING, current.morning_ui), (DoseTarget.EVENING, current.evening_ui)):
            adjustment = proposal.adjustment(target)
            if adjustment is not None and adjustment.changes_dose:
                doses.append(self._dose_card(
                    DOSE_TARGET_LABELS_FR[target], f"{adjustment.proposed_ui} UI", f"proposé (actuellement {ui} UI)", accent=True,
                ))
            else:
                doses.append(self._dose_card(DOSE_TARGET_LABELS_FR[target], f"{ui} UI", insulin))
        box.append(doses)

        for adjustment in proposal.adjustments:
            box.append(self._adjustment_group(proposal, adjustment))
        for adjustment in proposal.adjustments:
            box.append(self._references_group(adjustment))

        info = Adw.PreferencesGroup(title="Lecteur")
        last = self.state.last_import()
        info.add(Adw.ActionRow(
            title="Dernière récupération",
            subtitle=(f"{last.at:%d/%m/%Y à %H:%M} · {count_fr(last.added, 'nouvelle', 'nouvelles')} sur {last.received}" if last else "Jamais : branchez le lecteur puis cliquez sur Récupérer"),
        ))
        info.add(Adw.ActionRow(
            title="Mesures enregistrées",
            subtitle=(
                f"{self.state.store.count_readings()} dont {self.state.store.count_markers()} avec un marqueur repas"
                f" et {self.state.store.count_notes()} avec une note"
            ),
        ))
        meters = self.state.meters()
        if meters:
            meter_row = Adw.ActionRow(title="Lecteur", subtitle=meter_text(meters[0][0]))
            meter_row.set_use_markup(False)
            info.add(meter_row)
        if last is not None and last.source == "lecteur":
            info.add(Adw.ActionRow(title="Horloge du lecteur", subtitle=clock_text(last)))
        box.append(info)

        disclaimer = Gtk.Label(
            label="Les propositions appliquent le protocole prescrit et ne remplacent pas l'avis du médecin.",
            wrap=True, justify=Gtk.Justification.CENTER,
        )
        disclaimer.add_css_class("dim-label")
        disclaimer.add_css_class("caption")
        box.append(disclaimer)

    def _adjustment_group(self, proposal: DoseProposal, adjustment: DoseAdjustment) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title=f"Ajustement de la {DOSE_NAMES[adjustment.target]}")
        row = Adw.ActionRow(title=RULE_LABELS_FR[adjustment.rule], subtitle=adjustment.reason, subtitle_lines=0)
        row.set_use_markup(False)
        if adjustment.changes_dose:
            validate = Gtk.Button(label=f"Valider {adjustment.proposed_ui} UI", valign=Gtk.Align.CENTER)
            validate.add_css_class("suggested-action")
            validate.connect("clicked", lambda _b: self._confirm_validation(proposal, adjustment))
            row.add_suffix(validate)
            row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        else:
            row.add_prefix(Gtk.Image(icon_name="object-select-symbolic"))
        group.add(row)
        return group

    def _references_group(self, adjustment: DoseAdjustment) -> Gtk.Widget:
        target, ref = adjustment.target, REFERENCE_LABELS_FR[adjustment.target]
        rule = morning_rule_text if target is DoseTarget.EVENING else evening_rule_text
        group = Adw.PreferencesGroup(
            title=f"Glycémies du {ref} depuis le dernier changement de la {DOSE_NAMES[target]}",
            description=rule(self.state.settings),
        )
        shown = sorted([(m.reading, False) for m in adjustment.references] + [(r, True) for r in adjustment.excluded])
        if not shown:
            group.add(Adw.ActionRow(title=f"Aucune glycémie du {ref} pour l'instant"))
        for reading, excluded in reversed(shown[-10:]):
            subtitle = f"{reading.device_time:%H:%M} · {fmt_mg_dl(reading.mg_dl)}{_meal_suffix(reading)}"
            if excluded:
                subtitle += f"\nÉcartée de l'ajustement : {reading.note.summary}"
            elif reading.note is not None and reading.note.summary:
                subtitle += f"\nNote : {reading.note.summary}"
            row = Adw.ActionRow(title=f"{DAYS_FR[reading.day.weekday()]} {reading.day:%d/%m/%Y}", subtitle=subtitle)
            row.set_use_markup(False)
            value = _value_label(reading, self.state, target)
            if excluded:
                row.add_css_class("dim-label")
                value.set_tooltip_text("Écartée de l'ajustement de la dose par une note")
            row.add_suffix(value)
            row.set_activatable(True)
            row.set_tooltip_text("Ajouter ou modifier une note")
            row.connect("activated", lambda _r, rd=reading: note_dialog(self, self.state, rd, self.refresh))
            group.add(row)
        return group

    def _dose_card(self, title: str, value: str, subtitle: str, accent: bool = False) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.add_css_class("card")
        card.add_css_class("dose-card")
        heading = Gtk.Label(label=title)
        heading.add_css_class("title-4")
        number = Gtk.Label(label=value)
        number.add_css_class("dose-value")
        if accent:
            number.add_css_class("accent")
        sub = Gtk.Label(label=subtitle, wrap=True, justify=Gtk.Justification.CENTER)
        sub.add_css_class("dim-label")
        for widget in (heading, number, sub):
            card.append(widget)
        return card

    def _confirm_validation(self, proposal: DoseProposal, adjustment: DoseAdjustment) -> None:
        dose = DOSE_NAMES[adjustment.target]
        when = "ce soir" if adjustment.target is DoseTarget.EVENING else "demain matin"
        dialog = Adw.AlertDialog(
            heading=f"Passer la {dose} de {adjustment.current_ui} à {adjustment.proposed_ui} UI ?",
            body=f"{adjustment.reason}\n\nLa nouvelle dose s'applique à partir de {when}.",
        )
        dialog.add_response("cancel", "Annuler")
        dialog.add_response("validate", "Valider")
        dialog.set_response_appearance("validate", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response != "validate":
                return
            try:
                self.state.validate(proposal, target=adjustment.target)
            except StaleProposal as exc:
                self.toast(str(exc))
            else:
                self.toast(f"{dose.capitalize()} : {adjustment.proposed_ui} UI à partir de {when}")
            self.refresh()

        dialog.connect("response", on_response)
        dialog.present(self)

    # Doses

    def _build_doses(self) -> None:
        box = self.doses_box
        _clear(box)
        group = Adw.PreferencesGroup(title="Historique des doses", description="La dose la plus récente est celle en cours.")
        edit = Gtk.Button(label="Modifier…", valign=Gtk.Align.CENTER, action_name="win.manual-dose")
        edit.add_css_class("flat")
        group.set_header_suffix(edit)
        changes = self.state.dose_changes()
        if not changes:
            group.add(Adw.ActionRow(title="Aucune dose enregistrée"))
        for i, change in enumerate(reversed(changes)):
            details = "\n".join(change.evidence) or change.note
            if change.excluded:
                details += "\nÉcartées : " + "\n".join(change.excluded)
            row = Adw.ActionRow(
                title=f"Matin {change.morning_ui} UI · Soir {change.evening_ui} UI",
                subtitle=f"Depuis le {change.effective:%d/%m/%Y %H:%M} · {RULE_LABELS_FR[change.rule]}" + (f"\n{details}" if details else ""),
                subtitle_lines=0,
            )
            row.set_use_markup(False)
            if i == 0:
                badge = Gtk.Label(label="En cours", valign=Gtk.Align.CENTER)
                badge.add_css_class("accent")
                badge.add_css_class("heading")
                row.add_suffix(badge)
            group.add(row)
        box.append(group)
        self._build_protocol(box)

    def _build_protocol(self, box: Gtk.Box) -> None:
        settings = self.state.settings
        if settings is None:
            return
        group = Adw.PreferencesGroup(title="Protocole en cours", description="Recopié de l'ordonnance ; à modifier dans les Préférences.")
        edit = Gtk.Button(label="Modifier…", valign=Gtk.Align.CENTER, action_name="win.preferences")
        edit.add_css_class("flat")
        group.set_header_suffix(edit)
        for title, lines in protocol_sections(settings):
            row = Adw.ActionRow(title=title, subtitle="\n".join(lines), subtitle_lines=0)
            row.set_use_markup(False)
            group.add(row)
        box.append(group)

        history = Adw.PreferencesGroup(title="Historique du protocole", description="La version la plus récente est celle en cours.")
        entries = protocol_history(self.state.protocol_changes())
        if not entries:
            history.add(Adw.ActionRow(title="Aucune version enregistrée"))
        for i, (change, lines) in enumerate(entries):
            title = f"Depuis le {change.effective:%d/%m/%Y %H:%M}" + (f" · {change.note}" if change.note else "")
            row = Adw.ActionRow(title=title, subtitle="\n".join(lines) or "Aucun changement", subtitle_lines=0)
            row.set_use_markup(False)
            if i == 0:
                badge = Gtk.Label(label="En cours", valign=Gtk.Align.CENTER)
                badge.add_css_class("accent")
                badge.add_css_class("heading")
                row.add_suffix(badge)
            history.add(row)
        box.append(history)

    # Graphiques

    def _build_charts_page(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.append(_toggle_group(CHART_PERIODS, self.chart_days, self._on_chart_period))
        self.charts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.append(self.charts_box)
        return _page(outer, max_width=1100)

    def _on_chart_period(self, name: str | None) -> None:
        if name:
            self.chart_days = name
            self._build_charts()

    def _build_charts(self) -> None:
        self.charts_dirty = False
        _clear(self.charts_box)
        if not HAS_MPL:
            self.charts_box.append(Adw.StatusPage(
                icon_name="dialog-warning-symbolic",
                title="Graphiques indisponibles",
                description="Installez matplotlib (paquet python3-matplotlib)",
            ))
            return
        try:
            self._fill_charts()
        except Exception as exc:  # noqa: BLE001 - un graphique cassé ne doit pas rester silencieux
            log.exception("échec de l'affichage des graphiques")
            _clear(self.charts_box)
            self.charts_box.append(Adw.StatusPage(
                icon_name="dialog-error-symbolic",
                title="Graphiques indisponibles",
                description=f"Erreur : {exc}",
            ))

    def _fill_charts(self) -> None:
        from services.charts import compute_stats
        from services.charts.figures import distribution_figure, figure_png, morning_trend_figure, timeline_figure

        days = int(self.chart_days)
        since, until = self.state.period_bounds(days)
        readings = self.state.readings(days=days)
        changes = self.state.dose_changes()
        settings = self.state.settings
        if settings is None:
            self.charts_box.append(Adw.StatusPage(icon_name="preferences-system-symbolic", title="Protocole à saisir"))
            return

        stats = compute_stats(readings, settings)
        summary = Gtk.Box(spacing=12, homogeneous=True)
        for title, value in (
            ("Mesures", str(stats.count)),
            ("Moyenne", fmt_g_l(round(stats.mean_mg)) if stats.mean_mg is not None else "-"),
            ("Dans l'objectif", f"{stats.pct_in_range:.0f} %"),
            ("Sous l'objectif", f"{stats.pct_low:.0f} %"),
            ("Au-dessus", f"{stats.pct_high:.0f} %"),
        ):
            summary.append(self._stat_card(title, value))
        self.charts_box.append(summary)

        for title, fig in (
            ("Courbe des glycémies", timeline_figure(readings, changes, settings, since, until)),
            ("Glycémies du matin et dose du soir", morning_trend_figure(readings, changes, settings, since, until)),
            ("Répartition", distribution_figure(readings, settings)),
        ):
            label = Gtk.Label(label=title, xalign=0)
            label.add_css_class("title-4")
            texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(figure_png(fig, dpi=CHART_DPI)))
            picture = Gtk.Picture(paintable=texture, content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True)
            picture.set_alternative_text(title)
            fig_w, fig_h = fig.get_size_inches()
            picture.set_size_request(-1, round(CHART_WIDTH_PX * fig_h / fig_w))
            frame = Gtk.Box()
            frame.add_css_class("chart-card")
            frame.append(picture)
            self.charts_box.append(label)
            self.charts_box.append(frame)

    def _stat_card(self, title: str, value: str) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        card.add_css_class("card")
        card.add_css_class("dose-card")
        v = Gtk.Label(label=value)
        v.add_css_class("title-2")
        t = Gtk.Label(label=title)
        t.add_css_class("dim-label")
        card.append(v)
        card.append(t)
        return card
