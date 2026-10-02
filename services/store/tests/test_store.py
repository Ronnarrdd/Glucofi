import os
import sqlite3
import tempfile
import time as _time
import unittest
from datetime import datetime, time
from pathlib import Path

from contracts import (
    ClockAction,
    DoseChange,
    DoseRule,
    DosingSettings,
    Meal,
    MeterClock,
    MeterInfo,
    Reading,
    SegmentCount,
    local_epoch,
)
from services.store import Store


def reading(ts: str, mg: int, meal: Meal | None = None) -> Reading:
    t = datetime.fromisoformat(ts)
    return Reading(t, mg, int(t.timestamp()), meal=meal)


METER = MeterInfo("Roche", "925", "92500000042", "v1.9.6", "G", "", "0060190000000042")
CLOCK = MeterClock(datetime(2026, 10, 1, 21, 6, 58), datetime(2026, 10, 1, 20, 42, 52), 1446, True, True, ClockAction.SET)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_import_is_idempotent(self):
        batch = [reading("2026-09-01T08:00", 120), reading("2026-09-01T19:00", 160)]
        first = self.store.import_readings(batch, "lecteur")
        second = self.store.import_readings(batch + [reading("2026-09-02T08:00", 90)], "lecteur", rejected=1)
        self.assertEqual((first.received, first.added), (2, 2))
        self.assertEqual((second.received, second.added, second.duplicates, second.rejected), (3, 1, 2, 1))
        self.assertEqual(self.store.count_readings(), 3)
        self.assertEqual(self.store.last_import().added, 1)

    def test_readings_filter_and_order(self):
        self.store.import_readings(
            [reading("2026-09-03T08:00", 100), reading("2026-09-01T08:00", 110), reading("2026-09-02T08:00", 120)],
            "fichier",
        )
        got = self.store.readings(since=datetime(2026, 9, 2), until=datetime(2026, 9, 3))
        self.assertEqual([r.mg_dl for r in got], [120])
        self.assertEqual([r.mg_dl for r in self.store.readings()], [110, 120, 100])

    def test_dose_changes_roundtrip(self):
        self.assertIsNone(self.store.current_dose())
        start = self.store.add_dose_change(DoseChange(datetime(2026, 9, 1, 12), 8, 4, DoseRule.START))
        self.store.add_dose_change(
            DoseChange(datetime(2026, 9, 5, 20), 8, 6, DoseRule.INCREASE_HIGH_MORNINGS, ("a", "b"), "note")
        )
        self.assertIsNotNone(start.id)
        current = self.store.current_dose()
        self.assertEqual((current.evening_ui, current.rule, current.evidence), (6, DoseRule.INCREASE_HIGH_MORNINGS, ("a", "b")))
        self.assertEqual(len(self.store.dose_changes()), 2)

    def test_negative_dose_refused(self):
        with self.assertRaises(ValueError):
            self.store.add_dose_change(DoseChange(datetime(2026, 9, 1), 8, -2, DoseRule.MANUAL))

    def test_dosing_settings_roundtrip(self):
        self.assertIsNone(self.store.dosing_settings())
        self.assertEqual(self.store.dosing_draft(), {})
        custom = DosingSettings(
            insulin="Insuline test", low_g_l=0.9, high_g_l=1.4, step_ui=1, high_streak_days=2,
            morning_start=time(6, 0), morning_end=time(10, 30),
        )
        self.store.save_dosing_settings(custom)
        self.assertEqual(self.store.dosing_settings(), custom)

    def test_settings_saved_before_the_protocol_was_required(self):
        """Préférences enregistrées par une ancienne version : pas d'insuline, donc protocole à ressaisir."""
        self.store.set_setting("dosing", {"morning_start": "06:00", "morning_end": "11:00", "low_g_l": 0.9, "high_g_l": 1.4, "step_ui": 1})
        self.assertIsNone(self.store.dosing_settings())
        draft = self.store.dosing_draft()
        self.assertEqual((draft["morning_start"], draft["low_g_l"], draft["step_ui"]), (time(6, 0), 0.9, 1))

    def test_invalid_saved_protocol_is_asked_again(self):
        self.store.set_setting("dosing", {"insulin": "X", "low_g_l": 1.5, "high_g_l": 1.0, "step_ui": 2, "high_streak_days": 3})
        with self.assertLogs("glucofi.store", "WARNING"):
            self.assertIsNone(self.store.dosing_settings())

    def test_persists_on_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "glucofi.db"
            store = Store(path)
            store.import_readings([reading("2026-09-01T08:00", 120)], "lecteur")
            store.set_setting("patient_name", "Jean")
            store.close()
            reopened = Store(path)
            self.assertEqual(reopened.count_readings(), 1)
            self.assertEqual(reopened.get_setting("patient_name"), "Jean")
            reopened.close()



