"""Fenêtre principale : barre du haut, onglets, actions et tâches de fond (lecteur, tablette, PDF).

Chaque onglet est sa page : Aujourd'hui (today_page.py), Mesures (measures_page.py), Graphiques (charts_page.py),
Doses (doses_page.py). La barre du haut est plate sur le sauge : logo et « Glucofi » à gauche, onglets en pilules
au centre (en bas sous 600 px), tablette, PDF et menu à droite. « Récupérer les mesures » est dans la bannière
d'Aujourd'hui, dans le menu et sur Ctrl+R.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk

from app import theme
from app.art import GlucofiMark
from app.charts_page import CHART_PERIODS, HAS_MPL, ChartsPage
from app.components import NavBar, label
from app.dialogs import manual_dose_dialog, onboarding_dialog, preferences_dialog
from app.doses_page import DosesPage
from app.measures_page import MeasuresPage
from app.protocol import protocol_history, protocol_sections
from app.state import AppState, import_message, merge_message
from app.today_page import TodayPage
from contracts.tablet_sync import PC_FILE
from services import device
from services.store import MergeRefused
from services.tablet import TabletError, TabletLink
from services.tablet import result as sync_result

log = logging.getLogger("glucofi.ui")

PAGES = (
    ("today", "Aujourd'hui", "glucofi-home-symbolic", "glucofi-home-fill-symbolic"),
    ("measures", "Mesures", "glucofi-measures-symbolic", "glucofi-measures-symbolic"),
    ("charts", "Graphiques", "glucofi-charts-symbolic", "glucofi-charts-symbolic"),
    ("doses", "Doses", "glucofi-doses-symbolic", "glucofi-doses-fill-symbolic"),
)
NARROW = "max-width: 600sp"
# à 360 px, avec réduire, agrandir et fermer dans la barre, le nom à côté du logo ne tient plus
TINY = "max-width: 400sp"
ACCELS = {"win.fetch": ["<Control>r"], "win.export": ["<Control>p"], "win.preferences": ["<Control>comma"]}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, state: AppState):
        super().__init__(application=application, title="Glucofi", default_width=1100, default_height=800)
        self.state = state
        self.busy = False

        self._install_actions()
        self.toasts = Adw.ToastOverlay()
        self.stack = Adw.ViewStack()
        self.today = TodayPage(self, state, self.refresh, self.toast)
        self.measures = MeasuresPage(state, self.refresh)
        self.charts = ChartsPage(state)
        self.doses = DosesPage(state)
        for (name, title, icon, _active), page in zip(PAGES, (self.today, self.measures, self.charts, self.doses)):
            self.stack.add_titled_with_icon(page.widget, name, title, icon)
        self.stack.connect("notify::visible-child-name", self._on_page_changed)

        header = Adw.HeaderBar()
        header.add_css_class("glucofi-header")
        brand = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER, margin_start=4)
        brand.append(GlucofiMark(size=40))
        self.brand_name = label("Glucofi", "title-large")
        brand.append(self.brand_name)
        brand.update_property([Gtk.AccessibleProperty.LABEL], ["Glucofi"])
        header.pack_start(brand)
        self.nav = NavBar(self.stack, PAGES)
        header.set_title_widget(self.nav)

        menu = Gio.Menu()
        menu.append("Récupérer les mesures", "win.fetch")
        menu.append("Importer un fichier JSON…", "win.import")
        menu.append("Exporter en PDF…", "win.export")
        menu.append("Modifier la dose…", "win.manual-dose")
        sync = Gio.Menu()
        sync.append("Synchroniser avec la tablette (USB)", "win.sync-tablet")
        sync.append("Exporter la base pour la tablette…", "win.export-db")
        sync.append("Fusionner une base Glucofi…", "win.merge-db")
        menu.append_section(None, sync)
        section = Gio.Menu()
        section.append("Préférences", "win.preferences")
        section.append("À propos de Glucofi", "win.about")
        menu.append_section(None, section)
        menu_button = Gtk.MenuButton(icon_name="glucofi-menu-symbolic", menu_model=menu, tooltip_text="Menu principal")
        menu_button.update_property([Gtk.AccessibleProperty.LABEL], ["Menu principal"])
        header.pack_end(menu_button)
        pdf_button = Gtk.Button(icon_name="glucofi-pdf-symbolic", tooltip_text="Exporter en PDF (Ctrl+P)", action_name="win.export")
        pdf_button.update_property([Gtk.AccessibleProperty.LABEL], ["Exporter en PDF"])
        header.pack_end(pdf_button)
        self.sync_button = Gtk.Button(
            icon_name="glucofi-tablet-symbolic",
            tooltip_text="Synchroniser avec la tablette branchée en USB (dans les deux sens)",
            action_name="win.sync-tablet",
        )
        self.sync_button.update_property([Gtk.AccessibleProperty.LABEL], ["Synchroniser avec la tablette"])
        header.pack_end(self.sync_button)
        self.spinner = Adw.Spinner(visible=False)
        header.pack_end(self.spinner)
        for button in (menu_button, pdf_button, self.sync_button):
            button.add_css_class("header-action")

        self.bottom_nav = NavBar(self.stack, PAGES, compact=True)
        bottom = Gtk.Box()
        bottom.add_css_class("bottom-nav")
        bottom.append(self.bottom_nav)
        self.bottom_nav.set_hexpand(True)
        self.toolbar = Adw.ToolbarView(reveal_bottom_bars=False)
        self.toolbar.add_top_bar(header)
        self.toolbar.add_bottom_bar(bottom)
        self.toasts.set_child(self.stack)
        self.toolbar.set_content(self.toasts)
        self.set_content(self.toolbar)

        def narrow(condition: str) -> Adw.Breakpoint:
            breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(condition))
            breakpoint.add_setter(self.toolbar, "reveal-bottom-bars", True)
            breakpoint.add_setter(self.nav, "visible", False)
            for page in (self.today, self.measures, self.charts, self.doses):
                page.narrow_setters(breakpoint)
            self.add_breakpoint(breakpoint)
            return breakpoint

        narrow(NARROW)
        # le dernier point de rupture vérifié l'emporte : celui-ci reprend tout le précédent ; la tablette et le PDF
        # restent dans le menu
        tiny = narrow(TINY)
        for widget in (self.brand_name, self.sync_button, pdf_button):
            tiny.add_setter(widget, "visible", False)
        style_manager = Adw.StyleManager.get_default()
        dark_handler = style_manager.connect("notify::dark", self._on_dark_changed)
        self.connect("destroy", lambda _w: style_manager.disconnect(dark_handler))

        self.refresh()
        if self.state.needs_onboarding:
            GLib.idle_add(self._onboard)

    # Actions

    def _install_actions(self):
        for name, callback in (
            ("fetch", self.fetch_from_device),
            ("onboard", self._onboard),
            ("import", self.import_file),
            ("export", self.export_pdf),
            ("manual-dose", lambda: manual_dose_dialog(self, self.state, self.refresh)),
            ("sync-tablet", self.sync_tablet),
            ("export-db", self.export_db),
            ("merge-db", self.merge_db),
            ("preferences", self.show_preferences),
            ("about", self.show_about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
        application = self.get_application()
        if application is not None:
            for action, accels in ACCELS.items():
                application.set_accels_for_action(action, accels)

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
        for name in ("fetch", "sync-tablet", "import", "merge-db", "manual-dose"):
            self.lookup_action(name).set_enabled(not busy)
        self.today.set_busy(busy)

    @property
    def on_today(self) -> bool:
        return self.stack.get_visible_child_name() == "today" and self.get_mapped()

    # Lecteur

    def fetch_from_device(self) -> None:
        if self.busy:
            return
        self.set_busy(True)
        self.today.set_read("reading")
        if not self.on_today:
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
            self.today.set_read("failed", f"Lecture impossible : {error}")
            if not self.on_today:
                self.error("Lecture impossible", error)
            return False
        summary = self.state.import_fetch(result)
        message = import_message(summary)
        self.today.set_read("done", message, tuple(result.warnings))
        if not self.on_today:
            self.toast(message)
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

    # Tablette : synchronisation par adb (services/tablet)

    def sync_tablet(self) -> None:
        """Base de la tablette fusionnée ici, puis celle du PC fusionnée là-bas : les deux à jour.

        Les échanges adb tournent sur un thread ; la fusion et l'export du PC sur celui-ci, qui possède la base.
        """
        if self.busy:
            return
        self.set_busy(True)
        self.toast("Synchronisation avec la tablette… Ne la débranchez pas.", timeout=3)
        workdir = Path(tempfile.mkdtemp(prefix="glucofi-tablette-"))

        def finish(error: str | None, done=None) -> bool:
            shutil.rmtree(workdir, ignore_errors=True)
            self.set_busy(False)
            if error:
                self.error("Synchronisation impossible", error)
            elif done is not None:
                self._sync_done(done)
            self.refresh()
            return False

        def fetch() -> None:
            try:
                link = TabletLink.connect()
                path = link.fetch(workdir)
            except TabletError as exc:
                log.warning("synchronisation : %s", exc)
                GLib.idle_add(finish, str(exc))
                return
            except Exception as exc:  # noqa: BLE001 - toute erreur doit remonter à l'écran
                log.exception("synchronisation : erreur inattendue")
                GLib.idle_add(finish, f"Erreur inattendue : {exc}")
                return
            GLib.idle_add(merge_here, link, path)

        def merge_here(link: TabletLink, path: Path) -> bool:
            try:
                summary = self.state.merge_db(path)
                pc_db = self.state.export_db(workdir / PC_FILE)
            except MergeRefused as exc:
                return finish(f"La base reçue de la tablette n'a pas pu être fusionnée : {exc}")
            except (OSError, RuntimeError) as exc:
                return finish(f"Export de la base du PC impossible : {exc}")
            threading.Thread(target=send, args=(link, summary, pc_db), daemon=True).start()
            return False

        def send(link: TabletLink, summary, pc_db: Path) -> None:
            try:
                reply = link.send(pc_db)
            except TabletError as exc:
                log.warning("synchronisation, envoi : %s", exc)
                GLib.idle_add(finish, f"{merge_message(summary)}\n\nMais la tablette n'a pas reçu la base du PC : {exc}")
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("synchronisation : erreur inattendue à l'envoi")
                GLib.idle_add(finish, f"Erreur inattendue : {exc}")
                return
            GLib.idle_add(finish, None, sync_result(link.device, summary, reply))

        threading.Thread(target=fetch, daemon=True).start()

    def _sync_done(self, done) -> None:
        name = done.device.model or "la tablette"
        if not done.changed:
            self.toast(f"PC et tablette ({name}) déjà à jour.")
            return
        body = f"Sur le PC : {merge_message(done.pc)}\n\nSur la tablette : {done.tablet.message}"
        if done.warnings:
            body += "\n\nÀ vérifier :\n" + "\n".join(f"• {w}" for w in done.warnings)
            dialog = Adw.AlertDialog(heading="Synchronisation terminée, à vérifier", body=body)
            dialog.add_response("ok", "Fermer")
            dialog.present(self)
        else:
            self.toast(f"PC et tablette ({name}) synchronisés.", button="Détails", on_button=lambda: self.error("Synchronisation terminée", body), timeout=8)

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
        for days, text in CHART_PERIODS:
            chooser.add_response(days, text)
        chooser.set_response_appearance(self.charts.days, Adw.ResponseAppearance.SUGGESTED)
        chooser.set_default_response(self.charts.days)
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
        for family in (theme.DISPLAY_FONT, theme.TEXT_FONT):
            licence = theme.FONT_DIR / "licences" / f"{family}-OFL.txt"
            if licence.is_file():
                about.add_legal_section(f"Police {family}", None, Gtk.License.CUSTOM, licence.read_text(encoding="utf-8"))
        about.add_legal_section(
            "Icônes Material Symbols", "© Google", Gtk.License.APACHE_2_0, None,
        )
        about.present(self)

    # Rafraîchissement

    def refresh(self) -> None:
        self.today.refresh()
        self.doses.refresh()
        self.measures.refresh()
        self.charts.dirty = True
        if self.stack.get_visible_child_name() == "charts":
            self.charts.build()

    def _on_page_changed(self, *_args) -> None:
        if self.stack.get_visible_child_name() == "charts" and self.charts.dirty:
            self.charts.build()

    def _on_dark_changed(self, *_args) -> None:
        """Les graphiques sont des images : refaites aux couleurs du thème clair ou sombre."""
        self.charts.dirty = True
        self._on_page_changed()
