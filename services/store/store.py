"""Stockage local SQLite : mesures, changements de dose, réglages, journal d'imports."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime, time
from pathlib import Path
from typing import Iterable

from contracts import (
    NOTE_MAX_CHARS,
    PROTOCOL_FIELDS,
    ClockAction,
    DoseChange,
    DoseRule,
    DosingSettings,
    Meal,
    MeterClock,
    MeterInfo,
    NoteTag,
    Reading,
    ReadingNote,
    SegmentCount,
    local_epoch,
)

log = logging.getLogger("glucofi.store")

SCHEMA_VERSION = 4

READINGS_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id INTEGER PRIMARY KEY,
    epoch INTEGER NOT NULL,
    device_time TEXT NOT NULL,
    mg_dl INTEGER NOT NULL,
    device_id INTEGER,
    source TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    meal TEXT,
    meter_serial TEXT,
    UNIQUE (device_time, mg_dl)
);
"""

SCHEMA = READINGS_TABLE.format(name="readings") + """
CREATE INDEX IF NOT EXISTS readings_time ON readings (device_time);
CREATE TABLE IF NOT EXISTS dose_changes (
    id INTEGER PRIMARY KEY,
    effective TEXT NOT NULL,
    morning_ui INTEGER NOT NULL CHECK (morning_ui >= 0),
    evening_ui INTEGER NOT NULL CHECK (evening_ui >= 0),
    rule TEXT NOT NULL,
    evidence TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    excluded TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS reading_notes (
    device_time TEXT NOT NULL,
    mg_dl INTEGER NOT NULL,
    tags TEXT NOT NULL,
    text TEXT NOT NULL,
    exclude_from_dosing INTEGER NOT NULL CHECK (exclude_from_dosing IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (device_time, mg_dl)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    source TEXT NOT NULL,
    received INTEGER NOT NULL,
    added INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    meter_serial TEXT,
    clock_offset_s INTEGER,
    clock_action TEXT,
    glucose_announced INTEGER,
    markers_added INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meters (
    serial TEXT PRIMARY KEY,
    manufacturer TEXT NOT NULL,
    model TEXT NOT NULL,
    firmware TEXT NOT NULL,
    hardware TEXT NOT NULL,
    software TEXT NOT NULL,
    system_id TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
"""

# colonnes ajoutées en v3 (ALTER TABLE : les bases v2 gardent leurs lignes telles quelles)
V3_COLUMNS = {
    "readings": ("meal TEXT", "meter_serial TEXT"),
    "imports": (
        "meter_serial TEXT",
        "clock_offset_s INTEGER",
        "clock_action TEXT",
        "glucose_announced INTEGER",
        "markers_added INTEGER NOT NULL DEFAULT 0",
    ),
}
# v4 : table reading_notes (créée par SCHEMA) et mesures écartées de chaque dose validée
V4_COLUMNS = {"dose_changes": ("excluded TEXT NOT NULL DEFAULT '[]'",)}


def _note(tags: str, text: str, exclude: int) -> ReadingNote:
    """Note relue de la base ; une étiquette inconnue (version plus récente de Glucofi) est ignorée.

    Une note devenue invalide (motif perdu avec l'étiquette inconnue) n'écarte plus la mesure.
    """
    known = {tag.value for tag in NoteTag}
    chosen = tuple(NoteTag(t) for t in json.loads(tags) if t in known)
    try:
        return ReadingNote(chosen, text, bool(exclude))
    except ValueError:
        log.warning("note invalide relue de la base, mesure non écartée : %s %r", tags, text[:40])
        return ReadingNote(chosen, text[:NOTE_MAX_CHARS], False)


def default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "glucofi"


@dataclass(frozen=True)
class Migration:
    from_version: int
    to_version: int
    readings_before: int
    readings_after: int
    epochs_fixed: int
    backup: Path | None