V1_SCHEMA = """
CREATE TABLE readings (
    id INTEGER PRIMARY KEY, epoch INTEGER NOT NULL, device_time TEXT NOT NULL, mg_dl INTEGER NOT NULL,
    device_id INTEGER, source TEXT NOT NULL, imported_at TEXT NOT NULL, UNIQUE (epoch, mg_dl)
);
CREATE INDEX readings_time ON readings (device_time);
CREATE TABLE dose_changes (
    id INTEGER PRIMARY KEY, effective TEXT NOT NULL, morning_ui INTEGER NOT NULL, evening_ui INTEGER NOT NULL,
    rule TEXT NOT NULL, evidence TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE imports (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL,
    received INTEGER NOT NULL, added INTEGER NOT NULL, rejected INTEGER NOT NULL
);
INSERT INTO settings VALUES ('schema_version', '1'), ('patient_name', '"Test"');
"""

# epochs écrits par l'ancien accuchek : heure d'hiver toute l'année (+3600 s en été)
V1_ROWS = [
    (1610694000, "2021-01-15T08:00:00", 133),   # hiver : déjà juste
    (1782974160, "2026-07-02T07:36:00", 120),   # été : +1 h
    (1782993540, "2026-07-02T12:59:00", 180),   # été : +1 h
]


class TimezoneParis:
    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "Europe/Paris"
        _time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        _time.tzset()


