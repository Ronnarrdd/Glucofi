"""Lecture d'un capteur FreeStyle Libre 3 via le compte de suivi LibreLinkUp (bibliothèque pylibrelinkup).

API non officielle d'Abbott, sans engagement de stabilité : tout ce qui en dépend reste dans ce fichier.
pylibrelinkup n'est importée qu'à l'appel, pour que l'application démarre sans elle.
Le compte est celui de LibreLinkUp (suivi), jamais celui de LibreLink qui lit le capteur.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from contracts import MeterInfo, Reading, local_epoch
from services.device.accuchek import MG_DL_MAX, MG_DL_MIN, OFF_SCALE_MG_DL, DeviceError, ParseResult
from services.meals.gemini import env_file_candidates

log = logging.getLogger("glucofi.device")

EMAIL_VARIABLE = "LIBRELINKUP_EMAIL"
PASSWORD_VARIABLE = "LIBRELINKUP_PASSWORD"
REGION_VARIABLE = "LIBRELINKUP_REGION"
DEFAULT_REGION = "FR"
# noms de pylibrelinkup.APIUrl (vérifiés par test quand la bibliothèque est installée)
REGIONS = ("FR", "EU", "EU2", "DE", "US", "CA", "AE", "AP", "AU", "JP", "LA", "RU")
# le Libre 3 mesure chaque minute ; le moteur de doses attend quelques mesures par jour
DEFAULT_EVERY_MIN = 15


class LibreLinkUpError(DeviceError):
    """Erreur affichable telle quelle à l'utilisateur."""


class LibreLinkUpNotInstalled(LibreLinkUpError):
    pass


class NoCredentials(LibreLinkUpError):
    pass


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str
    region: str = DEFAULT_REGION


def _unquote(value: str) -> str:
    """Retire une paire de guillemets qui entoure toute la valeur, pas ceux qui font partie d'un mot de passe."""
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"" else value


def _quote(value: str) -> str:
    """Entoure d'une paire de guillemets de l'autre sorte une valeur qui en porte déjà aux deux bouts."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        other = '"' if value[0] == "'" else "'"
        return other + value + other
    return value


def _env_values(environ: Mapping[str, str], env_files: Sequence[Path]) -> dict[str, str]:
    """Variables LIBRELINKUP_* : l'environnement d'abord, puis les fichiers `.env` dans leur ordre."""
    wanted = (EMAIL_VARIABLE, PASSWORD_VARIABLE, REGION_VARIABLE)
    values = {name: (environ.get(name) or "").strip() for name in wanted}
    for path in env_files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            name, sep, value = line.strip().partition("=")
            name, value = name.strip(), _unquote(value.strip())
            if sep and name in values and value and not values[name]:
                values[name] = value
    return values


def load_credentials(
    environ: Mapping[str, str] | None = None, env_files: Sequence[Path] | None = None
) -> Credentials | None:
    """Identifiants du compte de suivi : variables d'environnement, sinon fichier `.env` (jamais suivi par git)."""
    environ = os.environ if environ is None else environ
    values = _env_values(environ, env_file_candidates(environ) if env_files is None else env_files)
    if not values[EMAIL_VARIABLE] or not values[PASSWORD_VARIABLE]:
        return None
    return Credentials(values[EMAIL_VARIABLE], values[PASSWORD_VARIABLE], values[REGION_VARIABLE] or DEFAULT_REGION)


def save_credentials(credentials: Credentials | None, path: Path) -> None:
    """Écrit (ou, avec None, retire) les identifiants dans le `.env` de `path`, droits 0600, autres lignes intactes.

    Le mot de passe ne va jamais dans la base : elle est exportée vers la tablette et fusionnable.
    """
    names = (EMAIL_VARIABLE, PASSWORD_VARIABLE, REGION_VARIABLE)
    try:
        kept = [
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip().partition("=")[0].strip() not in names
        ]
    except OSError:
        kept = []
    if credentials is not None:
        for name, value in zip(names, (credentials.email, credentials.password, credentials.region)):
            if "\n" in value or "\r" in value:
                raise ValueError("les identifiants ne peuvent pas contenir de saut de ligne")
            kept.append(f"{name}={_quote(value)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    # créé en 0600 d'emblée : jamais lisible par les autres, même un instant
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(kept) + ("\n" if kept else ""))
    path.chmod(0o600)


_US_TIMESTAMP = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}) (\d{1,2}):(\d{2}):(\d{2}) (AM|PM)")
US_TIMESTAMP_FORMAT = "%m/%d/%Y %I:%M:%S %p"


def parse_us_timestamp(text: str) -> datetime:
    """« 10/29/2026 8:22:28 PM » sans dépendre de la langue : %p de strptime vaut '' en fr_FR (locale que GTK donne
    au processus), « PM » n'y est alors pas reconnu."""
    match = _US_TIMESTAMP.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"heure LibreLinkUp illisible : {text!r}")
    month, day, year, hour, minute, second = (int(part) for part in match.groups()[:6])
    if not 1 <= hour <= 12:
        raise ValueError(f"heure LibreLinkUp illisible : {text!r}")
    return datetime(year, month, day, hour % 12 + (12 if match[7] == "PM" else 0), minute, second)


