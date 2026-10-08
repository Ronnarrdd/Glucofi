"""Écran Aujourd'hui : textes et choix, sans GTK, portés du pont de Glucofi pour Android (bridge.py, TodayScreen.kt).

Mêmes phrases que la tablette : « au lieu de 10 UI », « inchangée », « Matin −1 UI » (signe moins U+2212), date
longue « jeudi 1er octobre », pastilles de preuve, alertes urgentes (danger d'abord) au-dessus des tuiles et
informations sous la dernière mesure. app/today_page.py ne fait que poser ces valeurs dans les widgets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.injections import InjectionsView, injections_view
from app.measures import MONTHS_FR, WEEKDAYS_FR, Marker, marker_of
from app.protocol import DOSE_NAMES, evening_rule_text, morning_rule_text
from app.state import AppState, clock_text, meter_text
from contracts import (
    DOSE_TARGET_LABELS_FR,
    REFERENCE_LABELS_FR,
    RULE_LABELS_FR,
    AlertLevel,
    DoseAdjustment,
    DoseProposal,
    DoseTarget,
    Reading,
    Titration,
    count_fr,
)
from services.dosing import fmt_g_l

REFERENCES_SHOWN = 10
CHIPS_FALLBACK = 3
DAYS_SHORT_FR = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
MINUS = "\u2212"
WHEN = {DoseTarget.EVENING: "ce soir", DoseTarget.MORNING: "demain matin"}
MOMENT_WORDS = {DoseTarget.EVENING: "le soir", DoseTarget.MORNING: "le matin"}
DISCLAIMER = "Glucofi n'est pas un dispositif médical et ne remplace pas l'avis d'un médecin."
TAGLINE = "Glucofi propose, vous validez."


def long_date(day: date) -> str:
    """« mardi 6 octobre », « jeudi 1er octobre »."""
    return f"{WEEKDAYS_FR[day.weekday()]} {day.day}{'er' if day.day == 1 else ''} {MONTHS_FR[day.month - 1]}"


def level(reading: Reading, titration: Titration | None) -> str | None:
    """Niveau par rapport aux seuils de la dose concernée (mg/dL entiers, comme compute_stats)."""
    if titration is None:
        return None
    if reading.mg_dl < round(titration.low_g_l * 100):
        return "low"
    if reading.mg_dl > round(titration.high_g_l * 100):
        return "high"
    return "in"


def level_label(value: str | None, titration: Titration | None) -> str | None:
    if value is None or titration is None:
        return None
    low, high = fmt_g_l(round(titration.low_g_l * 100)), fmt_g_l(round(titration.high_g_l * 100))
    return {
        "low": f"Sous l'objectif (moins de {low})",
        "in": f"Dans l'objectif ({low} à {high})",
        "high": f"Au-dessus de l'objectif (plus de {high})",
    }[value]


@dataclass(frozen=True)
class ReadingItem:
    reading: Reading
    day: str
    hour: str
    g_l: str
    marker: Marker
    level: str | None
    level_label: str | None
    note: str | None
    excluded: bool = False
    evidence: bool = False
    # motif tiré du journal des injections (« dose du soir du 05/10 non prise ») quand c'est lui qui écarte la mesure
    excluded_why: str = ""

    @property
    def chip_text(self) -> str:
        return f"{self.day} {self.g_l}"


def reading_item(
    reading: Reading, titration: Titration | None, excluded: bool = False, evidence: bool = False, excluded_why: str = ""
) -> ReadingItem:
    value = level(reading, titration)
    return ReadingItem(
        reading=reading,
        day=f"{DAYS_SHORT_FR[reading.day.weekday()]} {reading.day:%d/%m}",
        hour=f"{reading.device_time:%H:%M}",
        g_l=fmt_g_l(reading.mg_dl),
        marker=marker_of(reading),
        level=value,
        level_label=level_label(value, titration),
        note=(reading.note.summary or None) if reading.note is not None else None,
        excluded=excluded,
        evidence=evidence,
        excluded_why=excluded_why,
    )


@dataclass(frozen=True)
class AdjustmentView:
    adjustment: DoseAdjustment
    rule_label: str
    reason: str
    references_title: str
    references_rule: str
    references: tuple[ReadingItem, ...]
    confirm_title: str
    confirm_body: str
    when: str


def adjustment_view(state: AppState, adjustment: DoseAdjustment) -> AdjustmentView:
    settings = state.settings
    target, ref = adjustment.target, REFERENCE_LABELS_FR[adjustment.target]
    titration = settings.titration(target)
    shown = sorted([(m.reading, False) for m in adjustment.references] + [(r, True) for r in adjustment.excluded])
    evidence = {(r.device_time, r.mg_dl) for r in adjustment.evidence}
    why = {(r.device_time, r.mg_dl): w for r, w in zip(adjustment.excluded, adjustment.excluded_why)}
    dose = DOSE_NAMES[target]
    rule_text = morning_rule_text if target is DoseTarget.EVENING else evening_rule_text
    return AdjustmentView(
        adjustment=adjustment,
        rule_label=RULE_LABELS_FR[adjustment.rule],
        reason=adjustment.reason,
        references_title=f"Glycémies du {ref} depuis le {adjustment.since:%d/%m/%Y}",
        references_rule=rule_text(settings),
        references=tuple(
            reading_item(
                reading, titration, excluded, (reading.device_time, reading.mg_dl) in evidence,
                why.get((reading.device_time, reading.mg_dl), ""),
            )
            for reading, excluded in reversed(shown[-REFERENCES_SHOWN:])
        ),
        confirm_title=f"Passer la {dose} de {adjustment.current_ui} à {adjustment.proposed_ui} UI ?",
        confirm_body=f"{adjustment.reason}\n\nLa nouvelle dose s'applique à partir de {WHEN[target]}.",
        when=WHEN[target],
    )


@dataclass(frozen=True)
class DoseCard:
    target: DoseTarget
    label: str
    current_ui: int
    proposed_ui: int | None
    status: str
    adjustment: AdjustmentView | None

    @property
    def shown_ui(self) -> int:
        return self.proposed_ui if self.proposed_ui is not None else self.current_ui

    @property
    def description(self) -> str:
        if self.proposed_ui is not None:
            return f"{self.label} : nouvelle dose proposée {self.proposed_ui} UI, {self.status}"
        return f"{self.label} : {self.current_ui} UI, {self.status}"

    @property
    def detail_title(self) -> str:
        if self.proposed_ui is not None:
            return f"Pourquoi {self.proposed_ui} UI {MOMENT_WORDS[self.target]} ?"
        return f"{self.label} : dose inchangée"


def dose_card(state: AppState, proposal: DoseProposal | None, target: DoseTarget, current_ui: int) -> DoseCard:
    adjustment = proposal.adjustment(target) if proposal is not None else None
    changes = adjustment is not None and adjustment.changes_dose
    if changes:
        status = f"au lieu de {current_ui} UI"
    elif adjustment is not None:
        status = "inchangée"
    else:
        status = "pas d'ajustement automatique"
    return DoseCard(
        target=target,
        label=DOSE_TARGET_LABELS_FR[target],
        current_ui=current_ui,
        proposed_ui=adjustment.proposed_ui if changes else None,
        status=status,
        adjustment=None if adjustment is None else adjustment_view(state, adjustment),
    )


def delta(card: DoseCard) -> str | None:
    """« Soir +2 UI », « Matin −1 UI » : le signe moins typographique se lit mieux en gros."""
    if card.proposed_ui is None:
        return None
    d = card.proposed_ui - card.current_ui
    return f"{card.label} +{d} UI" if d >= 0 else f"{card.label} {MINUS}{-d} UI"


def chips(adjustment: AdjustmentView) -> tuple[ReadingItem, ...]:
    """Glycémies qui déclenchent l'ajustement, à défaut les trois dernières retenues, de la plus ancienne à la plus récente."""
    shown = [r for r in adjustment.references if r.evidence]
    if not shown:
        shown = [r for r in adjustment.references if not r.excluded][:CHIPS_FALLBACK]
    return tuple(reversed(shown))


