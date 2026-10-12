"""Synchronisation Wi-Fi : la tablette (Glucofi ouverte) répond sur le réseau local, chiffré par le code de couplage.

NetLink a la même forme que TabletLink (device, fetch, send) : la fenêtre et sync() ne voient pas la différence.
Contrat : contracts/tablet_sync.py.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

from contracts.tablet_sync import (
    NET_CALL_PATH,
    NET_CHALLENGE_BYTES,
    NET_CHALLENGE_HEADER,
    NET_CHALLENGE_PATH,
    NET_MAX_BODY,
    NET_METHODS,
    NET_PORT,
    NET_SERVICE,
    PC_FILE,
    SYNC_VERSION,
    TABLET_FILE,
)
from services.tablet import secure
from services.tablet.adb import Device, TabletError
from services.tablet.sync import TabletReply

log = logging.getLogger("glucofi.tablet")

PAIRING_FILE = "tablet-wifi.json"
_LOCKED = "La tablette a bloqué les essais pendant une minute : patientez, puis recommencez."
HELLO_TIMEOUT_S = 4.0  # une tablette allumée sur le même Wi-Fi répond en moins d'une seconde
CALL_TIMEOUT_S = 60.0

Fetch = Callable[[urllib.request.Request, float], tuple[int, bytes]]
Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess]


class PairingRefused(TabletError):
    """La tablette ne reconnaît pas la clé de ce PC : il faut coupler à nouveau."""


@dataclass(frozen=True)
class Pairing:
    """Ce que le PC garde de la tablette couplée : le secret (fichier privé), la dernière adresse vue."""

    secret: str  # hexadécimal
    host: str
    port: int = NET_PORT

    @property
    def key(self) -> bytes:
        return secure.derive_key(bytes.fromhex(self.secret))

    def at(self, host: str, port: int) -> "Pairing":
        return Pairing(self.secret, host, port)


def load_pairing(directory: Path) -> Pairing | None:
    path = Path(directory) / PAIRING_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pairing = Pairing(str(data["secret"]), str(data["host"]), int(data.get("port", NET_PORT)))
        pairing.key  # noqa: B018 - secret hexadécimal valide ?
        return pairing
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("couplage Wi-Fi illisible (%s) : ignoré", exc)
        return None


def save_pairing(directory: Path, pairing: Pairing) -> None:
    """Fichier lisible par l'utilisateur seul : le secret donne accès aux données de santé de la tablette."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PAIRING_FILE
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(asdict(pairing), out)
    os.replace(tmp, path)


def forget_pairing(directory: Path) -> None:
    (Path(directory) / PAIRING_FILE).unlink(missing_ok=True)


@dataclass(frozen=True)
class Found:
    host: str
    port: int
    name: str


def parse_avahi(text: str) -> list[Found]:
    """Sortie de `avahi-browse -rpt` : lignes « =;interface;IPv4;nom;type;domaine;machine;adresse;port;txt »."""
    found: dict[tuple[str, int], Found] = {}
    for line in text.splitlines():
        parts = line.split(";")
        if len(parts) < 9 or parts[0] != "=" or parts[2] != "IPv4":
            continue
        try:
            found[(parts[7], int(parts[8]))] = Found(parts[7], int(parts[8]), parts[3])
        except ValueError:
            continue
    return list(found.values())


def _run(args: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, check=False)


