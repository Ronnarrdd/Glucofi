import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from contracts import DoseChange, DoseRule, DosingSettings, DoseTarget, InjectionState
from services.store import Store
from services.store.store import SCHEMA_VERSION

MORNING, EVENING = DoseTarget.MORNING, DoseTarget.EVENING
TAKEN, MISSED = InjectionState.TAKEN, InjectionState.MISSED
D1, D2, D3 = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)


class InjectionStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_roundtrip_in_day_order(self):
        self.store.set_injection(D2, EVENING, MISSED)
        self.store.set_injection(D1, EVENING, TAKEN)
        self.store.set_injection(D1, MORNING, TAKEN)
        got = [(i.day, i.target, i.state) for i in self.store.injections()]
        self.assertEqual(got, [(D1, EVENING, TAKEN), (D1, MORNING, TAKEN), (D2, EVENING, MISSED)])
        self.assertEqual(self.store.count_injections(), 3)

    def test_a_dose_has_one_state_the_last_one_declared(self):
        self.store.set_injection(D1, EVENING, MISSED)
        self.store.set_injection(D1, EVENING, TAKEN)
        self.assertEqual([i.state for i in self.store.injections()], [TAKEN])

    def test_back_to_unset_hides_the_row_but_keeps_it_for_the_merge(self):
        self.store.set_injection(D1, EVENING, TAKEN)
        self.store.set_injection(D1, EVENING, None)
        self.assertEqual(self.store.injections(), [])
        self.assertEqual(self.store.count_injections(), 0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*), MAX(state IS NULL) FROM injections").fetchone(), (1, 1))

    def test_since_is_included_until_is_excluded(self):
        for day in (D1, D2, D3):
            self.store.set_injection(day, EVENING, TAKEN)
        self.assertEqual([i.day for i in self.store.injections(since=D2)], [D2, D3])
        self.assertEqual([i.day for i in self.store.injections(until=D3)], [D1, D2])
        self.assertEqual([i.day for i in self.store.injections(since=D2, until=D3)], [D2])

    def test_unknown_values_are_refused_by_the_table(self):
        with self.assertRaises(ValueError):
            self.store.set_injection(D1, "night", TAKEN)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.db.execute("INSERT INTO injections VALUES ('2026-09-01', 'evening', 'maybe', 'x')")

    def test_a_new_database_is_at_the_current_schema(self):
        self.assertEqual(self.store.get_setting("schema_version"), SCHEMA_VERSION)
        self.assertEqual(SCHEMA_VERSION, 7)


class MigrationV6Test(unittest.TestCase):
    def test_v5_database_gets_an_empty_journal_and_keeps_its_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "glucofi.db"
            store = Store(path)
            store.add_dose_change(DoseChange(datetime(2026, 9, 1, 12), 10, 6, DoseRule.START))
            store.db.execute("DROP TABLE injections")
            store.db.execute("UPDATE settings SET value = '5' WHERE key = 'schema_version'")
            store.db.commit()
            store.close()
            before = path.read_bytes()
            migrated = Store(path)
            self.addCleanup(migrated.close)
            self.assertEqual([(m.from_version, m.to_version) for m in migrated.migrations], [(5, 6), (6, 7)])
            self.assertEqual(migrated.get_setting("schema_version"), 7)
            self.assertEqual(migrated.injections(), [])
            self.assertEqual(len(migrated.dose_changes()), 1)
            backup = migrated.last_migration.backup
            self.assertEqual(backup.read_bytes()[:16], before[:16])
            self.assertEqual(sqlite3.connect(backup).execute("SELECT value FROM settings WHERE key='schema_version'").fetchone()[0], "5")
            migrated.set_injection(D1, EVENING, TAKEN)
            self.assertEqual(migrated.count_injections(), 1)

    def test_migration_runs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "glucofi.db"
            Store(path).close()
            again = Store(path)
            self.addCleanup(again.close)
            self.assertEqual(again.migrations, [])
            self.assertEqual(len(list(Path(tmp).glob("*.bak"))), 0)


class InjectionMergeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        self.pc = Store(root / "base-pc.db")
        self.tablet = Store(root / "base-tablette.db")
        self.addCleanup(self.pc.close)
        self.addCleanup(self.tablet.close)

    def export(self, store: Store, name: str) -> Path:
        return store.export_to(Path(self.dir.name) / name)

    def stamp(self, store: Store, when: str, day: date = D1, target: DoseTarget = EVENING) -> None:
        store.db.execute(
            "UPDATE injections SET updated_at = ? WHERE day = ? AND target = ?", (when, day.isoformat(), target.value)
        )
        store.db.commit()

    def rows(self, store: Store):
        return store.db.execute("SELECT day, target, state, updated_at FROM injections ORDER BY day, target").fetchall()

    def test_new_injections_arrive_and_are_counted(self):
        self.pc.set_injection(D1, EVENING, TAKEN)
        self.pc.set_injection(D2, EVENING, MISSED)
        summary = self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.assertEqual((summary.injections_added, summary.injections_updated), (2, 0))
        self.assertTrue(summary.changed)
        self.assertEqual([(i.day, i.state) for i in self.tablet.injections()], [(D1, TAKEN), (D2, MISSED)])

    def test_a_merge_with_only_injections_changes_the_base_and_keeps_a_backup(self):
        self.pc.set_injection(D1, EVENING, TAKEN)
        summary = self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.assertIsNotNone(summary.backup)
        self.assertTrue(summary.backup.exists())

    def test_idempotent(self):
        self.pc.set_injection(D1, EVENING, TAKEN)
        source = self.export(self.pc, "pc.db")
        self.tablet.merge_from(source)
        self.assertFalse(self.tablet.merge_from(source).changed)

    def test_most_recent_declaration_wins_in_both_directions(self):
        self.pc.set_injection(D1, EVENING, TAKEN)
        self.stamp(self.pc, "2026-09-02T10:00:00")
        self.tablet.set_injection(D1, EVENING, MISSED)
        self.stamp(self.tablet, "2026-09-02T11:00:00")
        summary = self.pc.merge_from(self.export(self.tablet, "t.db"))
        self.assertEqual(summary.injections_updated, 1)
        self.assertEqual([i.state for i in self.pc.injections()], [MISSED])
        back = self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.assertEqual(back.injections_updated, 0)
        self.assertEqual(self.rows(self.pc), self.rows(self.tablet))

    def test_erasing_reaches_the_other_device_and_stays_erased(self):
        self.pc.set_injection(D1, EVENING, TAKEN)
        self.stamp(self.pc, "2026-09-02T10:00:00")
        self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.tablet.set_injection(D1, EVENING, None)
        self.assertEqual(self.pc.merge_from(self.export(self.tablet, "t.db")).injections_updated, 1)
        self.assertEqual(self.pc.injections(), [])
        self.assertFalse(self.tablet.merge_from(self.export(self.pc, "pc2.db")).changed)

    def test_an_erased_row_unknown_here_is_not_created(self):
        self.pc.set_injection(D1, EVENING, TAKEN)
        self.pc.set_injection(D1, EVENING, None)
        summary = self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.assertEqual((summary.injections_added, summary.injections_updated), (0, 0))
        self.assertEqual(self.rows(self.tablet), [])

    def test_same_second_conflict_converges(self):
        self.pc.set_injection(D1, EVENING, TAKEN)
        self.tablet.set_injection(D1, EVENING, MISSED)
        self.stamp(self.pc, "2026-09-02T10:00:00")
        self.stamp(self.tablet, "2026-09-02T10:00:00")
        self.pc.merge_from(self.export(self.tablet, "t.db"))
        self.tablet.merge_from(self.export(self.pc, "pc.db"))
        self.assertEqual(self.rows(self.pc), self.rows(self.tablet))

    def test_a_v5_source_without_the_journal_merges_cleanly(self):
        old = Path(self.dir.name) / "v5.db"
        source = Store(old)
        source.db.execute("DROP TABLE injections")
        source.db.execute("UPDATE settings SET value = '5' WHERE key = 'schema_version'")
        source.db.commit()
        source.close()
        self.tablet.set_injection(D1, EVENING, TAKEN)
        summary = self.tablet.merge_from(old)
        self.assertEqual((summary.injections_added, summary.injections_updated), (0, 0))
        self.assertEqual(self.tablet.count_injections(), 1)


if __name__ == "__main__":
    unittest.main()