def why_doses(doses: tuple[DoseCard, ...]) -> tuple[tuple[DoseCard, ...], tuple[DoseCard, ...]]:
    """(colonne « Pourquoi ? », feuille du détail) : les doses expliquées, celles qui changent d'abord ; la colonne
    garde la place pour les doses qui changent, le détail montre tout."""
    explained = tuple(sorted((d for d in doses if d.adjustment is not None), key=lambda d: d.proposed_ui is None))
    featured = tuple(d for d in explained if d.proposed_ui is not None) or explained
    return featured, explained


@dataclass(frozen=True)
class LastReading:
    g_l: str
    level: str | None
    level_label: str | None
    when: str


@dataclass(frozen=True)
class AlertItem:
    kind: str
    message: str


@dataclass(frozen=True)
class TodayView:
    date: str
    doses: tuple[DoseCard, ...]
    urgent: tuple[AlertItem, ...]
    info: tuple[AlertItem, ...]
    last_reading: LastReading | None
    insulin: str | None
    readings: int
    meter_rows: tuple[tuple[str, str], ...]
    proposal: DoseProposal | None
    injections: InjectionsView | None = None

    @property
    def footer(self) -> str | None:
        if self.insulin is None:
            return None
        return f"{self.insulin} · {count_fr(self.readings, 'mesure', 'mesures')}"

    @property
    def why(self) -> tuple[tuple[DoseCard, ...], tuple[DoseCard, ...]]:
        return why_doses(self.doses)


