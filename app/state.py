"""État applicatif sans GTK : fait le lien entre store, lecteur et moteur de dose."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from app.protocol import (  # noqa: F401  (réexportés : dialogues, pont Android)
    DOSE_NAMES,
    FormError,
    evening_rule_text,
    fmt_form_g_l,
    morning_rule_text,
    parse_count,
    parse_g_l,
    protocol_diff,
    protocol_from_form,
    protocol_history,
    protocol_sections,
)
from contracts import (
    REFERENCE_LABELS_FR,
    ClockAction,
    DoseChange,
    DoseProposal,
    DoseRule,
    DoseTarget,
    DosingSettings,
    Injection,
    InjectionState,
    MealEntry,
    MealSlot,
    MeterInfo,
    ProtocolChange,
    Reading,
    ReadingNote,
    count_fr,
)
from services.adherence import missed_doses
from services.device import FetchResult, fmt_offset, parse_file
from services.dosing import apply_adjustment, can_exclude, fmt_g_l, propose, reference_target
from services.meals import MealEstimate, estimate_meal, load_api_key
from services.store import ImportSummary, MergeSummary, Store

log = logging.getLogger("glucofi.state")


class StaleProposal(Exception):
    """Les données ont changé depuis l'affichage de la proposition."""


def import_message(summary: ImportSummary) -> str:
    """Toast après un import : nouvelles mesures, ignorées, marqueurs ajoutés, remise à l'heure."""
    message = (
        f"{count_fr(summary.added, 'nouvelle mesure', 'nouvelles mesures')} "
        f"sur {count_fr(summary.received, 'lue', 'lues')}"
    )
    if summary.rejected:
        message += f", {count_fr(summary.rejected, 'ignorée', 'ignorées')}"
    if summary.markers_added:
        message += f", {count_fr(summary.markers_added, 'marqueur repas ajouté', 'marqueurs repas ajoutés')}"
    if summary.clock_action == ClockAction.SET and summary.clock_offset_s is not None:
        message += f", lecteur remis à l'heure ({fmt_offset(summary.clock_offset_s)} corrigée)"
    return message


def meter_text(meter: MeterInfo) -> str:
    parts = [meter.model_name]
    if meter.serial:
        parts.append(f"n° {meter.serial}")
    if meter.firmware:
        parts.append(f"logiciel {meter.firmware}")
    return " · ".join(parts)


def clock_text(summary: ImportSummary) -> str:
    """État de l'horloge du lecteur lors de la dernière récupération."""
    offset = summary.clock_offset_s
    action = summary.clock_action
    if action is None:
        return "Inconnue (lecteur ancien ou import de fichier)"
    if action == ClockAction.SET and offset is not None:
        return f"Remise à l'heure du PC le {summary.at:%d/%m/%Y} ({fmt_offset(offset)} corrigée)"
    if offset is None:
        return "Écart avec le PC inconnu"
    gap = "à l'heure" if offset == 0 else fmt_offset(offset)
    suffix = {
        ClockAction.WITHIN_TOLERANCE: "",
        ClockAction.NOT_SETTABLE: " ; ce lecteur ne se règle pas par USB",
        ClockAction.PC_NOT_SYNCHRONIZED: " ; heure du PC non synchronisée, lecteur laissé tel quel",
        ClockAction.REJECTED: " ; le lecteur a refusé la mise à l'heure",
        ClockAction.NOT_REQUESTED: " ; mise à l'heure non demandée",
    }.get(action, "")
    return f"{gap[0].upper()}{gap[1:]} le {summary.at:%d/%m/%Y}{suffix}"


