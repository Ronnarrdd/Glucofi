"""Moteur de titration de l'insuline du soir.

Protocole, tous les nombres venant de l'ordonnance (DosingSettings) :
- glycémie du matin < seuil bas : dose du soir - pas ;
- glycémie du matin > seuil haut `high_streak_days` jours consécutifs : dose du soir + pas.

Le moteur ne fait que proposer : une proposition ne devient la dose en cours
qu'une fois validée (apply_proposal + Store.add_dose_change). Les comparaisons
se font en mg/dL entiers pour qu'une glycémie égale à un seuil ne déclenche rien.

Une mesure dont la note demande de l'écarter est traitée comme absente pour le
choix de la glycémie du matin, sauf si elle est sous le seuil bas : une baisse
de dose ne peut jamais être masquée par une note. Les alertes hypo / hyper
voient toutes les mesures, écartées ou non.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Sequence

from contracts import (
    Alert,
    AlertLevel,
    DoseChange,
    DoseProposal,
    DoseRule,
    MG_DL_HIGH,
    MG_DL_LOW,
    MORNING_ALLOWED_MEALS,
    MORNING_FIRST_CHOICE,
    DosingSettings,
    MorningReading,
    Reading,
)

ALERT_LOOKBACK_DAYS = 7


def fmt_g_l(mg_dl: int) -> str:
    if mg_dl >= MG_DL_HIGH:
        return "HI (> 6,00 g/L)"
    if mg_dl <= MG_DL_LOW:
        return "LO (< 0,10 g/L)"
    return f"{mg_dl / 100:.2f}".replace(".", ",") + " g/L"


def fmt_mg_dl(mg_dl: int) -> str:
    if mg_dl >= MG_DL_HIGH:
        return "> 600 mg/dL"
    if mg_dl <= MG_DL_LOW:
        return "< 10 mg/dL"
    return f"{mg_dl} mg/dL"


def fmt_reading(reading: Reading) -> str:
    return f"{reading.device_time:%d/%m/%Y %H:%M} : {fmt_g_l(reading.mg_dl)}"


def fmt_excluded(reading: Reading) -> str:
    """Mesure écartée avec son motif, telle qu'enregistrée dans DoseChange.excluded."""
    motive = reading.note.summary if reading.note is not None else ""
    return f"{fmt_reading(reading)} ({motive})" if motive else fmt_reading(reading)


def _mg(g_l: float) -> int:
    return round(g_l * 100)


def is_morning_candidate(reading: Reading, settings: DosingSettings) -> bool:
    """Dans la plage du matin (bornes incluses) avec un marqueur qui peut faire la glycémie du matin."""
    t = reading.device_time.time()
    return settings.morning_start <= t <= settings.morning_end and reading.meal in MORNING_ALLOWED_MEALS


def can_exclude(reading: Reading, settings: DosingSettings) -> bool:
    """Une glycémie sous le seuil bas compte toujours : l'écarter masquerait une baisse de dose."""
    return reading.mg_dl >= _mg(settings.low_g_l)


def _exclusion_asked(reading: Reading, settings: DosingSettings) -> bool:
    return reading.note is not None and reading.note.exclude_from_dosing and is_morning_candidate(reading, settings)


def excluded_from_dosing(reading: Reading, settings: DosingSettings) -> bool:
    """Glycémie du matin possible que sa note retire du calcul de la dose."""
    return _exclusion_asked(reading, settings) and can_exclude(reading, settings)


def exclusion_refused(reading: Reading, settings: DosingSettings) -> bool:
    """Glycémie du matin possible marquée à écarter, comptée quand même parce qu'elle est sous le seuil bas."""
    return _exclusion_asked(reading, settings) and not can_exclude(reading, settings)


