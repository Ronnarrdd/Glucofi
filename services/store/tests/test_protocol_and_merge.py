import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path

from contracts import DoseChange, DoseRule, DosingSettings, HighTier, LowTier, Meal, NoteTag, Reading, ReadingNote, Titration
from services.store import MergeRefused, Store, check_glucofi_db
from services.store.store import SCHEMA_VERSION

PROTOCOL = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.30, step_ui=2, high_streak_days=3)
FULL = replace(
    PROTOCOL,
    low_tiers=(LowTier(0.60, 4),),
    high_tiers=(HighTier(1.80, 4, 2),),
    morning_titration=Titration(0.90, 1.40, 1, 2, (LowTier(0.70, 2),), ()),
    evening_start=time(18, 0),
    evening_end=time(20, 30),
)


def reading(ts: str, mg: int, meal: Meal | None = None) -> Reading:
    t = datetime.fromisoformat(ts)
    return Reading(t, mg, int(t.timestamp()), meal=meal)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ProtocolHistoryTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_every_new_version_enters_the_history_once(self):
        first = self.store.save_dosing_settings(PROTOCOL, effective=datetime(2026, 9, 1, 9, 0))
        self.assertIsNotNone(first)
        self.assertIsNone(self.store.save_dosing_settings(PROTOCOL), "même protocole : pas de nouvelle version")
        second = self.store.save_dosing_settings(
            replace(PROTOCOL, high_g_l=1.50), note="Consultation du Dr Test", effective=datetime(2026, 10, 1, 10, 0)
        )
        history = self.store.protocol_changes()
        self.assertEqual([(c.effective, c.settings.high_g_l, c.note) for c in history], [
            (datetime(2026, 9, 1, 9, 0), 1.30, ""),
            (datetime(2026, 10, 1, 10, 0), 1.50, "Consultation du Dr Test"),
        ])
        self.assertEqual(second.id, history[-1].id)
        self.assertEqual(self.store.dosing_settings().high_g_l, 1.50)

    def test_tiers_morning_titration_and_evening_window_roundtrip(self):
        self.store.save_dosing_settings(FULL)
        self.assertEqual(self.store.dosing_settings(), FULL)
        self.assertEqual(self.store.protocol_changes()[0].settings, FULL)
        saved = self.store.get_setting("dosing")
        self.assertEqual((saved["evening_start"], saved["low_tiers"]), ("18:00", [{"below_g_l": 0.6, "step_ui": 4}]))

    def test_tiers_are_ordered_whatever_the_input_order(self):
        settings = replace(PROTOCOL, low_tiers=(LowTier(0.50, 6), LowTier(0.70, 4)), high_tiers=(HighTier(2.5, 6, 1), HighTier(1.8, 4, 2)))
        self.assertEqual([t.below_g_l for t in settings.low_tiers], [0.70, 0.50])
        self.assertEqual([t.above_g_l for t in settings.high_tiers], [1.8, 2.5])

    def test_invalid_tiers_and_windows_are_refused(self):
        for bad in (
            dict(low_tiers=(LowTier(0.90, 4),)),
            dict(low_tiers=(LowTier(0.60, 4), LowTier(0.60, 6))),
            dict(high_tiers=(HighTier(1.20, 4, 2),)),
            dict(high_tiers=(HighTier(1.80, 0, 2),)),
            dict(high_tiers=(HighTier(1.80, 2, 0),)),
            dict(morning_titration=Titration(0.9, 1.4, 1, 2), evening_start=time(11, 0)),
            dict(evening_start=time(21, 0), evening_end=time(18, 0)),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                replace(PROTOCOL, **bad)

    def test_unreadable_version_is_skipped(self):
        self.store.save_dosing_settings(PROTOCOL)
        self.store.db.execute(
            "INSERT INTO protocol_changes (effective, settings, note, created_at) VALUES ('2026-10-02T00:00:00', '{}', '', '')"
        )
        with self.assertLogs("glucofi.store", "WARNING"):
            self.assertEqual(len(self.store.protocol_changes()), 1)


V4_WITH_PROTOCOL = """
CREATE TABLE readings (id INTEGER PRIMARY KEY, epoch INTEGER NOT NULL, device_time TEXT NOT NULL, mg_dl INTEGER NOT NULL,
    device_id INTEGER, source TEXT NOT NULL, imported_at TEXT NOT NULL, meal TEXT, meter_serial TEXT, UNIQUE (device_time, mg_dl));
CREATE TABLE dose_changes (id INTEGER PRIMARY KEY, effective TEXT NOT NULL, morning_ui INTEGER NOT NULL, evening_ui INTEGER NOT NULL,
    rule TEXT NOT NULL, evidence TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL, excluded TEXT NOT NULL DEFAULT '[]');
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO settings VALUES ('schema_version', '4');
INSERT INTO settings VALUES ('dosing', '{"insulin": "Insuline test", "low_g_l": 0.8, "high_g_l": 1.3, "step_ui": 2,
    "high_streak_days": 3, "morning_start": "05:00", "morning_end": "11:59", "hypo_alert_g_l": 0.7, "hyper_alert_g_l": 3.0, "stale_days": 2}');
INSERT INTO dose_changes VALUES (1, '2026-07-01T00:00:00', 10, 6, 'start', '[]', '', '2026-07-01T00:00:00', '[]');
"""


class MigrationV5Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "glucofi.db"

    def open(self, script: str) -> Store:
        db = sqlite3.connect(self.path)
        db.executescript(script)
        db.close()
        store = Store(self.path)
        self.addCleanup(store.close)
        return store

    def test_existing_protocol_becomes_the_first_version(self):
        store = self.open(V4_WITH_PROTOCOL)
        self.assertEqual([(m.from_version, m.to_version) for m in store.migrations], [(4, 5), (5, 6)])
        history = store.protocol_changes()
        self.assertEqual([(c.effective, c.settings, c.note) for c in history], [
            (datetime(2026, 7, 1), PROTOCOL, "Protocole saisi avant l'historique"),
        ])
        self.assertIsNone(store.save_dosing_settings(PROTOCOL), "le même protocole ne crée pas de 2e version")

    def test_no_protocol_no_seed(self):
        store = self.open(V4_WITH_PROTOCOL.replace("INSERT INTO settings VALUES ('dosing'", "INSERT INTO settings VALUES ('x'"))
        self.assertEqual(store.protocol_changes(), [])


class ExportTest(unittest.TestCase):
    def test_export_is_a_complete_reopenable_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "glucofi.db")
            store.import_readings([reading("2026-09-01T08:00", 120)], "lecteur")
            store.save_dosing_settings(FULL)
            target = Path(tmp) / "out" / "glucofi-export.db"
            target.parent.mkdir()
            target.write_bytes(b"ancien contenu")
            store.export_to(target)
            store.close()
            self.assertIsNone(check_glucofi_db(target))
            copy = Store(target)
            self.assertEqual((copy.count_readings(), copy.dosing_settings()), (1, FULL))
            copy.close()


