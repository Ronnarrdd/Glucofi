"""Eval de rejeu du moteur de dose.

Rejoue un historique jour par jour (proposition chaque soir à 20:00, chaque
dose qui change est validée, la dose du soir puis la dose du matin sur une
proposition recalculée), puis vérifie :
- les invariants de sécurité (dose >= 0, au plus un changement par dose et par
  jour, l'autre dose jamais touchée, pas conformes aux paliers, preuves
  conformes au protocole et postérieures au dernier changement de CETTE dose) ;
- l'accord avec un oracle indépendant, réécrit naïvement depuis le texte du
  protocole, pour chaque jour et chaque dose : glycémie du matin (dans la
  plage : « à jeun » d'abord, sinon « avant repas » ou sans marqueur) pour la
  dose du soir, glycémie du soir (dans la plage : « avant repas » d'abord,
  sinon sans marqueur) pour la dose du matin ; baisse du palier franchi le
  plus bas, hausse du palier atteint le plus haut.
Chaque patient est rejoué deux fois : protocole simple (SETTINGS, dose du soir
seule) et protocole complet (FULL : paliers et dose du matin).
Affiche aussi combien de glycémies du matin changent par rapport à l'ancienne
règle (première mesure de la plage, marqueurs ignorés).

Notes : chaque patient synthétique est aussi rejoué avec des notes tirées au
hasard (with_notes), dont certaines demandent d'écarter la mesure, y compris
des glycémies basses. L'oracle relit la règle : une mesure marquée à écarter
est ignorée, sauf sous le seuil bas. Invariants en plus : aucune preuve
écartée, aucune glycémie basse écartée, mesures écartées enregistrées avec la
dose validée. Affiche le nombre de décisions changées par les notes.

Journal des injections : chaque patient synthétique est aussi rejoué avec un journal tiré au hasard (doses du matin
et du soir prises, non prises ou non renseignées) et le protocole qui écarte la glycémie suivant une dose non prise
(skip_after_missed_dose). L'oracle relit la règle : le matin d'un jour suit la dose du soir de la veille, le soir d'un
jour suit la dose du matin du même jour ; seule une dose déclarée non prise écarte, jamais une dose non renseignée,
jamais sous le seuil bas. Invariants en plus : aucune preuve écartée par une dose non prise, mesures écartées
enregistrées avec la dose validée. Affiche le nombre de décisions changées par le journal.

Sources : un export accuchek réel (--json) et/ou N patients synthétiques
générés avec une graine fixe (--synthetic). Seuil de réussite : 100 %.

Usage :
    python3 -m evals.replay_history --json test.json --synthetic 200
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from contracts import (
    DoseChange,
    DoseRule,
    DoseTarget,
    DosingSettings,
    HighTier,
    LowTier,
    Meal,
    NoteTag,
    Reading,
    ReadingNote,
    Titration,
)
from services.device import parse_file
from contracts import Injection, InjectionState
from services.dosing import apply_adjustment, fmt_reading, propose

OUT_DIR = Path("/tmp/glucofi-eval")
EVENING = time(20, 0)
SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
SKIPPING = replace(SETTINGS, skip_after_missed_dose=True)
FULL = replace(
    SETTINGS,
    low_tiers=(LowTier(0.60, 4),),
    high_tiers=(HighTier(2.20, 4, 2),),
    morning_titration=Titration(0.90, 1.60, 1, 2, (LowTier(0.70, 2),), (HighTier(2.40, 3, 1),)),
)

@dataclass
class DayRow:
    source: str
    day: date
    dose: str
    reference_mg: int | None
    ui_before: int
    ui_after: int
    engine_rule: str
    oracle_rule: str
    reason: str


FULL_SKIPPING = replace(FULL, skip_after_missed_dose=True)


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def oracle_rule_text(s: DosingSettings, target: DoseTarget) -> dict:
    """Le protocole tel que l'ordonnance l'écrit, champ par champ, sans les helpers des contrats."""
    if target is DoseTarget.EVENING:
        low, high, step, days = s.low_g_l, s.high_g_l, s.step_ui, s.high_streak_days
        low_tiers, high_tiers = s.low_tiers, s.high_tiers
        window = (_minutes(s.morning_start), _minutes(s.morning_end))
        first, then = ("fasting",), ("before_meal", None)
    else:
        m = s.morning_titration
        low, high, step, days = m.low_g_l, m.high_g_l, m.step_ui, m.high_streak_days
        low_tiers, high_tiers = m.low_tiers, m.high_tiers
        window = (_minutes(s.evening_start), _minutes(s.evening_end))
        first, then = ("before_meal",), (None,)
    return {
        "low": round(low * 100),
        "lows": [(round(low * 100), step)] + [(round(t.below_g_l * 100), t.step_ui) for t in low_tiers],
        "highs": [(round(high * 100), step, days)] + [(round(t.above_g_l * 100), t.step_ui, t.days) for t in high_tiers],
        "window": window,
        "first": first,
        "then": then,
    }


Journal = dict[tuple[date, str], str]


def oracle_set_aside(
    rd: Reading, s: DosingSettings, low_mg: int | None = None, missed: frozenset = frozenset(),
    target: DoseTarget = DoseTarget.EVENING,
) -> bool:
    """Texte de la règle : jamais sous le seuil bas ; sinon marquée « écarter de l'ajustement », ou, si le protocole le
    demande, précédée d'une dose déclarée non prise (la dose du soir de la veille pour la glycémie du matin, la dose du
    matin du même jour pour la glycémie du soir)."""
    low_mg = round(s.low_g_l * 100) if low_mg is None else low_mg
    if rd.mg_dl < low_mg:
        return False
    if rd.note is not None and rd.note.exclude_from_dosing:
        return True
    if not s.skip_after_missed_dose:
        return False
    day = rd.device_time.date()
    before = (day - timedelta(days=1), "evening") if target is DoseTarget.EVENING else (day, "morning")
    return before in missed


def oracle_candidate(rd: Reading, s: DosingSettings, target: DoseTarget = DoseTarget.EVENING) -> bool:
    rule = oracle_rule_text(s, target)
    lo, hi = rule["window"]
    marker = rd.meal.value if rd.meal is not None else None
    return lo <= _minutes(rd.device_time.time()) <= hi and marker in rule["first"] + rule["then"]


def oracle_in_morning(rd: Reading, s: DosingSettings) -> bool:
    return oracle_candidate(rd, s, DoseTarget.EVENING)


def oracle_references(
    readings: list[Reading], s: DosingSettings, target: DoseTarget, missed: frozenset = frozenset()
) -> dict[date, Reading]:
    rule = oracle_rule_text(s, target)
    lo, hi = rule["window"]
    in_window = [
        rd for rd in sorted(readings, key=lambda x: x.device_time)
        if lo <= _minutes(rd.device_time.time()) <= hi and not oracle_set_aside(rd, s, rule["low"], missed, target)
    ]
    out: dict[date, Reading] = {}
    for markers in (rule["first"], rule["then"]):
        for rd in in_window:
            if (rd.meal.value if rd.meal is not None else None) in markers:
                out.setdefault(rd.device_time.date(), rd)
    return out


def oracle_mornings(readings: list[Reading], s: DosingSettings, use_markers: bool = True) -> dict[date, Reading]:
    """Glycémie du matin par jour, relue depuis le texte de la règle, sans le code du moteur."""
    if use_markers:
        return oracle_references(readings, s, DoseTarget.EVENING)
    lo, hi = _minutes(s.morning_start), _minutes(s.morning_end)
    out: dict[date, Reading] = {}
    for rd in sorted(readings, key=lambda x: x.device_time):
        if lo <= _minutes(rd.device_time.time()) <= hi and not oracle_set_aside(rd, s):
            out.setdefault(rd.device_time.date(), rd)
    return out


def oracle_since(changes: list[DoseChange], target: DoseTarget) -> datetime:
    """Le décompte d'une dose repart de son dernier changement de valeur (ou d'un nouveau départ)."""
    attr = "evening_ui" if target is DoseTarget.EVENING else "morning_ui"
    since = changes[0].effective
    for previous, change in zip(changes, changes[1:]):
        if change.rule is DoseRule.START or getattr(change, attr) != getattr(previous, attr):
            since = change.effective
    return since


def oracle(
    readings: list[Reading], since: datetime, today: date, s: DosingSettings, target: DoseTarget,
    missed: frozenset = frozenset(),
) -> tuple[str, int]:
    """(décision, pas en UI) relus littéralement du protocole, sans réutiliser le code du moteur."""
    rule = oracle_rule_text(s, target)
    refs = {d: rd for d, rd in oracle_references(readings, s, target, missed).items() if rd.device_time > since}
    if not refs:
        return "keep", 0
    last = max(refs)
    if (today - last).days > s.stale_days:
        return "keep", 0
    value = refs[last].mg_dl
    crossed = [(threshold, step) for threshold, step in rule["lows"] if value < threshold]
    if crossed:
        return "decrease", min(crossed)[1]
    reached = [
        (above, step) for above, step, n in rule["highs"]
        if all((last - timedelta(days=k)) in refs and refs[last - timedelta(days=k)].mg_dl > above for k in range(n))
    ]
    if reached:
        return "increase", max(reached)[1]
    return "keep", 0


RULE_TO_ORACLE = {
    DoseRule.KEEP: "keep",
    DoseRule.DECREASE_LOW_MORNING: "decrease",
    DoseRule.INCREASE_HIGH_MORNINGS: "increase",
    DoseRule.DECREASE_LOW_EVENING: "decrease",
    DoseRule.INCREASE_HIGH_EVENINGS: "increase",
}
DOSE_ATTR = {DoseTarget.EVENING: "evening_ui", DoseTarget.MORNING: "morning_ui"}


def replay(
    source: str, readings: list[Reading], start_evening: int = 4, settings: DosingSettings = SETTINGS, start_morning: int = 8,
    journal: Journal | None = None,
) -> tuple[list[DayRow], list[str]]:
    readings = sorted(readings)
    if not readings:
        return [], []
    targets = [DoseTarget.EVENING] + ([DoseTarget.MORNING] if settings.morning_titration is not None else [])
    first_day, last_day = readings[0].day, readings[-1].day
    changes = [DoseChange(datetime.combine(first_day - timedelta(days=1), EVENING), start_morning, start_evening, DoseRule.START)]
    rows: list[DayRow] = []
    failures: list[str] = []
    index = 0
    day = first_day
    while day <= last_day:
        cutoff = datetime.combine(day, EVENING)
        while index < len(readings) and readings[index].device_time <= cutoff:
            index += 1
        visible = readings[:index]
        declared = {key: state for key, state in (journal or {}).items() if key[0] <= day}
        missed = frozenset(key for key, state in declared.items() if state == "missed")
        injections = [Injection(d, DoseTarget(t), InjectionState(state)) for (d, t), state in declared.items()]
        for target in targets:
            attr = DOSE_ATTR[target]
            proposal = propose(visible, changes, settings, day, injections)
            adjustment = proposal.adjustment(target)
            since = oracle_since(changes, target)
            expected, step = oracle(visible, since, day, settings, target, missed)
            before = getattr(changes[-1], attr)
            # À 0 UI, le moteur garde la dose : l'oracle dit "decrease" mais sans effet possible.
            if expected == "decrease" and before == 0:
                expected, step = "keep", 0
            engine = RULE_TO_ORACLE[adjustment.rule]
            want_ui = {"keep": before, "decrease": max(0, before - step), "increase": before + step}[expected]
            if engine != expected or adjustment.proposed_ui != want_ui:
                failures.append(
                    f"{source} {day} {target.value} : moteur={engine} {adjustment.proposed_ui} UI, "
                    f"oracle={expected} {want_ui} UI ({adjustment.reason})"
                )
            low_mg = oracle_rule_text(settings, target)["low"]
            failures.extend(
                f"{source} {day} : glycémie basse écartée {fmt_reading(x)}" for x in adjustment.excluded if x.mg_dl < low_mg
            )
            if adjustment.changes_dose:
                change = apply_adjustment(proposal, target, cutoff)
                failures.extend(check_change(source, day, change, changes[-1], since, visible, settings, target, missed))
                changes.append(change)
            reference = next((m.reading.mg_dl for m in adjustment.references if m.day == day), None)
            rows.append(DayRow(
                source, day, target.value, reference, before, getattr(changes[-1], attr),
                adjustment.rule.value, expected, adjustment.reason,
            ))
        day += timedelta(days=1)

    per_day: dict[tuple[date, str], int] = {}
    for previous, change in zip(changes, changes[1:]):
        for target, attr in DOSE_ATTR.items():
            if getattr(change, attr) != getattr(previous, attr):
                key = (change.effective.date(), target.value)
                per_day[key] = per_day.get(key, 0) + 1
    failures.extend(f"{source} {d} {t} : {n} changements le même jour" for (d, t), n in per_day.items() if n > 1)
    return rows, failures


def check_change(
    source: str, day: date, change: DoseChange, previous: DoseChange, since: datetime, visible: list[Reading],
    settings: DosingSettings = SETTINGS, target: DoseTarget = DoseTarget.EVENING, missed: frozenset = frozenset(),
) -> list[str]:
    rule = oracle_rule_text(settings, target)
    attr, other = DOSE_ATTR[target], DOSE_ATTR[DoseTarget.MORNING if target is DoseTarget.EVENING else DoseTarget.EVENING]
    errors = []
    if getattr(change, attr) < 0:
        errors.append(f"{source} {day} : dose négative {getattr(change, attr)}")
    if getattr(change, other) != getattr(previous, other):
        errors.append(f"{source} {day} : l'autre dose ({other}) a été modifiée")
    delta = getattr(change, attr) - getattr(previous, attr)
    increase = RULE_TO_ORACLE[change.rule] == "increase"
    if increase and delta not in {step for _, step, _ in rule["highs"]}:
        errors.append(f"{source} {day} : hausse de {delta} UI hors paliers")
    if not increase and not (delta < 0 and (-delta in {s for _, s in rule["lows"]} or getattr(change, attr) == 0)):
        errors.append(f"{source} {day} : baisse de {delta} UI hors paliers")
    set_aside = [
        r for r in sorted(visible)
        if r.device_time > since and oracle_candidate(r, settings, target) and oracle_set_aside(r, settings, rule["low"], missed, target)
    ]
    if len(change.excluded) != len(set_aside) or not all(
        e.startswith(fmt_reading(r)) for e, r in zip(change.excluded, set_aside)
    ):
        errors.append(f"{source} {day} : mesures écartées {change.excluded}, attendu {[fmt_reading(r) for r in set_aside]}")
    by_key = {fmt_reading(r): r for r in visible}
    evidence = [by_key.get(e) for e in change.evidence]
    if None in evidence or not evidence:
        errors.append(f"{source} {day} : preuves introuvables {change.evidence}")
        return errors
    if any(oracle_set_aside(r, settings, rule["low"], missed, target) for r in evidence):
        errors.append(f"{source} {day} : preuve écartée par une note ou une dose non prise {change.evidence}")
    if any(r.device_time <= since for r in evidence):
        errors.append(f"{source} {day} : preuve antérieure au dernier changement de la dose")
    if increase:
        days = [r.day for r in evidence]
        consecutive = all((b - a).days == 1 for a, b in zip(days, days[1:]))
        justified = any(
            len(evidence) == n and step == delta and all(r.mg_dl > above for r in evidence)
            for above, step, n in rule["highs"]
        )
        if not consecutive or not justified:
            errors.append(f"{source} {day} : hausse mal justifiée {change.evidence}")
    elif len(evidence) != 1 or evidence[0].mg_dl >= rule["low"]:
        errors.append(f"{source} {day} : baisse mal justifiée {change.evidence}")
    return errors


def evening_variety(readings: list[Reading], seed: int) -> list[Reading]:
    """Ajoute des mesures du soir piégeuses : après repas avant le dîner, coucher, hors plage, soirs bas."""
    rng = random.Random(seed * 7919 + 3)
    out = list(readings)
    for day in sorted({r.day for r in readings}):
        roll = rng.random()
        base = datetime.combine(day, time())
        if roll < 0.15:
            t = base.replace(hour=17, minute=rng.randint(0, 50))
            out.append(Reading(t, rng.randint(200, 350), int(t.timestamp()) + 3, meal=Meal.AFTER_MEAL))
        elif roll < 0.25:
            t = base.replace(hour=17, minute=rng.randint(0, 50))
            out.append(Reading(t, rng.choice([60, 69, 70, 89, 90, 91, 160, 161, 240, 241]), int(t.timestamp()) + 3))
        elif roll < 0.35:
            t = base.replace(hour=22, minute=rng.randint(0, 59))
            out.append(Reading(t, rng.randint(40, 400), int(t.timestamp()) + 3))
        elif roll < 0.42:
            t = base.replace(hour=21, minute=rng.randint(0, 59))
            out.append(Reading(t, rng.randint(50, 300), int(t.timestamp()) + 3, meal=Meal.BEDTIME))
    return sorted(out)


MORNING_MARKERS = (None, Meal.FASTING, Meal.FASTING, Meal.BEFORE_MEAL, Meal.AFTER_MEAL, Meal.BEDTIME, Meal.CASUAL)


def synthetic_patient(seed: int, days: int = 90) -> list[Reading]:
    """Patient fictif : glycémie du matin qui dérive, jours sans mesure, mesures multiples.

    Les graines impaires ont des marqueurs repas, parfois une mesure « après repas »
    avant la mesure à jeun (cas où l'ancienne règle se trompait de mesure).
    """
    rng = random.Random(seed)
    marked = seed % 2 == 1
    base = rng.randint(70, 220)
    start = datetime(2026, 1, 1)
    out: list[Reading] = []
    for d in range(days):
        base = max(45, min(380, base + rng.randint(-25, 25)))
        day = start + timedelta(days=d)
        if rng.random() < 0.15:
            continue
        slots = ((rng.randint(4, 12), rng.randint(0, 59), 0, MORNING_MARKERS), (12, 30, 40, (Meal.AFTER_MEAL, None)), (19, 0, 50, (Meal.BEFORE_MEAL, None)))
        for hour, minute, spread, markers in slots:
            if rng.random() < 0.8:
                t = day.replace(hour=hour, minute=minute)
                mg = max(20, min(600, base + rng.randint(-spread, spread)))
                out.append(Reading(t, mg, int(t.timestamp()), meal=rng.choice(markers) if marked else None))
        if rng.random() < 0.1:
            t = day.replace(hour=rng.randint(6, 11), minute=rng.randint(0, 59))
            meal = rng.choice(MORNING_MARKERS) if marked else None
            out.append(Reading(t, rng.choice([79, 80, 81, 149, 150, 151]), int(t.timestamp()) + 1, meal=meal))
        if marked and rng.random() < 0.2:
            t = day.replace(hour=5, minute=rng.randint(0, 59))
            out.append(Reading(t, rng.randint(160, 320), int(t.timestamp()) + 2, meal=Meal.AFTER_MEAL))
    return out


def with_notes(readings: list[Reading], seed: int, rate: float = 0.25) -> list[Reading]:
    """Mêmes mesures avec des notes tirées au hasard : la plupart écartent la mesure, glycémies basses comprises."""
    rng = random.Random(seed * 104729 + 1)
    tags = list(NoteTag)
    out = []
    for rd in readings:
        if rng.random() < rate:
            note = ReadingNote((rng.choice(tags),), rng.choice(("", "repas chez des amis", "fièvre")), rng.random() < 0.8)
            rd = replace(rd, note=note)
        out.append(rd)
    return out


def synthetic_journal(readings: list[Reading], seed: int) -> Journal:
    """Journal tiré au hasard sur les jours du patient : chaque dose est prise (75 %), non prise (13 %) ou non renseignée."""
    rng = random.Random(seed * 15485863 + 7)
    journal: Journal = {}
    for day in sorted({r.day for r in readings} | {r.day - timedelta(days=1) for r in readings}):
        for dose in ("morning", "evening"):
            roll = rng.random()
            if roll < 0.75:
                journal[(day, dose)] = "taken"
            elif roll < 0.88:
                journal[(day, dose)] = "missed"
    return journal


def decisions_changed(plain: list[DayRow], noted: list[DayRow]) -> int:
    """Jours où les notes changent la décision du moteur (même patient, même jour)."""
    return sum(a.engine_rule != b.engine_rule for a, b in zip(plain, noted))


def changed_mornings(readings: list[Reading]) -> int:
    """Jours dont la glycémie du matin change entre l'ancienne règle et la règle avec marqueurs."""
    old = oracle_mornings(readings, SETTINGS, use_markers=False)
    new = oracle_mornings(readings, SETTINGS)
    return sum(1 for d in old.keys() | new.keys() if old.get(d) != new.get(d))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path, action="append", default=[], help="export accuchek à rejouer")
    parser.add_argument("--synthetic", type=int, default=200, help="nombre de patients synthétiques")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    all_rows: list[DayRow] = []
    failures: list[str] = []
    changed: dict[str, int] = {}

    def run(source: str, readings: list[Reading], settings: DosingSettings = SETTINGS, journal: Journal | None = None) -> list[DayRow]:
        rows, errs = replay(source, readings, settings=settings, journal=journal)
        all_rows.extend(rows)
        failures.extend(errs)
        return rows

    for path in args.json:
        readings = list(parse_file(path).readings)
        run(path.name, readings)
        run(f"{path.name}-complet", readings, FULL)
        changed[path.name] = changed_mornings(readings)
    synthetic_changed = 0
    notes_changed = notes_days = set_aside = kept_low = 0
    journal_changed = journal_days = journal_set_aside = 0
    for seed in range(args.synthetic):
        readings = synthetic_patient(seed)
        rows = run(f"synthetique-{seed}", readings)
        synthetic_changed += changed_mornings(readings)
        noted = with_notes(readings, seed)
        noted_rows = run(f"synthetique-notes-{seed}", noted)
        notes_changed += decisions_changed(rows, noted_rows)
        notes_days += len(noted_rows)
        asked = [r for r in noted if r.note is not None and r.note.exclude_from_dosing and oracle_in_morning(r, SETTINGS)]
        set_aside += sum(oracle_set_aside(r, SETTINGS) for r in asked)
        kept_low += sum(not oracle_set_aside(r, SETTINGS) for r in asked)
        varied = evening_variety(readings, seed)
        run(f"complet-{seed}", varied, FULL)
        run(f"complet-notes-{seed}", with_notes(varied, seed), FULL)
        journal = synthetic_journal(varied, seed)
        journal_rows = run(f"journal-{seed}", readings, SKIPPING, journal)
        journal_changed += decisions_changed(rows, journal_rows)
        journal_days += len(journal_rows)
        run(f"journal-complet-{seed}", with_notes(varied, seed), FULL_SKIPPING, journal)
        journal_set_aside += sum(
            oracle_set_aside(r, SKIPPING, None, frozenset(k for k, v in journal.items() if v == "missed"))
            and not oracle_set_aside(r, SETTINGS, None)
            for r in readings if oracle_in_morning(r, SETTINGS)
        )

    args.out.mkdir(parents=True, exist_ok=True)
    csv_path = args.out / f"replay-{datetime.now():%Y%m%d-%H%M%S}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source", "jour", "dose", "reference_mg_dl", "ui_avant", "ui_apres", "regle_moteur", "regle_oracle", "raison"])
        for row in all_rows:
            writer.writerow([
                row.source, row.day, row.dose, row.reference_mg, row.ui_before, row.ui_after,
                row.engine_rule, row.oracle_rule, row.reason,
            ])

    agree = sum(1 for row in all_rows if RULE_TO_ORACLE[DoseRule(row.engine_rule)] == row.oracle_rule)
    total = len(all_rows)
    score = agree / total if total else 1.0
    print(f"décisions rejouées : {total}, accord moteur/oracle : {score:.2%}, échecs invariants/accord : {len(failures)}")
    for dose in ("evening", "morning"):
        counts: dict[str, int] = {}
        for row in all_rows:
            if row.dose == dose:
                counts[row.engine_rule] = counts.get(row.engine_rule, 0) + 1
        print(f"dose {'du soir' if dose == 'evening' else 'du matin'} : {counts}")
    tiers = sum(1 for row in all_rows if row.engine_rule != "keep" and abs(row.ui_after - row.ui_before) > 2)
    print(f"changements par un palier (plus de 2 UI) : {tiers}")
    print(f"glycémies du matin changées par les marqueurs (synthétiques) : {synthetic_changed}")
    print(
        f"notes : {set_aside} glycémie(s) du matin écartée(s), {kept_low} glycémie(s) basse(s) marquée(s) à écarter "
        f"et comptée(s), {notes_changed} décision(s) changée(s) sur {notes_days} jours"
    )
    print(
        f"journal des injections : {journal_set_aside} glycémie(s) du matin possible(s) écartée(s) par une dose du soir non prise, "
        f"{journal_changed} décision(s) changée(s) sur {journal_days} jours"
    )
    for name, n in changed.items():
        print(f"{name} : {n} glycémie(s) du matin changée(s) par les marqueurs")
    for path in args.json:
        final = [row for row in all_rows if row.source == path.name]
        if final:
            print(f"{path.name} : dose du soir finale simulée {final[-1].ui_after} UI")
    print(f"CSV : {csv_path}")
    for failure in failures[:20]:
        print("ÉCHEC", failure)
    return 0 if not failures and score == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
