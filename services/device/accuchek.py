"""Lecture d'un Accu-Chek Guide via /usr/local/bin/accuchek.

accuchek tourne avec les droits de l'utilisateur : la règle udev
packaging/udev/70-glucofi-accuchek.rules donne l'accès USB au lecteur à la
session locale active (uaccess). La liste des lecteurs acceptés est intégrée à
accuchek (accuchek --known-devices).
accuchek écrit le JSON sur stdout seulement si la lecture a réussi ; sinon son
code de sortie (contracts.AccuchekExit) et la ligne "accuchek: <raison>" sur
stderr disent pourquoi. ACCUCHEK_DBG ajoute des logs sur stderr, jamais sur stdout.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from contracts import AccuchekExit, ClockAction, Meal, MeterClock, MeterInfo, Reading, SegmentCount

log = logging.getLogger("glucofi.device")

ACCUCHEK_BIN = "/usr/local/bin/accuchek"
UDEV_RULE = "/etc/udev/rules.d/70-glucofi-accuchek.rules"
ROCHE_VENDOR_ID = "173a"
SYSFS_USB = Path("/sys/bus/usb/devices")
MG_DL_MIN, MG_DL_MAX = 10, 600
OFF_SCALE_MG_DL = {"high": 601, "low": 9}
TIMESTAMP_FORMAT = "%Y/%m/%d %H:%M"
CLOCK_FORMAT = "%Y/%m/%d %H:%M:%S"
OUTPUT_FORMAT = 2
DEFAULT_TIMEOUT_S = 180

class DeviceError(Exception):
    """Erreur affichable telle quelle à l'utilisateur."""


class DeviceNotConnected(DeviceError):
    pass


class DeviceAccessDenied(DeviceError):
    pass


class DeviceReadFailed(DeviceError):
    pass


class DeviceProtocolError(DeviceReadFailed):
    """Le lecteur a interrompu l'échange ou répondu de façon inattendue."""


class InvalidOutput(DeviceError):
    pass


class NotInstalled(DeviceError):
    pass


@dataclass(frozen=True)
class ParseResult:
    readings: tuple[Reading, ...]
    rejected: tuple[str, ...]
    meter: MeterInfo | None = None
    clock: MeterClock | None = None
    glucose: SegmentCount | None = None
    meal: SegmentCount | None = None
    meals_unmatched: int = 0

    @property
    def markers(self) -> int:
        return sum(1 for r in self.readings if r.meal is not None)

    @property
    def warnings(self) -> tuple[str, ...]:
        return output_warnings(self)


@dataclass(frozen=True)
class FetchResult:
    readings: tuple[Reading, ...]
    rejected: tuple[str, ...]
    raw_path: Path | None
    meter: MeterInfo | None = None
    clock: MeterClock | None = None
    glucose: SegmentCount | None = None
    meal: SegmentCount | None = None
    meals_unmatched: int = 0

    @property
    def markers(self) -> int:
        return sum(1 for r in self.readings if r.meal is not None)

    @property
    def warnings(self) -> tuple[str, ...]:
        return output_warnings(self)


# au-delà, une horloge du lecteur laissée telle quelle décale les mesures dans la journée
CLOCK_WARNING_S = 5 * 60


def fmt_offset(seconds: int) -> str:
    """"12 min d'avance", "1 h 05 min de retard", "40 s d'avance" (horloge du lecteur par rapport au PC)."""
    direction = "d'avance" if seconds > 0 else "de retard"
    s = abs(seconds)
    if s < 60:
        amount = f"{s} s"
    elif s < 3600:
        amount = f"{round(s / 60)} min"
    else:
        hours, minutes = divmod(round(s / 60), 60)
        amount = f"{hours} h {minutes:02d} min"
    return f"{amount} {direction}"


