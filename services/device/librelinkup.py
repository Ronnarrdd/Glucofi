"""Lecture d'un capteur FreeStyle Libre 3 via le compte de suivi LibreLinkUp (bibliothèque pylibrelinkup).

API non officielle d'Abbott, sans engagement de stabilité : tout ce qui en dépend reste dans ce fichier.
pylibrelinkup n'est importée qu'à l'appel, pour que l'application démarre sans elle.
Le compte est celui de LibreLinkUp (suivi), jamais celui de LibreLink qui lit le capteur.
"""

from __future__ import annotations

import logging
import os
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
            name, value = name.strip(), value.strip().strip("'\"")
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


def fetch(credentials: Credentials, every_min: int | None = DEFAULT_EVERY_MIN) -> ParseResult:
    """Dernière valeur et graphique du premier patient partagé. Bloquant : à appeler hors du thread GTK."""
    try:
        from pylibrelinkup import APIUrl, PyLibreLinkUp, exceptions
    except ImportError as exc:
        raise LibreLinkUpNotInstalled(
            "La bibliothèque pylibrelinkup manque : pip install --user pylibrelinkup."
        ) from exc
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