def exclusion_option(reading: Reading, settings: DosingSettings) -> tuple[bool, str]:
    """Peut-on écarter cette mesure de l'ajustement, et l'explication à afficher sous l'interrupteur."""
    target = reference_target(reading, settings)
    if target is None:
        what = "une glycémie du matin ou du soir" if settings.morning_titration is not None else "une glycémie du matin"
        return False, (
            f"Cette mesure n'est pas {what} possible (heure ou marqueur) : "
            "elle ne sert pas à l'ajustement de la dose."
        )
    ref, dose = REFERENCE_LABELS_FR[target], DOSE_NAMES[target]
    if not can_exclude(reading, settings):
        low = settings.titration(target).low_g_l
        return False, (
            f"Sous le seuil bas ({fmt_g_l(round(low * 100))}) : une glycémie basse compte toujours. "
            "Si elle vous semble fausse, ne validez pas la baisse proposée."
        )
    return True, (
        f"La mesure ne compte plus comme glycémie du {ref} pour la {dose} : la suivante de la plage la remplace, "
        f"sinon le jour n'a pas de glycémie du {ref} et la série de jours hauts repart de zéro."
    )


def merge_message(summary: MergeSummary) -> str:
    """Résultat d'une fusion, en une phrase (toast, bandeau) ; les alertes sont affichées à part."""
    if not summary.changed:
        return f"Rien de nouveau dans {summary.source} : les deux bases étaient déjà à jour."
    parts = [
        count_fr(count, one, many)
        for count, one, many in (
            (summary.readings_added, "mesure ajoutée", "mesures ajoutées"),
            (summary.markers_added, "marqueur repas ajouté", "marqueurs repas ajoutés"),
            (summary.notes_added + summary.notes_updated, "note ajoutée", "notes ajoutées"),
            (summary.injections_added + summary.injections_updated, "injection renseignée", "injections renseignées"),
            (summary.meals_added + summary.meals_updated, "repas renseigné", "repas renseignés"),
            (summary.doses_added, "dose validée ajoutée", "doses validées ajoutées"),
            (summary.protocol_versions_added, "version du protocole ajoutée", "versions du protocole ajoutées"),
        )
        if count
    ]
    text = f"Fusion de {summary.source} : " + (", ".join(parts) if parts else "lectures du lecteur ajoutées")
    if summary.protocol_changed:
        text += " ; protocole mis à jour"
    return text + "."


