"""Stockage local SQLite : mesures, changements de dose, réglages, historique du protocole, journal d'imports."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable

from contracts import (
    NOTE_MAX_CHARS,
    PROTOCOL_FIELDS,
    ClockAction,
    DoseChange,
    DoseRule,
    DoseTarget,
    DosingSettings,
    Injection,
    InjectionState,
    Meal,
    MeterClock,
    MeterInfo,
    NoteTag,
    ProtocolChange,
    Reading,
    ReadingNote,
    SegmentCount,
    local_epoch,
)

log = logging.getLogger("glucofi.store")

SCHEMA_VERSION = 6
SQLITE_HEADER = b"SQLite format 3\x00"
TIME_FIELDS = ("morning_start", "morning_end", "evening_start", "evening_end")
# sauvegardes « avant-fusion » gardées : une par fusion qui a changé quelque chose
MERGE_BACKUPS_KEPT = 20

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
CREATE TABLE IF NOT EXISTS protocol_changes (
    id INTEGER PRIMARY KEY,
    effective TEXT NOT NULL,
    settings TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS injections (
    day TEXT NOT NULL,
    target TEXT NOT NULL CHECK (target IN ('morning', 'evening')),
    state TEXT CHECK (state IS NULL OR state IN ('taken', 'missed')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (day, target)
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


def _is_erased(tags: str, text: str, exclude: int) -> bool:
    """Note effacée : la ligne reste (vide, datée) pour que l'effacement se propage à la fusion."""
    return json.loads(tags) == [] and text == "" and not exclude


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


def settings_json(settings: DosingSettings) -> dict:
    """Protocole tel qu'enregistré (réglage "dosing", historique) : heures en HH:MM, paliers en listes."""
    values = asdict(settings)
    for key in TIME_FIELDS:
        values[key] = getattr(settings, key).strftime("%H:%M")
    return values


def _canonical(values: dict) -> str:
    """Forme comparable d'un protocole : un protocole d'une ancienne version (champs absents) vaut le même
    protocole complété par les valeurs par défaut."""
    try:
        values = settings_json(DosingSettings(**settings_from_json(values)))
    except (TypeError, ValueError):
        pass
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def settings_from_json(values: dict) -> dict:
    """Réglages relus (même incomplets) ; les heures redeviennent des `time`, les champs inconnus sont ignorés."""
    values = dict(values)
    for key in TIME_FIELDS:
        if isinstance(values.get(key), str):
            values[key] = time.fromisoformat(values[key])
    known = DosingSettings.__dataclass_fields__.keys()
    return {k: v for k, v in values.items() if k in known}


def parse_settings(values: dict) -> DosingSettings | None:
    """Protocole complet et valide, sinon None (champ de l'ordonnance manquant ou valeurs incohérentes)."""
    draft = settings_from_json(values)
    if any(draft.get(k) in (None, "") for k in PROTOCOL_FIELDS):
        return None
    try:
        return DosingSettings(**draft)
    except (TypeError, ValueError) as exc:
        log.warning("protocole enregistré invalide, à ressaisir : %s", exc)
        return None


_BACKUP_STAMP = re.compile(r"-(\d{8}-\d{6})(?:-(\d+))?\.bak$")


def _backup_order(path: Path) -> tuple[str, int, str]:
    """Ordre chronologique d'après le nom (…-AAAAMMJJ-HHMMSS[-n].bak), pas d'après la date du fichier."""
    match = _BACKUP_STAMP.search(path.name)
    return (match[1], int(match[2] or 0), path.name) if match else ("", 0, path.name)


class MergeRefused(ValueError):
    """Le fichier à fusionner n'est pas une base Glucofi lisible par cette version."""