class MergeTest(unittest.TestCase):
    """Deux appareils, deux bases : la fusion réunit tout, dans un sens comme dans l'autre."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        (root / "pc").mkdir()
        (root / "tablette").mkdir()
        self.pc = Store(root / "pc" / "glucofi.db")
        self.tablet = Store(root / "tablette" / "glucofi.db")
        self.addCleanup(self.pc.close)
        self.addCleanup(self.tablet.close)
        self.pc.set_setting("patient_name", "Patient fictif")
        self.pc.save_dosing_settings(PROTOCOL, effective=datetime(2026, 9, 1, 9))
        self.pc.add_dose_change(DoseChange(datetime(2026, 9, 1, 12), 10, 6, DoseRule.START))
        self.pc.import_readings([reading("2026-09-02T07:30", 150, Meal.FASTING), reading("2026-09-02T19:00", 140)], "lecteur")
        self.pc.set_note(self.pc.readings()[0], ReadingNote((NoteTag.ILLNESS,), "fièvre", True))
        self.tablet.import_readings([reading("2026-09-02T19:00", 140, Meal.BEFORE_MEAL), reading("2026-09-03T07:20", 160)], "lecteur")

    def export(self, store: Store, name: str) -> Path:
        return store.export_to(Path(self.dir.name) / name)

    def content(self, store: Store):
        return (
            [(r.device_time, r.mg_dl, r.meal, r.note) for r in store.readings()],
            [(c.effective, c.morning_ui, c.evening_ui, c.rule) for c in store.dose_changes()],
            [(c.effective, c.settings) for c in store.protocol_changes()],
            store.dosing_settings(),
            store.get_setting("patient_name"),
        )

    def test_fresh_tablet_receives_everything_from_the_pc(self):
        summary = self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.assertEqual(
            (summary.readings_added, summary.markers_added, summary.notes_added, summary.doses_added,
             summary.protocol_versions_added, summary.protocol_changed, summary.patient_name_taken, summary.warnings),
            (1, 1, 1, 1, 1, True, True, ()),
        )
        self.assertEqual(self.tablet.count_readings(), 3)
        self.assertEqual(self.tablet.dosing_settings(), PROTOCOL)
        self.assertEqual(self.tablet.readings()[0].note.text, "fièvre")
        self.assertEqual(self.tablet.readings()[1].meal, Meal.BEFORE_MEAL, "le marqueur connu de la tablette reste")
        self.assertTrue(summary.backup.exists())

    def test_merge_is_idempotent(self):
        source = self.export(self.pc, "pc.db")
        self.tablet.merge_from(source)
        once = self.content(self.tablet)
        again = self.tablet.merge_from(source)
        self.assertFalse(again.changed)
        self.assertEqual(self.content(self.tablet), once)

    def backups(self, store: Store) -> list[Path]:
        return sorted(store.path.parent.glob("glucofi.db.avant-fusion-*.bak"))

    def test_merge_that_changes_nothing_leaves_no_backup(self):
        source = self.export(self.pc, "pc.db")
        first = self.tablet.merge_from(source)
        self.assertEqual(self.backups(self.tablet), [first.backup])
        again = self.tablet.merge_from(source)
        self.assertIsNone(again.backup)
        self.assertEqual(self.backups(self.tablet), [first.backup])

    def test_two_merges_in_the_same_second_keep_the_first_backup(self):
        from unittest import mock

        import services.store.store as store_module
        frozen = mock.Mock(wraps=datetime)
        frozen.now.return_value = datetime(2026, 10, 7, 7, 0, 19)
        source = self.export(self.pc, "pc.db")
        with mock.patch.object(store_module, "datetime", frozen):
            first = self.tablet.merge_from(source)
            again = self.tablet.merge_from(source)
        self.assertEqual(first.backup.name, "glucofi.db.avant-fusion-20261007-070019.bak")
        self.assertIsNone(again.backup)
        self.assertEqual(self.backups(self.tablet), [first.backup], "la fusion sans effet n'efface pas celle d'avant")

    def test_backups_are_ordered_by_their_name(self):
        from services.store.store import _backup_order
        names = ["x.avant-fusion-20261007-070019-1.bak", "x.avant-fusion-20261006-235959.bak", "x.avant-fusion-20261007-070019.bak"]
        self.assertEqual(
            sorted(names, key=lambda n: _backup_order(Path(n))),
            ["x.avant-fusion-20261006-235959.bak", "x.avant-fusion-20261007-070019.bak", "x.avant-fusion-20261007-070019-1.bak"],
        )

    def test_only_the_latest_merge_backups_are_kept(self):
        from services.store.store import MERGE_BACKUPS_KEPT
        folder = self.tablet.path.parent
        old = [folder / f"glucofi.db.avant-fusion-202601{day:02d}-120000.bak" for day in range(1, MERGE_BACKUPS_KEPT + 3)]
        for path in old:
            path.write_bytes(b"ancienne")
        other = folder / "glucofi.db.avant-migration-v4-20260101-120000.bak"
        other.write_bytes(b"migration")
        summary = self.tablet.merge_from(self.export(self.pc, "pc.db"))
        kept = self.backups(self.tablet)
        self.assertEqual(len(kept), MERGE_BACKUPS_KEPT)
        self.assertEqual(kept[-1], summary.backup, "la sauvegarde de cette fusion est gardée")
        self.assertFalse(old[0].exists())
        self.assertTrue(other.exists(), "les autres sauvegardes ne sont pas concernées")

    def test_both_directions_converge(self):
        self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.tablet.add_dose_change(DoseChange(datetime(2026, 9, 5, 20), 10, 8, DoseRule.INCREASE_HIGH_MORNINGS, ("x",)))
        self.tablet.save_dosing_settings(FULL, effective=datetime(2026, 9, 6, 10))
        back = self.pc.merge_from(self.export(self.tablet, "tablette.db"))
        self.assertEqual((back.doses_added, back.protocol_versions_added, back.protocol_changed, back.warnings), (1, 1, True, ()))
        self.assertEqual(self.content(self.pc), self.content(self.tablet))
        self.assertEqual(self.pc.current_dose().evening_ui, 8)
        self.assertEqual(self.pc.dosing_settings(), FULL)

    def test_the_most_recently_edited_note_wins(self):
        self.tablet.merge_from(self.export(self.pc, "pc.db"))
        target = self.tablet.readings()[0]
        self.tablet.db.execute(
            "UPDATE reading_notes SET text = 'ancienne', updated_at = '2000-01-01T00:00:00' WHERE device_time = ?",
            (target.device_time.isoformat(),),
        )
        self.tablet.db.commit()
        summary = self.tablet.merge_from(self.export(self.pc, "pc2.db"))
        self.assertEqual((summary.notes_added, summary.notes_updated), (0, 1))
        self.assertEqual(self.tablet.readings()[0].note.text, "fièvre")
        self.pc.db.execute("UPDATE reading_notes SET text = 'vieille', updated_at = '1999-01-01T00:00:00'")
        self.pc.db.commit()
        self.assertEqual(self.tablet.merge_from(self.export(self.pc, "pc3.db")).notes_updated, 0)
        self.assertEqual(self.tablet.readings()[0].note.text, "fièvre")

    def test_erasing_a_note_reaches_the_other_device(self):
        self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.tablet.set_note(self.tablet.readings()[0], None)
        self.pc.db.execute("UPDATE reading_notes SET updated_at = '2000-01-01T00:00:00'")
        self.pc.db.commit()
        self.assertEqual(self.tablet.count_notes(), 0)
        self.assertEqual(self.pc.merge_from(self.export(self.tablet, "tablette.db")).notes_updated, 1)
        self.assertIsNone(self.pc.readings()[0].note)
        self.assertEqual(self.pc.count_notes(), 0)
        self.assertFalse(self.tablet.merge_from(self.export(self.pc, "pc2.db")).changed, "l'effacement ne revient pas")

    def test_same_second_edits_converge(self):
        self.tablet.merge_from(self.export(self.pc, "pc.db"))
        for store, text in ((self.pc, "version PC"), (self.tablet, "version tablette")):
            store.set_note(store.readings()[0], ReadingNote(text=text))
            store.db.execute("UPDATE reading_notes SET updated_at = '2026-09-03T10:00:00'")
            store.db.commit()
        self.pc.merge_from(self.export(self.tablet, "tablette.db"))
        self.tablet.merge_from(self.export(self.pc, "pc2.db"))
        self.assertEqual(self.pc.readings()[0].note, self.tablet.readings()[0].note)

    def test_doses_validated_on_both_devices_are_kept_with_a_warning(self):
        self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.pc.add_dose_change(DoseChange(datetime(2026, 9, 5, 20), 10, 8, DoseRule.INCREASE_HIGH_MORNINGS))
        self.tablet.add_dose_change(DoseChange(datetime(2026, 9, 6, 20), 10, 4, DoseRule.DECREASE_LOW_MORNING))
        summary = self.tablet.merge_from(self.export(self.pc, "pc2.db"))
        self.assertEqual(summary.doses_added, 1)
        self.assertEqual(len(summary.warnings), 1)
        self.assertIn("deux appareils", summary.warnings[0])
        self.assertEqual([c.evening_ui for c in self.tablet.dose_changes()], [6, 8, 4])

    def test_protocol_edited_on_both_devices_latest_wins_with_a_warning(self):
        self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.pc.save_dosing_settings(replace(PROTOCOL, step_ui=1), effective=datetime(2026, 9, 10, 9))
        self.tablet.save_dosing_settings(replace(PROTOCOL, step_ui=3), effective=datetime(2026, 9, 8, 9))
        summary = self.tablet.merge_from(self.export(self.pc, "pc2.db"))
        self.assertTrue(summary.protocol_changed)
        self.assertEqual(self.tablet.dosing_settings().step_ui, 1)
        self.assertIn("10/09/2026 09:00", summary.warnings[0])

    def test_source_file_is_never_modified_even_when_older(self):
        older = Path(self.dir.name) / "v4.db"
        db = sqlite3.connect(older)
        db.executescript(V4_WITH_PROTOCOL)
        db.close()
        before = digest(older)
        summary = self.tablet.merge_from(older)
        self.assertEqual(digest(older), before)
        self.assertEqual((summary.doses_added, summary.protocol_versions_added), (1, 1))
        self.assertEqual(self.tablet.dosing_settings(), PROTOCOL)
        self.assertFalse(self.tablet.merge_from(older).changed, "protocole d'ancien format : même protocole")

    def test_uncommitted_write_refuses_to_copy_instead_of_hanging(self):
        self.tablet.db.execute("UPDATE settings SET value = '\"x\"' WHERE key = 'schema_version'")
        with self.assertRaises(RuntimeError):
            self.tablet.export_to(Path(self.dir.name) / "out.db")
        self.tablet.db.rollback()

    def test_refusals(self):
        junk = Path(self.dir.name) / "junk.db"
        junk.write_bytes(b"pas une base")
        newer = self.export(self.pc, "newer.db")
        db = sqlite3.connect(newer)
        db.execute("UPDATE settings SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 1),))
        db.commit()
        db.close()
        for path, words in (
            (junk, "pas une base Glucofi"),
            (newer, "plus récente"),
            (self.tablet.path, "elle-même"),
            (Path(self.dir.name) / "absent.db", "illisible"),
        ):
            with self.subTest(path=path.name), self.assertRaises(MergeRefused) as caught:
                self.tablet.merge_from(path)
            self.assertIn(words, str(caught.exception))
        self.assertEqual(self.tablet.count_readings(), 2)


if __name__ == "__main__":
    unittest.main()
