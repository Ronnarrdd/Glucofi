"""Eval PC + tablette sur le même capteur : chacun lit LibreLinkUp à son rythme, puis ils fusionnent dans les deux sens.

Chaque scénario (graine fixe) tire un capteur (une valeur toutes les 5 min environ, secondes qui dérivent, trous), un PC qui
lit toutes les 15 min pendant des plages d'allumage, une tablette qui lit à l'ouverture (parfois deux fois, parfois des
trous de plus de 12 h, la durée que LibreLinkUp garde), des mesures du lecteur Accu-Chek sur l'un ou l'autre appareil, puis
deux fusions aller-retour. Les lectures passent par le vrai fetch_cgm (faux serveur) et AppState.import_cgm.

Vérifié à chaque scénario :
- convergence : les deux appareils ont les mêmes mesures du capteur et les mêmes mesures du lecteur ;
- rien de perdu : chaque valeur qu'un appareil a rangée est chez l'autre ;
- rien en double : au plus une valeur du capteur par tranche de 15 min (deux appareils qui ne voient pas la même fenêtre de
  12 h ne gardent pas un point différent dans la tranche coupée) ;
- le journal des imports ne contient aucune ligne du capteur, et « dernière récupération » reste celle du lecteur ;
- idempotence : une fusion de plus ne change rien et ne se dit pas « modifiée ».
Seuil de réussite : 100 % des scénarios.

Sorties dans /tmp/glucofi-cgm-devices/<horodatage>/ : scenarios.csv et le rapport.

Usage : python3 -m evals.cgm_two_devices [--scenarios 30] [--days 3]
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from app.state import AppState
from contracts import CGM_SOURCE, Meal, Reading
from services.device import Credentials, fetch_cgm
from services.device.tests.fake_llu import FakeLlu, graph_data, item
from services.store import Store

OUT_DIR = Path("/tmp/glucofi-cgm-devices")
WINDOW = timedelta(hours=12)
ACCOUNT = Credentials("suivi@example.org", "secret", "FR")
START = datetime(2026, 10, 1, 0, 0)


@dataclass
class Scenario:
    seed: int
    problems: list[str] = field(default_factory=list)
    facts: dict[str, int] = field(default_factory=dict)


def us(at: datetime) -> str:
    return f"{at.month}/{at.day}/{at.year} {at.hour % 12 or 12}:{at:%M:%S} {'PM' if at.hour >= 12 else 'AM'}"


def sensor_series(rng: random.Random, days: int) -> list[tuple[datetime, int]]:
    out, at, phase = [], START - WINDOW - timedelta(hours=1), rng.uniform(0, 6)
    end = START + timedelta(days=days)
    while at <= end:
        if rng.random() > 0.02:  # le capteur perd parfois un point
            out.append((at, max(40, round(130 + 60 * math.sin(at.timestamp() / 5400 + phase) + rng.uniform(-8, 8)))))
        at += timedelta(minutes=5, seconds=rng.choice((-3, 0, 2)))
    return out


def read(state: AppState, series, now: datetime) -> None:
    window = [(at, mg) for at, mg in series if now - WINDOW <= at <= now]
    if not window:
        return
    points = [item(us(at), mg) for at, mg in window[:-1]]
    fake = FakeLlu(graph_data(points, item(us(window[-1][0]), window[-1][1])))
    state.import_cgm(fetch_cgm(ACCOUNT, transport=fake))


def sensor_keys(store: Store) -> set[tuple[datetime, int]]:
    return {(r.device_time, r.mg_dl) for r in store.readings() if r.cgm}


def meter_keys(store: Store) -> set[tuple[datetime, int, str | None]]:
    return {(r.device_time, r.mg_dl, r.meal.value if r.meal else None) for r in store.readings() if not r.cgm}


def run_scenario(seed: int, days: int = 3) -> Scenario:
    rng = random.Random(seed)
    scenario = Scenario(seed)
    series = sensor_series(rng, days)
    end = START + timedelta(days=days)
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        devices = {}
        for name in ("PC", "tablette"):
            (root / name).mkdir()
            store = Store(root / name / "glucofi.db")
            devices[name] = (store, AppState(store, root / name))
        pc, tablet = devices["PC"][1], devices["tablette"][1]

        reads: list[tuple[datetime, AppState]] = []
        at = START + timedelta(hours=rng.randint(6, 10))
        while at <= end:  # le PC : plages d'allumage, toutes les 15 min
            if rng.random() < 0.85:
                reads.append((at, pc))
            at += timedelta(minutes=15)
        at = START + timedelta(hours=rng.randint(5, 9))
        while at <= end:  # la tablette : à l'ouverture, parfois après plus de 12 h sans l'ouvrir
            reads.append((at, tablet))
            if rng.random() < 0.6:
                reads.append((at + timedelta(minutes=rng.randint(10, 20)), tablet))
            at += timedelta(hours=rng.choice((2, 5, 9, 13, 16, 20)))
        # des mesures du lecteur Accu-Chek, sur l'un ou l'autre
        for _ in range(rng.randint(2, 8)):
            when = (START + timedelta(minutes=rng.randint(0, days * 1440))).replace(second=0, microsecond=0)
            owner = rng.choice((pc, tablet))
            meal = rng.choice((Meal.FASTING, Meal.BEFORE_MEAL, None))
            owner.store.import_readings([Reading(when, rng.choice((70, 95, 130, 190)), int(when.timestamp()), meal=meal)], "lecteur")
        for when, state in sorted(reads, key=lambda r: r[0]):
            read(state, series, when)

        stored = {name: sensor_keys(devices[name][0]) for name in devices}
        meter = {name: meter_keys(devices[name][0]) for name in devices}
        before = {name: len(devices[name][0].db.execute("SELECT 1 FROM imports").fetchall()) for name in devices}
        scenario.facts["lectures du capteur"] = len(reads)
        scenario.facts["points du PC seul"] = len(stored["PC"] - stored["tablette"])
        scenario.facts["points de la tablette seule"] = len(stored["tablette"] - stored["PC"])

        for round_ in range(2):  # aller-retour, deux fois : la seconde ne doit rien changer
            summaries = []
            for src, dst in (("tablette", "PC"), ("PC", "tablette")):
                path = devices[src][0].export_to(root / f"{src}-{round_}.db")
                summaries.append(devices[dst][0].merge_from(path))
            if round_ == 1 and any(s.changed for s in summaries):
                scenario.problems.append("une fusion de plus a encore changé quelque chose")

        pc_store, tab_store = devices["PC"][0], devices["tablette"][0]
        if sensor_keys(pc_store) != sensor_keys(tab_store):
            scenario.problems.append("les mesures du capteur n'ont pas convergé")
        if meter_keys(pc_store) != meter_keys(tab_store):
            scenario.problems.append("les mesures du lecteur n'ont pas convergé")
        if sensor_keys(pc_store) != stored["PC"] | stored["tablette"]:
            scenario.problems.append("une valeur lue par un appareil a été perdue ou inventée")
        if meter_keys(pc_store) != meter["PC"] | meter["tablette"]:
            scenario.problems.append("une mesure du lecteur a été perdue, dupliquée ou changée de marqueur")
        buckets: dict[tuple, int] = {}
        for at_, _mg in sensor_keys(pc_store):
            key = (at_.date(), (at_.hour * 60 + at_.minute) // 15)
            buckets[key] = buckets.get(key, 0) + 1
        extra = sum(n - 1 for n in buckets.values())
        scenario.facts["points en trop dans une tranche"] = extra
        if extra:
            scenario.problems.append(f"{extra} point(s) en trop dans une même tranche de 15 min")
        for name, (store, _state) in devices.items():
            rows = store.db.execute("SELECT COUNT(*) FROM imports WHERE source = ?", (CGM_SOURCE,)).fetchone()[0]
            if rows:
                scenario.problems.append(f"{name} : {rows} ligne(s) du capteur dans le journal des imports")
            last = store.last_import()
            if last is not None and last.source == CGM_SOURCE:
                scenario.problems.append(f"{name} : « dernière récupération » montre le capteur")
            if len(store.db.execute("SELECT 1 FROM imports").fetchall()) > before[name] + before["PC"] + before["tablette"]:
                scenario.problems.append(f"{name} : le journal des imports a grossi")
        for store, _state in devices.values():
            store.close()
    return scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenarios", type=int, default=30)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    out = args.out / datetime.now().strftime("%Y%m%d-%H%M%S")
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
    lines.append(f"Verdict : {'OK' if not failed else 'ÉCHEC'} (seuil : 100 %) ; détail dans {out / 'scenarios.csv'}")
    (out / "rapport.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
