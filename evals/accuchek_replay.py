"""Eval d'accuchek : rejoue des échanges USB enregistrés (traces) avec le vrai binaire.

Pour chaque trace, lance `accuchek --replay TRACE [args de la ligne "# args:"]` et
vérifie le contrat de sortie (format 2) contre ce que les octets de la trace disent,
décodés ici sans le code C++ :
- code 0, stderr vide, stdout identique avec ACCUCHEK_DBG=1 (logs sur stderr) ;
- autant de mesures que d'entrées de 12 octets dans les segments de données, autant de
  marqueurs que d'entrées de 10 octets, compteurs "received" égaux ;
- chaque mesure porte le marqueur dont l'heure (à la seconde) est la sienne, les
  autres marqueurs sont comptés dans "unmatched" ;
- aucune ligne rejetée sauf pour statut du lecteur, identifiants 0..n-1, epoch égal
  à l'heure du lecteur dans le fuseau local (heure d'été comprise) ;
- numéro de série présent en ASCII dans la trace ; avec --set-time --now, écart
  lecteur - PC recalculé ici, et message de mise à l'heure (0C17) envoyé avec l'heure
  --now en BCD si et seulement si l'écart dépasse 60 s.
Seuil de réussite : 100 % des traces. Rapport CSV dans /tmp/glucofi-eval/.

Traces :
- services/device/accuchek-src/tests/fixtures/*.trace (synthétiques, versionnées) ;
- ~/.local/share/glucofi/traces/*.trace (lecteur réel, jamais versionnées), créées avec :
  /usr/local/bin/accuchek --capture ~/.local/share/glucofi/traces/$(date +%F).trace > /dev/null

Usage : python3 -m evals.accuchek_replay [--bin BINAIRE] [TRACE ...]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from contracts import local_epoch
from services.device import InvalidOutput, parse_output

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "services/device/accuchek-src"
DEFAULT_BIN = SRC / "accuchek"
FIXTURES = SRC / "tests/fixtures"
REAL_TRACES = Path.home() / ".local/share/glucofi/traces"
OUT_DIR = Path("/tmp/glucofi-eval")


@dataclass
class TraceResult:
    trace: Path
    exit_code: int
    readings: int = 0
    flagged: int = 0
    markers: int = 0
    clock_action: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def default_traces() -> list[Path]:
    return sorted(FIXTURES.glob("*.trace")) + sorted(REAL_TRACES.glob("*.trace"))


SEGMENT_DATA_EVENT = 0x0D21
SET_TIME_ACTION = 0x0C17
GLUCOSE_ENTRY, MEAL_ENTRY = 12, 10
# codes MDC des marqueurs repas (IEEE 11073-10417)
MEAL_CODES = {29260: "before_meal", 29264: "after_meal", 29268: "fasting", 29272: "casual", 29300: "bedtime"}
CLOCK_TOLERANCE_S = 60
TIME_FORMAT = "%Y/%m/%d %H:%M:%S"


@dataclass
class TraceContent:
    """Ce que les octets de la trace disent, décodé sans le code C++."""

    args: list[str]
    glucose_keys: list[bytes]  # heure BCD (8 octets) de chaque mesure, dans l'ordre de la trace
    meals: list[tuple[bytes, int]]  # (heure BCD, code) de chaque marqueur
    sent: list[bytes]
    text: str

    def meal_for(self, index: int) -> str | None:
        key = self.glucose_keys[index]
        code = next((c for k, c in self.meals if k == key), None)
        return None if code is None else MEAL_CODES.get(code, "other")

    @property
    def unmatched(self) -> int:
        keys = set(self.glucose_keys)
        return sum(1 for k, _ in self.meals if k not in keys)


def read_trace(trace: Path) -> TraceContent:
    text = trace.read_text()
    args: list[str] = []
    glucose: list[bytes] = []
    meals: list[tuple[bytes, int]] = []
    sent: list[bytes] = []
    for line in text.splitlines():
        if line.startswith("# args: "):
            args = shlex.split(line.removeprefix("# args: "))
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or parts[0] not in "<>" or parts[1].startswith("!"):
            continue
        data = bytes.fromhex(parts[1].replace(" ", ""))
        if parts[0] == ">":
            sent.append(data)
            continue
        if len(data) < 36 or data[0:2] != b"\xe7\x00" or int.from_bytes(data[18:20], "big") != SEGMENT_DATA_EVENT:
            continue
        count = int.from_bytes(data[30:32], "big")
        if count == 0:
            continue
        size = int.from_bytes(data[34:36], "big") // count
        entries = [data[36 + i * size : 36 + (i + 1) * size] for i in range(count)]
        if size == GLUCOSE_ENTRY:
            glucose += [e[0:8] for e in entries]
        elif size == MEAL_ENTRY:
            meals += [(e[0:8], int.from_bytes(e[8:10], "big")) for e in entries]
    return TraceContent(args, glucose, meals, sent, text.upper())


def bcd_time(value: datetime) -> bytes:
    digits = f"{value:%Y%m%d%H%M%S}00"
    return bytes.fromhex(digits)


def replay(binary: Path, trace: Path, timeout_s: float, debug: bool, args: list[str] = ()) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ACCUCHEK_DBG"}
    if debug:
        env["ACCUCHEK_DBG"] = "1"
    return subprocess.run(
        [str(binary), "--replay", str(trace), *args],
        capture_output=True, text=True, timeout=timeout_s, check=False, env=env,
    )


def check_trace(binary: Path, trace: Path, timeout_s: float = 30) -> TraceResult:
    content = read_trace(trace)
    proc = replay(binary, trace, timeout_s, debug=False, args=content.args)
    result = TraceResult(trace, proc.returncode)
    if proc.returncode != 0:
        result.problems.append(f"code de sortie {proc.returncode} : {proc.stderr.strip()[-300:]}")
        return result
    if proc.stderr:
        result.problems.append(f"stderr non vide sans ACCUCHEK_DBG : {proc.stderr[:120]!r}")
    if replay(binary, trace, timeout_s, debug=True, args=content.args).stdout != proc.stdout:
        result.problems.append("stdout différent avec ACCUCHEK_DBG=1 (logs mêlés au JSON)")
    try:
        parsed = parse_output(proc.stdout)
    except InvalidOutput as exc:
        result.problems.append(f"sortie illisible : {exc}")
        return result
    result.readings = len(parsed.readings)
    result.flagged = sum("statut" in reason for reason in parsed.rejected)
    other = [reason for reason in parsed.rejected if "statut" not in reason]
    if other:
        result.problems.append(f"{len(other)} ligne(s) rejetée(s) : {other[:2]}")
    output = json.loads(proc.stdout)
    items = output["readings"]
    expected = len(content.glucose_keys)
    if len(items) != expected or output["glucose"]["received"] != expected:
        result.problems.append(
            f"{len(items)} mesure(s) en sortie (received {output['glucose']['received']}) pour {expected} dans la trace"
        )
    ids = [item["id"] for item in items]
    if sorted(ids) != list(range(len(ids))):
        result.problems.append(f"identifiants non contigus : {ids[:10]}")
    check_meals(content, output, result)
    check_meter(content, output, result)
    check_clock(content, output, result)
    wrong_epochs = [r for r in parsed.readings if r.epoch != local_epoch(r.device_time)]
    if wrong_epochs:
        r = wrong_epochs[0]
        result.problems.append(
            f"{len(wrong_epochs)} epoch(s) incohérent(s) avec l'heure du lecteur, ex. {r.device_time} : "
            f"{r.epoch} au lieu de {local_epoch(r.device_time)} ({r.epoch - local_epoch(r.device_time):+d} s)"
        )
    return result


def check_meals(content: TraceContent, output: dict, result: TraceResult) -> None:
    items = output["readings"]
    if len(items) != len(content.glucose_keys):
        return
    wrong = [
        (item["id"], item.get("meal"), content.meal_for(item["id"]))
        for item in items
        if item.get("meal") != content.meal_for(item["id"])
    ]
    if wrong:
        result.problems.append(f"{len(wrong)} marqueur(s) mal rattaché(s), ex. (id, sortie, trace) {wrong[:3]}")
    result.markers = sum(1 for item in items if "meal" in item)
    meal = output["meal"]
    if content.meals and meal is None:
        result.problems.append(f"{len(content.meals)} marqueur(s) dans la trace mais \"meal\": null")
    if meal is not None and (meal["received"], meal["unmatched"]) != (len(content.meals), content.unmatched):
        result.problems.append(
            f"marqueurs reçus/orphelins {meal['received']}/{meal['unmatched']}, "
            f"trace {len(content.meals)}/{content.unmatched}"
        )


def check_meter(content: TraceContent, output: dict, result: TraceResult) -> None:
    meter = output["meter"]
    if meter is not None and meter["serial"] and meter["serial"].encode().hex().upper() not in content.text:
        result.problems.append(f"numéro de série {meter['serial']!r} absent des octets de la trace")


def check_clock(content: TraceContent, output: dict, result: TraceResult) -> None:
    clock = output["clock"]
    set_requests = [m for m in content.sent if len(m) >= 26 and int.from_bytes(m[14:16], "big") == SET_TIME_ACTION]
    result.clock_action = clock["action"] if clock else ""
    if clock is None:
        if set_requests:
            result.problems.append("mise à l'heure envoyée sans horloge du lecteur connue")
        return
    if "--now" not in content.args:
        if clock["pc"] is not None or clock["offset_s"] is not None:
            result.problems.append("heure du PC inventée en rejeu sans --now")
        if set_requests:
            result.problems.append("mise à l'heure envoyée sans --now")
        return
    now = datetime.strptime(content.args[content.args.index("--now") + 1], TIME_FORMAT)
    offset = int((datetime.strptime(clock["meter"], TIME_FORMAT) - now).total_seconds())
    if clock["offset_s"] != offset:
        result.problems.append(f"écart lecteur - PC {clock['offset_s']} s, recalculé {offset} s")
    should_set = "--set-time" in content.args and clock["settable"] and abs(offset) > CLOCK_TOLERANCE_S
    if should_set != (len(set_requests) == 1):
        result.problems.append(f"{len(set_requests)} mise(s) à l'heure envoyée(s) pour un écart de {offset} s")
    if set_requests and set_requests[0][18:26] != bcd_time(now):
        result.problems.append(f"heure envoyée {set_requests[0][18:26].hex()} au lieu de {bcd_time(now).hex()}")


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
    if not traces:
        print("aucune trace à rejouer")
        return 2

    results = [check_trace(args.bin, t) for t in traces]
    args.out.mkdir(parents=True, exist_ok=True)
    csv_path = args.out / f"accuchek-replay-{datetime.now():%Y%m%d-%H%M%S}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["trace", "code", "mesures", "statut_signale", "marqueurs", "horloge", "ok", "problemes"])
        for r in results:
            writer.writerow([
                r.trace, r.exit_code, r.readings, r.flagged, r.markers, r.clock_action, r.ok, " | ".join(r.problems),
            ])

    passed = sum(r.ok for r in results)
    for r in results:
        flagged = f", {r.flagged} écartée(s) pour statut" if r.flagged else ""
        clock = f", horloge {r.clock_action}" if r.clock_action else ""
        print(f"{'OK   ' if r.ok else 'ÉCHEC'} {r.trace.name} : {r.readings} mesure(s), {r.markers} marqueur(s){flagged}{clock}")
        for problem in r.problems:
            print(f"      {problem}")
    print(f"{passed}/{len(results)} traces conformes ({100 * passed / len(results):.0f} %, seuil 100 %)")
    print(f"CSV : {csv_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
