"""Eval de fusion de bases : deux appareils (PC et tablette) vivent leur vie, fusionnent dans un sens ou dans l'autre.

Chaque scénario (graine fixe) tire au hasard une suite d'opérations sur l'un ou l'autre appareil :
lecture du lecteur (plages de mesures qui se recouvrent, marqueurs parfois absents), note posée, modifiée
ou effacée (parfois à la même seconde sur les deux appareils), dose déclarée prise, non prise ou remise à
« non renseignée » (journal des injections), repas écrit puis estimé, corrigé ou effacé (journal alimentaire), dose validée, protocole modifié, fusion
(export d'un appareil, fusion dans l'autre). À la fin, deux allers-retours.

Vérifié à la fin, contre un oracle tenu à côté (tout ce qui a été fait, sur les deux appareils) :
- convergence : les deux bases ont le même contenu ;
- rien de perdu, rien en double : chaque mesure, dose validée et version du protocole une seule fois ;
- marqueurs : une mesure porte le marqueur qu'une lecture lui a donné ;
- notes : la dernière modification (date, puis contenu) l'emporte, effacement compris ;
- journal des injections : la dernière déclaration (date, puis état) l'emporte, effacement compris ;
- journal alimentaire : la dernière écriture (date, puis texte, glucides, calories) l'emporte, effacement compris ;
- protocole en cours : la version la plus récente ;
- idempotence : une fusion de plus ne change rien ;
- le fichier fusionné n'est jamais modifié.
Seuil de réussite : 100 % des scénarios.

Sorties dans /tmp/glucofi-merge/<horodatage>/ : fusions.csv (une ligne par fusion) et le rapport.

Usage : python3 -m evals.store_merge [--scenarios 60] [--steps 80]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path

from contracts import DoseChange, DoseRule, DosingSettings, DoseTarget, InjectionState, Meal, MealEntry, MealSlot, NoteTag, Reading, ReadingNote
from services.store import MergeSummary, Store
from services.store.store import settings_json

OUT_DIR = Path("/tmp/glucofi-merge")
PROTOCOL = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
T0 = datetime(2026, 1, 1, 8, 0)


@dataclass
class Oracle:
    readings: dict[tuple[str, int], str | None] = field(default_factory=dict)
    notes: dict[tuple[str, int], tuple[str, tuple]] = field(default_factory=dict)
    doses: set[tuple] = field(default_factory=set)
    protocols: set[tuple[str, str]] = field(default_factory=set)
    injections: dict[tuple[str, str], tuple[str, str]] = field(default_factory=dict)

    meals: dict[tuple[str, str], tuple[str, tuple]] = field(default_factory=dict)

    def meal(self, key, at: str, content: tuple) -> None:
        if key not in self.meals or (at, content) > self.meals[key]:
            self.meals[key] = (at, content)

    def injection(self, key, at: str, state: str) -> None:
        if key not in self.injections or (at, state) > self.injections[key]:
            self.injections[key] = (at, state)

    def note(self, key, at: str, content: tuple) -> None:
        if key not in self.notes or (at, content) > self.notes[key]:
            self.notes[key] = (at, content)


def meter_pool(rng: random.Random, days: int) -> list[Reading]:
    out = []
    for d in range(days):
        for hour in (7, 12, 19):
            if rng.random() < 0.85:
                t = T0.replace(hour=hour, minute=rng.randint(0, 59)) + timedelta(days=d)
                meal = rng.choice((Meal.FASTING, Meal.BEFORE_MEAL, Meal.AFTER_MEAL, None))
                out.append(Reading(t, rng.randint(50, 350), int(t.timestamp()), meal=meal))
    return out


def content_of(store: Store) -> tuple:
    return (
        [(r.device_time, r.mg_dl, r.meal, r.note) for r in store.readings()],
        sorted((c.effective, c.morning_ui, c.evening_ui, c.rule.value, c.evidence) for c in store.dose_changes()),
        sorted((c.effective, json.dumps(settings_json(c.settings), sort_keys=True)) for c in store.protocol_changes()),
        store.db.execute("SELECT day, target, state, updated_at FROM injections WHERE state IS NOT NULL ORDER BY day, target").fetchall(),
        store.db.execute(
            "SELECT day, slot, text, calories_kcal, carbs_g, carbs_low_g, carbs_high_g, source, updated_at FROM meals"
            " WHERE text IS NOT NULL ORDER BY day, slot"
        ).fetchall(),
        store.dosing_settings(),
        store.get_setting("patient_name"),
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Scenario:
    def __init__(self, seed: int, steps: int, work: Path):
        self.seed, self.steps, self.work = seed, steps, work
        self.rng = random.Random(seed)
        self.pool = meter_pool(self.rng, 40)
        self.devices = {name: Store(work / name / "glucofi.db") for name in ("pc", "tablette")}
        self.oracle = Oracle()
        self.clock = T0
        self.exports = 0
        self.merges: list[tuple[str, str, MergeSummary]] = []
        self.problems: list[str] = []
        self.ops: Counter[str] = Counter()
        self.noted_this_second: set[str] = set()
        self.declared_this_second: set[str] = set()

    def tick(self, same_second: bool = False) -> datetime:
        if not same_second:
            self.clock += timedelta(minutes=self.rng.randint(1, 600))
            self.noted_this_second = set()
            self.declared_this_second = set()
        return self.clock

    def close(self) -> None:
        for store in self.devices.values():
            store.close()

    # opérations

    def read_meter(self, name: str) -> None:
        store = self.devices[name]
        lo = self.rng.randrange(len(self.pool))
        batch = self.pool[lo:lo + self.rng.randint(1, 30)]
        if self.rng.random() < 0.3:
            batch = [replace(r, meal=None) for r in batch]
        store.import_readings(batch, "lecteur")
        for r in batch:
            key = (r.device_time.isoformat(), r.mg_dl)
            marker = r.meal.value if r.meal is not None else None
            self.oracle.readings[key] = self.oracle.readings.get(key) or marker

    def edit_note(self, name: str, same_second: bool) -> None:
        store = self.devices[name]
        known = store.readings()
        if not known:
            return
        target = self.rng.choice(known)
        if self.rng.random() < 0.25:
            note = None
        else:
            note = ReadingNote(
                (self.rng.choice(list(NoteTag)),), self.rng.choice(("", "fièvre", "repas de famille")), self.rng.random() < 0.5
            )
        store.set_note(target, note)
        # même seconde : seulement entre deux appareils (sur un appareil, la dernière écriture l'emporte)
        shared = same_second and bool(self.noted_this_second) and name not in self.noted_this_second
        self.ops["note à la même seconde que l'autre appareil"] += shared
        at = self.tick(shared).isoformat()
        self.noted_this_second.add(name)
        key = (target.device_time.isoformat(), target.mg_dl)
        store.db.execute("UPDATE reading_notes SET updated_at = ? WHERE device_time = ? AND mg_dl = ?", (at, *key))
        store.db.commit()
        note = note or ReadingNote()
        self.oracle.note(key, at, (json.dumps([t.value for t in note.tags]), note.text, int(note.exclude_from_dosing)))

    def declare_injection(self, name: str, same_second: bool) -> None:
        store = self.devices[name]
        day = (T0 + timedelta(days=self.rng.randint(0, 9))).date()
        target = self.rng.choice(list(DoseTarget))
        state = self.rng.choice((InjectionState.TAKEN, InjectionState.TAKEN, InjectionState.MISSED, None))
        store.set_injection(day, target, state)
        shared = same_second and bool(self.declared_this_second) and name not in self.declared_this_second
        self.ops["injection à la même seconde que l'autre appareil"] += shared
        at = self.tick(shared).isoformat()
        self.declared_this_second.add(name)
        store.db.execute("UPDATE injections SET updated_at = ? WHERE day = ? AND target = ?", (at, day.isoformat(), target.value))
        store.db.commit()
        self.oracle.injection((day.isoformat(), target.value), at, state.value if state is not None else "")

    def write_meal(self, name: str, same_second: bool) -> None:
        """Repas écrit puis estimé ou corrigé à la main, ou effacé ; les jours et repas se recoupent entre appareils."""
        store = self.devices[name]
        day = (T0 + timedelta(days=self.rng.randint(0, 5))).date()
        slot = self.rng.choice(list(MealSlot))
        kind = self.rng.choice(("estimé", "estimé", "corrigé", "texte seul", "effacé"))
        if kind == "effacé":
            store.set_meal(None, day=day, slot=slot)
            content = ("", 0, 0)
        else:
            kcal = self.rng.randint(150, 1200)
            carbs = float(self.rng.randint(5, 150))
            text = f"repas {self.rng.randint(0, 3)}"
            if kind == "texte seul":
                store.set_meal(MealEntry(day, slot, text))
                content = (text, 0, 0)
            else:
                store.set_meal(MealEntry(day, slot, text, kcal, carbs, carbs - 5, carbs + 5, "gemini" if kind == "estimé" else "manual"))
                content = (text, carbs, kcal)
        shared = same_second and bool(self.declared_this_second) and name not in self.declared_this_second
        self.ops["repas à la même seconde que l'autre appareil"] += shared
        at = self.tick(shared).isoformat()
        self.declared_this_second.add(name)
        store.db.execute("UPDATE meals SET updated_at = ? WHERE day = ? AND slot = ?", (at, day.isoformat(), slot.value))
        store.db.commit()
        self.oracle.meal((day.isoformat(), slot.value), at, content)

    def validate_dose(self, name: str) -> None:
        store = self.devices[name]
        current = store.current_dose()
        evening = max(0, (current.evening_ui if current else 6) + self.rng.choice((-2, 2)))
        rule = DoseRule.START if current is None else self.rng.choice((DoseRule.MANUAL, DoseRule.INCREASE_HIGH_MORNINGS))
        change = DoseChange(self.tick(), 10, evening, rule, (f"preuve {self.seed}-{self.ops.total()}",))
        store.add_dose_change(change)
        self.oracle.doses.add((change.effective, 10, evening, rule.value, change.evidence))

    def edit_protocol(self, name: str) -> None:
        settings = replace(PROTOCOL, high_g_l=self.rng.choice((1.3, 1.4, 1.5, 1.6)), step_ui=self.rng.choice((1, 2, 3)))
        saved = self.devices[name].save_dosing_settings(settings, effective=self.tick())
        if saved is not None:
            self.oracle.protocols.add((saved.effective, json.dumps(settings_json(settings), sort_keys=True)))

    def merge(self, into: str, source: str) -> MergeSummary:
        self.exports += 1
        path = self.devices[source].export_to(self.work / f"export-{self.exports}.db")
        before = digest(path)
        summary = self.devices[into].merge_from(path)
        if digest(path) != before:
            self.problems.append(f"fusion {self.exports} : fichier source modifié")
        self.merges.append((into, source, summary))
        return summary

    def run(self) -> None:
        if self.rng.random() < 0.5:
            self.devices["pc"].set_setting("patient_name", "Patient fictif")
        for _ in range(self.steps):
            name = self.rng.choice(("pc", "tablette"))
            other = "tablette" if name == "pc" else "pc"
            roll = self.rng.random()
            if roll < 0.30:
                self.ops["lecture"] += 1
                self.read_meter(name)
            elif roll < 0.55:
                self.ops["note"] += 1
                self.edit_note(name, same_second=self.rng.random() < 0.4)
            elif roll < 0.65:
                self.ops["injection"] += 1
                self.declare_injection(name, same_second=self.rng.random() < 0.4)
            elif roll < 0.69:
                self.ops["repas"] += 1
                self.write_meal(name, same_second=self.rng.random() < 0.4)
            elif roll < 0.74:
                self.ops["dose"] += 1
                self.validate_dose(name)
            elif roll < 0.80:
                self.ops["protocole"] += 1
                self.edit_protocol(name)
            else:
                self.ops["fusion"] += 1
                self.merge(name, other)
        for _ in range(2):
            self.merge("tablette", "pc")
            self.merge("pc", "tablette")
        self.check()

    # vérifications

    def check(self) -> None:
        pc, tablet = (self.devices[n] for n in ("pc", "tablette"))
        if content_of(pc) != content_of(tablet):
            self.problems.append("les deux bases n'ont pas convergé")
        for name, store in self.devices.items():
            readings = store.readings()
            keys = [(r.device_time.isoformat(), r.mg_dl) for r in readings]
            if len(keys) != len(set(keys)):
                self.problems.append(f"{name} : mesures en double")
            if set(keys) != set(self.oracle.readings):
                self.problems.append(f"{name} : {len(set(self.oracle.readings) - set(keys))} mesure(s) perdue(s)")
            for r, key in zip(readings, keys):
                marker = r.meal.value if r.meal is not None else None
                if marker != self.oracle.readings.get(key):
                    self.problems.append(f"{name} {key} : marqueur {marker}, attendu {self.oracle.readings.get(key)}")
                expected = self.oracle.notes.get(key)
                got = None if r.note is None else (json.dumps([t.value for t in r.note.tags]), r.note.text, int(r.note.exclude_from_dosing))
                want = None if expected is None or expected[1] == ("[]", "", 0) else expected[1]
                if got != want:
                    self.problems.append(f"{name} {key} : note {got}, attendu {want}")
            declared = {(i.day.isoformat(), i.target.value): i.state.value for i in store.injections()}
            wanted = {key: state for key, (_at, state) in self.oracle.injections.items() if state}
            if declared != wanted:
                self.problems.append(f"{name} : journal des injections {declared}, attendu {wanted}")
            meals = {(m.day.isoformat(), m.slot.value): m.text for m in store.meals()}
            wanted_meals = {key: content[0] for key, (_at, content) in self.oracle.meals.items() if content[0]}
            if meals != wanted_meals:
                self.problems.append(f"{name} : journal alimentaire {meals}, attendu {wanted_meals}")
            doses = {(c.effective, c.morning_ui, c.evening_ui, c.rule.value, c.evidence) for c in store.dose_changes()}
            if doses != self.oracle.doses or len(store.dose_changes()) != len(self.oracle.doses):
                self.problems.append(f"{name} : doses {len(store.dose_changes())}, attendu {len(self.oracle.doses)}")
            versions = [(c.effective, json.dumps(settings_json(c.settings), sort_keys=True)) for c in store.protocol_changes()]
            if set(versions) != self.oracle.protocols or len(versions) != len(set(versions)):
                self.problems.append(f"{name} : {len(versions)} versions du protocole, attendu {len(self.oracle.protocols)}")
            if self.oracle.protocols:
                latest = max(self.oracle.protocols)[1]
                if json.dumps(settings_json(store.dosing_settings()), sort_keys=True) != latest:
                    self.problems.append(f"{name} : protocole en cours qui n'est pas la version la plus récente")
        for into, source in (("pc", "tablette"), ("tablette", "pc")):
            if self.merge(into, source).changed:
                self.problems.append(f"fusion de plus {source} -> {into} : changements alors que tout est déjà fusionné")


def run_scenario(seed: int, steps: int) -> Scenario:
    with tempfile.TemporaryDirectory(prefix="glucofi-merge-eval-") as tmp:
        scenario = Scenario(seed, steps, Path(tmp))
        try:
            scenario.run()
        finally:
            scenario.close()
    return scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", type=int, default=60)
    parser.add_argument("--steps", type=int, default=80)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    work = args.out / f"{datetime.now():%Y%m%d-%H%M%S}"
    work.mkdir(parents=True, exist_ok=True)
    csv_path = work / "fusions.csv"
    ops: Counter[str] = Counter()
    totals: Counter[str] = Counter()
    failed: list[tuple[int, list[str]]] = []
    warned = 0
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "scenario", "vers", "depuis", "mesures", "marqueurs", "notes_ajoutees", "notes_mises_a_jour",
            "injections_ajoutees", "injections_mises_a_jour", "repas_ajoutes", "repas_mis_a_jour", "doses", "versions_protocole", "protocole_change", "alertes",
        ])
        for seed in range(args.scenarios):
            scenario = run_scenario(seed, args.steps)
            ops += scenario.ops
            if scenario.problems:
                failed.append((seed, scenario.problems))
            for into, source, s in scenario.merges:
                writer.writerow([
                    seed, into, source, s.readings_added, s.markers_added, s.notes_added, s.notes_updated,
                    s.injections_added, s.injections_updated, s.meals_added, s.meals_updated, s.doses_added, s.protocol_versions_added, int(s.protocol_changed), " | ".join(s.warnings),
                ])
                totals["fusions"] += 1
                totals["mesures"] += s.readings_added
                totals["marqueurs"] += s.markers_added
                totals["notes"] += s.notes_added + s.notes_updated
                totals["injections"] += s.injections_added + s.injections_updated
                totals["repas"] += s.meals_added + s.meals_updated
                totals["doses"] += s.doses_added
                totals["versions"] += s.protocol_versions_added
                warned += bool(s.warnings)

    passed = args.scenarios - len(failed)
    print(f"scénarios : {args.scenarios}, réussis : {passed} ({passed / max(1, args.scenarios):.0%})")
    print(f"opérations : {dict(ops)}")
    print()
    print("| Ramené par les fusions | Total |")
    print("| --- | --- |")
    for label, key in (("fusions", "fusions"), ("mesures", "mesures"), ("marqueurs complétés", "marqueurs"),
                       ("notes ajoutées ou mises à jour", "notes"),
                       ("injections ajoutées ou mises à jour", "injections"),
                       ("repas ajoutés ou mis à jour", "repas"), ("doses validées", "doses"),
                       ("versions du protocole", "versions")):
        print(f"| {label} | {totals[key]} |")
    print(f"| fusions avec alerte (modifié des deux côtés) | {warned} |")
    print()
    for seed, problems in failed[:10]:
        for problem in problems[:5]:
            print(f"ÉCHEC scénario {seed} : {problem}")
    print(f"Verdict : {'OK' if not failed else 'ÉCHEC'}")
    print(f"CSV : {csv_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
