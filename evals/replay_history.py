"""Eval de rejeu du moteur de dose.

Rejoue un historique jour par jour (proposition chaque soir à 20:00, validée
systématiquement), puis vérifie :
- les invariants de sécurité (dose >= 0, au plus un changement par jour,
  preuves conformes au protocole) ;
- l'accord avec un oracle indépendant, réécrit naïvement depuis le texte du
  protocole et de la règle du matin (dans la plage : « à jeun » d'abord, sinon
  « avant repas » ou sans marqueur), pour chaque jour.
Affiche aussi combien de glycémies du matin changent par rapport à l'ancienne
règle (première mesure de la plage, marqueurs ignorés).

Notes : chaque patient synthétique est aussi rejoué avec des notes tirées au
hasard (with_notes), dont certaines demandent d'écarter la mesure, y compris
des glycémies basses. L'oracle relit la règle : une mesure marquée à écarter
est ignorée, sauf sous le seuil bas. Invariants en plus : aucune preuve
écartée, aucune glycémie basse écartée, mesures écartées enregistrées avec la
dose validée. Affiche le nombre de décisions changées par les notes.

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

from contracts import DoseChange, DoseRule, DosingSettings, Meal, NoteTag, Reading, ReadingNote
from services.device import parse_file
from services.dosing import apply_proposal, fmt_reading, propose

OUT_DIR = Path("/tmp/glucofi-eval")
EVENING = time(20, 0)
SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)


@dataclass
class DayRow:
    source: str
    day: date
    morning_mg: int | None
    evening_before: int
    evening_after: int
    engine_rule: str
    oracle_rule: str
    reason: str


def oracle_set_aside(rd: Reading, s: DosingSettings) -> bool:
    """Texte de la règle : marquée « écarter de l'ajustement » et pas sous le seuil bas."""
    return rd.note is not None and rd.note.exclude_from_dosing and rd.mg_dl >= round(s.low_g_l * 100)


def oracle_in_morning(rd: Reading, s: DosingSettings) -> bool:
    lo = s.morning_start.hour * 60 + s.morning_start.minute
    hi = s.morning_end.hour * 60 + s.morning_end.minute
    marker = rd.meal.value if rd.meal is not None else None
    return lo <= rd.device_time.hour * 60 + rd.device_time.minute <= hi and marker in ("fasting", "before_meal", None)


def oracle_mornings(readings: list[Reading], s: DosingSettings, use_markers: bool = True) -> dict[date, Reading]:
    """Glycémie du matin par jour, relue depuis le texte de la règle, sans le code du moteur."""
    lo = s.morning_start.hour * 60 + s.morning_start.minute
    hi = s.morning_end.hour * 60 + s.morning_end.minute
    in_window = [
        rd for rd in sorted(readings, key=lambda x: x.device_time)
        if lo <= rd.device_time.hour * 60 + rd.device_time.minute <= hi and not oracle_set_aside(rd, s)
    ]
    out: dict[date, Reading] = {}
    if not use_markers:
        for rd in in_window:
            out.setdefault(rd.device_time.date(), rd)
        return out
    for rd in in_window:
        if rd.meal is not None and rd.meal.value == "fasting":
            out.setdefault(rd.device_time.date(), rd)
    for rd in in_window:
        if rd.meal is None or rd.meal.value == "before_meal":
            out.setdefault(rd.device_time.date(), rd)
    return out


def oracle(readings: list[Reading], last_change: datetime, today: date, s: DosingSettings) -> str:
    """Relecture littérale du protocole, sans réutiliser le code du moteur."""
    mornings = oracle_mornings(readings, s)
    firsts = {d: rd.mg_dl for d, rd in mornings.items()}
    times = {d: rd.device_time for d, rd in mornings.items()}
    days = sorted(d for d in firsts if times[d] > last_change)
    if not days:
        return "keep"
    last = days[-1]
    if (today - last).days > s.stale_days:
        return "keep"
    if firsts[last] < round(s.low_g_l * 100):
        return "decrease"
    need = s.high_streak_days
    window = [last - timedelta(days=k) for k in range(need)]
    if all(d in firsts and times[d] > last_change and firsts[d] > round(s.high_g_l * 100) for d in window):
        return "increase"
    return "keep"


RULE_TO_ORACLE = {
    DoseRule.KEEP: "keep",
    DoseRule.DECREASE_LOW_MORNING: "decrease",
    DoseRule.INCREASE_HIGH_MORNINGS: "increase",
}


def replay(source: str, readings: list[Reading], start_evening: int = 4) -> tuple[list[DayRow], list[str]]:
    readings = sorted(readings)
    if not readings:
        return [], []
    first_day, last_day = readings[0].day, readings[-1].day
    changes = [DoseChange(datetime.combine(first_day - timedelta(days=1), EVENING), 8, start_evening, DoseRule.START)]
    rows: list[DayRow] = []
    failures: list[str] = []
    index = 0
    day = first_day
    while day <= last_day:
        cutoff = datetime.combine(day, EVENING)
        while index < len(readings) and readings[index].device_time <= cutoff:
            index += 1
        visible = readings[:index]
        proposal = propose(visible, changes, SETTINGS, day)
        expected = oracle(visible, changes[-1].effective, day, SETTINGS)
        before = changes[-1].evening_ui
        engine = RULE_TO_ORACLE[proposal.rule]
        # À 0 UI, le moteur garde la dose : l'oracle dit "decrease" mais sans effet possible.
        if expected == "decrease" and before == 0:
            expected = "keep"
        if engine != expected:
            failures.append(f"{source} {day} : moteur={engine} oracle={expected} ({proposal.reason})")
        failures.extend(
            f"{source} {day} : glycémie basse écartée {fmt_reading(x)}"
            for x in proposal.excluded if x.mg_dl < round(SETTINGS.low_g_l * 100)
        )
        if proposal.changes_dose:
            change = apply_proposal(proposal, cutoff)
            failures.extend(check_change(source, day, change, changes[-1], visible))
            changes.append(change)
        morning = next((m.reading.mg_dl for m in proposal.mornings if m.day == day), None)
        rows.append(
            DayRow(source, day, morning, before, changes[-1].evening_ui, proposal.rule.value, expected, proposal.reason)
        )
        day += timedelta(days=1)

    per_day: dict[date, int] = {}
    for change in changes[1:]:
        per_day[change.effective.date()] = per_day.get(change.effective.date(), 0) + 1
    failures.extend(f"{source} {d} : {n} changements le même jour" for d, n in per_day.items() if n > 1)
    return rows, failures