def discover(run: Runner = _run, timeout: float = 5.0) -> list[Found]:
    """Tablettes Glucofi annoncées sur le réseau (avahi) ; vide si avahi manque : l'adresse gardée suffit alors."""
    try:
        result = run(["avahi-browse", "-rpt", NET_SERVICE], timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.info("recherche mDNS impossible : %s", exc)
        return []
    return parse_avahi(result.stdout or "")


def _http(request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - http://, adresse locale
            return response.status, response.read(NET_MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, b""


class NetLink:
    """Échanges avec la tablette couplée ; chaque étape lève TabletError avec un message à montrer."""

    def __init__(self, pairing: Pairing, fetch: Fetch = _http):
        self.pairing = pairing
        self._http = fetch
        self.device = Device(serial=f"{pairing.host}:{pairing.port}", state="device", model="Wi-Fi")

    @classmethod
    def connect(cls, pairing: Pairing, fetch: Fetch = _http, find: Callable[[], list[Found]] | None = None) -> "NetLink":
        """Adresse gardée d'abord, puis les tablettes annoncées ; la clé dit laquelle est la bonne."""
        link = cls(pairing, fetch)
        try:
            link.hello()
            return link
        except PairingRefused:  # clé refusée : une autre adresse n'y changera rien
            raise
        except TabletError as exc:
            first = exc
        for found in (find or discover)():
            if (found.host, found.port) == (pairing.host, pairing.port):
                continue
            link = cls(pairing.at(found.host, found.port), fetch)
            try:
                link.hello()
                log.info("tablette retrouvée à %s:%s", found.host, found.port)
                return link
            except TabletError:
                continue
        raise first

    def _url(self, path: str) -> str:
        return f"http://{self.pairing.host}:{self.pairing.port}{path}"

    def _request(self, request: urllib.request.Request, timeout: float) -> tuple[int, bytes]:
        try:
            return self._http(request, timeout)
        except (OSError, TimeoutError) as exc:  # URLError, refus de connexion, délai dépassé
            raise TabletError(
                f"La tablette ne répond pas à {self.pairing.host}:{self.pairing.port} ({getattr(exc, 'reason', exc)}). "
                "Ouvrez Glucofi sur la tablette, allumée et sur le même Wi-Fi que ce PC."
            ) from exc

    def call(self, method: str, blob: bytes = b"", timeout: float = CALL_TIMEOUT_S) -> tuple[TabletReply, bytes]:
        if method not in NET_METHODS:
            raise ValueError(method)
        try:
            key = self.pairing.key
        except ImportError as exc:
            raise TabletError(
                "La synchronisation Wi-Fi demande le paquet python3-cryptography, absent de ce PC : installez-le, "
                "ou synchronisez par le câble USB."
            ) from exc
        status, body = self._request(urllib.request.Request(self._url(NET_CHALLENGE_PATH)), HELLO_TIMEOUT_S)
        if status == 429:
            raise TabletError(_LOCKED)
        if status != 200:
            raise TabletError(f"La tablette a refusé la synchronisation (code {status}).")
        try:
            challenge = bytes.fromhex(body.decode("ascii").strip())
        except ValueError as exc:
            raise TabletError("Réponse illisible de la tablette.") from exc
        if len(challenge) != NET_CHALLENGE_BYTES:
            raise TabletError("Réponse illisible de la tablette.")
        request = urllib.request.Request(
            self._url(NET_CALL_PATH + method),
            data=secure.seal(key, challenge, method, "req", secure.pack({}, blob)),
            headers={NET_CHALLENGE_HEADER: challenge.hex(), "Content-Type": "application/octet-stream"},
            method="POST",
        )
        status, body = self._request(request, timeout)
        if status == 403:
            raise PairingRefused(
                "La tablette n'accepte plus ce couplage : dans Glucofi sur la tablette, affichez le code de couplage, "
                "puis couplez à nouveau ce PC (menu > Coupler la tablette (Wi-Fi))."
            )
        if status == 429:
            raise TabletError(_LOCKED)
        if status != 200:
            raise TabletError(f"La tablette a répondu une erreur ({status}).")
        try:
            header, data = secure.unpack(secure.unseal(key, challenge, method, "rep", body))
            reply = TabletReply.of(header)
        except (secure.BadMessage, KeyError, TypeError) as exc:
            raise TabletError(f"Réponse illisible de la tablette ({exc}).") from exc
        if header.get("sync") != SYNC_VERSION:
            raise TabletError(
                f"La tablette parle la version {header.get('sync')} de la synchronisation, ce PC la version {SYNC_VERSION} : "
                "mettez à jour Glucofi sur l'appareil le plus ancien."
            )
        if not reply.ok:
            raise TabletError(reply.message)
        return reply, data

    def hello(self) -> TabletReply:
        return self.call("hello", timeout=HELLO_TIMEOUT_S)[0]

    def fetch(self, workdir: Path) -> Path:
        _, data = self.call("export")
        if not data:
            raise TabletError("La tablette n'a envoyé aucune base : recommencez.")
        target = Path(workdir) / TABLET_FILE
        target.write_bytes(data)
        return target

    def send(self, pc_db: Path) -> TabletReply:
        return self.call("merge", Path(pc_db).read_bytes())[0]


def connect_link(directory: Path, usb: Callable[[], object], find: Callable[[], list[Found]] | None = None):
    """Wi-Fi si un couplage existe (et enregistre l'adresse retrouvée), sinon ou en cas d'échec USB (adb)."""
    pairing = load_pairing(directory)
    if pairing is None:
        try:
            return usb()
        except TabletError as exc:
            raise TabletError(f"{exc}\n\nSans câble : menu > Coupler la tablette (Wi-Fi).") from exc
    try:
        link = NetLink.connect(pairing, find=find)
    except TabletError as wifi:
        log.warning("synchronisation Wi-Fi : %s", wifi)
        try:
            return usb()
        except TabletError as cable:
            raise TabletError(f"Par le Wi-Fi : {wifi}\n\nPar USB : {cable}") from cable
    if link.pairing != pairing:
        save_pairing(directory, link.pairing)
    return link


def parse_address(text: str) -> tuple[str, int]:
    """« 192.168.1.20 » ou « 192.168.1.20:47831 » ; ValueError avec un message à montrer."""
    host, _, port = text.strip().partition(":")
    if not host or any(c.isspace() for c in host):
        raise ValueError("Adresse de la tablette invalide : par exemple 192.168.1.20")
    try:
        number = int(port) if port else NET_PORT
    except ValueError:
        number = 0
    if not 0 < number < 65536:
        raise ValueError("Port de la tablette invalide : laissez-le vide, ou un nombre entre 1 et 65535.")
    return host, number


def pair(
    directory: Path, code: str, address: str = "", fetch: Fetch = _http, find: Callable[[], list[Found]] | None = None,
) -> Pairing:
    """Couple ce PC à la tablette qui affiche ce code : adresse saisie, sinon tablettes annoncées sur le réseau.

    Le couplage n'est gardé qu'une fois la tablette atteinte avec cette clé (un `hello` chiffré a réussi).
    """
    try:
        secret = secure.parse_code(code)
    except ValueError as exc:
        raise TabletError(str(exc)) from exc
    if address.strip():
        try:
            candidates = [parse_address(address)]
        except ValueError as exc:
            raise TabletError(str(exc)) from exc
    else:
        candidates = [(f.host, f.port) for f in (find or discover)()]
        if not candidates:
            raise TabletError(
                "Aucune tablette Glucofi trouvée sur le réseau : ouvrez Glucofi sur la tablette (même Wi-Fi que ce PC), "
                "ou saisissez l'adresse affichée sur la tablette."
            )
    last: TabletError | None = None
    for host, port in candidates:
        pairing = Pairing(secret.hex(), host, port)
        try:
            NetLink(pairing, fetch).hello()
        except PairingRefused:
            last = TabletError("Code de couplage refusé par la tablette : vérifiez-le (16 lettres et chiffres affichés sur la tablette).")
        except TabletError as exc:
            last = exc
        else:
            save_pairing(directory, pairing)
            return pairing
    raise last  # type: ignore[misc]  # candidates n'est pas vide
