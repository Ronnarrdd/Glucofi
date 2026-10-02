"""Export accuchek fictif (format 2) pour les captures d'écran et les démonstrations.

Mesures inventées, graine fixe : même fichier à chaque lancement pour une même date de fin.

Usage : python3 -m scripts.demo_export SORTIE.json [--days 60] [--end AAAA-MM-JJ]
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, time, timedelta

# (heure, marqueur, glycémie moyenne en mg/dL, écart type)
SLOTS = (
    (time(7, 20), "fasting", 125, 28),
    (time(12, 0), "before_meal", 115, 25),
    (time(14, 10), "after_meal", 165, 35),
    (time(19, 30), "before_meal", 130, 30),
    (time(22, 40), "bedtime", 145, 30),
)


def build(days: int, end: date, seed: int = 20261002) -> dict:
    rng = random.Random(seed)
    readings = []
    for d in range(days):
        day = end - timedelta(days=days - 1 - d)
        for slot, meal, mean, sd in SLOTS:
            if meal in ("after_meal", "bedtime") and rng.random() < 0.5:
                continue
            at = datetime.combine(day, slot) + timedelta(minutes=rng.randint(-25, 25))
            mg = max(45, min(420, round(rng.gauss(mean, sd))))
            reading = {
                "id": len(readings),
                "epoch": int(at.timestamp()),
                "timestamp": at.strftime("%Y/%m/%d %H:%M"),
                "mg/dL": mg,
                "mmol/L": round(mg / 18, 6),
                "status": 0,
            }
            if rng.random() < 0.9:
                reading["meal"] = meal
            readings.append(reading)
    markers = sum("meal" in r for r in readings)
    return {
        "format": 2,
        "meter": None,
        "clock": None,
        "glucose": {"announced": len(readings), "received": len(readings)},
        "meal": {"announced": markers, "received": markers, "unmatched": 0},
        "readings": readings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out")
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    data = build(args.days, args.end)
    with open(args.out, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"{len(data['readings'])} mesures fictives du {data['readings'][0]['timestamp']} au {data['readings'][-1]['timestamp']} : {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