class MigrationV2Test(TimezoneParis, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "glucofi.db"
        db = sqlite3.connect(self.path)
        db.executescript(V1_SCHEMA)
        db.executemany(
            "INSERT INTO readings (epoch, device_time, mg_dl, device_id, source, imported_at)"
            " VALUES (?, ?, ?, NULL, 'lecteur', '2026-09-30T21:31:46')",
            V1_ROWS,
        )
        db.commit()
        db.close()

    def open(self) -> Store:
        store = Store(self.path)
        self.addCleanup(store.close)
        return store

    def test_summer_readings_reread_after_fix_are_not_duplicated(self):
        store = self.open()
        fixed = [Reading(datetime.fromisoformat(t), mg, local_epoch(datetime.fromisoformat(t))) for _, t, mg in V1_ROWS]
        summary = store.import_readings(fixed, "lecteur")
        self.assertEqual((summary.received, summary.added), (3, 0))
        self.assertEqual(store.count_readings(), 3)

    def test_epochs_are_recomputed_and_backup_kept(self):
        store = self.open()
        migration = store.last_migration
        self.assertEqual((migration.readings_before, migration.readings_after, migration.epochs_fixed), (3, 3, 2))
        self.assertEqual([r.epoch for r in store.readings()], [1610694000, 1782970560, 1782989940])
        self.assertEqual(store.get_setting("schema_version"), 3)
        self.assertEqual([(m.from_version, m.to_version) for m in store.migrations], [(1, 2), (2, 3)])
        self.assertEqual(store.get_setting("patient_name"), "Test")
        backup = sqlite3.connect(migration.backup)
        self.addCleanup(backup.close)
        self.assertEqual(
            backup.execute("SELECT epoch, device_time, mg_dl FROM readings ORDER BY id").fetchall(), V1_ROWS
        )

    def test_migration_runs_once(self):
        self.open().close()
        store = Store(self.path)
        self.addCleanup(store.close)
        self.assertIsNone(store.last_migration)
        self.assertEqual(len(list(Path(self.dir.name).glob("*.bak"))), 1)

    def test_new_database_starts_at_v3(self):
        store = Store(Path(self.dir.name) / "neuve.db")
        self.addCleanup(store.close)
        self.assertEqual(store.get_setting("schema_version"), 3)
        self.assertIsNone(store.last_migration)
        t = datetime(2026, 7, 2, 7, 36)
        store.import_readings([Reading(t, 120, 1782974160)], "lecteur")
        self.assertEqual(store.import_readings([Reading(t, 120, 1782970560)], "lecteur").added, 0)


class MealMarkersTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_markers_roundtrip(self):
        summary = self.store.import_readings(
            [reading("2026-09-01T07:30", 110, Meal.FASTING), reading("2026-09-01T13:00", 180, Meal.AFTER_MEAL),
             reading("2026-09-01T19:00", 150)],
            "lecteur",
        )
        self.assertEqual((summary.added, summary.markers_added), (3, 2))
        self.assertEqual([r.meal for r in self.store.readings()], [Meal.FASTING, Meal.AFTER_MEAL, None])

    def test_reimport_fills_markers_of_known_readings(self):
        """Mesures importées avant le support des marqueurs : le prochain import les complète, sans doublon."""
        self.store.import_readings([reading("2026-09-01T07:30", 110), reading("2026-09-01T13:00", 180)], "lecteur")
        summary = self.store.import_readings(
            [reading("2026-09-01T07:30", 110, Meal.FASTING), reading("2026-09-01T13:00", 180, Meal.AFTER_MEAL)],
            "lecteur",
        )
        self.assertEqual((summary.added, summary.duplicates, summary.markers_added), (0, 2, 2))
        self.assertEqual(self.store.count_readings(), 2)
        self.assertEqual(self.store.count_markers(), 2)

    def test_import_without_marker_keeps_known_marker(self):
        self.store.import_readings([reading("2026-09-01T07:30", 110, Meal.FASTING)], "lecteur")
        summary = self.store.import_readings([reading("2026-09-01T07:30", 110)], "fichier:ancien.json")
        self.assertEqual(summary.markers_added, 0)
        self.assertEqual(self.store.readings()[0].meal, Meal.FASTING)

    def test_meter_marker_wins_over_stored_one(self):
        self.store.import_readings([reading("2026-09-01T07:30", 110, Meal.BEFORE_MEAL)], "lecteur")
        self.store.import_readings([reading("2026-09-01T07:30", 110, Meal.FASTING)], "lecteur")
        self.assertEqual(self.store.readings()[0].meal, Meal.FASTING)

    def test_meter_and_clock_are_journaled(self):
        summary = self.store.import_readings(
            [reading("2026-09-01T07:30", 110)], "lecteur", meter=METER, clock=CLOCK, glucose=SegmentCount(1, 1)
        )
        self.assertEqual((summary.meter_serial, summary.clock_offset_s, summary.clock_action), ("92500000042", 1446, ClockAction.SET))
        self.assertEqual(self.store.last_import(), summary)
        meters = self.store.meters()
        self.assertEqual([m for m, _, _ in meters], [METER])
        serial = self.store.db.execute("SELECT meter_serial FROM readings").fetchone()[0]
        self.assertEqual(serial, "92500000042")
        self.assertEqual(self.store.readings()[0].meter_serial, "92500000042")

    def test_meter_identity_is_updated(self):
        self.store.import_readings([], "lecteur", meter=METER)
        newer = MeterInfo("Roche", "925", "92500000042", "v2.0.0", "G", "", "0060190000000042")
        self.store.import_readings([], "lecteur", meter=newer)
        self.assertEqual([m.firmware for m, _, _ in self.store.meters()], ["v2.0.0"])

    def test_file_import_has_no_meter(self):
        summary = self.store.import_readings([reading("2026-09-01T07:30", 110)], "fichier:x.json")
        self.assertIsNone(summary.meter_serial)
        self.assertIsNone(self.store.last_import().clock_action)
        self.assertEqual(self.store.meters(), [])
        self.assertIsNone(self.store.readings()[0].meter_serial)


V2_SCHEMA = """
CREATE TABLE readings (
    id INTEGER PRIMARY KEY, epoch INTEGER NOT NULL, device_time TEXT NOT NULL, mg_dl INTEGER NOT NULL,
    device_id INTEGER, source TEXT NOT NULL, imported_at TEXT NOT NULL, UNIQUE (device_time, mg_dl)
);
CREATE INDEX readings_time ON readings (device_time);
CREATE TABLE dose_changes (
    id INTEGER PRIMARY KEY, effective TEXT NOT NULL, morning_ui INTEGER NOT NULL, evening_ui INTEGER NOT NULL,
    rule TEXT NOT NULL, evidence TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE imports (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, source TEXT NOT NULL,
    received INTEGER NOT NULL, added INTEGER NOT NULL, rejected INTEGER NOT NULL
);
INSERT INTO settings VALUES ('schema_version', '2'), ('patient_name', '"Test"');
INSERT INTO readings (epoch, device_time, mg_dl, device_id, source, imported_at)
    VALUES (1782970560, '2026-07-02T07:36:00', 120, 4, 'lecteur', '2026-09-30T21:31:46');
INSERT INTO imports (at, source, received, added, rejected) VALUES ('2026-09-30T21:31:46', 'lecteur', 1, 1, 0);
"""


class MigrationV3Test(TimezoneParis, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "glucofi.db"
        db = sqlite3.connect(self.path)
        db.executescript(V2_SCHEMA)
        db.close()

    def test_v2_database_gets_marker_columns_and_keeps_rows(self):
        store = Store(self.path)
        self.addCleanup(store.close)
        self.assertEqual([(m.from_version, m.to_version) for m in store.migrations], [(2, 3)])
        self.assertEqual(store.get_setting("schema_version"), 3)
        self.assertEqual(store.readings(), [Reading(datetime(2026, 7, 2, 7, 36), 120, 1782970560, 4)])
        self.assertIsNone(store.readings()[0].meal)
        old = store.last_import()
        self.assertEqual((old.received, old.markers_added, old.meter_serial), (1, 0, None))
        backup = sqlite3.connect(store.last_migration.backup)
        self.addCleanup(backup.close)
        self.assertEqual(backup.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone(), ("2",))

    def test_marker_arrives_on_next_import(self):
        store = Store(self.path)
        self.addCleanup(store.close)
        summary = store.import_readings(
            [Reading(datetime(2026, 7, 2, 7, 36), 120, 1782970560, 4, Meal.FASTING)], "lecteur", meter=METER
        )
        self.assertEqual((summary.added, summary.markers_added), (0, 1))
        self.assertEqual(store.readings()[0].meal, Meal.FASTING)

    def test_migration_runs_once(self):
        Store(self.path).close()
        store = Store(self.path)
        self.addCleanup(store.close)
        self.assertEqual(store.migrations, [])
        self.assertEqual(len(list(Path(self.dir.name).glob("glucofi.db.v2-*.bak"))), 1)


if __name__ == "__main__":
    unittest.main()
