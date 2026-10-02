"""Eval des erreurs d'accuchek : pannes injectées dans des traces USB rejouées.

Pour chaque trace et chaque message reçu du lecteur (association, identité, horloge,
mise à l'heure, segments de mesures et de marqueurs repas), la trace est cassée de quatre
façons (timeout, lecteur débranché, abandon de l'association, trace coupée) puis
rejouée avec le vrai binaire, via services.device.fetch comme le bouton « Récupérer ».
Réussite d'un cas : code de sortie attendu (contracts.AccuchekExit), stdout vide,
une ligne "accuchek: <raison>" sur stderr, erreur Glucofi du bon type dont le
message contient cette raison. Exception : une panne sur la confirmation de
libération (dernier message) arrive quand tout est reçu, les mesures sont gardées
(code 0, autant de mesures que sans panne). Les traces rejouées avec des arguments
(ligne "# args:", --set-time --now) les gardent pour chaque cas. Seuil : 100 % des cas. Rapport CSV dans /tmp/glucofi-eval/.

Usage : python3 -m evals.accuchek_errors [--bin BINAIRE] [TRACE ...]
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from contracts import AccuchekExit
from evals.accuchek_replay import DEFAULT_BIN, OUT_DIR, default_traces, read_trace
from services.device import DeviceError, DeviceProtocolError, DeviceReadFailed, accuchek

ABORT = "< E60000020000"

# panne -> (remplacement de la ligne "<", code attendu, erreur Glucofi attendue)
FAULTS: dict[str, tuple[str | None, AccuchekExit, type[DeviceError]]] = {
    "timeout": ("< !-7", AccuchekExit.TRANSFER, DeviceReadFailed),
    "debranche": ("< !-4", AccuchekExit.TRANSFER, DeviceReadFailed),
    "abandon": (ABORT, AccuchekExit.PROTOCOL, DeviceProtocolError),
    "coupee": (None, AccuchekExit.TRANSFER, DeviceReadFailed),
}


@dataclass
class Case:
    trace: Path
    line: int
    fault: str
    exit_code: int = -1
    error: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def received_lines(lines: list[str]) -> list[int]:
    return [i for i, line in enumerate(lines) if line.startswith("< ") and not line.startswith("< !")]


def broken_trace(lines: list[str], index: int, fault: str) -> str:
    replacement = FAULTS[fault][0]
    if replacement is None:
        return "".join(lines[:index])
    return "".join(lines[:index] + [replacement + "\n"] + lines[index + 1 :])


def check_release_case(binary: Path, path: Path, case: Case, expected_readings: int, args: list[str]) -> Case:
    try:
        result = accuchek.fetch(command=[str(binary), "--replay", str(path), *args], sysfs_root=path.parent / "nosysfs")
    except DeviceError as exc:
        case.error = f"{type(exc).__name__}: {exc}"
        case.problems.append("mesures perdues sur une panne de libération")
        return case
    case.exit_code = 0
    got = len(result.readings) + len(result.rejected)
    if got != expected_readings:
        case.problems.append(f"{got} mesure(s) au lieu de {expected_readings}")
    return case


def check_case(binary: Path, trace: Path, lines: list[str], index: int, fault: str, workdir: Path) -> Case:
    case = Case(trace, index + 1, fault)
    _, expected_code, expected_error = FAULTS[fault]
    path = workdir / f"{trace.stem}-{index}-{fault}.trace"
    path.write_text(broken_trace(lines, index, fault))
    content = read_trace(trace)
    command = [str(binary), "--replay", str(path), *content.args]
    if index == received_lines(lines)[-1]:
        return check_release_case(binary, path, case, len(content.glucose_keys), content.args)

    proc = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    case.exit_code = proc.returncode
    if proc.returncode != expected_code:
        case.problems.append(f"code {proc.returncode} au lieu de {int(expected_code)}")
    if proc.stdout:
        case.problems.append(f"stdout non vide : {proc.stdout[:80]!r}")
    reason = accuchek.accuchek_reason(proc.stderr)
    if not proc.stderr.startswith("accuchek: ") or not reason:
        case.problems.append(f"stderr sans raison : {proc.stderr[:120]!r}")

    try:
        accuchek.fetch(command=command, sysfs_root=workdir / "nosysfs", timeout_s=30)
        case.problems.append("fetch n'a levé aucune erreur")
    except DeviceError as exc:
        case.error = f"{type(exc).__name__}: {exc}"
        if type(exc) is not expected_error:
            case.problems.append(f"{type(exc).__name__} au lieu de {expected_error.__name__}")
        if reason and reason not in str(exc):
            case.problems.append("raison d'accuchek absente du message affiché")
    return case


def check_trace(binary: Path, trace: Path, faults: list[str] | None = None) -> list[Case]:
    lines = trace.read_text().splitlines(keepends=True)
    cases = []
    with tempfile.TemporaryDirectory(prefix="glucofi-eval-") as tmp:
        for index in received_lines(lines):
            for fault in faults or list(FAULTS):
                cases.append(check_case(binary, trace, lines, index, fault, Path(tmp)))
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bin", type=Path, default=DEFAULT_BIN, help="binaire accuchek à évaluer")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("traces", nargs="*", type=Path)
    args = parser.parse_args(argv)

    if not args.bin.exists():
        print(f"binaire absent : {args.bin} (make -C services/device/accuchek-src)")
        return 2
    traces = args.traces or default_traces()
    cases = [case for trace in traces for case in check_trace(args.bin, trace)]
    if not cases:
        print("aucune trace à casser")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    csv_path = args.out / f"accuchek-errors-{datetime.now():%Y%m%d-%H%M%S}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["trace", "ligne", "panne", "code", "erreur_glucofi", "ok", "problemes"])
        for c in cases:
            writer.writerow([c.trace.name, c.line, c.fault, c.exit_code, c.error, c.ok, " | ".join(c.problems)])

    failed = [c for c in cases if not c.ok]
    for c in failed:
        print(f"ÉCHEC {c.trace.name} ligne {c.line} ({c.fault}) : {' | '.join(c.problems)}")
    passed = len(cases) - len(failed)
    print(f"{passed}/{len(cases)} pannes correctement signalées ({100 * passed / len(cases):.0f} %, seuil 100 %)")
    print(f"CSV : {csv_path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
