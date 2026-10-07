"""adb : trouver la tablette branchée en USB et parler à l'app Glucofi Android (contracts/tablet_sync.py)."""

from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from contracts.tablet_sync import AUTHORITY, PACKAGE, REMOTE_DIR, REPLY_KEY, SYNC_VERSION

log = logging.getLogger("glucofi.tablet")

Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess]
TIMEOUT_S = 120.0


class TabletError(Exception):
    """Synchronisation impossible ; le message dit quoi faire."""


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str = ""

    @property
    def emulator(self) -> bool:
        return self.serial.startswith("emulator-")


def find_adb(environ: dict[str, str] | None = None, home: Path | None = None) -> str | None:
    """adb du PATH, sinon celui du SDK Android (ANDROID_HOME, ANDROID_SDK_ROOT, ~/Android/Sdk)."""
    environ = os.environ if environ is None else environ
    found = shutil.which("adb", path=environ.get("PATH"))
    if found:
        return found
    roots = [environ.get("ANDROID_HOME"), environ.get("ANDROID_SDK_ROOT"), str((home or Path.home()) / "Android" / "Sdk")]
    for root in filter(None, roots):
        candidate = Path(root) / "platform-tools" / "adb"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def parse_devices(text: str) -> list[Device]:
    """Sortie de `adb devices -l` : une ligne par appareil, « numéro  état  model:… »."""
    devices = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or line.startswith(("List of devices", "*")):
            continue
        model = next((p.removeprefix("model:") for p in parts[2:] if p.startswith("model:")), "")
        devices.append(Device(parts[0], parts[1], model.replace("_", " ")))
    return devices


def parse_reply(output: str) -> dict:
    """Sortie de `content call` : « Result: Bundle[{reply=<base64>}] » ; TabletError si l'app n'a pas répondu."""
    marker = f"{REPLY_KEY}="
    if "Result:" not in output or marker not in output:
        if "Unknown authority" in output or "Could not find provider" in output or "Unknown URI" in output:
            raise TabletError(
                "Cette version de Glucofi sur la tablette ne sait pas encore se synchroniser : "
                "installez la dernière version de l'app."
            )
        if "SecurityException" in output:
            raise TabletError("La tablette a refusé la synchronisation (appel non autorisé).")
        raise TabletError(f"Réponse inattendue de la tablette : {output.strip()[:300] or 'rien'}")
    encoded = output.split(marker, 1)[1].split("}", 1)[0].strip()
    try:
        reply = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TabletError(f"Réponse illisible de la tablette ({exc}).") from exc
    if not isinstance(reply, dict) or "ok" not in reply or "message" not in reply:
        raise TabletError("Réponse incomplète de la tablette.")
    if reply.get("sync") != SYNC_VERSION:
        raise TabletError(
            f"La tablette parle la version {reply.get('sync')} de la synchronisation, ce PC la version {SYNC_VERSION} : "
            "mettez à jour Glucofi sur l'appareil le plus ancien."
        )
    return reply


def _run(args: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=False)