def split_alerts(proposal: DoseProposal | None) -> tuple[tuple[AlertItem, ...], tuple[AlertItem, ...]]:
    """(urgentes, informations) : le danger (hypoglycémie) en tête, puis les avertissements, dans leur ordre."""
    alerts = proposal.alerts if proposal is not None else ()
    urgent = [a for a in alerts if a.level is not AlertLevel.INFO]
    urgent.sort(key=lambda a: a.level is not AlertLevel.DANGER)
    return (
        tuple(AlertItem("danger" if a.level is AlertLevel.DANGER else "warning", a.message) for a in urgent),
        tuple(AlertItem("info", a.message) for a in alerts if a.level is AlertLevel.INFO),
    )


def meter_rows(state: AppState) -> tuple[tuple[str, str], ...]:
    """Section Lecteur du détail : dernière récupération, mesures enregistrées, modèle, horloge."""
    last = state.last_import()
    rows = [(
        "Dernière récupération",
        f"{last.at:%d/%m/%Y à %H:%M} · {count_fr(last.added, 'nouvelle', 'nouvelles')} sur {last.received}"
        if last else "Jamais : branchez le lecteur puis « Récupérer les mesures »",
    ), (
        "Mesures enregistrées",
        f"{state.store.count_readings()} dont {state.store.count_markers()} avec un marqueur repas"
        f" et {state.store.count_notes()} avec une note",
    )]
    meters = state.meters()
    if meters:
        rows.append(("Lecteur", meter_text(meters[0][0])))
    if last is not None and last.source == "lecteur":
        rows.append(("Horloge du lecteur", clock_text(last)))
    return tuple(rows)


def today_view(state: AppState) -> TodayView:
    settings = state.settings
    current = state.store.current_dose()
    proposal = state.proposal()
    readings = state.readings()
    doses: tuple[DoseCard, ...] = ()
    if current is not None and settings is not None:
        doses = (
            dose_card(state, proposal, DoseTarget.MORNING, current.morning_ui),
            dose_card(state, proposal, DoseTarget.EVENING, current.evening_ui),
        )
    last = readings[-1] if readings else None
    titration = settings.evening_titration if settings is not None else None
    last_item = None
    if last is not None:
        item = reading_item(last, titration)
        last_item = LastReading(item.g_l, item.level, item.level_label, f"{item.day} {item.hour}")
    urgent, info = split_alerts(proposal)
    return TodayView(
        date=long_date(state._today()),
        doses=doses,
        urgent=urgent,
        info=info,
        last_reading=last_item,
        insulin=settings.insulin if settings is not None else None,
        readings=len(readings),
        meter_rows=meter_rows(state),
        proposal=proposal,
        injections=injections_view(state),
    )