class AppState:
    def __init__(
        self,
        store: Store,
        data_dir: Path,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = datetime.now,
        meal_estimator: Callable[[str], MealEstimate] | None = None,
    ):
        self.store = store
        self.data_dir = data_dir
        self._today = today
        self._now = now
        # remplaçable (tests, tablette) ; par défaut Gemini, avec la clé de GEMINI_API_KEY ou du fichier .env
        self.meal_estimator = meal_estimator or (lambda text: estimate_meal(text, load_api_key()))

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def settings(self) -> DosingSettings | None:
        """None tant que le protocole n'a pas été saisi : aucune proposition ni seuil sans lui."""
        return self.store.dosing_settings()

    @property
    def patient_name(self) -> str:
        return self.store.get_setting("patient_name", "")

    def set_patient_name(self, name: str) -> None:
        self.store.set_setting("patient_name", name.strip())

    @property
    def needs_start_dose(self) -> bool:
        return self.store.current_dose() is None

    @property
    def needs_onboarding(self) -> bool:
        """Dose de départ ou protocole manquant (installation neuve, ou base d'avant le protocole saisi)."""
        return self.needs_start_dose or self.settings is None

    def configure_protocol(
        self, settings: DosingSettings, note: str = "", effective: datetime | None = None
    ) -> ProtocolChange | None:
        """Nouveau protocole (ordonnance) ; rend la version ajoutée à l'historique, None si rien n'a changé."""
        previous = self.settings
        effective = effective or self._now().replace(second=0, microsecond=0)
        change = self.store.save_dosing_settings(settings, note=note, effective=effective)
        if change is not None:
            log.info("protocole %s : %s", "saisi" if previous is None else "modifié", " ; ".join(protocol_diff(previous, settings)))
        return change

    def protocol_changes(self) -> list[ProtocolChange]:
        return self.store.protocol_changes()

    def start_protocol(
        self, patient_name: str, start: datetime, morning_ui: int, evening_ui: int, settings: DosingSettings
    ) -> DoseChange:
        if morning_ui < 0 or evening_ui < 0:
            raise ValueError("une dose ne peut pas être négative")
        self.set_patient_name(patient_name)
        self.configure_protocol(settings, effective=start)
        change = self.store.add_dose_change(
            DoseChange(start, morning_ui, evening_ui, DoseRule.START)
        )
        log.info("début du protocole %s : matin %s UI, soir %s UI", start, morning_ui, evening_ui)
        return change

    def readings(self, days: int | None = None) -> list[Reading]:
        if days is None:
            return self.store.readings()
        since = datetime.combine(self._today() - timedelta(days=days - 1), datetime.min.time())
        return self.store.readings(since=since)

    def period_bounds(self, days: int) -> tuple[datetime, datetime]:
        until = datetime.combine(self._today() + timedelta(days=1), datetime.min.time())
        return until - timedelta(days=days), until

    def dose_changes(self) -> list[DoseChange]:
        return self.store.dose_changes()

    def report_input(self, days: int):
        """Tout ce que le rapport PDF pour le médecin lit (PC et tablette) sur les `days` derniers jours."""
        from services.report.pdf import ReportInput

        since, until = self.period_bounds(days)
        return ReportInput(
            readings=self.readings(),
            changes=self.dose_changes(),
            settings=self.settings,
            since=since,
            until=until,
            patient_name=self.patient_name,
            proposal=self.proposal(),
            injections=self.injections(),
            generated_at=self._now(),
            protocol=protocol_sections(self.settings),
            protocol_history=[
                (f"{change.effective:%d/%m/%Y %H:%M}" + (f" · {change.note}" if change.note else ""), lines)
                for change, lines in protocol_history(self.protocol_changes())
            ],
        )

    def proposal(self) -> DoseProposal | None:
        changes = self.store.dose_changes()
        settings = self.settings
        if not changes or settings is None:
            return None
        return propose(
            self.store.readings(), changes, settings, self._today(), self.store.injections(), self._now()
        )

    def injections(self, since: date | None = None, until: date | None = None) -> list[Injection]:
        """Journal des injections : doses déclarées prises ou non prises (les autres sont « non renseignées »)."""
        return self.store.injections(since, until)

    def missed(self) -> frozenset:
        """Doses déclarées non prises, au format que le moteur et les vues attendent."""
        return missed_doses(self.store.injections())

    def set_injection(self, day: date, target: DoseTarget, state: InjectionState | None) -> None:
        """Déclare la dose `target` du jour `day` prise ou non prise ; None la remet à « non renseignée »."""
        if day > self._today():
            raise ValueError("une dose à venir ne peut pas être déclarée")
        self.store.set_injection(day, target, state)
        log.info("injection : %s du %s, %s", DOSE_NAMES[target], day, state.value if state else "non renseignée")

    def validate(self, shown: DoseProposal, note: str = "", target: DoseTarget = DoseTarget.EVENING) -> DoseChange:
        """Valide l'ajustement affiché d'une dose, après avoir vérifié qu'il est toujours d'actualité."""
        fresh = self.proposal()
        before, after = shown.adjustment(target), fresh.adjustment(target) if fresh is not None else None
        if (
            after is None
            or before is None
            or fresh.current != shown.current
            or (after.rule, after.proposed_ui, after.current_ui) != (before.rule, before.proposed_ui, before.current_ui)
        ):
            raise StaleProposal("Les données ont changé : vérifiez la nouvelle proposition.")
        change = self.store.add_dose_change(
            apply_adjustment(fresh, target, self._now().replace(second=0, microsecond=0), note)
        )
        log.info("dose validée : %s, %s %s -> %s UI", change.rule.value, DOSE_NAMES[target], after.current_ui, after.proposed_ui)
        return change

    def manual_change(self, morning_ui: int, evening_ui: int, note: str) -> DoseChange:
        if morning_ui < 0 or evening_ui < 0:
            raise ValueError("une dose ne peut pas être négative")
        change = self.store.add_dose_change(
            DoseChange(self._now().replace(second=0, microsecond=0), morning_ui, evening_ui, DoseRule.MANUAL, note=note)
        )
        log.info("dose modifiée manuellement : matin %s UI, soir %s UI (%s)", morning_ui, evening_ui, note)
        return change

    def set_note(self, reading: Reading, note: ReadingNote | None) -> None:
        self.store.set_note(reading, note)
        if note is None or note.empty:
            log.info("note effacée : mesure du %s, %s mg/dL", reading.device_time, reading.mg_dl)
            return
        log.info(
            "note : mesure du %s, %s mg/dL, étiquettes %s, %s caractères de texte, %s",
            reading.device_time, reading.mg_dl, [tag.value for tag in note.tags], len(note.text),
            "écartée de l'ajustement" if note.exclude_from_dosing else "comptée pour l'ajustement",
        )

    def import_fetch(self, result: FetchResult) -> ImportSummary:
        for reason in result.rejected:
            log.warning("mesure du lecteur non retenue : %s", reason)
        summary = self.store.import_readings(
            result.readings, "lecteur", rejected=len(result.rejected),
            meter=result.meter, clock=result.clock, glucose=result.glucose,
        )
        log.info(
            "import lecteur : %s reçues, %s nouvelles, %s rejetées, %s marqueurs ajoutés, copie %s",
            summary.received, summary.added, summary.rejected, summary.markers_added, result.raw_path,
        )
        return summary

    def meters(self):
        return self.store.meters()

    def import_file(self, path: Path) -> ImportSummary:
        parsed = parse_file(path)
        for reason in parsed.rejected:
            log.warning("mesure du fichier non retenue : %s", reason)
        summary = self.store.import_readings(
            parsed.readings, f"fichier:{Path(path).name}", rejected=len(parsed.rejected),
            meter=parsed.meter, clock=parsed.clock, glucose=parsed.glucose,
        )
        log.info(
            "import fichier %s : %s reçues, %s nouvelles, %s rejetées, %s marqueurs ajoutés",
            path, summary.received, summary.added, summary.rejected, summary.markers_added,
        )
        return summary

    def last_import(self) -> ImportSummary | None:
        return self.store.last_import()

    def export_db(self, path: Path) -> Path:
        """Copie de la base à fusionner sur l'autre appareil (PC ou tablette)."""
        return self.store.export_to(path)

    def merge_db(self, path: Path) -> MergeSummary:
        """Fusionne la base d'un autre appareil ; MergeRefused si le fichier n'est pas une base Glucofi lisible."""
        summary = self.store.merge_from(path)
        for warning in summary.warnings:
            log.warning("fusion de %s : %s", summary.source, warning)
        return summary

    # Journal alimentaire

    def meals(self, since: date | None = None, until: date | None = None) -> list[MealEntry]:
        return self.store.meals(since, until)

    def estimate_meal(self, text: str) -> MealEstimate:
        """Estimation par Gemini (réseau, plusieurs secondes : hors du fil de l'interface). Lève EstimateError."""
        return self.meal_estimator(text)

    def save_meal(self, entry: MealEntry) -> None:
        if entry.day > self._today():
            raise ValueError("un repas à venir ne peut pas être enregistré")
        self.store.set_meal(entry)
        log.info("repas : %s du %s, %s", entry.slot.value, entry.day, entry.source or "sans estimation")

    def clear_meal(self, day: date, slot: MealSlot) -> None:
        self.store.set_meal(None, day=day, slot=slot)
        log.info("repas : %s du %s effacé", slot.value, day)
