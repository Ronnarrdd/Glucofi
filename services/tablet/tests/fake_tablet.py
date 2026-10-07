"""Tablette simulée derrière un faux adb : vraie base SQLite, fichiers « distants » dans un dossier local.

Répond comme l'app Android (contracts/tablet_sync.py) : content call --method hello/export/merge/cleanup,
pull et push dans REMOTE_DIR, rm -r -f REMOTE_DIR, pm path, devices -l. Sert aux tests de services/tablet et de la fenêtre.

Propriétaire du dossier d'échange, comme vérifié sur Android 16 : créé par l'app, adb peut y lire et écrire ;
créé par adb (push dans un dossier absent), il appartient à l'uid shell et l'app ne peut plus y écrire.
"""

from __future__ import annotations

import base64
import contextlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from contracts.tablet_sync import AUTHORITY, PACKAGE, PC_FILE, REMOTE_DIR, REPLY_KEY, SYNC_VERSION, TABLET_FILE
from services.store import MergeRefused, Store


def reply_output(reply: dict) -> str:
    encoded = base64.b64encode(json.dumps(reply, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return f"Result: Bundle[{{{REPLY_KEY}={encoded}}}]\n"


class FakeTablet:
    def __init__(self, root: Path, serial: str = "R92X0000TEST", model: str = "SM_X210", state: str = "device"):
        self.root = Path(root)
        self.remote = self.root / "remote"
        self.remote_owner: str | None = None  # None : dossier absent ; "app" ou "shell"
        self.store = Store(self.root / "glucofi.db")
        self.serial, self.model, self.state = serial, model, state
        self.installed = True
        self.sync_version = SYNC_VERSION
        self.provider = True
        self.fail: dict[str, str] = {}
        self.calls: list[str] = []
        self.others: list[tuple[str, str, bool]] = []

    def close(self) -> None:
        self.store.close()

    def local(self, remote: str) -> Path:
        if not remote.startswith(REMOTE_DIR + "/"):
            raise AssertionError(f"chemin hors du dossier de synchronisation : {remote}")
        return self.remote / remote.removeprefix(REMOTE_DIR + "/")

    def files(self) -> list[str]:
        return sorted(p.name for p in self.remote.iterdir()) if self.remote_owner else []

    def leave_folder_created_by_adb(self) -> None:
        """Dossier d'échange laissé par un adb push manuel (ou une ancienne version), avec une vieille copie."""
        self._make_remote("shell")
        (self.remote / PC_FILE).write_bytes(b"ancienne copie")

    def _make_remote(self, owner: str) -> None:
        self.remote.mkdir(parents=True, exist_ok=True)
        self.remote_owner = owner

    def __call__(self, args: Sequence[str], timeout: float) -> subprocess.CompletedProcess:
        args = list(args)[1:]
        if args[:2] == ["devices", "-l"]:
            lines = [f"{self.serial}\t{self.state} usb:1-4 product:x model:{self.model} device:x"]
            lines += [f"{serial}\t{state} model:{model}" for serial, model, _ in self.others for state in ("device",)]
            return self._ok("List of devices attached\n" + "\n".join(lines) + "\n\n")
        if args[:2] != ["-s", self.serial]:
            other = next((o for o in self.others if args[:2] == ["-s", o[0]]), None)
            if other and args[2:5] == ["shell", "pm", "path"]:
                return self._ok("package:/data/app/x/base.apk\n") if other[2] else self._fail("")
            return self._fail(f"adb: device '{args[1]}' not found")
        command = args[2:]
        if command[:3] == ["shell", "pm", "path"]:
            return self._ok("package:/data/app/x/base.apk\n") if self.installed else self._fail("")
        if command[:3] == ["shell", "content", "call"]:
            assert command[3:5] == ["--uri", f"content://{AUTHORITY}"], command
            return self._content(command[command.index("--method") + 1])
        if command[0] == "pull":
            source = self.local(command[1])
            if not source.exists():
                return self._fail(f"adb: error: failed to stat remote object '{command[1]}': No such file or directory")
            shutil.copyfile(source, command[2])
            return self._ok(f"{command[1]}: 1 file pulled.\n")
        if command[0] == "push":
            target = self.local(command[2])
            if self.remote_owner is None:  # adb push crée les dossiers manquants
                self._make_remote("shell")
            shutil.copyfile(command[1], target)
            return self._ok(f"{command[1]}: 1 file pushed.\n")
        if command[:2] == ["shell", "rm"]:
            assert command == ["shell", "rm", "-r", "-f", REMOTE_DIR], command
            shutil.rmtree(self.remote, ignore_errors=True)
            self.remote_owner = None
            return self._ok("")
        raise AssertionError(f"commande adb inattendue : {args}")

    def _content(self, method: str) -> subprocess.CompletedProcess:
        self.calls.append(method)
        if not self.provider:
            return self._ok(f"Error while accessing provider:{AUTHORITY}\njava.lang.IllegalArgumentException: Unknown authority {AUTHORITY}\n")
        reply = {"sync": self.sync_version, "ok": True, "message": "", "warnings": [], "changed": False, "app": "test"}
        if method in self.fail:
            reply.update(ok=False, message=self.fail[method])
        elif method == "hello":
            reply["message"] = f"Glucofi Android, base {PACKAGE}"
        elif method == "export":
            if self.remote_owner == "shell":
                reply.update(ok=False, message=f"Erreur sur la tablette : PermissionError: [Errno 13] Permission denied: '{REMOTE_DIR}/{TABLET_FILE}'")
                return self._ok(reply_output(reply))
            if self.remote_owner is None:
                self._make_remote("app")
            with self._own_connection() as store:
                store.export_to(self.remote / TABLET_FILE)
            reply["message"] = "Base exportée."
        elif method == "merge":
            source = self.remote / PC_FILE
            try:
                with self._own_connection() as store:
                    summary = store.merge_from(source)
                message = f"Fusion de {summary.source} faite." if summary.changed else f"Rien de nouveau dans {summary.source}."
                reply.update(changed=summary.changed, message=message, warnings=list(summary.warnings))
            except MergeRefused as exc:
                reply.update(ok=False, message=str(exc))
            finally:
                source.unlink(missing_ok=True)
        elif method == "cleanup":
            for name in (TABLET_FILE, PC_FILE):
                if self.remote_owner == "app":  # l'app ne peut pas toucher un dossier de shell
                    (self.remote / name).unlink(missing_ok=True)
        else:
            raise AssertionError(f"méthode inconnue : {method}")
        return self._ok(reply_output(reply))

    @contextlib.contextmanager
    def _own_connection(self):
        """Connexion propre à l'appel : adb tourne sur un autre thread que le test, comme l'app sur la tablette."""
        store = Store(self.store.path)
        try:
            yield store
        finally:
            store.close()

    @staticmethod
    def _ok(stdout: str) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], 0, stdout, "")

    @staticmethod
    def _fail(stderr: str) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], 1, "", stderr)