def check_glucofi_db(path: Path | str) -> str | None:
    """None si `path` est une base Glucofi lisible par cette version, sinon la raison du refus."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            header = handle.read(len(SQLITE_HEADER))
    except OSError as exc:
        return f"Fichier illisible : {exc.strerror or exc}."
    if header != SQLITE_HEADER:
        return "Ce fichier n'est pas une base Glucofi (glucofi.db)."
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return "La base est abîmée (contrôle d'intégrité SQLite en échec)."
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"settings", "readings"} <= tables:
                return "Ce fichier SQLite n'est pas une base Glucofi (tables manquantes)."
            row = db.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
        finally:
            db.close()
    except sqlite3.DatabaseError as exc:
        return f"Ce fichier n'est pas une base Glucofi lisible ({exc})."
    if row is None:
        return "Ce fichier SQLite n'est pas une base Glucofi (version du schéma absente)."
    version = int(row[0])
    if version > SCHEMA_VERSION:
        return (
            f"La base vient d'une version plus récente de Glucofi (schéma {version}, cette version lit "
            f"jusqu'au {SCHEMA_VERSION}) : mettez Glucofi à jour."
        )
    return None


@dataclass(frozen=True)
class MergeSummary:
    """Ce qu'une fusion a ajouté à la base qui la reçoit."""

    source: str
    readings_added: int
    markers_added: int
    notes_added: int
    notes_updated: int
    doses_added: int
    protocol_versions_added: int
    protocol_changed: bool
    imports_added: int
    patient_name_taken: bool
    warnings: tuple[str, ...]
    backup: Path | None
    injections_added: int = 0
    injections_updated: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.readings_added or self.markers_added or self.notes_added or self.notes_updated
            or self.injections_added or self.injections_updated
            or self.doses_added or self.protocol_versions_added or self.imports_added or self.patient_name_taken
        )


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
            if version < 4:
                self.migrations.append(self._migrate_to_v4(backup))
            if version < 5:
                self.migrations.append(self._migrate_to_v5(backup))
            self.migrations.append(self._migrate_to_v6(backup))

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

    def _migrate_to_v5(self, backup: Path | None) -> "Migration":
        """v4 -> v5 : historique du protocole (table protocol_changes, créée par SCHEMA).

        Le protocole déjà saisi devient la première version, datée du début du protocole (première dose)
        faute de mieux : la base v4 ne savait pas quand il avait été saisi.
        """
        with self.db:
            before = self._count()
            settings = self.dosing_settings()
            seeded = settings is not None and not self.db.execute("SELECT 1 FROM protocol_changes").fetchone()
            if seeded:
                first = self.db.execute("SELECT MIN(effective) FROM dose_changes").fetchone()[0]
                effective = datetime.fromisoformat(first) if first else datetime.now().replace(microsecond=0)
                self._insert_protocol(effective, settings_json(settings), "Protocole saisi avant l'historique")
            self.db.execute("UPDATE settings SET value = '5' WHERE key = 'schema_version'")
            after = self._count()
        log.info("base migrée v4 -> v5 : historique du protocole %s, sauvegarde %s", "amorcé" if seeded else "vide", backup)
        return Migration(4, 5, before, after, 0, backup)

    def _migrate_to_v6(self, backup: Path | None) -> "Migration":
        """v5 -> v6 : journal des injections (table injections, créée par SCHEMA), vide au départ.

        Aucune ligne modifiée : les jours d'avant le journal restent « non renseignés ».
        """
        with self.db:
            before = self._count()
            self.db.execute("UPDATE settings SET value = '6' WHERE key = 'schema_version'")
            after = self._count()
        log.info("base migrée v5 -> v6 : journal des injections, sauvegarde %s", backup)
        return Migration(5, 6, before, after, 0, backup)

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
                note=None if tags is None or _is_erased(tags, text, exclude) else _note(tags, text, exclude),
            )
            for t, mg, epoch, dev_id, meal, serial, tags, text, exclude in self.db.execute(query, args)
        ]

    # Notes

    def set_note(self, reading: Reading, note: ReadingNote | None) -> None:
        """Note de la mesure (device_time, mg_dl) ; une note vide ou None l'efface.

        Les notes vivent à côté des mesures : un import du lecteur ne les touche jamais. Effacer garde une
        note vide datée, pour que la fusion avec un autre appareil propage l'effacement.
        """
        key = (reading.device_time.isoformat(), reading.mg_dl)
        with self.db:
            known = self.db.execute("SELECT 1 FROM readings WHERE device_time = ? AND mg_dl = ?", key).fetchone()
            if not known:
                raise ValueError(f"mesure inconnue : {reading.device_time:%d/%m/%Y %H:%M}, {reading.mg_dl} mg/dL")
            if note is None or note.empty:
                note = ReadingNote()
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
        return sum(
            not _is_erased(*row) for row in self.db.execute("SELECT tags, text, exclude_from_dosing FROM reading_notes")
        )

    # Journal des injections

    def set_injection(self, day: date, target: DoseTarget, state: InjectionState | None) -> None:
        """Dose `target` du jour `day` : prise, non prise, ou None pour revenir à « non renseignée ».

        Effacer garde une ligne vide, datée, pour que la fusion avec un autre appareil propage l'effacement
        (comme pour les notes).
        """
        with self.db:
            self.db.execute(
                "INSERT INTO injections (day, target, state, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (day, target) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                (
                    day.isoformat(), DoseTarget(target).value, state.value if state is not None else None,
                    datetime.now().replace(microsecond=0).isoformat(),
                ),
            )

    def injections(self, since: date | None = None, until: date | None = None) -> list[Injection]:
        """Injections renseignées, du `since` (inclus) au `until` (exclu), dans l'ordre des jours."""
        query = "SELECT day, target, state FROM injections WHERE state IS NOT NULL"
        args: list[str] = []
        if since is not None:
            query += " AND day >= ?"
            args.append(since.isoformat())
        if until is not None:
            query += " AND day < ?"
            args.append(until.isoformat())
        query += " ORDER BY day, target"
        return [
            Injection(date.fromisoformat(day), DoseTarget(target), InjectionState(state))
            for day, target, state in self.db.execute(query, args)
        ]

    def count_injections(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM injections WHERE state IS NOT NULL").fetchone()[0]

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
        return settings_from_json(self.get_setting("dosing") or {})

    def dosing_settings(self) -> DosingSettings | None:
        """Protocole saisi par l'utilisateur ; None tant qu'un champ de l'ordonnance manque."""
        return parse_settings(self.get_setting("dosing") or {})

    def save_dosing_settings(
        self, settings: DosingSettings, note: str = "", effective: datetime | None = None
    ) -> ProtocolChange | None:
        """Protocole en cours ; une version différente de la précédente entre dans l'historique (rendue)."""
        values = settings_json(settings)
        current = self.get_setting("dosing")
        unchanged = current is not None and _canonical(current) == _canonical(values)
        last = self.db.execute("SELECT settings FROM protocol_changes ORDER BY effective DESC, id DESC LIMIT 1").fetchone()
        if unchanged and last is not None and _canonical(json.loads(last[0])) == _canonical(values):
            return None
        effective = effective or datetime.now().replace(second=0, microsecond=0)
        with self.db:
            self.db.execute(
                "INSERT INTO settings (key, value) VALUES ('dosing', ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (json.dumps(values, ensure_ascii=False),),
            )
            row_id = self._insert_protocol(effective, values, note.strip())
        return ProtocolChange(effective, settings, note.strip(), row_id)

    def _insert_protocol(self, effective: datetime, values: dict, note: str, created_at: str | None = None) -> int:
        cursor = self.db.execute(
            "INSERT INTO protocol_changes (effective, settings, note, created_at) VALUES (?, ?, ?, ?)",
            (
                effective.isoformat(), _canonical(values), note,
                created_at or datetime.now().replace(microsecond=0).isoformat(),
            ),
        )
        return cursor.lastrowid

    def protocol_changes(self) -> list[ProtocolChange]:
        """Versions du protocole, de la plus ancienne à la plus récente ; une version illisible est sautée."""
        out = []
        for row_id, effective, values, note in self.db.execute(
            "SELECT id, effective, settings, note FROM protocol_changes ORDER BY effective, id"
        ):
            settings = parse_settings(json.loads(values))
            if settings is None:
                log.warning("version %s du protocole illisible, ignorée dans l'historique", row_id)
                continue
            out.append(ProtocolChange(datetime.fromisoformat(effective), settings, note, row_id))
        return out

    # Fusion et export

    def export_to(self, path: Path | str) -> Path:
        """Copie cohérente de la base (API de sauvegarde SQLite), pour la fusionner sur un autre appareil."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        self._copy_to(path)
        log.info("base exportée : %s (%s mesures)", path, self._count())
        return path

    def merge_from(self, source: Path | str) -> MergeSummary:
        """Ajoute à cette base ce que la base `source` (autre appareil) a en plus ; `source` n'est jamais modifiée.

        Mesures, marqueurs, lecteurs, journal des lectures : réunis sans doublon. Notes et journal des injections : la plus
        récemment modifiée l'emporte. Doses validées et versions du protocole : réunies par date ; la version la plus
        récente du protocole devient le protocole en cours. Le nom du patient n'est repris que s'il manque.
        Une copie de cette base est faite avant : glucofi.db.avant-fusion-AAAAMMJJ-HHMMSS.bak, effacée si la fusion
        n'a rien changé ; seules les MERGE_BACKUPS_KEPT plus récentes sont gardées (synchronisation fréquente).
        """
        source = Path(source)
        if str(self.path) != ":memory:" and source.resolve() == self.path.resolve():
            raise MergeRefused("C'est la base de Glucofi elle-même : choisissez la copie venant de l'autre appareil.")
        problem = check_glucofi_db(source)
        if problem is not None:
            raise MergeRefused(problem)
        with tempfile.TemporaryDirectory(prefix="glucofi-fusion-") as tmp:
            staging = Path(tmp) / "fusion.db"
            reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
            writer = sqlite3.connect(str(staging))
            try:
                reader.backup(writer)
            finally:
                writer.close()
                reader.close()
            other = Store(staging)
            try:
                backup = self._backup_named("avant-fusion")
                summary = self._merge(other, source.name, backup)
            finally:
                other.close()
        if backup is not None and not summary.changed:
            backup.unlink(missing_ok=True)
            summary = replace(summary, backup=None)
        self._prune_backups("avant-fusion", MERGE_BACKUPS_KEPT)
        log.info(
            "fusion de %s : %s mesures, %s marqueurs, %s notes ajoutées et %s mises à jour, %s injections ajoutées "
            "et %s mises à jour, %s doses, %s versions du protocole (protocole en cours %s), %s alertes, sauvegarde %s",
            summary.source, summary.readings_added, summary.markers_added, summary.notes_added, summary.notes_updated,
            summary.injections_added, summary.injections_updated, summary.doses_added, summary.protocol_versions_added, "changé" if summary.protocol_changed else "inchangé",
            len(summary.warnings), summary.backup,
        )
        return summary

    def _prune_backups(self, label: str, keep: int) -> None:
        if str(self.path) == ":memory:":
            return
        backups = sorted(self.path.parent.glob(f"{self.path.name}.{label}-*.bak"), key=_backup_order)
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)
            log.info("ancienne sauvegarde effacée : %s", old.name)

    def _backup_named(self, label: str) -> Path | None:
        if str(self.path) == ":memory:":
            return None
        stamp = f"{self.path.name}.{label}-{datetime.now():%Y%m%d-%H%M%S}"
        backup = self.path.with_name(f"{stamp}.bak")
        counter = 0
        while backup.exists():  # deux fusions dans la même seconde : ne jamais écraser la sauvegarde d'avant
            counter += 1
            backup = self.path.with_name(f"{stamp}-{counter}.bak")
        self._copy_to(backup)
        return backup

    def _copy_to(self, path: Path) -> None:
        # sqlite3 backup() boucle sans fin si la connexion source a une écriture non validée.
        if self.db.in_transaction:
            raise RuntimeError("écriture non validée en cours sur la base : copie impossible")
        target = sqlite3.connect(str(path))
        try:
            self.db.backup(target)
        finally:
            target.close()

    def _merge(self, other: "Store", name: str, backup: Path | None) -> MergeSummary:
        warnings: list[str] = []
        with self.db:
            before, markers_before = self._count(), self._count_markers()
            self.db.executemany(
                "INSERT INTO readings (epoch, device_time, mg_dl, device_id, source, imported_at, meal, meter_serial)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (device_time, mg_dl) DO UPDATE SET"
                " meal = COALESCE(readings.meal, excluded.meal),"
                " meter_serial = COALESCE(readings.meter_serial, excluded.meter_serial)",
                other.db.execute(
                    "SELECT epoch, device_time, mg_dl, device_id, source, imported_at, meal, meter_serial FROM readings"
                ).fetchall(),
            )
            readings_added = self._count() - before
            markers_added = self._count_markers() - markers_before

            self.db.executemany(
                "INSERT INTO meters (serial, manufacturer, model, firmware, hardware, software, system_id, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (serial) DO UPDATE SET"
                " manufacturer = CASE WHEN excluded.last_seen > meters.last_seen THEN excluded.manufacturer ELSE meters.manufacturer END,"
                " model = CASE WHEN excluded.last_seen > meters.last_seen THEN excluded.model ELSE meters.model END,"
                " firmware = CASE WHEN excluded.last_seen > meters.last_seen THEN excluded.firmware ELSE meters.firmware END,"
                " hardware = CASE WHEN excluded.last_seen > meters.last_seen THEN excluded.hardware ELSE meters.hardware END,"
                " software = CASE WHEN excluded.last_seen > meters.last_seen THEN excluded.software ELSE meters.software END,"
                " system_id = CASE WHEN excluded.last_seen > meters.last_seen THEN excluded.system_id ELSE meters.system_id END,"
                " first_seen = MIN(meters.first_seen, excluded.first_seen),"
                " last_seen = MAX(meters.last_seen, excluded.last_seen)",
                other.db.execute(
                    "SELECT serial, manufacturer, model, firmware, hardware, software, system_id, first_seen, last_seen FROM meters"
                ).fetchall(),
            )

            notes_added = notes_updated = 0
            for row in other.db.execute(
                "SELECT device_time, mg_dl, tags, text, exclude_from_dosing, updated_at FROM reading_notes"
            ).fetchall():
                mine = self.db.execute(
                    "SELECT updated_at, tags, text, exclude_from_dosing FROM reading_notes WHERE device_time = ? AND mg_dl = ?",
                    row[:2],
                ).fetchone()
                # même seconde sur les deux appareils : départage fixe, pour que les deux bases convergent
                if mine is not None and (mine[0], mine[1:]) >= (row[5], (row[2], row[3], row[4])):
                    continue
                if mine is None and _is_erased(*row[2:5]):
                    continue
                self.db.execute(
                    "INSERT INTO reading_notes (device_time, mg_dl, tags, text, exclude_from_dosing, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (device_time, mg_dl) DO UPDATE SET tags = excluded.tags,"
                    " text = excluded.text, exclude_from_dosing = excluded.exclude_from_dosing, updated_at = excluded.updated_at",
                    row,
                )
                if mine is None:
                    notes_added += 1
                else:
                    notes_updated += 1

            injections_added = injections_updated = 0
            for row in other.db.execute("SELECT day, target, state, updated_at FROM injections").fetchall():
                mine = self.db.execute(
                    "SELECT updated_at, state FROM injections WHERE day = ? AND target = ?", row[:2]
                ).fetchone()
                # même seconde sur les deux appareils : départage fixe (« missed » avant « taken », vide en dernier)
                if mine is not None and (mine[0], mine[1] or "") >= (row[3], row[2] or ""):
                    continue
                if mine is None and row[2] is None:
                    continue
                self.db.execute(
                    "INSERT INTO injections (day, target, state, updated_at) VALUES (?, ?, ?, ?)"
                    " ON CONFLICT (day, target) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                    row,
                )
                if mine is None:
                    injections_added += 1
                else:
                    injections_updated += 1

            columns = "effective, morning_ui, evening_ui, rule, evidence, note, created_at, excluded"
            mine_doses = {tuple(r) for r in self.db.execute("SELECT effective, morning_ui, evening_ui, rule FROM dose_changes")}
            theirs = other.db.execute(f"SELECT {columns} FROM dose_changes ORDER BY effective, id").fetchall()
            their_keys = {tuple(r[:4]) for r in theirs}
            new_doses = [r for r in theirs if tuple(r[:4]) not in mine_doses]
            self.db.executemany(f"INSERT INTO dose_changes ({columns}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", new_doses)
            if new_doses and mine_doses - their_keys:
                warnings.append(
                    "Des doses ont été enregistrées sur les deux appareils depuis la dernière fusion : les deux "
                    "historiques sont réunis par date, la plus récente est la dose en cours. Vérifiez l'historique des doses."
                )

            mine_versions = {
                (effective, _canonical(json.loads(values)))
                for effective, values in self.db.execute("SELECT effective, settings FROM protocol_changes")
            }
            their_versions = other.db.execute(
                "SELECT effective, settings, note, created_at FROM protocol_changes ORDER BY effective, id"
            ).fetchall()
            new_versions = [v for v in their_versions if (v[0], _canonical(json.loads(v[1]))) not in mine_versions]
            for effective, values, note, created_at in new_versions:
                self._insert_protocol(datetime.fromisoformat(effective), json.loads(values), note, created_at)
            protocol_changed = False
            # départage sur le contenu, pas sur l'id : les deux bases doivent élire la même version
            latest = self.db.execute(
                "SELECT settings, effective FROM protocol_changes ORDER BY effective DESC, settings DESC LIMIT 1"
            ).fetchone()
            current = self.get_setting("dosing")
            if latest is not None and (current is None or _canonical(current) != _canonical(json.loads(latest[0]))):
                self.db.execute(
                    "INSERT INTO settings (key, value) VALUES ('dosing', ?)"
                    " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (json.dumps(json.loads(latest[0]), ensure_ascii=False),),
                )
                protocol_changed = True
            their_keys_v = {(v[0], _canonical(json.loads(v[1]))) for v in their_versions}
            if new_versions and mine_versions - their_keys_v:
                warnings.append(
                    "Le protocole a été modifié sur les deux appareils : la version la plus récente "
                    f"(du {datetime.fromisoformat(latest[1]):%d/%m/%Y %H:%M}) est celle qui s'applique. Vérifiez-la."
                )

            mine_imports = {tuple(r) for r in self.db.execute("SELECT at, source, received, added, rejected FROM imports")}
            import_cols = (
                "at, source, received, added, rejected, meter_serial, clock_offset_s, clock_action, glucose_announced, markers_added"
            )
            new_imports = [
                r for r in other.db.execute(f"SELECT {import_cols} FROM imports ORDER BY id").fetchall()
                if tuple(r[:5]) not in mine_imports
            ]
            self.db.executemany(f"INSERT INTO imports ({import_cols}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", new_imports)

            patient_name_taken = False
            theirs_name = other.get_setting("patient_name", "")
            if not self.get_setting("patient_name", "") and theirs_name:
                self.db.execute(
                    "INSERT INTO settings (key, value) VALUES ('patient_name', ?)"
                    " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                    (json.dumps(theirs_name, ensure_ascii=False),),
                )
                patient_name_taken = True
        return MergeSummary(
            name, readings_added, markers_added, notes_added, notes_updated, len(new_doses), len(new_versions),
            protocol_changed, len(new_imports), patient_name_taken, tuple(warnings), backup,
            injections_added, injections_updated,
        )
