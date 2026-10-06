"""Moteur de titration des doses d'insuline.

Deux ajustements, chacun d'après sa glycémie de référence, tous les nombres venant de l'ordonnance
(DosingSettings) :
- la dose du soir d'après la glycémie du matin, toujours ;
- la dose du matin d'après la glycémie du soir (avant le dîner), si l'ordonnance le prévoit
  (DosingSettings.morning_titration).

Règle de chaque ajustement (Titration) :
- dernière glycémie de référence sous le seuil bas : dose - pas ; sous un palier de baisse, c'est le
  pas du palier le plus bas franchi qui s'applique ;
- glycémie de référence au-dessus du seuil haut `high_streak_days` jours consécutifs : dose + pas ;
  un palier de hausse atteint (seuil plus haut, son pas, ses jours) l'emporte, le plus haut d'abord ;
- une baisse passe toujours avant une hausse.

Chaque dose a son propre suivi : seules comptent les glycémies de référence postérieures au dernier
changement de cette dose (ou au début du protocole). Changer la dose du matin ne remet pas à zéro le
suivi de la dose du soir, et inversement.

Le moteur ne fait que proposer : une proposition ne devient la dose en cours qu'une fois validée
(apply_adjustment + Store.add_dose_change). Les comparaisons se font en mg/dL entiers pour qu'une
glycémie égale à un seuil ne déclenche rien.

Une mesure dont la note demande de l'écarter est traitée comme absente pour le choix de la glycémie de
référence, sauf si elle est sous le seuil bas de son ajustement : une baisse de dose ne peut jamais être
masquée par une note. Les alertes hypo / hyper voient toutes les mesures, écartées ou non.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Sequence

from contracts import (
    EVENING_ALLOWED_MEALS,
    EVENING_FIRST_CHOICE,
    MG_DL_HIGH,
    MG_DL_LOW,
    MORNING_ALLOWED_MEALS,
    MORNING_FIRST_CHOICE,
    REFERENCE_LABELS_FR,
    Alert,
    AlertLevel,
    DoseAdjustment,
    DoseChange,
    DoseProposal,
    DoseRule,
    DoseTarget,
    DosingSettings,
    MorningReading,
    Reading,
)

ALERT_LOOKBACK_DAYS = 7

DOSE_WORDS = {DoseTarget.EVENING: "du soir", DoseTarget.MORNING: "du matin"}
RULES = {
    DoseTarget.EVENING: (DoseRule.DECREASE_LOW_MORNING, DoseRule.INCREASE_HIGH_MORNINGS),
    DoseTarget.MORNING: (DoseRule.DECREASE_LOW_EVENING, DoseRule.INCREASE_HIGH_EVENINGS),
}
# suffixe des codes d'alerte propres à un ajustement : "stale" pour la dose du soir, "stale_morning" pour celle du matin
CODE_SUFFIX = {DoseTarget.EVENING: "", DoseTarget.MORNING: "_morning"}


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


def _ui(change: DoseChange, target: DoseTarget) -> int:
    return change.evening_ui if target is DoseTarget.EVENING else change.morning_ui


def is_reference_candidate(reading: Reading, settings: DosingSettings, target: DoseTarget) -> bool:
    """Dans la plage de la glycémie de référence de `target` (bornes incluses), avec un marqueur admis."""
    t = reading.device_time.time()
    if target is DoseTarget.EVENING:
        return settings.morning_start <= t <= settings.morning_end and reading.meal in MORNING_ALLOWED_MEALS
    return (
        settings.morning_titration is not None
        and settings.evening_start <= t <= settings.evening_end
        and reading.meal in EVENING_ALLOWED_MEALS
    )


def is_morning_candidate(reading: Reading, settings: DosingSettings) -> bool:
    """Dans la plage du matin (bornes incluses) avec un marqueur qui peut faire la glycémie du matin."""
    return is_reference_candidate(reading, settings, DoseTarget.EVENING)


def is_evening_candidate(reading: Reading, settings: DosingSettings) -> bool:
    """Glycémie du soir possible : plage du soir, « avant repas » ou sans marqueur, dose du matin ajustée."""
    return is_reference_candidate(reading, settings, DoseTarget.MORNING)


def reference_target(reading: Reading, settings: DosingSettings) -> DoseTarget | None:
    """Dose que cette mesure peut servir à ajuster (les deux plages ne se chevauchent jamais)."""
    for target in settings.targets:
        if is_reference_candidate(reading, settings, target):
            return target
    return None


def can_exclude(reading: Reading, settings: DosingSettings) -> bool:
    """Une glycémie sous le seuil bas de son ajustement compte toujours : l'écarter masquerait une baisse de dose."""
    titration = settings.titration(reference_target(reading, settings) or DoseTarget.EVENING)
    return reading.mg_dl >= _mg(titration.low_g_l)


