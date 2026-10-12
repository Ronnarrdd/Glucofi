"""Tablette Wi-Fi simulée : vrai serveur HTTP en boucle locale, vraie base SQLite, même protocole que l'app Android.

Reprend les règles de contracts/tablet_sync.py : défi à usage unique, AES-GCM, 403 sans corps si la clé ne va pas,
verrou après NET_MAX_FAILURES refus d'affilée. Sert aux tests de services/tablet et de la fenêtre.
"""

from __future__ import annotations

import http.server
import os
import tempfile
import threading
import time
from pathlib import Path

from contracts.tablet_sync import (
    NET_CALL_PATH,
    NET_CHALLENGE_BYTES,
    NET_CHALLENGE_HEADER,
    NET_CHALLENGE_PATH,
    NET_LOCK_S,
    NET_MAX_FAILURES,
    NET_METHODS,
    SYNC_VERSION,
)
from services.store import MergeRefused, Store
from services.tablet import secure


class FakeNetTablet:
    def __init__(self, root: Path, secret: bytes | None = None):
        self.root = Path(root)
        self.store = Store(self.root / "glucofi.db")
        self.secret = secret or secure.new_secret()
        self.key = secure.derive_key(self.secret)
        self.sync_version = SYNC_VERSION
        self.calls: list[str] = []
        self.fail: dict[str, str] = {}
        self.challenges: set[bytes] = set()
        self.failures = 0
        self.locked_until = 0.0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):  # silence
                pass

            def _send(self, status: int, body: bytes = b"") -> None:
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path != NET_CHALLENGE_PATH:
                    return self._send(404)
                if time.monotonic() < outer.locked_until:
                    return self._send(429)
                challenge = os.urandom(NET_CHALLENGE_BYTES)
                outer.challenges.add(challenge)
                self._send(200, challenge.hex().encode("ascii"))

            def do_POST(self):
                self._send(*outer.call(self.path, self.headers.get(NET_CHALLENGE_HEADER, ""), self.rfile.read(int(self.headers.get("Content-Length", 0)))))

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.store.close()

    def call(self, path: str, challenge_hex: str, body: bytes) -> tuple[int, bytes]:
        if time.monotonic() < self.locked_until:
            return 429, b""
        method = path.removeprefix(NET_CALL_PATH)
        try:
            challenge = bytes.fromhex(challenge_hex)
        except ValueError:
            return 400, b""
        if not path.startswith(NET_CALL_PATH) or method not in NET_METHODS:
            return 404, b""
        if challenge not in self.challenges:
            return self._refuse()
        self.challenges.discard(challenge)
        try:
            header, blob = secure.unpack(secure.unseal(self.key, challenge, method, "req", body))
        except secure.BadMessage:
            return self._refuse()
        self.failures = 0
        self.calls.append(method)
        reply, data = self._handle(method, blob)
        return 200, secure.seal(self.key, challenge, method, "rep", secure.pack(reply, data))

    def _refuse(self) -> tuple[int, bytes]:
        self.failures += 1
        if self.failures >= NET_MAX_FAILURES:
            self.locked_until = time.monotonic() + NET_LOCK_S
            self.failures = 0
        return 403, b""

    def _handle(self, method: str, blob: bytes) -> tuple[dict, bytes]:
        reply = {"sync": self.sync_version, "ok": True, "message": "", "warnings": [], "changed": False, "app": "test"}
        data = b""
        if method in self.fail:
            reply.update(ok=False, message=self.fail[method])
        elif method == "hello":
            reply["message"] = "Glucofi Android de test prête à synchroniser."
        else:
            with tempfile.TemporaryDirectory() as tmp:
                store = Store(self.store.path)  # connexion propre au thread du serveur
                try:
                    if method == "export":
                        data = store.export_to(Path(tmp) / "t.db").read_bytes()
                        reply["message"] = "Base exportée."
                    else:
                        source = Path(tmp) / "pc.db"
                        source.write_bytes(blob)
                        try:
                            summary = store.merge_from(source)
                            message = f"Fusion de {summary.source} faite." if summary.changed else f"Rien de nouveau dans {summary.source}."
                            reply.update(changed=summary.changed, message=message, warnings=list(summary.warnings))
                        except MergeRefused as exc:
                            reply.update(ok=False, message=str(exc))
                finally:
                    store.close()
        return reply, data
