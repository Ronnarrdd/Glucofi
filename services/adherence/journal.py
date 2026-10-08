"""Journal des injections : jours dus, observance, doses « pas cochées ».

Pur et déterministe : aucune horloge ni base ici, l'appelant donne `today` et `now`.

Vocabulaire :
- une dose est *due* un jour où la dose en cours ce jour-là est supérieure à 0 UI, du premier jour du protocole
  à hier (aujourd'hui n'est jamais compté : la journée n'est pas finie) ;
- le patient la déclare *prise* ou *non prise* ; sans déclaration elle est *non renseignée* ;
- l'observance est le nombre de doses prises sur le nombre de doses dues : une dose non renseignée compte comme
  non prise (le rapport les distingue, l'observance non).

Seule une dose déclarée *non prise* (jamais une dose non renseignée) peut écarter une glycémie de référence de
l'ajustement : voir `services.dosing.engine` et le réglage `skip_after_missed_dose`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Sequence

from contracts import (
    Alert,
    AlertLevel,
    DoseChange,
    DoseTarget,
    DosingSettings,
    Injection,
    InjectionState,
)

# jours d'avant aujourd'hui où une dose non cochée déclenche une alerte (plus loin, le moment est passé)
UNLOGGED_LOOKBACK_DAYS = 3
TARGET_ORDER = (DoseTarget.MORNING, DoseTarget.EVENING)
DOSE_WORDS = {DoseTarget.MORNING: "du matin", DoseTarget.EVENING: "du soir"}
DAYS_SHORT_FR = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
Key = tuple[date, DoseTarget]


def states(injections: Iterable[Injection]) -> dict[Key, InjectionState]:
    """Dose déclarée par (jour, dose) ; la dernière déclaration d'une même dose l'emporte."""
    return {(i.day, DoseTarget(i.target)): InjectionState(i.state) for i in injections}


def missed_doses(injections: Iterable[Injection]) -> frozenset[Key]:
    """Doses déclarées non prises, sous la forme que le moteur de dose attend."""
    return frozenset(key for key, state in states(injections).items() if state is InjectionState.MISSED)


def dose_ui(changes: Sequence[DoseChange], day: date, target: DoseTarget) -> int | None:
    """Dose en cours à la fin de `day` (None avant la première dose du protocole)."""
    in_force = [c for c in changes if c.effective.date() <= day]
    if not in_force:
        return None
    last = max(in_force, key=lambda c: (c.effective, c.id or 0))
    return last.morning_ui if target is DoseTarget.MORNING else last.evening_ui


def due_days(changes: Sequence[DoseChange], target: DoseTarget, since: date, until: date, today: date) -> list[date]:
    """Jours de [since, until[ où la dose `target` est due, hier au plus tard."""
    last = min(until, today)
    days = []
    day = since
    while day < last:
        if (dose_ui(changes, day, target) or 0) > 0:
            days.append(day)
        day += timedelta(days=1)
    return days


@dataclass(frozen=True)
class DoseAdherence:
    target: DoseTarget
    due: int
    taken: int
    missed: int
    unset: int
    missed_days: tuple[date, ...] = ()
    unset_days: tuple[date, ...] = ()

    @property
    def rate(self) -> float | None:
        """Part des doses dues déclarées prises, en %, None s'il n'y a aucune dose due."""
        return None if self.due == 0 else 100.0 * self.taken / self.due

    @property
    def rate_text(self) -> str:
        return "-" if self.rate is None else f"{round(self.rate)} %"


@dataclass(frozen=True)
class Adherence:
    """Observance de la période, par dose (matin puis soir)."""

    since: date
    until: date
    doses: tuple[DoseAdherence, ...]

    @property
    def declared(self) -> int:
        """Doses déclarées (prises ou non) : 0 si le journal n'a pas servi sur la période."""
        return sum(d.taken + d.missed for d in self.doses)

    @property
    def due(self) -> int:
        return sum(d.due for d in self.doses)

    def dose(self, target: DoseTarget) -> DoseAdherence:
        return next(d for d in self.doses if d.target is target)


def adherence(
    injections: Iterable[Injection],
    changes: Sequence[DoseChange],
    since: date,
    until: date,
    today: date,
) -> Adherence:
    """Observance sur [since, until[ (jours), aujourd'hui exclu."""
    declared = states(injections)
    lines = []
    for target in TARGET_ORDER:
        days = due_days(changes, target, since, until, today)
        taken = [d for d in days if declared.get((d, target)) is InjectionState.TAKEN]
        missed = [d for d in days if declared.get((d, target)) is InjectionState.MISSED]
        unset = [d for d in days if (d, target) not in declared]
        lines.append(DoseAdherence(target, len(days), len(taken), len(missed), len(unset), tuple(missed), tuple(unset)))
    return Adherence(since, until, tuple(lines))


def day_word(day: date, today: date) -> str:
    """« aujourd'hui », « hier », « avant-hier », sinon « lun. 05/10 »."""
    delta = (today - day).days
    if delta == 0:
        return "aujourd'hui"
    if delta == 1:
        return "hier"
    if delta == 2:
        return "avant-hier"
    return f"{DAYS_SHORT_FR[day.weekday()]} {day:%d/%m}"


def unlogged_days(
    injections: Iterable[Injection],
    changes: Sequence[DoseChange],
    settings: DosingSettings,
    target: DoseTarget,
    today: date,
    now: datetime | None = None,
) -> list[date]:
    """Jours récents où la dose `target` est due mais ni cochée ni déclarée non prise, du plus ancien au plus récent.

    Les trois derniers jours, plus aujourd'hui une fois la plage de la glycémie de référence de cette dose passée
    (fin de la plage du matin pour la dose du matin, du soir pour celle du soir) : avant, la dose peut encore venir.
    """
    declared = states(injections)
    end = settings.morning_end if target is DoseTarget.MORNING else settings.evening_end
    last = today + timedelta(days=1) if now is not None and now.time() > end else today
    first = max(today - timedelta(days=UNLOGGED_LOOKBACK_DAYS), changes[0].effective.date() if changes else today)
    days = []
    day = first
    while day < last:
        if (dose_ui(changes, day, target) or 0) > 0 and (day, target) not in declared:
            days.append(day)
        day += timedelta(days=1)
    return days


def unlogged_alerts(
    injections: Iterable[Injection],
    changes: Sequence[DoseChange],
    settings: DosingSettings,
    today: date,
    now: datetime | None = None,
) -> list[Alert]:
    """Une alerte par dose pas cochée : « La dose du soir n'est pas cochée : hier, avant-hier. »"""
    injections = list(injections)
    alerts = []
    for target in (DoseTarget.EVENING, DoseTarget.MORNING):
        days = unlogged_days(injections, changes, settings, target, today, now)
        if not days:
            continue
        when = ", ".join(day_word(d, today) for d in days)
        if len(days) == 1:
            message = f"Dose {DOSE_WORDS[target]} pas cochée : {when}. Dites si vous l'avez prise (Aujourd'hui, Injections)."
        else:
            message = f"Doses {DOSE_WORDS[target]} pas cochées : {when}. Dites si vous les avez prises (Aujourd'hui, Injections)."
        alerts.append(Alert(AlertLevel.WARNING, f"unlogged_{target.value}", message))
    return alerts


def dose_before(reference_day: date, target: DoseTarget) -> Key:
    """Dose qui précède la glycémie de référence de `reference_day` pour l'ajustement de `target`.

    La glycémie du matin d'un jour suit la dose du soir de la veille (ajustement de la dose du soir) ; la glycémie
    du soir d'un jour suit la dose du matin du même jour (ajustement de la dose du matin).
    """
    if target is DoseTarget.EVENING:
        return reference_day - timedelta(days=1), DoseTarget.EVENING
    return reference_day, DoseTarget.MORNING