def _exclusion_asked(reading: Reading, settings: DosingSettings) -> bool:
    return (
        reading.note is not None
        and reading.note.exclude_from_dosing
        and reference_target(reading, settings) is not None
    )


def excluded_from_dosing(reading: Reading, settings: DosingSettings) -> bool:
    """Glycémie de référence possible que sa note retire du calcul de la dose."""
    return _exclusion_asked(reading, settings) and can_exclude(reading, settings)


def exclusion_refused(reading: Reading, settings: DosingSettings) -> bool:
    """Glycémie de référence possible marquée à écarter, comptée quand même parce qu'elle est sous le seuil bas."""
    return _exclusion_asked(reading, settings) and not can_exclude(reading, settings)


def reference_readings(readings: Sequence[Reading], settings: DosingSettings, target: DoseTarget) -> list[MorningReading]:
    """Glycémie de référence de chaque jour pour `target`, parmi les mesures de sa plage (bornes incluses).

    Matin (dose du soir) : la première « à jeun », sinon la première « avant repas » ou sans marqueur.
    Soir (dose du matin) : la première « avant repas », sinon la première sans marqueur.
    Les mesures écartées par une note (excluded_from_dosing) ne sont jamais retenues.
    """
    first_choice = MORNING_FIRST_CHOICE if target is DoseTarget.EVENING else EVENING_FIRST_CHOICE
    preferred: dict[date, Reading] = {}
    fallback: dict[date, Reading] = {}
    for reading in sorted(readings):
        if excluded_from_dosing(reading, settings) or not is_reference_candidate(reading, settings, target):
            continue
        chosen = preferred if reading.meal == first_choice else fallback
        chosen.setdefault(reading.day, reading)
    found = {**fallback, **preferred}
    return [MorningReading(day, r) for day, r in sorted(found.items())]


def morning_readings(readings: Sequence[Reading], settings: DosingSettings) -> list[MorningReading]:
    """Glycémie du matin de chaque jour, parmi les mesures de la plage matin (bornes incluses) :
    la première marquée "à jeun", sinon la première marquée "avant repas" ou sans marqueur.

    Les mesures "après repas", "coucher" ou "autre moment" ne sont jamais la glycémie du matin,
    ni les mesures écartées par une note (excluded_from_dosing).
    """
    return reference_readings(readings, settings, DoseTarget.EVENING)


def evening_readings(readings: Sequence[Reading], settings: DosingSettings) -> list[MorningReading]:
    """Glycémie du soir de chaque jour (vide si le protocole n'ajuste pas la dose du matin)."""
    return reference_readings(readings, settings, DoseTarget.MORNING)


def _ordered(changes: Sequence[DoseChange]) -> list[DoseChange]:
    return sorted(changes, key=lambda c: (c.effective, c.id or 0))


def tracking_start(changes: Sequence[DoseChange], target: DoseTarget) -> DoseChange:
    """Changement à partir duquel la dose `target` est suivie : son dernier changement de valeur, ou le début."""
    ordered = _ordered(changes)
    start = ordered[0]
    for previous, change in zip(ordered, ordered[1:]):
        if change.rule is DoseRule.START or _ui(change, target) != _ui(previous, target):
            start = change
    return start