def output_warnings(result: ParseResult | FetchResult) -> tuple[str, ...]:
    """Avertissements à montrer après un import : lecture incomplète, marqueurs orphelins, horloge."""
    warnings: list[str] = []
    for count, what, received in ((result.glucose, "mesures", "reçues"), (result.meal, "marqueurs repas", "reçus")):
        if count is not None and not count.complete:
            warnings.append(f"Le lecteur annonce {count.announced} {what}, {count.received} {received}.")
    if result.meals_unmatched:
        warnings.append(f"{result.meals_unmatched} marqueur(s) repas sans mesure à la même seconde, ignoré(s).")
    clock = result.clock
    if clock is None:
        return tuple(warnings)
    offset = clock.offset_s
    if clock.action == ClockAction.REJECTED:
        detail = f" (horloge du lecteur : {fmt_offset(offset)} sur le PC)" if offset else ""
        warnings.append(f"Le lecteur a refusé la mise à l'heure{detail} : corrigez l'heure sur le lecteur.")
    elif clock.action != ClockAction.SET and offset is not None and abs(offset) > CLOCK_WARNING_S:
        hint = {
            ClockAction.NOT_SETTABLE: " Ce lecteur ne se règle pas par USB : corrigez l'heure sur le lecteur.",
            ClockAction.PC_NOT_SYNCHRONIZED: " L'heure du PC n'est pas synchronisée (NTP), elle n'a pas été copiée sur le lecteur.",
        }.get(clock.action, "")
        warnings.append(f"L'horloge du lecteur a {fmt_offset(offset)} sur le PC.{hint}")
    return tuple(warnings)


def parse_output(text: str) -> ParseResult:
    """Parse la sortie JSON d'accuchek, format 2 ou ancien tableau. Les mesures invalides sont écartées et listées."""
    stripped = text.strip()
    if stripped in ("", "[", "{"):
        raise InvalidOutput("Aucune donnée reçue du lecteur.")
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise InvalidOutput(f"Données du lecteur illisibles ou incomplètes ({exc.msg}, ligne {exc.lineno}).") from exc
    if isinstance(data, list):
        return _parse_readings(data)
    if not isinstance(data, dict) or data.get("format") != OUTPUT_FORMAT or not isinstance(data.get("readings"), list):
        raise InvalidOutput(
            f"Format inattendu : sortie accuchek format {OUTPUT_FORMAT} ou tableau de mesures attendu."
        )
    try:
        meter = _parse_meter(data["meter"])
        clock = _parse_clock(data["clock"])
        glucose = _parse_count(data["glucose"])
        meal_count = data["meal"]
        meal = _parse_count(meal_count) if meal_count is not None else None
        unmatched = meal_count.get("unmatched", 0) if isinstance(meal_count, dict) else 0
        if isinstance(unmatched, bool) or not isinstance(unmatched, int) or unmatched < 0:
            raise TypeError("meal.unmatched doit être un entier positif")
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidOutput(f"En-tête de la sortie accuchek invalide : {exc}.") from exc
    parsed = _parse_readings(data["readings"])
    return ParseResult(parsed.readings, parsed.rejected, meter, clock, glucose, meal, unmatched)


def _parse_readings(items: list) -> ParseResult:
    readings: list[Reading] = []
    rejected: list[str] = []
    for index, item in enumerate(items):
        try:
            readings.append(_parse_item(item))
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append(f"mesure n°{index} ({item!r}) : {exc}")
    readings.sort()
    return ParseResult(tuple(readings), tuple(rejected))


def _text(data: dict, key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} doit être une chaîne")
    return value


def _parse_meter(data: object) -> MeterInfo | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise TypeError("meter doit être un objet")
    return MeterInfo(
        manufacturer=_text(data, "manufacturer"),
        model=_text(data, "model"),
        serial=_text(data, "serial"),
        firmware=_text(data, "firmware"),
        hardware=_text(data, "hardware"),
        software=_text(data, "software"),
        system_id=_text(data, "system_id"),
    )