def _locale_proof_timestamps(data_module) -> None:
    """Fait lire les heures à pylibrelinkup par parse_us_timestamp (seul strptime de la bibliothèque, models/data.py).

    shortcut: remplace le `datetime` de ce module ; à revoir si la bibliothèque change sa façon de lire les heures.
    """
    base = data_module.datetime
    if getattr(base, "locale_proof", False):
        return

    class _Instances(type):
        def __instancecheck__(cls, obj):  # la bibliothèque teste isinstance(v, datetime) sur de vrais datetime
            return isinstance(obj, base)

    class LocaleProofDatetime(base, metaclass=_Instances):
        locale_proof = True

        @classmethod
        def strptime(cls, text, fmt):
            return parse_us_timestamp(text) if fmt == US_TIMESTAMP_FORMAT else super().strptime(text, fmt)

    data_module.datetime = LocaleProofDatetime


def to_reading(timestamp: datetime, mg_dl: float, is_high: bool = False, is_low: bool = False) -> Reading:
    """Une mesure du capteur. Heure locale du téléphone, à la minute (comme le lecteur) : un réimport ne duplique pas."""
    if is_high:
        value = OFF_SCALE_MG_DL["high"]
    elif is_low:
        value = OFF_SCALE_MG_DL["low"]
    else:
        value = round(mg_dl)
        if not MG_DL_MIN <= value <= MG_DL_MAX:
            raise ValueError(f"valeur hors plage {MG_DL_MIN}-{MG_DL_MAX} mg/dL")
    device_time = timestamp.replace(tzinfo=None, second=0, microsecond=0)
    return Reading(device_time=device_time, mg_dl=value, epoch=local_epoch(device_time))


def thin(readings: Sequence[Reading], every_min: int | None = DEFAULT_EVERY_MIN) -> tuple[Reading, ...]:
    """Garde la première mesure de chaque tranche de `every_min` minutes ; None les garde toutes."""
    ordered = sorted(readings)
    if not every_min:
        return tuple(ordered)
    kept: list[Reading] = []
    bucket = None
    for reading in ordered:
        current = (reading.device_time.date(), (reading.device_time.hour * 60 + reading.device_time.minute) // every_min)
        if current != bucket:
            kept.append(reading)
            bucket = current
    return tuple(kept)


def fetch_cgm(credentials: Credentials, every_min: int | None = DEFAULT_EVERY_MIN) -> ParseResult:
    """Dernière valeur et graphique du premier patient partagé. Bloquant : à appeler hors du thread GTK."""
    try:
        from pydantic import ValidationError
        from pylibrelinkup import APIUrl, PyLibreLinkUp, exceptions
    except ImportError as exc:
        raise LibreLinkUpNotInstalled(
            "La bibliothèque pylibrelinkup manque : pip install --user pylibrelinkup."
        ) from exc
    try:
        from pylibrelinkup.models import data as library_data
    except ImportError:
        log.warning("pylibrelinkup.models.data introuvable : lecture des heures laissée à la bibliothèque")
    else:
        _locale_proof_timestamps(library_data)
    try:
        api_url = APIUrl[credentials.region.upper()]
    except KeyError:
        raise LibreLinkUpError(f"Région LibreLinkUp inconnue : {credentials.region!r}.") from None
    try:
        client = PyLibreLinkUp(email=credentials.email, password=credentials.password, api_url=api_url)
        client.authenticate()
        patients = client.get_patients()
        if not patients:
            raise LibreLinkUpError(
                "Aucun patient partagé : la personne doit vous inviter depuis LibreLink (Partager, puis LibreLinkUp)."
            )
        patient = patients[0]
        data = client.read(patient).data
        measurements = [*data.graph_data, client.latest(patient)]
        sensors = data.active_sensors
    except LibreLinkUpError:
        raise
    except exceptions.AuthenticationError as exc:
        raise LibreLinkUpError("Identifiants LibreLinkUp refusés : vérifiez l'e-mail et le mot de passe.") from exc
    except exceptions.PyLibreLinkUpError as exc:
        raise LibreLinkUpError(f"LibreLinkUp a refusé la demande : {exc}.") from exc
    except ValidationError as exc:
        where = ".".join(str(part) for part in exc.errors()[0]["loc"])
        raise LibreLinkUpError(
            f"Réponse de LibreLinkUp inattendue ({exc.error_count()} champs invalides, dont {where}) : "
            "mettez pylibrelinkup à jour (pip install --user -U pylibrelinkup)."
        ) from exc
    except OSError as exc:  # requests.RequestException hérite d'OSError
        raise LibreLinkUpError(f"LibreLinkUp injoignable : {exc}.") from exc

    readings: list[Reading] = []
    rejected: list[str] = []
    for index, measurement in enumerate(measurements):
        try:
            readings.append(to_reading(
                measurement.timestamp, measurement.value_in_mg_per_dl, measurement.is_high, measurement.is_low
            ))
        except (TypeError, ValueError) as exc:
            rejected.append(f"mesure n°{index} ({measurement}) : {exc}")
    serial = sensors[0].sensor.sn if sensors else ""
    meter = MeterInfo(manufacturer="Abbott", model="FreeStyle Libre 3", serial=serial) if serial else None
    return ParseResult(thin(set(readings), every_min), tuple(rejected), meter)