def morning_readings(readings: Sequence[Reading], settings: DosingSettings) -> list[MorningReading]:
    """Glycémie du matin de chaque jour, parmi les mesures de la plage matin (bornes incluses) :
    la première marquée "à jeun", sinon la première marquée "avant repas" ou sans marqueur.

    Les mesures "après repas", "coucher" ou "autre moment" ne sont jamais la glycémie du matin,
    ni les mesures écartées par une note (excluded_from_dosing).
    """
    fasting: dict[date, Reading] = {}
    fallback: dict[date, Reading] = {}
    for reading in sorted(readings):
        if excluded_from_dosing(reading, settings) or not is_morning_candidate(reading, settings):
            continue
        chosen = fasting if reading.meal == MORNING_FIRST_CHOICE else fallback
        chosen.setdefault(reading.day, reading)
    mornings = {**fallback, **fasting}
    return [MorningReading(day, r) for day, r in sorted(mornings.items())]


def propose(
    readings: Sequence[Reading],
    changes: Sequence[DoseChange],
    settings: DosingSettings,
    today: date,
) -> DoseProposal:
    if not changes:
        raise ValueError("aucune dose de départ : renseigner le début du protocole")
    current = max(changes, key=lambda c: (c.effective, c.id or 0))
    low_mg, high_mg = _mg(settings.low_g_l), _mg(settings.high_g_l)

    mornings = [m for m in morning_readings(readings, settings) if m.reading.device_time > current.effective]
    since_current = [r for r in sorted(readings) if r.device_time > current.effective]
    excluded = tuple(r for r in since_current if excluded_from_dosing(r, settings))
    refused = [r for r in since_current if exclusion_refused(r, settings)]
    alerts = list(_alerts(readings, current, settings, today))
    alerts += _exclusion_alerts(excluded, refused, low_mg)

    def proposal(evening_ui: int, rule: DoseRule, reason: str, evidence: Sequence[Reading]) -> DoseProposal:
        return DoseProposal(
            current, evening_ui, rule, reason, tuple(evidence), tuple(mornings), tuple(alerts), excluded
        )

    def keep(reason: str, evidence: Sequence[Reading] = ()) -> DoseProposal:
        return proposal(current.evening_ui, DoseRule.KEEP, reason, evidence)

    if not mornings:
        return keep("Pas encore de glycémie du matin depuis la dernière dose validée.")

    latest = mornings[-1]
    age_days = (today - latest.day).days
    if age_days > settings.stale_days:
        alerts.append(
            Alert(
                AlertLevel.WARNING,
                "stale",
                f"Dernière glycémie du matin il y a {age_days} jours ({latest.day:%d/%m/%Y}) : "
                "récupérez les mesures du lecteur avant d'ajuster la dose.",
            )
        )
        return keep("Données trop anciennes pour proposer un ajustement.", (latest.reading,))

    if latest.reading.mg_dl < low_mg:
        proposed = max(0, current.evening_ui - settings.step_ui)
        reason = (
            f"Glycémie du matin du {latest.day:%d/%m} à {fmt_g_l(latest.reading.mg_dl)}, "
            f"sous {fmt_g_l(low_mg)} : diminuer la dose du soir de {settings.step_ui} UI."
        )
        if proposed == current.evening_ui:
            return keep(reason + " La dose du soir est déjà à 0 UI.", (latest.reading,))
        return proposal(proposed, DoseRule.DECREASE_LOW_MORNING, reason, (latest.reading,))

    streak = _high_streak(mornings, high_mg)
    if len(streak) >= settings.high_streak_days:
        used = streak[-settings.high_streak_days :]
        values = ", ".join(f"{m.day:%d/%m} {fmt_g_l(m.reading.mg_dl)}" for m in used)
        reason = (
            f"Glycémie du matin au-dessus de {fmt_g_l(high_mg)} {settings.high_streak_days} jours de suite "
            f"({values}) : augmenter la dose du soir de {settings.step_ui} UI."
        )
        return proposal(
            current.evening_ui + settings.step_ui, DoseRule.INCREASE_HIGH_MORNINGS, reason, [m.reading for m in used]
        )

    if streak:
        return keep(
            f"Glycémie du matin haute depuis {len(streak)} jour(s) consécutif(s) sur {settings.high_streak_days} "
            "nécessaires : dose inchangée pour l'instant.",
            tuple(m.reading for m in streak),
        )
    return keep(f"Dernière glycémie du matin dans l'objectif ({fmt_g_l(latest.reading.mg_dl)}) : dose inchangée.", (latest.reading,))


