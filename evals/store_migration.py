"""Rapport de migration de la base Glucofi, exécuté sur une COPIE (la base d'origine n'est jamais ouverte en écriture).

Copie la base dans /tmp/glucofi-migration/<horodatage>/, l'ouvre avec Store (qui migre),
puis compare chaque mesure avant/après. Réussite si : aucune mesure perdue, chaque epoch
égal à local_epoch(device_time), et chaque correction vaut 0 s (hiver) ou -3600 s (été).

Avec --reimport SORTIE_ACCUCHEK.json, réimporte ensuite cette lecture du lecteur dans la
copie, comme « Récupérer » : c'est le remplissage des marqueurs repas des mesures déjà en
base. Réussite en plus si : aucune mesure en double, chaque mesure de la lecture porte en
base le marqueur de la lecture, aucun marqueur effacé.

Usage : python3 -m evals.store_migration [--db ~/.local/share/glucofi/glucofi.db] [--reimport FICHIER]
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from contracts import local_epoch
from services.device import parse_file
from services.store import Store, default_data_dir

OUT_DIR = Path("/tmp/glucofi-migration")


def rows(path: Path) -> dict[int, tuple[str, int, int]]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {i: (t, mg, epoch) for i, t, mg, epoch in db.execute("SELECT id, device_time, mg_dl, epoch FROM readings")}
    finally:
        db.close()


def meals(path: Path) -> dict[tuple[str, int], str | None]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {(t, mg): meal for t, mg, meal in db.execute("SELECT device_time, mg_dl, meal FROM readings")}
    finally:
        db.close()


def reimport(copy: Path, source: Path, work: Path) -> list[str]:
    """Réimporte une sortie accuchek dans la copie et rapporte les marqueurs avant/après."""
    parsed = parse_file(source)
    before = meals(copy)
    store = Store(copy)
    summary = store.import_readings(
        parsed.readings, f"fichier:{source.name}", len(parsed.rejected), parsed.meter, parsed.clock, parsed.glucose
    )
    store.close()
    after = meals(copy)

    problems = []
    categories: Counter[str] = Counter()
    examples: dict[str, tuple] = {}
    csv_path = work / "marqueurs-avant-apres.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["device_time", "mg_dl", "marqueur_avant", "marqueur_apres", "marqueur_lecteur", "categorie"])
        for r in parsed.readings:
            key = (r.device_time.isoformat(), r.mg_dl)
            source_meal = r.meal.value if r.meal is not None else None
            old, new = before.get(key), after.get(key)
            if key not in before:
                category = "mesure nouvelle"
            elif old == new:
                category = "marqueur inchangé" if old else "sans marqueur"
            elif old is None:
                category = f"marqueur ajouté ({new})"
            else:
                category = f"marqueur modifié ({old} -> {new})"
            if source_meal is not None and new != source_meal:
                problems.append(f"{key} : marqueur {new!r} en base, {source_meal!r} sur le lecteur")
            if old is not None and new is None:
                problems.append(f"{key} : marqueur {old!r} effacé")
            categories[category] += 1
            examples.setdefault(category, (key[0], key[1], old, new))
            writer.writerow([key[0], key[1], old, new, source_meal, category])
    lost = [k for k in before if k not in after]
    if lost:
        problems.append(f"{len(lost)} mesure(s) perdue(s), ex. {lost[:3]}")
    new_rows = sum(1 for r in parsed.readings if (r.device_time.isoformat(), r.mg_dl) not in before)
    if summary.added != new_rows or len(after) != len(before) + new_rows:
        problems.append(f"{summary.added} mesure(s) ajoutée(s) pour {new_rows} nouvelle(s) : doublons")

    print()
    print(f"Réimport de {source} : {summary.received} mesures, {summary.added} nouvelles, "
          f"{summary.markers_added} marqueurs ajoutés")
    print()
    print("| Catégorie | Mesures | Exemple : heure du lecteur, mg/dL, marqueur avant -> après |")
    print("| --- | --- | --- |")
    for category, count in categories.most_common():
        t, mg, old, new = examples[category]
        print(f"| {category} | {count} | {t}, {mg}, {old} -> {new} |")
    print(f"CSV marqueurs : {csv_path}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=default_data_dir() / "glucofi.db")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--reimport", type=Path, help="sortie accuchek (format 2) à réimporter dans la copie")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"base absente : {args.db}")
        return 2
    work = args.out / f"{datetime.now():%Y%m%d-%H%M%S}"
    work.mkdir(parents=True, exist_ok=True)
    copy = work / "glucofi.db"
    shutil.copy2(args.db, copy)
    before = rows(copy)

    store = Store(copy)
    migration = store.last_migration
    store.close()
    after = rows(copy)

    problems = []
    categories: Counter[str] = Counter()
    examples: dict[str, tuple] = {}
    csv_path = work / "avant-apres.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "device_time", "mg_dl", "epoch_avant", "epoch_apres", "correction_s", "categorie"])
        for row_id, (t, mg, old) in sorted(before.items()):
            new = after.get(row_id, (None, None, None))[2]
            if new is None:
                category = "perdue (doublon)"
                problems.append(f"mesure {row_id} ({t}, {mg}) absente après migration")
            else:
                delta = new - old
                category = {0: "inchangée (hiver)", -3600: "corrigée -1 h (été)"}.get(delta, f"autre ({delta:+d} s)")
                if delta not in (0, -3600):
                    problems.append(f"mesure {row_id} ({t}) : correction inattendue {delta:+d} s")
                if new != local_epoch(datetime.fromisoformat(t)):
                    problems.append(f"mesure {row_id} ({t}) : epoch {new} != local_epoch")
            categories[category] += 1
            examples.setdefault(category, (t, mg, old, new))
            writer.writerow([row_id, t, mg, old, new, "" if new is None else new - old, category])

    print(f"Base : {args.db} (copie migrée : {copy})")
    if migration is None:
        print("Aucune migration : la base est déjà à jour.")
    for m in store.migrations:
        print(f"Migration v{m.from_version} -> v{m.to_version} : "
              f"{m.readings_before} mesures avant, {m.readings_after} après, {m.epochs_fixed} epochs corrigés")
    print()
    print("| Catégorie | Mesures | Exemple : heure du lecteur, mg/dL, epoch avant -> après |")
    print("| --- | --- | --- |")
    for category, count in categories.most_common():
        t, mg, old, new = examples[category]
        print(f"| {category} | {count} | {t}, {mg}, {old} -> {new} |")
    print()
    if args.reimport is not None:
        problems += reimport(copy, args.reimport, work)
    for problem in problems[:20]:
        print("ÉCHEC", problem)
    print(f"Verdict : {'OK' if not problems else 'ÉCHEC'} ({len(problems)} problème(s))")
    print(f"CSV : {csv_path}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
