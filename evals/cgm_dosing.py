"""Eval du moteur de doses avec le capteur en continu : traces tirées au hasard contre un oracle tenu à côté.

Chaque scénario (graine fixe) tire un protocole (seuils, heures du capteur, tolérance, dose du matin ou non), puis des
jours de mesures du capteur toutes les 15 min (bruit, trous, creux de nuit, valeurs hors échelle) mêlées à des mesures du
lecteur au hasard (marqueurs « à jeun » ou « avant repas », parfois très basses), et parfois une note qui écarte une
mesure du capteur.

Vérifié à chaque scénario, par un oracle écrit sans le code du moteur (force brute sur les mesures) :
- glycémie de référence : une mesure du capteur, à la tolérance de l'heure réglée, la plus proche (la plus tôt en cas
  d'égalité), jamais une mesure du lecteur, jamais une mesure écartée par une note au-dessus du seuil bas ;
- jour sans valeur du capteur dans la tolérance : pas de référence, même avec une mesure du lecteur ce jour-là ;
- dose proposée : baisse sous le seuil bas, hausse après N jours de suite au-dessus du seuil haut, sinon inchangée ;
- alerte hypoglycémie : un épisode du capteur (mesures à 30 min ou moins) compte pour un, chaque mesure du lecteur pour une.
Seuil de réussite : 100 % des scénarios.

Sorties dans /tmp/glucofi-cgm/<horodatage>/ : scenarios.csv (une ligne par scénario) et le rapport.

Usage : python3 -m evals.cgm_dosing [--scenarios 200] [--days 12]
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from contracts import (
    MG_DL_HIGH,
    MG_DL_LOW,
    DoseChange,
    DoseRule,
    DoseTarget,
    DosingSettings,
    Meal,
    NoteTag,
    Reading,
    ReadingNote,
    Titration,
)
from services.dosing import propose

OUT_DIR = Path("/tmp/glucofi-cgm")
START = DoseChange(datetime(2026, 1, 1, 0, 0), 10, 6, DoseRule.START)
FIRST_DAY = date(2026, 1, 2)
CGM_STEP_MIN = 15


@dataclass
class Scenario:
    seed: int
    settings: DosingSettings
    readings: list[Reading]
    problems: list[str] = field(default_factory=list)
    facts: dict[str, int] = field(default_factory=dict)


def minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def make_settings(rng: random.Random) -> DosingSettings:
    low = rng.choice((0.7, 0.8, 0.9))
    high = round(low + rng.choice((0.5, 0.7, 0.9)), 2)
    settings = DosingSettings(
        insulin="Insuline test", low_g_l=low, high_g_l=high, step_ui=rng.randint(1, 4), high_streak_days=rng.randint(1, 3),
        cgm_morning=time(rng.randint(6, 9), rng.choice((0, 15, 30, 45))), cgm_window_min=rng.choice((5, 15, 20, 30)),
    )
    if rng.random() < 0.5:
        settings = replace(
            settings,
            morning_titration=Titration(low, high, rng.randint(1, 3), rng.randint(1, 3)),
            cgm_evening=time(rng.randint(17, 20), rng.choice((0, 30))),
        )
    return settings


def make_readings(rng: random.Random, settings: DosingSettings, days: int) -> list[Reading]:
    readings: list[Reading] = []
    base = rng.choice((70, 90, 110, 140, 170, 210))
    for offset in range(days):
        midnight = datetime.combine(FIRST_DAY + timedelta(days=offset), time(0, 0))
        level = max(30, min(500, base + rng.randint(-40, 40)))
        gap = rng.random() < 0.15
        gap_from = rng.randint(0, 80)
        for slot in range(24 * 60 // CGM_STEP_MIN):
            if gap and gap_from <= slot < gap_from + rng.randint(2, 20):
                continue
            when = midnight + timedelta(minutes=slot * CGM_STEP_MIN)
            value = level + rng.randint(-15, 15)
            if midnight.hour == 0 and when.hour < 5 and rng.random() < 0.05:
                value = rng.randint(40, 65)  # creux de nuit
            value = max(10, min(600, value))
            if rng.random() < 0.003:
                value = rng.choice((MG_DL_HIGH, MG_DL_LOW))
            readings.append(Reading(when, value, int(when.timestamp()), cgm=True))
        for _ in range(rng.randint(0, 3)):
            when = midnight + timedelta(minutes=rng.randint(6 * 60, 21 * 60))
            readings.append(Reading(
                when, rng.choice((45, 70, 95, 130, 260)), int(when.timestamp()),
                meal=rng.choice((Meal.FASTING, Meal.BEFORE_MEAL, None)),
            ))
    # une mesure du capteur au hasard écartée par une note
    sensor = [i for i, r in enumerate(readings) if r.cgm]
    for i in rng.sample(sensor, k=min(len(sensor), days // 3)):
        readings[i] = replace(readings[i], note=ReadingNote((NoteTag.ILLNESS,), "", True))
    dedup = {(r.device_time, r.mg_dl): r for r in readings}
    return sorted(dedup.values())


def oracle_reference(readings, settings: DosingSettings, at: time, low_mg: int) -> dict[date, Reading]:
    """Mesure du capteur la plus proche de l'heure, par jour, écrite en force brute."""
    out: dict[date, Reading] = {}
    for day in sorted({r.day for r in readings}):
        best = None
        for r in readings:
            if r.day != day or not r.cgm:
                continue
            distance = abs(minutes(r.device_time.time()) - minutes(at))
            if distance > settings.cgm_window_min:
                continue
            if r.note is not None and r.note.exclude_from_dosing and r.mg_dl >= low_mg:
                continue
            if best is None or (distance, r.device_time) < best[0]:
                best = ((distance, r.device_time), r)
        if best is not None:
            out[day] = best[1]
    return out