class Adb:
    def __init__(self, path: str, run: Runner = _run, timeout: float = TIMEOUT_S):
        self.path = path
        self._run = run
        self.timeout = timeout

    @classmethod
    def locate(cls) -> "Adb":
        path = find_adb()
        if path is None:
            raise TabletError(
                "adb est introuvable sur ce PC : installez le paquet android-tools (outils Android), puis recommencez."
            )
        return cls(path)

    def run(self, *args: str, serial: str | None = None) -> str:
        command = [self.path, *(("-s", serial) if serial else ()), *args]
        try:
            result = self._run(command, self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise TabletError(f"La tablette ne répond plus (plus de {self.timeout:.0f} s) : débranchez-la, rebranchez-la, recommencez.") from exc
        except OSError as exc:
            raise TabletError(f"adb n'a pas pu être lancé ({exc}).") from exc
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            log.warning("adb %s : code %s : %s", " ".join(args), result.returncode, output.strip())
            if "unauthorized" in output:
                raise TabletError(_UNAUTHORIZED)
            if "no devices" in output or "not found" in output:
                raise TabletError("La tablette n'est plus branchée.")
            raise TabletError(f"adb a échoué : {output.strip()[:300]}")
        return output

    def devices(self) -> list[Device]:
        return parse_devices(self.run("devices", "-l"))

    def has_app(self, serial: str) -> bool:
        try:
            return "package:" in self.run("shell", "pm", "path", PACKAGE, serial=serial)
        except TabletError:  # pm sort en erreur quand le paquet est absent
            return False

    def call(self, serial: str, method: str) -> dict:
        output = self.run("shell", "content", "call", "--uri", f"content://{AUTHORITY}", "--method", method, serial=serial)
        reply = parse_reply(output)
        log.info("tablette %s, %s : %s", serial, method, reply.get("message"))
        return reply

    def pull(self, serial: str, remote: str, local: Path) -> Path:
        self.run("pull", remote, str(local), serial=serial)
        if not local.is_file():
            raise TabletError(f"La copie de la base de la tablette n'est pas arrivée sur le PC ({local.name}).")
        return local

    def push(self, serial: str, local: Path, remote: str) -> None:
        self.run("push", str(local), remote, serial=serial)

    def reset_sync_dir(self, serial: str) -> None:
        """Efface le dossier d'échange : l'app le recrée à l'export.

        Un dossier créé par adb (push) appartient à l'uid shell : l'app ne pourrait plus y écrire.
        """
        self.run("shell", "rm", "-r", "-f", REMOTE_DIR, serial=serial)


_UNAUTHORIZED = (
    "La tablette n'a pas encore autorisé ce PC : sur la tablette, acceptez la fenêtre « Autoriser le débogage USB ? » "
    "(cochez « Toujours autoriser »), puis recommencez."
)


ALLOW_EMULATOR_ENV = "GLUCOFI_SYNC_EMULATOR"


def pick_device(adb: Adb, environ: dict[str, str] | None = None) -> Device:
    """La tablette où Glucofi est installée ; un message clair sinon (rien de branché, non autorisée, plusieurs).

    Un émulateur n'est jamais choisi, sauf GLUCOFI_SYNC_EMULATOR=1 : ses bases de démonstration
    se mélangeraient aux vraies mesures, d'un côté puis de l'autre.
    """
    environ = os.environ if environ is None else environ
    found = adb.devices()
    emulators = [d for d in found if d.emulator]
    devices = found if environ.get(ALLOW_EMULATOR_ENV) == "1" else [d for d in found if not d.emulator]
    ready = [d for d in devices if d.state == "device"]
    if not ready:
        if any(d.state == "unauthorized" for d in devices):
            raise TabletError(_UNAUTHORIZED)
        if devices:
            raise TabletError(f"La tablette n'est pas prête (état adb : {devices[0].state}) : débranchez-la et rebranchez-la.")
        if emulators:
            raise TabletError(
                f"Seul un émulateur Android est branché ({', '.join(d.serial for d in emulators)}), pas de tablette : "
                "Glucofi ne se synchronise pas avec un émulateur, pour ne pas mélanger des données de test aux vraies. "
                "Branchez la tablette, puis recommencez."
            )
        raise TabletError(
            "Aucune tablette trouvée : branchez-la en USB, déverrouillez-la, et vérifiez que le débogage USB est "
            "activé (Paramètres > Options pour les développeurs)."
        )
    with_app = [d for d in ready if adb.has_app(d.serial)]
    if not with_app:
        raise TabletError("Glucofi n'est pas installée sur la tablette branchée.")
    if len(with_app) > 1:
        real = [d for d in with_app if not d.emulator]
        if len(real) == 1:
            return real[0]
        names = ", ".join(d.model or d.serial for d in with_app)
        raise TabletError(f"Plusieurs appareils avec Glucofi sont branchés ({names}) : n'en gardez qu'un.")
    return with_app[0]
