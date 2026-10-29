"""Faux serveur LibreLinkUp pour les tests : un `transport` (llu_api.Transport) qui répond comme l'API réelle.

Les valeurs sont inventées ; les clés sont celles d'une vraie réponse (login, connections, graph).
"""

from __future__ import annotations

import gzip
import json
from urllib.parse import urlparse

TOKEN = "jeton-de-test"
USER_ID = "00000000-0000-4000-8000-000000000001"
PATIENT_ID = "00000000-0000-4000-8000-000000000002"


def item(local: str, mg: float, high: bool = False, low: bool = False, utc: str = "1/1/2026 0:00:00 AM") -> dict:
    return {
        "FactoryTimestamp": utc, "Timestamp": local, "type": 1, "ValueInMgPerDl": mg, "MeasurementColor": 1,
        "GlucoseUnits": 0, "Value": mg, "isHigh": high, "isLow": low,
    }


def graph_data(points: list[dict], current: dict | None, sensor: str | None = "3MH00ABC") -> dict:
    return {
        "connection": {
            "id": PATIENT_ID, "patientId": PATIENT_ID, "glucoseMeasurement": {**(current or {}), "TrendArrow": 3},
        } if current is not None else {},
        "activeSensors": [] if sensor is None else [{"sensor": {"sn": sensor, "a": 0}, "device": {}}],
        "graphData": points,
    }


class FakeLlu:
    """Garde chaque requête (méthode, hôte, chemin, en-têtes, corps) dans `calls`."""

    def __init__(self, graph: dict | None = None, patients: list | None = None):
        self.graph = graph if graph is not None else graph_data([], None)
        self.patients = [{"patientId": PATIENT_ID}] if patients is None else patients
        self.login_replies: list[tuple[int, dict]] = []  # une par appel à /login ; sinon la réponse normale
        self.fail: dict[str, tuple[int, dict, bytes]] = {}  # chemin -> réponse imposée
        self.network_error: Exception | None = None
        self.gzip = False
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        parts = urlparse(url)
        self.calls.append({
            "method": "GET" if body is None else "POST", "host": parts.netloc, "path": parts.path,
            "headers": dict(headers), "body": None if body is None else json.loads(body), "timeout": timeout,
        })
        if self.network_error is not None:
            raise self.network_error
        if parts.path in self.fail:
            return self.fail[parts.path]
        if parts.path == "/llu/auth/login":
            status, reply = self.login_replies.pop(0) if self.login_replies else (200, self.ok_login())
            return self.reply(status, reply)
        if headers.get("authorization") != f"Bearer {TOKEN}":
            return self.reply(401, {"status": 401})
        if parts.path == "/llu/connections":
            return self.reply(200, {"status": 0, "data": self.patients})
        if parts.path == f"/llu/connections/{PATIENT_ID}/graph":
            return self.reply(200, {"status": 0, "data": self.graph})
        return self.reply(404, {})

    @staticmethod
    def ok_login() -> dict:
        return {"status": 0, "data": {"user": {"id": USER_ID}, "authTicket": {"token": TOKEN, "expires": 1, "duration": 1}}}

    def reply(self, status: int, payload: dict):
        raw = json.dumps(payload).encode()
        if self.gzip:
            return status, {"Content-Encoding": "gzip"}, gzip.compress(raw)
        return status, {}, raw
