import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from contracts import MealEntry, MealSlot
from services.store import Store

D1, D2 = date(2026, 9, 1), date(2026, 9, 2)
B, L, D = MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER


def entry(day=D1, slot=L, text="pâtes", kcal=600, carbs=80.0, source="gemini") -> MealEntry:
    return MealEntry(day, slot, text, kcal, carbs, carbs - 10, carbs + 10, source)


class MealStoreTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_roundtrip_in_day_then_meal_order(self):
        self.store.set_meal(entry(D2, B, "café"))
        self.store.set_meal(entry(D1, D, "soupe"))
        self.store.set_meal(entry(D1, B, "tartines"))
        self.store.set_meal(entry(D1, L, "riz"))
        self.assertEqual([(m.day, m.slot) for m in self.store.meals()], [(D1, B), (D1, L), (D1, D), (D2, B)])
        self.assertEqual(self.store.count_meals(), 4)
        self.assertEqual(self.store.meals()[1], entry(D1, L, "riz"))

    def test_period_is_since_inclusive_until_exclusive(self):
        self.store.set_meal(entry(D1))
        self.store.set_meal(entry(D2))
        self.assertEqual([m.day for m in self.store.meals(D2)], [D2])
        self.assertEqual([m.day for m in self.store.meals(until=D2)], [D1])

    def test_a_meal_has_one_value_the_last_saved(self):
        self.store.set_meal(entry(text="pâtes", carbs=80))
        self.store.set_meal(MealEntry(D1, L, "pâtes, corrigé", 500, 60.0, None, None, "manual"))
        (meal,) = self.store.meals()
        self.assertEqual((meal.text, meal.carbs_g, meal.carbs_low_g, meal.source), ("pâtes, corrigé", 60.0, None, "manual"))

    def test_a_meal_can_be_saved_before_its_estimate(self):
        self.store.set_meal(MealEntry(D1, L, "pâtes"))
        (meal,) = self.store.meals()
        self.assertFalse(meal.estimated)

    def test_erasing_hides_the_row_but_keeps_it_for_the_merge(self):
        self.store.set_meal(entry())
        self.store.set_meal(None, day=D1, slot=L)
        self.assertEqual((self.store.meals(), self.store.count_meals()), ([], 0))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*), MAX(text IS NULL) FROM meals").fetchone(), (1, 1))

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            self.store.set_meal(entry(text="   "))
        with self.assertRaises(ValueError):
            self.store.set_meal(entry(text="x" * 501))
        with self.assertRaises(ValueError):
            self.store.set_meal(None)
        self.assertEqual(self.store.count_meals(), 0)

    def test_text_is_trimmed(self):
        self.store.set_meal(entry(text="  riz  "))
        self.assertEqual(self.store.meals()[0].text, "riz")


class MigrationV7Test(unittest.TestCase):
    def test_v6_database_gets_an_empty_food_journal_and_keeps_its_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "glucofi.db"
            store = Store(path)
            store.set_setting("patient_name", "Test")
            store.db.execute("DROP TABLE meals")
            store.db.execute("UPDATE settings SET value = '6' WHERE key = 'schema_version'")
            store.db.commit()
            store.close()
            migrated = Store(path)
            self.addCleanup(migrated.close)
            self.assertEqual([(m.from_version, m.to_version) for m in migrated.migrations], [(6, 7)])
            self.assertEqual(migrated.get_setting("schema_version"), 7)
            self.assertEqual(migrated.get_setting("patient_name"), "Test")
            self.assertEqual(migrated.meals(), [])
            self.assertEqual(sqlite3.connect(migrated.last_migration.backup).execute(
                "SELECT value FROM settings WHERE key='schema_version'").fetchone()[0], "6")
            migrated.set_meal(entry())
            self.assertEqual(migrated.count_meals(), 1)