def propose(
    readings: Sequence[Reading],
    changes: Sequence[DoseChange],
    settings: DosingSettings,
    today: date,
) -> DoseProposal:
    if not changes:
        raise ValueError("aucune dose de départ : renseigner le début du protocole")
    ordered = _ordered(changes)
    current = ordered[-1]
    alerts = list(_alerts(readings, current, settings, today))
    evening = _adjust(readings, ordered, settings, DoseTarget.EVENING, today, alerts)
    morning = (
        _adjust(readings, ordered, settings, DoseTarget.MORNING, today, alerts)
        if settings.morning_titration is not None else None
    )
    return DoseProposal(current, evening, morning, tuple(alerts))


def _adjust(
    readings: Sequence[Reading],
    ordered: Sequence[DoseChange],
    settings: DosingSettings,
    target: DoseTarget,
    today: date,
    alerts: list[Alert],
) -> DoseAdjustment:
    titration = settings.titration(target)
    since = tracking_start(ordered, target).effective
    current_ui = _ui(ordered[-1], target)
    ref, dose = REFERENCE_LABELS_FR[target], DOSE_WORDS[target]
    decrease_rule, increase_rule = RULES[target]

    references = [m for m in reference_readings(readings, settings, target) if m.reading.device_time > since]
    candidates = [r for r in sorted(readings) if r.device_time > since and reference_target(r, settings) is target]
    excluded = tuple(r for r in candidates if excluded_from_dosing(r, settings))
    refused = [r for r in candidates if exclusion_refused(r, settings)]
    alerts += _exclusion_alerts(excluded, refused, _mg(titration.low_g_l), target)

    def make(proposed_ui: int, rule: DoseRule, reason: str, evidence: Sequence[Reading]) -> DoseAdjustment:
        return DoseAdjustment(
            target, current_ui, proposed_ui, rule, reason, since, tuple(evidence), tuple(references), excluded
        )

    def keep(reason: str, evidence: Sequence[Reading] = ()) -> DoseAdjustment:
        return make(current_ui, DoseRule.KEEP, reason, evidence)

    if not references:
        return keep(f"Pas encore de glycémie du {ref} depuis le dernier changement de la dose {dose}.")

    latest = references[-1]
    age_days = (today - latest.day).days
    if age_days > settings.stale_days:
        alerts.append(
            Alert(
                AlertLevel.WARNING,
                "stale" + CODE_SUFFIX[target],
                f"Dernière glycémie du {ref} il y a {age_days} jours ({latest.day:%d/%m/%Y}) : "
                f"récupérez les mesures du lecteur avant d'ajuster la dose {dose}.",
            )
        )
        return keep("Données trop anciennes pour proposer un ajustement.", (latest.reading,))

    mg = latest.reading.mg_dl
    crossed = [tier for tier in titration.all_low_tiers if mg < _mg(tier.below_g_l)]
    if crossed:
        tier = crossed[-1]
        proposed = max(0, current_ui - tier.step_ui)
        reason = (
            f"Glycémie du {ref} du {latest.day:%d/%m} à {fmt_g_l(mg)}, "
            f"sous {fmt_g_l(_mg(tier.below_g_l))} : diminuer la dose {dose} de {tier.step_ui} UI."
        )
        if proposed == current_ui:
            return keep(reason + f" La dose {dose} est déjà à 0 UI.", (latest.reading,))
        return make(proposed, decrease_rule, reason, (latest.reading,))

    reached = None
    for tier in titration.all_high_tiers:
        streak = _high_streak(references, _mg(tier.above_g_l))
        if len(streak) >= tier.days:
            reached = tier, streak[-tier.days:]
    if reached is not None:
        tier, used = reached
        values = ", ".join(f"{m.day:%d/%m} {fmt_g_l(m.reading.mg_dl)}" for m in used)
        reason = (
            f"Glycémie du {ref} au-dessus de {fmt_g_l(_mg(tier.above_g_l))} {tier.days} jours de suite "
            f"({values}) : augmenter la dose {dose} de {tier.step_ui} UI."
        )
        return make(current_ui + tier.step_ui, increase_rule, reason, [m.reading for m in used])

    base = titration.all_high_tiers[0]
    streak = _high_streak(references, _mg(base.above_g_l))
    if streak:
        return keep(
            f"Glycémie du {ref} haute depuis {len(streak)} jour(s) consécutif(s) sur {base.days} "
            "nécessaires : dose inchangée pour l'instant.",
            tuple(m.reading for m in streak),
        )
    return keep(f"Dernière glycémie du {ref} dans l'objectif ({fmt_g_l(mg)}) : dose inchangée.", (latest.reading,))