def _parse_clock(data: object) -> MeterClock | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise TypeError("clock doit être un objet")
    pc = data["pc"]
    offset = data["offset_s"]
    synchronized = data["pc_synchronized"]
    if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int)):
        raise TypeError("clock.offset_s doit être un entier")
    if not isinstance(data["settable"], bool) or (synchronized is not None and not isinstance(synchronized, bool)):
        raise TypeError("clock.settable et clock.pc_synchronized doivent être des booléens")
    return MeterClock(
        meter=datetime.strptime(_text(data, "meter"), CLOCK_FORMAT),
        pc=datetime.strptime(pc, CLOCK_FORMAT) if pc is not None else None,
        offset_s=offset,
        settable=data["settable"],
        pc_synchronized=synchronized,
        action=ClockAction(data["action"]),
    )


def _parse_count(data: object) -> SegmentCount:
    if not isinstance(data, dict):
        raise TypeError("compteur attendu")
    announced, received = data["announced"], data["received"]
    for value in (announced, received):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise TypeError("compteur entier positif attendu")
    if received is None:
        raise TypeError("received ne peut pas être null")
    return SegmentCount(announced, received)


def _parse_item(item: object) -> Reading:
    if not isinstance(item, dict):
        raise TypeError("objet attendu")
    if item.get("error"):
        raise ValueError(f"mesure illisible sur le lecteur ({item['error']})")
    mg_dl = item["mg/dL"]
    epoch = item["epoch"]
    status = item.get("status", 0)
    off_scale = item.get("range")
    if isinstance(mg_dl, bool) or not isinstance(mg_dl, int):
        raise TypeError("mg/dL doit être un entier")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise TypeError("epoch doit être un entier positif")
    if isinstance(status, bool) or not isinstance(status, int):
        raise TypeError("status doit être un entier")
    if off_scale is not None:
        if OFF_SCALE_MG_DL.get(off_scale) != mg_dl:
            raise ValueError(f"range {off_scale!r} incohérent avec {mg_dl} mg/dL")
    else:
        if status != 0:
            raise ValueError(f"statut 0x{status:04x} signalé par le lecteur, mesure non retenue")
        if not MG_DL_MIN <= mg_dl <= MG_DL_MAX:
            raise ValueError(f"valeur hors plage {MG_DL_MIN}-{MG_DL_MAX} mg/dL")
    device_time = datetime.strptime(item["timestamp"], TIMESTAMP_FORMAT)
    device_id = item.get("id")
    meal = item.get("meal")
    return Reading(
        device_time=device_time,
        mg_dl=mg_dl,
        epoch=epoch,
        device_id=device_id if isinstance(device_id, int) else None,
        meal=Meal(meal) if meal is not None else None,
    )


def parse_file(path: Path) -> ParseResult:
    return parse_output(Path(path).read_text(encoding="utf-8"))


def is_device_connected(sysfs_root: Path = SYSFS_USB) -> bool:
    """Détecte un lecteur Roche branché, sans privilège."""
    if not sysfs_root.is_dir():
        return True  # sysfs indisponible (sandbox) : on laisse accuchek trancher
    for vendor_file in sysfs_root.glob("*/idVendor"):
        try:
            if vendor_file.read_text().strip().lower() == ROCHE_VENDOR_ID:
                return True
        except OSError:
            continue
    return False


def build_command() -> list[str]:
    """Commande de lecture : remet aussi le lecteur à l'heure du PC (si le PC est synchronisé, écart > 60 s)."""
    override = os.environ.get("GLUCOFI_ACCUCHEK_CMD")
    if override:
        return shlex.split(override)
    if not os.access(ACCUCHEK_BIN, os.X_OK):
        raise NotInstalled(f"{ACCUCHEK_BIN} est absent. Lancez une fois : sudo packaging/install-system.sh")
    return [ACCUCHEK_BIN, "--set-time"]