def oracle_ui(refs: dict[date, Reading], titration: Titration, current: int, today: date) -> int:
    """Règle de base sans paliers : baisse sous le seuil bas, hausse après N jours de suite au-dessus du seuil haut."""
    if not refs:
        return current
    days = sorted(refs)
    latest = refs[days[-1]]
    if (today - days[-1]).days > 2:
        return current
    if latest.mg_dl < round(titration.low_g_l * 100):
        return max(0, current - titration.step_ui)
    streak = 0
    for day in reversed(days):
        if refs[day].mg_dl <= round(titration.high_g_l * 100) or (streak and (days[-1] - day).days != streak):
            break
        streak += 1
    return current + titration.step_ui if streak >= titration.high_streak_days else current


def oracle_hypo_count(readings, hypo_mg: int, today: date) -> int:
    since = datetime.combine(today - timedelta(days=6), time(0, 0))
    lows = [r for r in readings if r.device_time >= since and r.mg_dl < hypo_mg]
    count = sum(not r.cgm for r in lows)
    previous = None
    for r in (r for r in lows if r.cgm):
        if previous is None or r.device_time - previous > timedelta(minutes=30):
            count += 1
        previous = r.device_time
    return count


def run_scenario(seed: int, days: int = 12) -> Scenario:
    rng = random.Random(seed)
    settings = make_settings(rng)
    readings = make_readings(rng, settings, days)
    scenario = Scenario(seed, settings, readings)
    today = readings[-1].day
    proposal = propose(readings, [START], settings, today)

    for target, at in ((DoseTarget.EVENING, settings.cgm_morning), (DoseTarget.MORNING, settings.cgm_evening)):
        if settings.titration(target) is None:
            continue
        adjustment = proposal.evening if target is DoseTarget.EVENING else proposal.morning
        titration = settings.titration(target)
        low_mg = round(titration.low_g_l * 100)
        expected = oracle_reference(readings, settings, at, low_mg)
        expected = {d: r for d, r in expected.items() if r.device_time > START.effective}
        got = {m.day: m.reading for m in adjustment.references}
        name = target.value
        if got != expected:
            scenario.problems.append(f"référence {name} : {len(got)} jours rendus, {len(expected)} attendus")
        if any(not r.cgm for r in got.values()):
            scenario.problems.append(f"référence {name} : une mesure du lecteur a servi")
        for day, r in got.items():
            if abs(minutes(r.device_time.time()) - minutes(at)) > settings.cgm_window_min:
                scenario.problems.append(f"référence {name} du {day} hors tolérance : {r.device_time:%H:%M}")
        current = START.evening_ui if target is DoseTarget.EVENING else START.morning_ui
        if adjustment.proposed_ui != oracle_ui(expected, titration, current, today):
            scenario.problems.append(
                f"dose {name} : {adjustment.proposed_ui} UI proposées, {oracle_ui(expected, titration, current, today)} attendues"
            )
        scenario.facts[f"jours sans référence {name}"] = len({r.day for r in readings} - set(got))
        scenario.facts[f"hausses ou baisses {name}"] = int(adjustment.changes_dose)

    hypo_mg = round(settings.hypo_alert_g_l * 100)
    expected_hypos = oracle_hypo_count(readings, hypo_mg, today)
    alert = next((a for a in proposal.alerts if a.code == "hypo"), None)
    shown = int(alert.message.split(" ", 1)[0]) if alert else 0
    if shown != expected_hypos:
        scenario.problems.append(f"hypoglycémies : {shown} annoncées, {expected_hypos} attendues")
    scenario.facts["épisodes d'hypoglycémie"] = expected_hypos
    return scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", type=int, default=200)
    parser.add_argument("--days", type=int, default=12)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out / stamp
    out.mkdir(parents=True, exist_ok=True)
    scenarios = [run_scenario(seed, args.days) for seed in range(args.scenarios)]
    failed = [s for s in scenarios if s.problems]
    keys = sorted({k for s in scenarios for k in s.facts})
    with (out / "scenarios.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["graine", "problemes", *keys])
        for s in scenarios:
            writer.writerow([s.seed, " | ".join(s.problems), *(s.facts.get(k, 0) for k in keys)])
    lines = [f"{len(scenarios)} scénarios, {len(failed)} en échec"]
    lines += [f"  couverture : {k} = {sum(s.facts.get(k, 0) for s in scenarios)}" for k in keys]
    lines += [f"  graine {s.seed} : {s.problems[0]}" for s in failed[:10]]
    verdict = "OK" if not failed else "ÉCHEC"
    lines.append(f"Verdict : {verdict} (seuil : 100 %) ; détail dans {out / 'scenarios.csv'}")
    (out / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
