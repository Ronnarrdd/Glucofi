"""Client LibreLinkUp en bibliothèque standard (urllib) : connexion, patient partagé, graphique.

API non officielle d'Abbott (celle de l'application Android LibreLinkUp), sans engagement de stabilité : si elle
change, c'est ici (VERSION, en-têtes, chemins) qu'il faut corriger. Aucune dépendance : le même code tourne sur le PC
et sur la tablette (Chaquopy n'a pas pydantic-core pour Python 3.13, donc pas la bibliothèque pylibrelinkup). Ne rend que des dicts JSON ; la
conversion en mesures est dans librelinkup.py.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping

# valeur annoncée par l'application officielle ; Abbott refuse les versions trop anciennes (à relever alors)
VERSION = "4.16.0"
HEADERS = {
    "accept-encoding": "gzip",
    "cache-control": "no-cache",
    "connection": "Keep-Alive",
    "content-type": "application/json",
    "product": "llu.android",
    "version": VERSION,
}
REGION_URLS = {
    "FR": "https://api-fr.libreview.io",
    "EU": "https://api-eu.libreview.io",
    "EU2": "https://api-eu2.libreview.io",
    "DE": "https://api-de.libreview.io",
    "US": "https://api-us.libreview.io",
    "CA": "https://api-ca.libreview.io",
    "AE": "https://api-ae.libreview.io",
    "AP": "https://api-ap.libreview.io",
    "AU": "https://api-au.libreview.io",
    "JP": "https://api-jp.libreview.io",
    "LA": "https://api-la.libreview.io",
    "RU": "https://api.libreview.ru",
}
TIMEOUT_S = 20
# étapes que le serveur impose avant de donner accès (clé de data.step.type) : à faire dans l'application officielle
STEPS = {
    "tou": "acceptez les conditions d'utilisation dans l'application LibreLinkUp",
    "pp": "acceptez la politique de confidentialité dans l'application LibreLinkUp",
    "verifyEmail": "vérifiez votre adresse e-mail (message envoyé par LibreLinkUp)",
}
AUTH_REFUSED = "Identifiants LibreLinkUp refusés : vérifiez l'e-mail et le mot de passe"

# transport(url, headers, body, timeout) -> (statut HTTP, en-têtes, corps) ; remplacé par un faux dans les tests
Transport = Callable[[str, Mapping[str, str], "bytes | None", float], "tuple[int, Mapping[str, str], bytes]"]


class LluError(Exception):
    """Erreur affichable telle quelle : une phrase française avec majuscule, sans point final."""


class LluAuthError(LluError):
    pass


class LluRateLimit(LluError):
    pass


def urllib_transport(url: str, headers: Mapping[str, str], body: bytes | None, timeout: float):
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:  # un statut d'erreur est une réponse, pas une panne réseau
        return exc.code, exc.headers, exc.read()


def _payload(status: int, headers: Mapping[str, str], raw: bytes) -> dict:
    if status == 429:
        raise LluRateLimit("Trop de demandes à LibreLinkUp, réessayez dans quelques minutes")
    if status in (401, 403):
        raise LluAuthError(AUTH_REFUSED)
    if status >= 400:
        raise LluError(f"LibreLinkUp a répondu {status}")
    if headers.get("Content-Encoding", "").lower() == "gzip":
        raw = gzip.decompress(raw)
    try:
        data = json.loads(raw)
    except ValueError:
        raise LluError("Réponse illisible de LibreLinkUp") from None
    if not isinstance(data, dict):
        raise LluError("Réponse inattendue de LibreLinkUp")
    return data


@dataclass
class LluClient:
    """Une session : `login()` puis `patients()` et `graph(patient_id)`. Suit une seule redirection de région."""

    email: str
    password: str
    region: str = "FR"
    transport: Transport = urllib_transport
    timeout: float = TIMEOUT_S
    base: str = field(init=False, default="")
    _headers: dict = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        try:
            self.base = REGION_URLS[self.region.upper()]
        except KeyError:
            raise LluError(f"Région LibreLinkUp inconnue : {self.region!r}") from None
        self._headers = dict(HEADERS)

    def _call(self, path: str, body: dict | None = None) -> dict:
        encoded = None if body is None else json.dumps(body).encode()
        return _payload(*self.transport(self.base + path, self._headers, encoded, self.timeout))

    def login(self) -> None:
        reply = self._call("/llu/auth/login", {"email": self.email, "password": self.password})
        data = reply.get("data") or {}
        if data.get("redirect"):
            region = str(data.get("region", "")).upper()
            if region not in REGION_URLS:
                raise LluError(f"LibreLinkUp demande la région {region!r}, que Glucofi ne connaît pas")
            self.region, self.base = region, REGION_URLS[region]
            reply = self._call("/llu/auth/login", {"email": self.email, "password": self.password})
            data = reply.get("data") or {}
        step = (data.get("step") or {}).get("type")
        if step:
            raise LluError("LibreLinkUp demande une étape avant l'accès : " + STEPS.get(step, f"terminez-la dans son application ({step})"))
        try:
            token, account = data["authTicket"]["token"], data["user"]["id"]
        except (KeyError, TypeError):
            raise LluAuthError(AUTH_REFUSED) from None
        self._headers["authorization"] = f"Bearer {token}"
        # exigé par le serveur depuis 2024 : empreinte de l'identifiant du compte
        self._headers["account-id"] = hashlib.sha256(str(account).encode()).hexdigest()

    def _data(self, path: str):
        reply = self._call(path)
        if reply.get("status") not in (0, None):
            raise LluError("LibreLinkUp a refusé la demande")
        return reply.get("data")

    def patients(self) -> list[dict]:
        data = self._data("/llu/connections")
        if not isinstance(data, list):
            raise LluError("Réponse inattendue de LibreLinkUp (liste des patients)")
        return data

    def graph(self, patient_id: str) -> dict:
        data = self._data(f"/llu/connections/{patient_id}/graph")
        if not isinstance(data, dict):
            raise LluError("Réponse inattendue de LibreLinkUp (graphique)")
        return data