@dataclass(frozen=True)
class ImportSummary:
    source: str
    received: int
    added: int
    rejected: int
    at: datetime
    markers_added: int = 0
    meter_serial: str | None = None
    clock_offset_s: int | None = None
    clock_action: ClockAction | None = None
    glucose_announced: int | None = None

    @property
    def duplicates(self) -> int:
        return self.received - self.added


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("PRAGMA foreign_keys = ON")
        version = self._schema_version()
        self.db.executescript(SCHEMA)
        self.migrations: list[Migration] = []
        if version is None:
            self.db.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
            self.db.commit()
        elif version < SCHEMA_VERSION:
            backup = self._backup(version)
            if version < 2:
                self.migrations.append(self._migrate_to_v2(backup))
            if version < 3:
                self.migrations.append(self._migrate_to_v3(backup))
            self.migrations.append(self._migrate_to_v4(backup))

    @property
    def last_migration(self) -> "Migration | None":
        """Première étape de la dernière ouverture (v1 -> v2, v2 -> v3...), None si la base était à jour."""
        return self.migrations[0] if self.migrations else None

    def _backup(self, version: int) -> Path | None:
        """Copie de la base avant migration : glucofi.db.v<version>-AAAAMMJJ-HHMMSS.bak."""
        if str(self.path) == ":memory:":
            return None
        backup = self.path.with_name(f"{self.path.name}.v{version}-{datetime.now():%Y%m%d-%H%M%S}.bak")
        with sqlite3.connect(str(backup)) as target:
            self.db.backup(target)
        target.close()
        return backup

    def _schema_version(self) -> int | None:
        has_settings = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'settings'"
        ).fetchone()
        if not has_settings:
            return None
        row = self.db.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
        return int(row[0]) if row else None

    def _migrate_to_v2(self, backup: Path | None) -> "Migration":
        """v1 -> v2 : epoch recalculé avec l'heure d'été, mesures dédoublonnées sur (device_time, mg_dl).

        Les bases v1 ont des epochs calculés en heure d'hiver toute l'année (ancien accuchek) :
        sans migration, les mesures d'été relues avec accuchek corrigé seraient importées en double.
        Une copie de la base est faite avant (`glucofi.db.v1-AAAAMMJJ-HHMMSS.bak`).
        """
        self.db.create_function("local_epoch", 1, lambda t: local_epoch(datetime.fromisoformat(t)), deterministic=True)
        with self.db:
            before = self._count()
            changed = self.db.execute(
                "SELECT COUNT(*) FROM readings WHERE epoch != local_epoch(device_time)"
            ).fetchone()[0]
            self.db.execute(READINGS_TABLE.format(name="readings_v2"))
            self.db.execute(
                "INSERT OR IGNORE INTO readings_v2 (id, epoch, device_time, mg_dl, device_id, source, imported_at)"
                " SELECT id, local_epoch(device_time), device_time, mg_dl, device_id, source, imported_at"
                " FROM readings ORDER BY id"
            )
            self.db.execute("DROP TABLE readings")
            self.db.execute("ALTER TABLE readings_v2 RENAME TO readings")
            self.db.execute("CREATE INDEX IF NOT EXISTS readings_time ON readings (device_time)")
            self.db.execute("UPDATE settings SET value = '2' WHERE key = 'schema_version'")
            after = self._count()
        migration = Migration(1, 2, before, after, changed, backup)
        log.info(
            "base migrée v1 -> v2 : %s mesures, %s epochs corrigés, %s doublons retirés, sauvegarde %s",
            before, changed, before - after, backup,
        )
        return migration

    def _migrate_to_v3(self, backup: Path | None) -> "Migration":
        """v2 -> v3 : marqueur repas et numéro de série par mesure, table meters, détail des imports.

        Colonnes ajoutées vides : les marqueurs des mesures déjà en base arrivent au prochain import du lecteur.
        """
        with self.db:
            before = self._count()
            self._add_columns(V3_COLUMNS)
            self.db.execute("UPDATE settings SET value = '3' WHERE key = 'schema_version'")
            after = self._count()
        log.info("base migrée v2 -> v3 : %s mesures, sauvegarde %s", after, backup)
        return Migration(2, 3, before, after, 0, backup)

    def _migrate_to_v4(self, backup: Path | None) -> "Migration":
        """v3 -> v4 : notes sur les mesures (table reading_notes) et mesures écartées de chaque dose validée.

        Aucune ligne modifiée : les doses déjà validées n'avaient aucune mesure écartée.
        """
        with self.db:
            before = self._count()
            self._add_columns(V4_COLUMNS)
            self.db.execute("UPDATE settings SET value = '4' WHERE key = 'schema_version'")
            after = self._count()
        log.info("base migrée v3 -> v4 : %s mesures, sauvegarde %s", after, backup)
        return Migration(3, 4, before, after, 0, backup)

    def _add_columns(self, tables: dict[str, tuple[str, ...]]) -> None:
        for table, columns in tables.items():
            existing = {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column.split()[0] not in existing:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")

    @classmethod
    def open_default(cls) -> "Store":
        return cls(default_data_dir() / "glucofi.db")

    def close(self) -> None:
        self.db.close()

    # Mesures

    def import_readings(
        self,
        readings: Iterable[Reading],
        source: str,
        rejected: int = 0,
        meter: MeterInfo | None = None,
        clock: MeterClock | None = None,
        glucose: SegmentCount | None = None,
    ) -> ImportSummary:
        """Ajoute les nouvelles mesures ; pour celles déjà en base, prend le marqueur repas du lecteur s'il en a un.

        Un import sans marqueur (fichier ancien format) n'efface jamais un marqueur déjà connu.
        """
        readings = list(readings)
        now = datetime.now().replace(microsecond=0)
        serial = meter.serial if meter is not None and meter.serial else None
        with self.db:
            before = self._count()
            markers_before = self._count_markers()
            self.db.executemany(
                "INSERT INTO readings (epoch, device_time, mg_dl, device_id, source, imported_at, meal, meter_serial)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (device_time, mg_dl) DO UPDATE SET"
                " meal = COALESCE(excluded.meal, readings.meal),"
                " meter_serial = COALESCE(readings.meter_serial, excluded.meter_serial)",
                [
                    (
                        r.epoch, r.device_time.isoformat(), r.mg_dl, r.device_id, source, now.isoformat(),
                        r.meal.value if r.meal is not None else None, serial,
                    )
                    for r in readings
                ],
            )
            added = self._count() - before
            markers_added = self._count_markers() - markers_before
            if serial is not None:
                self._remember_meter(meter, now)
            self.db.execute(
                "INSERT INTO imports (at, source, received, added, rejected, meter_serial, clock_offset_s, clock_action,"
                " glucose_announced, markers_added) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now.isoformat(), source, len(readings), added, rejected, serial,
                    clock.offset_s if clock is not None else None,
                    clock.action.value if clock is not None else None,
                    glucose.announced if glucose is not None else None,
                    markers_added,
                ),
            )
        return ImportSummary(
            source, len(readings), added, rejected, now, markers_added, serial,
            clock.offset_s if clock is not None else None,
            clock.action if clock is not None else None,
            glucose.announced if glucose is not None else None,
        )

    def _count_markers(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM readings WHERE meal IS NOT NULL").fetchone()[0]

    def _remember_meter(self, meter: MeterInfo, now: datetime) -> None:
        self.db.execute(
            "INSERT INTO meters (serial, manufacturer, model, firmware, hardware, software, system_id, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (serial) DO UPDATE SET manufacturer = excluded.manufacturer, model = excluded.model,"
            " firmware = excluded.firmware, hardware = excluded.hardware, software = excluded.software,"
            " system_id = excluded.system_id, last_seen = excluded.last_seen",
            (
                meter.serial, meter.manufacturer, meter.model, meter.firmware, meter.hardware, meter.software,
                meter.system_id, now.isoformat(), now.isoformat(),
            ),
        )

    def meters(self) -> list[tuple[MeterInfo, datetime, datetime]]:
        """Lecteurs déjà importés : identité, première et dernière lecture."""
        rows = self.db.execute(
            "SELECT manufacturer, model, serial, firmware, hardware, software, system_id, first_seen, last_seen"
            " FROM meters ORDER BY last_seen DESC"
        )
        return [
            (MeterInfo(*row[:7]), datetime.fromisoformat(row[7]), datetime.fromisoformat(row[8]))
            for row in rows
        ]

    def count_markers(self) -> int:
        return self._count_markers()

    def _count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM readings").fetchone()[0]

    def count_readings(self) -> int:
        return self._count()

    def readings(self, since: datetime | None = None, until: datetime | None = None) -> list[Reading]:
        """Mesures avec leur marqueur et leur note (Reading.note)."""
        query = (
            "SELECT r.device_time, r.mg_dl, r.epoch, r.device_id, r.meal, r.meter_serial, n.tags, n.text, n.exclude_from_dosing"
            " FROM readings r LEFT JOIN reading_notes n ON n.device_time = r.device_time AND n.mg_dl = r.mg_dl WHERE 1=1"
        )
        args: list[str] = []
        if since is not None:
            query += " AND r.device_time >= ?"
            args.append(since.isoformat())
        if until is not None:
            query += " AND r.device_time < ?"
            args.append(until.isoformat())
        query += " ORDER BY r.device_time, r.epoch"
        return [
            Reading(
                device_time=datetime.fromisoformat(t),
                mg_dl=mg,
                epoch=epoch,
                device_id=dev_id,
                meal=Meal(meal) if meal is not None else None,
                meter_serial=serial,
                note=None if tags is None else _note(tags, text, exclude),
            )
            for t, mg, epoch, dev_id, meal, serial, tags, text, exclude in self.db.execute(query, args)
        ]

    # Notes

    def set_note(self, reading: Reading, note: ReadingNote | None) -> None:
        """Note de la mesure (device_time, mg_dl) ; une note vide ou None l'efface.

        Les notes vivent à côté des mesures : un import du lecteur ne les touche jamais.
        """
        key = (reading.device_time.isoformat(), reading.mg_dl)
        with self.db:
            known = self.db.execute("SELECT 1 FROM readings WHERE device_time = ? AND mg_dl = ?", key).fetchone()
            if not known:
                raise ValueError(f"mesure inconnue : {reading.device_time:%d/%m/%Y %H:%M}, {reading.mg_dl} mg/dL")
            if note is None or note.empty:
                self.db.execute("DELETE FROM reading_notes WHERE device_time = ? AND mg_dl = ?", key)
                return
            self.db.execute(
                "INSERT INTO reading_notes (device_time, mg_dl, tags, text, exclude_from_dosing, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (device_time, mg_dl) DO UPDATE SET tags = excluded.tags, text = excluded.text,"
                " exclude_from_dosing = excluded.exclude_from_dosing, updated_at = excluded.updated_at",
                (
                    *key,
                    json.dumps([tag.value for tag in note.tags]),
                    note.text,
                    int(note.exclude_from_dosing),
                    datetime.now().replace(microsecond=0).isoformat(),
                ),
            )

    def count_notes(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM reading_notes").fetchone()[0]

    def last_import(self) -> ImportSummary | None:
        row = self.db.execute(
            "SELECT source, received, added, rejected, at, markers_added, meter_serial, clock_offset_s, clock_action,"
            " glucose_announced FROM imports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return ImportSummary(
            row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5], row[6], row[7],
            ClockAction(row[8]) if row[8] is not None else None, row[9],
        )

    # Doses

    def add_dose_change(self, change: DoseChange) -> DoseChange:
        if change.morning_ui < 0 or change.evening_ui < 0:
            raise ValueError("une dose ne peut pas être négative")
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO dose_changes (effective, morning_ui, evening_ui, rule, evidence, note, created_at, excluded)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    change.effective.isoformat(),
                    change.morning_ui,
                    change.evening_ui,
                    change.rule.value,
                    json.dumps(list(change.evidence), ensure_ascii=False),
                    change.note,
                    datetime.now().replace(microsecond=0).isoformat(),
                    json.dumps(list(change.excluded), ensure_ascii=False),
                ),
            )
        return replace(change, id=cursor.lastrowid)

    def dose_changes(self) -> list[DoseChange]:
        rows = self.db.execute(
            "SELECT id, effective, morning_ui, evening_ui, rule, evidence, note, excluded FROM dose_changes"
            " ORDER BY effective, id"
        )
        return [
            DoseChange(
                effective=datetime.fromisoformat(effective),
                morning_ui=morning,
                evening_ui=evening,
                rule=DoseRule(rule),
                evidence=tuple(json.loads(evidence)),
                note=note,
                id=row_id,
                excluded=tuple(json.loads(excluded)),
            )
            for row_id, effective, morning, evening, rule, evidence, note, excluded in rows
        ]

    def current_dose(self) -> DoseChange | None:
        changes = self.dose_changes()
        return changes[-1] if changes else None

    # Réglages

    def get_setting(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def dosing_draft(self) -> dict:
        """Réglages enregistrés, même incomplets (pré-remplissage du formulaire du protocole)."""
        values = dict(self.get_setting("dosing") or {})
        for key in ("morning_start", "morning_end"):
            if key in values:
                values[key] = time.fromisoformat(values[key])
        known = DosingSettings.__dataclass_fields__.keys()
        return {k: v for k, v in values.items() if k in known}

    def dosing_settings(self) -> DosingSettings | None:
        """Protocole saisi par l'utilisateur ; None tant qu'un champ de l'ordonnance manque."""
        values = self.dosing_draft()
        if any(values.get(k) in (None, "") for k in PROTOCOL_FIELDS):
            return None
        try:
            return DosingSettings(**values)
        except (TypeError, ValueError) as exc:
            log.warning("protocole enregistré invalide, à ressaisir : %s", exc)
            return None

    def save_dosing_settings(self, settings: DosingSettings) -> None:
        values = asdict(settings)
        values["morning_start"] = settings.morning_start.strftime("%H:%M")
        values["morning_end"] = settings.morning_end.strftime("%H:%M")
        self.set_setting("dosing", values)