class MealMergeTest(unittest.TestCase):
    def setUp(self):
        self.pc, self.tablet = Store(":memory:"), Store(":memory:")
        self.addCleanup(self.pc.close)
        self.addCleanup(self.tablet.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def merge(self, target: Store, source: Store):
        path = Path(self.tmp.name) / "source.db"
        path.unlink(missing_ok=True)
        source.export_to(path)
        return target.merge_from(path)

    def stamp(self, store: Store, day, slot, when: str) -> None:
        store.db.execute("UPDATE meals SET updated_at = ? WHERE day = ? AND slot = ?", (when, day.isoformat(), slot.value))
        store.db.commit()

    def test_new_meals_cross_over_in_both_directions(self):
        self.pc.set_meal(entry(D1, B, "pc"))
        self.tablet.set_meal(entry(D1, D, "tablette"))
        summary = self.merge(self.pc, self.tablet)
        self.merge(self.tablet, self.pc)
        self.assertEqual((summary.meals_added, summary.meals_updated, summary.changed), (1, 0, True))
        self.assertEqual([m.text for m in self.pc.meals()], ["pc", "tablette"])
        self.assertEqual(self.pc.meals(), self.tablet.meals())

    def test_the_most_recent_edit_wins_on_both_sides(self):
        self.pc.set_meal(entry(text="vieux"))
        self.tablet.set_meal(entry(text="récent"))
        self.stamp(self.pc, D1, L, "2026-09-01T12:00:00")
        self.stamp(self.tablet, D1, L, "2026-09-01T13:00:00")
        self.assertEqual(self.merge(self.pc, self.tablet).meals_updated, 1)
        self.assertEqual(self.merge(self.tablet, self.pc).meals_updated, 0)
        self.assertEqual([m.text for m in self.pc.meals()], ["récent"])
        self.assertEqual(self.pc.meals(), self.tablet.meals())

    def test_an_erasure_travels(self):
        self.pc.set_meal(entry())
        self.merge(self.tablet, self.pc)
        self.tablet.set_meal(None, day=D1, slot=L)
        self.stamp(self.pc, D1, L, "2026-09-01T12:00:00")
        self.stamp(self.tablet, D1, L, "2026-09-01T13:00:00")
        self.merge(self.pc, self.tablet)
        self.assertEqual(self.pc.meals(), [])

    def test_an_erased_meal_unknown_here_is_not_imported(self):
        self.tablet.set_meal(entry())
        self.tablet.set_meal(None, day=D1, slot=L)
        summary = self.merge(self.pc, self.tablet)
        self.assertEqual((summary.meals_added, summary.changed), (0, False))
        self.assertEqual(self.pc.db.execute("SELECT COUNT(*) FROM meals").fetchone()[0], 0)

    def test_same_second_ties_break_the_same_way_on_both_sides(self):
        self.pc.set_meal(entry(text="a", carbs=50))
        self.tablet.set_meal(entry(text="b", carbs=70))
        self.stamp(self.pc, D1, L, "2026-09-01T12:00:00")
        self.stamp(self.tablet, D1, L, "2026-09-01T12:00:00")
        self.merge(self.pc, self.tablet)
        self.merge(self.tablet, self.pc)
        self.assertEqual(self.pc.meals(), self.tablet.meals())
        self.assertEqual(self.pc.meals()[0].text, "b")

    def test_merging_twice_changes_nothing(self):
        self.tablet.set_meal(entry())
        self.merge(self.pc, self.tablet)
        self.assertFalse(self.merge(self.pc, self.tablet).changed)

    def test_a_v6_database_without_meals_still_merges(self):
        self.tablet.db.execute("DROP TABLE meals")
        self.tablet.db.execute("UPDATE settings SET value = '6' WHERE key = 'schema_version'")
        self.tablet.db.commit()
        self.pc.set_meal(entry())
        self.assertFalse(self.merge(self.pc, self.tablet).changed)
        self.assertEqual(self.pc.count_meals(), 1)


if __name__ == "__main__":
    unittest.main()
