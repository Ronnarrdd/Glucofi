"""État applicatif sans GTK : fait le lien entre store, lecteur et moteur de dose."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from contracts import ClockAction, DoseChange, DoseProposal, DoseRule, DosingSettings, MeterInfo, Reading, ReadingNote
from services.device import FetchResult, fmt_offset, parse_file
from services.dosing import apply_proposal, can_exclude, fmt_g_l, is_morning_candidate, propose
from services.store import ImportSummary, Store

log = logging.getLogger("glucofi.state")


class StaleProposal(Exception):
    """Les données ont changé depuis l'affichage de la proposition."""


def import_message(summary: ImportSummary) -> str:
    """Toast après un import : nouvelles mesures, ignorées, marqueurs ajoutés, remise à l'heure."""
    message = f"{summary.added} nouvelle(s) mesure(s) sur {summary.received} lue(s)"
    if summary.rejected:
        message += f", {summary.rejected} ignorée(s)"
    if summary.markers_added:
        message += f", {summary.markers_added} marqueur(s) repas ajouté(s)"
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


def morning_rule_text(settings: DosingSettings) -> str:
    return (
        f"Entre {settings.morning_start:%H:%M} et {settings.morning_end:%H:%M} : première mesure « à jeun », "
        "sinon première mesure « avant repas » ou sans marqueur. "
        "Les mesures « après repas », « coucher » et « autre moment » ne comptent pas."
    )


def exclusion_option(reading: Reading, settings: DosingSettings) -> tuple[bool, str]:
    """Peut-on écarter cette mesure de l'ajustement, et l'explication à afficher sous l'interrupteur."""
    if not is_morning_candidate(reading, settings):
        return False, (
            "Cette mesure n'est pas une glycémie du matin possible (heure ou marqueur) : "
            "elle ne sert pas à l'ajustement de la dose."
        )
    if not can_exclude(reading, settings):
        return False, (
            f"Sous le seuil bas ({fmt_g_l(round(settings.low_g_l * 100))}) : une glycémie basse compte toujours. "
            "Si elle vous semble fausse, ne validez pas la baisse proposée."
        )
    return True, (
        "La mesure ne compte plus comme glycémie du matin : la suivante de la plage la remplace, "
        "sinon le jour n'a pas de glycémie du matin et la série de jours hauts repart de zéro."
    )


class FormError(ValueError):
    """Champ de formulaire invalide : `field` désigne la ligne à signaler."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def parse_g_l(field: str, text: str) -> float:
    """« 0,80 » ou « 0.8 » en g/L, entre 0,20 et 5,00."""
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        raise FormError(field, f"« {text} » n'est pas une glycémie en g/L (ex. 1,20)") from None
    if not 0.2 <= value <= 5.0:
        raise FormError(field, f"{text} g/L est hors de la plage 0,20 à 5,00 g/L")
    return round(value, 2)


def parse_count(field: str, text: str, minimum: int, maximum: int) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        raise FormError(field, f"« {text} » n'est pas un nombre entier") from None
    if not minimum <= value <= maximum:
        raise FormError(field, f"{value} est hors de la plage {minimum} à {maximum}")
    return value


def protocol_from_form(form: dict[str, str], base: dict | None = None) -> DosingSettings:
    """Protocole saisi depuis l'ordonnance ; `base` garde les autres réglages (plage du matin, alertes)."""
    insulin = form.get("insulin", "").strip()
    if not insulin:
        raise FormError("insulin", "indiquez le nom de l'insuline prescrite")
    low = parse_g_l("low_g_l", form.get("low_g_l", ""))
    high = parse_g_l("high_g_l", form.get("high_g_l", ""))
    if low >= high:
        raise FormError("high_g_l", "le seuil haut doit être au-dessus du seuil bas")
    values = dict(base or {})
    values.update(
        insulin=insulin,
        low_g_l=low,
        high_g_l=high,
        step_ui=parse_count("step_ui", form.get("step_ui", ""), 1, 20),
        high_streak_days=parse_count("high_streak_days", form.get("high_streak_days", ""), 1, 14),
    )
    return DosingSettings(**values)


def fmt_form_g_l(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}".replace(".", ",")


class AppState:
    def __init__(
        self,
        store: Store,
        data_dir: Path,
        today: Callable[[], date] = date.today,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.store = store
        self.data_dir = data_dir
        self._today = today
        self._now = now

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

    def configure_protocol(self, settings: DosingSettings) -> None:
        self.store.save_dosing_settings(settings)
        log.info(
            "protocole : %s, objectif %s-%s g/L, pas %s UI, %s jours",
            settings.insulin, settings.low_g_l, settings.high_g_l, settings.step_ui, settings.high_streak_days,
        )

    def start_protocol(
        self, patient_name: str, start: datetime, morning_ui: int, evening_ui: int, settings: DosingSettings
    ) -> DoseChange:
        if morning_ui < 0 or evening_ui < 0:
            raise ValueError("une dose ne peut pas être négative")
        self.set_patient_name(patient_name)
        self.configure_protocol(settings)
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

    def proposal(self) -> DoseProposal | None:
        changes = self.store.dose_changes()
        settings = self.settings
        if not changes or settings is None:
            return None
        return propose(self.store.readings(), changes, settings, self._today())

    def validate(self, shown: DoseProposal, note: str = "") -> DoseChange:
        """Valide la proposition affichée, après avoir vérifié qu'elle est toujours d'actualité."""
        fresh = self.proposal()
        if (
            fresh is None
            or fresh.current != shown.current
            or fresh.rule != shown.rule
            or fresh.proposed_evening_ui != shown.proposed_evening_ui
        ):
            raise StaleProposal("Les données ont changé : vérifiez la nouvelle proposition.")
        change = self.store.add_dose_change(apply_proposal(fresh, self._now().replace(second=0, microsecond=0), note))
        log.info("dose validée : %s, soir %s -> %s UI", change.rule.value, fresh.current.evening_ui, change.evening_ui)
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
