"""Eval matériel : lecture réelle du lecteur branché, comme le bouton « Récupérer ».

Réussite si : lecture sans erreur, au moins --min mesures, aucune ligne rejetée,
dernière mesure pas dans le futur, autant de mesures et de marqueurs reçus
qu'annoncés, mise à l'heure non refusée. Affiche l'identité du lecteur et l'écart
de son horloge (remise à l'heure du PC si l'écart dépasse 60 s, comme « Récupérer »).
N'écrit pas dans la base de Glucofi (copie brute dans /tmp/glucofi-eval/raw).

Usage : python3 -m evals.device_smoke [--min 1]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from services.device import DeviceError, fetch

OUT = Path("/tmp/glucofi-eval/raw")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--min", type=int, default=1, help="nombre minimal de mesures attendu")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        result = fetch(raw_dir=OUT)
    except DeviceError as exc:
        print(f"ÉCHEC lecture : {type(exc).__name__} : {exc}")
        return 1

    readings = result.readings
    problems = []
    if len(readings) < args.min:
        problems.append(f"{len(readings)} mesure(s), minimum {args.min}")
    if result.rejected:
        problems.append(f"{len(result.rejected)} ligne(s) rejetée(s) : {result.rejected[:3]}")
    if readings and readings[-1].device_time > datetime.now() + timedelta(hours=2):
        problems.append(f"dernière mesure dans le futur : {readings[-1].device_time}")

    for count, what in ((result.glucose, "mesures"), (result.meal, "marqueurs")):
        if count is not None and not count.complete:
            problems.append(f"{what} : {count.received} reçus pour {count.announced} annoncés")
    if result.clock is not None and result.clock.action.value == "rejected":
        problems.append("le lecteur a refusé la mise à l'heure")

    span = f"du {readings[0].device_time:%d/%m/%Y} au {readings[-1].device_time:%d/%m/%Y %H:%M}" if readings else "-"
    print(f"mesures lues : {len(readings)} ({span}), copie brute : {result.raw_path}")
    if result.meter is not None:
        m = result.meter
        print(f"lecteur : {m.model_name}, n° {m.serial}, logiciel {m.firmware}, matériel {m.hardware}")
    if result.glucose is not None:
        print(f"mesures annoncées / reçues : {result.glucose.announced} / {result.glucose.received}")
    if result.meal is not None:
        print(
            f"marqueurs annoncés / reçus : {result.meal.announced} / {result.meal.received}, "
            f"{result.markers} rattachés, {result.meals_unmatched} orphelins"
        )
    if result.clock is not None:
        c = result.clock
        print(f"horloge : lecteur {c.meter}, PC {c.pc}, écart {c.offset_s} s, action {c.action.value}")
    for warning in result.warnings:
        print("AVERTISSEMENT", warning)
    for problem in problems:
        print("ÉCHEC", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
