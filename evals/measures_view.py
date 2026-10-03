"""Eval de l'onglet Mesures : la vue montre exactement les mesures, bien classées et bien qualifiées.

Pour chaque historique et chaque combinaison de filtres (période x moment x
marqueur), vérifie contre un oracle réécrit depuis le README, sans le code de
app/measures.py :
- chaque mesure filtrée apparaît une fois et une seule, jours puis heures du
  plus récent au plus ancien ; les filtres de marqueur forment une partition ;
- niveau : sous le seuil bas, dans l'objectif bornes incluses, au-dessus du
  seuil haut (mg/dL entiers), avec la flèche qui va avec ;
- glycémie du matin retenue : au plus une par jour, égale à celle du moteur de
  dose, jamais pour un filtre de moment autre que « Matin » ;
- barre de répartition : 100 parts, une part au moins par niveau présent, à
  1 part près du pourcentage exact, plus 1 par niveau relevé (moins de 1 % :
  exactement 1 part) ;
- chaque icône de marqueur existe (fichier de app/icons ou icône Adwaita).

Sources : un export accuchek réel (--json) et/ou N patients synthétiques
(--synthetic) dont les marqueurs sont tirés parmi tous ceux du lecteur.
Seuil de réussite : 100 %. Échecs détaillés dans /tmp/glucofi-eval/measures_view.csv.

Usage :
    python3 -m evals.measures_view --json test.json --synthetic 100
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from app.measures import LEVEL_ICONS, MARKER_ICONS, MEAL_FILTERS, measure_view
from contracts import DosingSettings, Meal, Reading
from evals.replay_history import synthetic_patient
from services.charts import PERIODS
from services.device import parse_file
from services.dosing import morning_readings

OUT_DIR = Path("/tmp/glucofi-eval")
SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
APP_ICONS = Path(__file__).resolve().parents[1] / "app" / "icons" / "hicolor" / "scalable" / "actions"
ADWAITA_ICONS = Path("/usr/share/icons/Adwaita/symbolic")
DAY_WINDOWS = (30, 90, None)


def oracle_level(mg: int, s: DosingSettings) -> str:
    low, high = round(s.low_g_l * 100), round(s.high_g_l * 100)
    return "low" if mg < low else "high" if mg > high else "in"


def oracle_meal(reading: Reading, meal: str) -> bool:
    if meal == "all":
        return True
    actual = reading.meal.value if reading.meal is not None else "none"
    return actual == meal


def all_markers_patient(seed: int, days: int = 60) -> list[Reading]:
    """Patient synthétique de replay_history, marqueurs retirés au hasard parmi tous ceux du lecteur."""
    rng = random.Random(seed * 7919)
    choices = list(Meal) + [None]
    return [
        Reading(r.device_time, r.mg_dl, r.epoch, meal=rng.choice(choices) if rng.random() < 0.6 else r.meal)
        for r in synthetic_patient(seed, days=days)
    ]


def window(readings: list[Reading], days: int | None, today: date) -> list[Reading]:
    if days is None:
        return readings
    first = today - timedelta(days=days - 1)
    return [r for r in readings if r.day >= first]


def check(source: str, readings: list[Reading], s: DosingSettings = SETTINGS) -> tuple[int, list[dict]]:
    """Nombre de vues vérifiées et liste des échecs."""
    failures: list[dict] = []
    today = max((r.day for r in readings), default=date(2026, 1, 1))
    views = 0

    def fail(filters: str, message: str) -> None:
        failures.append({"source": source, "filtres": filters, "echec": message})

    for days in DAY_WINDOWS:
        visible = window(readings, days, today)
        for period in ("all",) + PERIODS:
            by_period = [r for r in visible if period == "all" or _period(r, s) == period]
            engine = {m.day: m.reading for m in morning_readings(by_period, s)}
            partition = 0
            for meal, _label in MEAL_FILTERS:
                filters = f"{days or 'tout'}/{period}/{meal}"
                view = measure_view(visible, s, period, meal, [], today=today)
                views += 1
                expected = sorted((r for r in by_period if oracle_meal(r, meal)), key=lambda r: (r.day, r.device_time, r.epoch), reverse=True)
                if meal != "all":
                    partition += len(expected)
                lines = view.lines
                if len(lines) != len(expected):
                    fail(filters, f"{len(lines)} lignes pour {len(expected)} mesures")
                    continue
                for line, r in zip(lines, expected):
                    if line.time != f"{r.device_time:%H:%M}":
                        fail(filters, f"ordre : {line.time} au lieu de {r.device_time:%H:%M} le {r.day}")
                        break
                    level = oracle_level(r.mg_dl, s)
                    if line.level != level or line.level_icon != LEVEL_ICONS[level] or (level != "in") != bool(line.level_icon):
                        fail(filters, f"niveau {line.level} pour {r.mg_dl} mg/dL (attendu {level})")
                    if line.retained != (engine.get(r.day) == r and engine[r.day].meal == r.meal):
                        fail(filters, f"retenue incorrecte : {r.device_time} {r.mg_dl}")
                days_seen = [d.day for d in view.days]
                if days_seen != sorted(set(days_seen), reverse=True):
                    fail(filters, "jours en double ou mal classés")
                for d in view.days:
                    if sum(line.retained for line in d.lines) > 1:
                        fail(filters, f"plusieurs glycémies retenues le {d.day}")
                    if (d.morning is None) != (d.day not in engine):
                        fail(filters, f"en-tête du matin incohérent le {d.day}")
                    if period not in ("all", PERIODS[0]) and d.morning is not None:
                        fail(filters, f"glycémie du matin affichée pour le moment {period}")
                spans = [seg.span for seg in view.summary.segments]
                n = len(expected)
                if n == 0:
                    if any(spans):
                        fail(filters, "barre non vide sans mesure")
                    continue
                if sum(spans) != 100:
                    fail(filters, f"barre de {sum(spans)} parts")
                exact = {lv: 100 * sum(oracle_level(r.mg_dl, s) == lv for r in expected) / n for lv in LEVEL_ICONS}
                tolerance = 1 + sum(0 < e < 1 for e in exact.values())
                for seg in view.summary.segments:
                    e = exact[seg.level]
                    if (e > 0) != (seg.span > 0) or abs(seg.span - e) > tolerance or (0 < e < 1 and seg.span != 1):
                        fail(filters, f"part {seg.level} = {seg.span} pour {e:.1f} %")
            if partition != len(by_period):
                fail(f"{days or 'tout'}/{period}", f"filtres de marqueur : {partition} mesures pour {len(by_period)}")
    for name in MARKER_ICONS.values():
        if not (APP_ICONS / f"{name}.svg").is_file() and not any(ADWAITA_ICONS.glob(f"*/{name}.svg")):
            fail("icônes", f"icône introuvable : {name}")
    return views, failures


def _period(reading: Reading, s: DosingSettings) -> str:
    t = reading.device_time.time()
    if s.morning_start <= t <= s.morning_end:
        return PERIODS[0]
    if 12 <= t.hour < 18:
        return PERIODS[1]
    return PERIODS[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path, action="append", default=[], help="export accuchek à vérifier")
    parser.add_argument("--synthetic", type=int, default=100, help="nombre de patients synthétiques")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)

    sources = [(f"synthetique-{seed}", all_markers_patient(seed)) for seed in range(args.synthetic)]
    for path in args.json:
        sources.append((path.name, list(parse_file(path).readings)))
    total_views, failures = 0, []
    for name, readings in sources:
        views, failed = check(name, readings)
        total_views += views
        failures += failed
    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / "measures_view.csv"
    with report.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["source", "filtres", "echec"])
        writer.writeheader()
        writer.writerows(failures)
    failed_sources = len({f["source"] for f in failures})
    score = 1 - failed_sources / len(sources) if sources else 0.0
    print(f"{len(sources)} historiques, {total_views} vues vérifiées, {len(failures)} échec(s), score {score:.0%} (seuil 100 %)")
    print(f"détail : {report}")
    for item in failures[:10]:
        print(f"  {item['source']} [{item['filtres']}] {item['echec']}", file=sys.stderr)
    return 0 if not failures and sources else 1


if __name__ == "__main__":
    raise SystemExit(main())