def _high_streak(mornings: Sequence[MorningReading], high_mg: int) -> list[MorningReading]:
    """Série de matins > seuil sur des jours calendaires consécutifs, se terminant au dernier matin."""
    streak: list[MorningReading] = []
    for morning in reversed(mornings):
        if morning.reading.mg_dl <= high_mg:
            break
        if streak and (streak[-1].day - morning.day).days != 1:
            break
        streak.append(morning)
    streak.reverse()
    return streak


def _alerts(readings: Sequence[Reading], current: DoseChange, settings: DosingSettings, today: date):
    since = datetime.combine(today - timedelta(days=ALERT_LOOKBACK_DAYS - 1), datetime.min.time())
    recent = [r for r in readings if r.device_time >= since]
    hypo_mg, hyper_mg = _mg(settings.hypo_alert_g_l), _mg(settings.hyper_alert_g_l)

    hypos = [r for r in recent if r.mg_dl < hypo_mg]
    if hypos:
        yield Alert(
            AlertLevel.DANGER,
            "hypo",
            f"{len(hypos)} hypoglycémie(s) sous {fmt_g_l(hypo_mg)} sur {ALERT_LOOKBACK_DAYS} jours "
            f"(dernière : {fmt_reading(hypos[-1])}). Resucrez-vous et prévenez le médecin si cela se répète.",
        )
    hypers = [r for r in recent if r.mg_dl > hyper_mg]
    if hypers:
        yield Alert(
            AlertLevel.DANGER,
            "hyper",
            f"{len(hypers)} glycémie(s) au-dessus de {fmt_g_l(hyper_mg)} sur {ALERT_LOOKBACK_DAYS} jours "
            f"(dernière : {fmt_reading(hypers[-1])}). Contactez le médecin si cela persiste.",
        )
    if current.evening_ui == 0:
        yield Alert(AlertLevel.WARNING, "evening_zero", "La dose du soir est à 0 UI : faites le point avec le médecin.")


def _exclusion_alerts(excluded: Sequence[Reading], refused: Sequence[Reading], low_mg: int) -> list[Alert]:
    alerts = []
    if excluded:
        alerts.append(Alert(
            AlertLevel.INFO,
            "excluded",
            f"{len(excluded)} glycémie(s) du matin écartée(s) de l'ajustement depuis la dernière dose : "
            + " ; ".join(fmt_excluded(r) for r in excluded) + ".",
        ))
    if refused:
        alerts.append(Alert(
            AlertLevel.WARNING,
            "exclusion_refused",
            f"{len(refused)} glycémie(s) du matin sous {fmt_g_l(low_mg)} marquée(s) à écarter, mais comptée(s) quand même : "
            + " ; ".join(fmt_reading(r) for r in refused)
            + ". Une glycémie basse n'est jamais écartée ; ne validez pas une baisse que vous jugez fausse.",
        ))
    return alerts


def apply_proposal(proposal: DoseProposal, now: datetime, note: str = "") -> DoseChange:
    """Transforme une proposition validée par l'utilisateur en nouvelle dose en cours."""
    if not proposal.changes_dose:
        raise ValueError("cette proposition ne modifie pas la dose")
    if now < proposal.current.effective:
        raise ValueError("la validation ne peut pas précéder la dose en cours")
    return DoseChange(
        effective=now,
        morning_ui=proposal.current.morning_ui,
        evening_ui=proposal.proposed_evening_ui,
        rule=proposal.rule,
        evidence=tuple(fmt_reading(r) for r in proposal.evidence),
        note=note,
        excluded=tuple(fmt_excluded(r) for r in proposal.excluded),
    )