def _high_streak(references: Sequence[MorningReading], high_mg: int) -> list[MorningReading]:
    """Série de jours > seuil sur des jours calendaires consécutifs, se terminant au dernier jour."""
    streak: list[MorningReading] = []
    for item in reversed(references):
        if item.reading.mg_dl <= high_mg:
            break
        if streak and (streak[-1].day - item.day).days != 1:
            break
        streak.append(item)
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
    if settings.morning_titration is not None and current.morning_ui == 0:
        yield Alert(AlertLevel.WARNING, "morning_zero", "La dose du matin est à 0 UI : faites le point avec le médecin.")


def _exclusion_alerts(excluded: Sequence[Reading], refused: Sequence[Reading], low_mg: int, target: DoseTarget) -> list[Alert]:
    ref, dose, suffix = REFERENCE_LABELS_FR[target], DOSE_WORDS[target], CODE_SUFFIX[target]
    alerts = []
    if excluded:
        alerts.append(Alert(
            AlertLevel.INFO,
            "excluded" + suffix,
            f"{len(excluded)} glycémie(s) du {ref} écartée(s) de l'ajustement depuis le dernier changement de la "
            f"dose {dose} : " + " ; ".join(fmt_excluded(r) for r in excluded) + ".",
        ))
    if refused:
        alerts.append(Alert(
            AlertLevel.WARNING,
            "exclusion_refused" + suffix,
            f"{len(refused)} glycémie(s) du {ref} sous {fmt_g_l(low_mg)} marquée(s) à écarter, mais comptée(s) quand même : "
            + " ; ".join(fmt_reading(r) for r in refused)
            + ". Une glycémie basse n'est jamais écartée ; ne validez pas une baisse que vous jugez fausse.",
        ))
    return alerts


def apply_adjustment(proposal: DoseProposal, target: DoseTarget, now: datetime, note: str = "") -> DoseChange:
    """Transforme l'ajustement validé par l'utilisateur en nouvelle dose en cours (l'autre dose ne bouge pas)."""
    adjustment = proposal.adjustment(target)
    if adjustment is None or not adjustment.changes_dose:
        raise ValueError("cette proposition ne modifie pas la dose")
    if now < proposal.current.effective:
        raise ValueError("la validation ne peut pas précéder la dose en cours")
    current = proposal.current
    return DoseChange(
        effective=now,
        morning_ui=adjustment.proposed_ui if target is DoseTarget.MORNING else current.morning_ui,
        evening_ui=adjustment.proposed_ui if target is DoseTarget.EVENING else current.evening_ui,
        rule=adjustment.rule,
        evidence=tuple(fmt_reading(r) for r in adjustment.evidence),
        note=note,
        excluded=tuple(fmt_excluded(r) for r in adjustment.excluded),
    )


def apply_proposal(proposal: DoseProposal, now: datetime, note: str = "") -> DoseChange:
    """Valide l'ajustement de la dose du soir (voir apply_adjustment pour la dose du matin)."""
    return apply_adjustment(proposal, DoseTarget.EVENING, now, note)