def check_change(source: str, day: date, change: DoseChange, previous: DoseChange, visible: list[Reading]) -> list[str]:
    errors = []
    if change.evening_ui < 0:
        errors.append(f"{source} {day} : dose négative {change.evening_ui}")
    if change.morning_ui != previous.morning_ui:
        errors.append(f"{source} {day} : dose du matin modifiée")
    delta = change.evening_ui - previous.evening_ui
    if change.rule is DoseRule.INCREASE_HIGH_MORNINGS and delta != SETTINGS.step_ui:
        errors.append(f"{source} {day} : hausse de {delta} UI")
    if change.rule is DoseRule.DECREASE_LOW_MORNING and not -SETTINGS.step_ui <= delta < 0:
        errors.append(f"{source} {day} : baisse de {delta} UI")
    set_aside = [
        r for r in sorted(visible)
        if r.device_time > previous.effective and oracle_in_morning(r, SETTINGS) and oracle_set_aside(r, SETTINGS)
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
    if any(oracle_set_aside(r, SETTINGS) for r in evidence):
        errors.append(f"{source} {day} : preuve écartée par une note {change.evidence}")
    if any(r.device_time <= previous.effective for r in evidence):
        errors.append(f"{source} {day} : preuve antérieure au dernier changement")
    if change.rule is DoseRule.INCREASE_HIGH_MORNINGS:
        days = [r.day for r in evidence]
        consecutive = all((b - a).days == 1 for a, b in zip(days, days[1:]))
        if len(evidence) != SETTINGS.high_streak_days or not consecutive or any(r.mg_dl <= 150 for r in evidence):
            errors.append(f"{source} {day} : hausse mal justifiée {change.evidence}")
    if change.rule is DoseRule.DECREASE_LOW_MORNING and (len(evidence) != 1 or evidence[0].mg_dl >= 80):
        errors.append(f"{source} {day} : baisse mal justifiée {change.evidence}")
    return errors


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
    for path in args.json:
        readings = list(parse_file(path).readings)
        rows, errs = replay(path.name, readings)
        all_rows += rows
        failures += errs
        changed[path.name] = changed_mornings(readings)
    synthetic_changed = 0
    notes_changed = notes_days = set_aside = kept_low = 0
    for seed in range(args.synthetic):
        readings = synthetic_patient(seed)
        rows, errs = replay(f"synthetique-{seed}", readings)
        all_rows += rows
        failures += errs
        synthetic_changed += changed_mornings(readings)
        noted = with_notes(readings, seed)
        noted_rows, errs = replay(f"synthetique-notes-{seed}", noted)
        all_rows += noted_rows
        failures += errs
        notes_changed += decisions_changed(rows, noted_rows)
        notes_days += len(noted_rows)
        asked = [r for r in noted if r.note is not None and r.note.exclude_from_dosing and oracle_in_morning(r, SETTINGS)]
        set_aside += sum(oracle_set_aside(r, SETTINGS) for r in asked)
        kept_low += sum(not oracle_set_aside(r, SETTINGS) for r in asked)

    args.out.mkdir(parents=True, exist_ok=True)
    csv_path = args.out / f"replay-{datetime.now():%Y%m%d-%H%M%S}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["source", "jour", "matin_mg_dl", "soir_avant", "soir_apres", "regle_moteur", "regle_oracle", "raison"])
        for row in all_rows:
            writer.writerow([row.source, row.day, row.morning_mg, row.evening_before, row.evening_after, row.engine_rule, row.oracle_rule, row.reason])

    agree = sum(1 for row in all_rows if RULE_TO_ORACLE[DoseRule(row.engine_rule)] == row.oracle_rule or
                (row.oracle_rule == "decrease" and row.evening_before == 0))
    counts = {rule: sum(1 for row in all_rows if row.engine_rule == rule) for rule in ("keep", "decrease_low_morning", "increase_high_mornings")}
    total = len(all_rows)
    score = agree / total if total else 1.0
    print(f"jours rejoués : {total}, accord moteur/oracle : {score:.2%}, échecs invariants/accord : {len(failures)}")
    print(f"décisions : {counts}")
    print(f"glycémies du matin changées par les marqueurs (synthétiques) : {synthetic_changed}")
    print(
        f"notes : {set_aside} glycémie(s) du matin écartée(s), {kept_low} glycémie(s) basse(s) marquée(s) à écarter "
        f"et comptée(s), {notes_changed} décision(s) changée(s) sur {notes_days} jours"
    )
    for name, n in changed.items():
        print(f"{name} : {n} glycémie(s) du matin changée(s) par les marqueurs")
    for path in args.json:
        final = [row for row in all_rows if row.source == path.name]
        if final:
            print(f"{path.name} : dose du soir finale simulée {final[-1].evening_after} UI")
    print(f"CSV : {csv_path}")
    for failure in failures[:20]:
        print("ÉCHEC", failure)
    return 0 if not failures and score == 1.0 else 1


if __name__ == "__main__":
    sys.exit(main())