def fetch(
    raw_dir: Path | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    command: list[str] | None = None,
    sysfs_root: Path = SYSFS_USB,
) -> FetchResult:
    """Lit toutes les mesures du lecteur. Bloquant : à appeler hors du thread GTK."""
    if not is_device_connected(sysfs_root):
        raise DeviceNotConnected("Aucun lecteur Accu-Chek détecté. Branchez-le en USB puis réessayez.")

    cmd = command or build_command()
    log.info("lecture du lecteur : %s", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except FileNotFoundError as exc:
        raise DeviceReadFailed(f"Programme introuvable : {exc.filename}") from exc
    except subprocess.TimeoutExpired as exc:
        raise DeviceReadFailed(f"Le lecteur n'a pas répondu en {timeout_s} s.") from exc

    log.info("accuchek terminé : code %s, %d octets", proc.returncode, len(proc.stdout))
    if proc.stderr.strip():
        log.warning("stderr accuchek : %s", proc.stderr.strip()[-2000:])
    raw_path = _save_raw(raw_dir, proc.stdout) if raw_dir and proc.stdout.strip() not in ("", "[", "{") else None

    if proc.returncode != AccuchekExit.OK:
        raise exit_error(proc.returncode, proc.stderr)

    p = parse_output(proc.stdout)
    result = FetchResult(p.readings, p.rejected, raw_path, p.meter, p.clock, p.glucose, p.meal, p.meals_unmatched)
    log.info(
        "lecteur %s n°%s firmware %s ; horloge %s (écart %s s) ; mesures %s/%s ; marqueurs %s/%s, %s orphelins",
        result.meter.model_name if result.meter else "?",
        result.meter.serial if result.meter else "?",
        result.meter.firmware if result.meter else "?",
        result.clock.action.value if result.clock else "?",
        result.clock.offset_s if result.clock else "?",
        result.glucose.received if result.glucose else len(result.readings),
        result.glucose.announced if result.glucose else "?",
        result.meal.received if result.meal else 0,
        result.meal.announced if result.meal else "?",
        result.meals_unmatched,
    )
    for warning in result.warnings:
        log.warning("%s", warning)
    return result


RETRY_HINT = "Débranchez le lecteur, rebranchez-le, attendez l'écran de transfert puis réessayez."


def exit_error(code: int, stderr: str) -> DeviceError:
    """Erreur typée et message pour un code de sortie non nul d'accuchek."""
    reason = accuchek_reason(stderr)
    detail = f" Détail : {reason}." if reason else ""
    if code == AccuchekExit.NO_DEVICE:
        return DeviceNotConnected(f"Aucun lecteur Accu-Chek reconnu sur le port USB. {RETRY_HINT}{detail}")
    if code == AccuchekExit.ACCESS_DENIED:
        if os.path.exists(UDEV_RULE):
            hint = "débranchez puis rebranchez le lecteur"
        else:
            hint = f"la règle {UDEV_RULE} manque, lancez une fois : sudo packaging/install-system.sh"
        return DeviceAccessDenied(f"Accès USB au lecteur refusé : {hint}.{detail}")
    if code == AccuchekExit.TRANSFER:
        return DeviceReadFailed(
            "La communication avec le lecteur a été interrompue (lecteur débranché ou sans réponse). "
            f"Aucune mesure n'a été importée. {RETRY_HINT}{detail}"
        )
    if code == AccuchekExit.PROTOCOL:
        return DeviceProtocolError(
            f"Le lecteur a répondu de façon inattendue. Aucune mesure n'a été importée. {RETRY_HINT}{detail}"
        )
    return DeviceReadFailed(
        f"accuchek a échoué (code {code}). Relancez sudo packaging/install-system.sh puis réessayez.{detail}"
    )


def accuchek_reason(stderr: str) -> str:
    """Raison donnée par accuchek ("accuchek: ..." sur stderr), sinon la dernière ligne non vide."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("accuchek: "):
            return line.removeprefix("accuchek: ")
    return lines[-1][:300] if lines else ""


def _save_raw(raw_dir: Path, text: str) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"accuchek-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(text, encoding="utf-8")
    return path
