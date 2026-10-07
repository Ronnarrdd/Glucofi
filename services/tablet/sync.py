"""Aller-retour PC ↔ tablette : la tablette envoie sa base, le PC la fusionne, puis renvoie la sienne.

Après une synchronisation réussie, les deux bases contiennent la même chose (la fusion réunit tout
sans doublon, dans les deux sens). Une fusion qui ne change rien ne laisse ni sauvegarde ni trace.

TabletLink fait les échanges avec la tablette (adb, sans toucher à la base du PC) ; la fusion et
l'export côté PC se font entre les deux (AppState, sur le thread qui possède la base). sync() enchaîne
le tout sur un seul thread (tests, ligne de commande) ; la fenêtre fait la même chose en trois temps.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from contracts.tablet_sync import PC_FILE, REMOTE_DIR, TABLET_FILE
from services.store import MergeSummary
from services.tablet.adb import Adb, Device, TabletError, pick_device

log = logging.getLogger("glucofi.tablet")


class PcSide(Protocol):
    def merge_db(self, path: Path) -> MergeSummary: ...
    def export_db(self, path: Path) -> Path: ...


@dataclass(frozen=True)
class TabletReply:
    ok: bool
    message: str
    changed: bool = False
    warnings: tuple[str, ...] = ()
    app: str = ""

    @classmethod
    def of(cls, reply: dict) -> "TabletReply":
        return cls(
            bool(reply["ok"]), str(reply["message"]), bool(reply.get("changed", False)),
            tuple(str(w) for w in reply.get("warnings") or ()), str(reply.get("app", "")),
        )


@dataclass(frozen=True)
class SyncResult:
    device: Device
    pc: MergeSummary
    tablet: TabletReply
    warnings: tuple[str, ...] = field(default=())

    @property
    def changed(self) -> bool:
        return self.pc.changed or self.tablet.changed


class TabletLink:
    """Échanges avec une tablette précise ; chaque étape lève TabletError avec un message à montrer."""

    def __init__(self, adb: Adb, device: Device):
        self.adb = adb
        self.device = device

    @classmethod
    def connect(cls, adb: Adb | None = None) -> "TabletLink":
        adb = adb or Adb.locate()
        link = cls(adb, pick_device(adb))
        link.hello()
        return link

    def _call(self, method: str) -> TabletReply:
        reply = TabletReply.of(self.adb.call(self.device.serial, method))
        if not reply.ok:
            raise TabletError(reply.message)
        return reply

    def hello(self) -> TabletReply:
        return self._call("hello")

    def fetch(self, workdir: Path) -> Path:
        """Base de la tablette, copiée dans workdir ; la copie laissée sur la tablette est effacée."""
        self.adb.reset_sync_dir(self.device.serial)
        self._call("export")
        try:
            return self.adb.pull(self.device.serial, f"{REMOTE_DIR}/{TABLET_FILE}", Path(workdir) / TABLET_FILE)
        finally:
            self._cleanup()

    def send(self, pc_db: Path) -> TabletReply:
        """Fusionne la base du PC sur la tablette (qui efface ensuite la copie reçue)."""
        try:
            self.adb.push(self.device.serial, Path(pc_db), f"{REMOTE_DIR}/{PC_FILE}")
            return self._call("merge")
        finally:
            self._cleanup()

    def _cleanup(self) -> None:
        try:
            self._call("cleanup")
        except TabletError as exc:  # rien de grave : l'app efface aussi ces fichiers au prochain appel
            log.warning("nettoyage de la tablette impossible : %s", exc)


def sync(pc: PcSide, link: TabletLink, workdir: Path) -> SyncResult:
    """Synchronisation complète, sur le thread qui possède la base du PC."""
    workdir = Path(workdir)
    tablet_db = link.fetch(workdir)
    summary = pc.merge_db(tablet_db)
    pc_db = pc.export_db(workdir / PC_FILE)
    reply = link.send(pc_db)
    return result(link.device, summary, reply)


def result(device: Device, summary: MergeSummary, reply: TabletReply) -> SyncResult:
    warnings = tuple(f"Sur le PC : {w}" for w in summary.warnings) + tuple(f"Sur la tablette : {w}" for w in reply.warnings)
    log.info(
        "synchronisation avec %s : PC %s, tablette %s, %s alertes",
        device.model or device.serial, "changé" if summary.changed else "inchangé",
        "changée" if reply.changed else "inchangée", len(warnings),
    )
    return SyncResult(device, summary, reply, warnings)
