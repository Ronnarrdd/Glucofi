"""Journal des injections dans le moteur : une glycémie qui suit une dose déclarée non prise est écartée (si le protocole
le demande), jamais sous le seuil bas, jamais pour une dose simplement non renseignée."""

import unittest
from dataclasses import replace
from datetime import date, datetime

from contracts import (
    AlertLevel,
    DoseChange,
    DoseRule,
    DosingSettings,
    DoseTarget,
    Injection,
    InjectionState,
    LowTier,
    Meal,
    NoteTag,
    Reading,
    ReadingNote,
    Titration,
)
from services.adherence import missed_doses
from services.dosing import (
    apply_proposal,
    excluded_from_dosing,
    fmt_excluded,
    missed_dose_behind,
    missed_dose_why,
    morning_readings,
    propose,
)
from services.dosing.engine import apply_adjustment

BASE = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=2)
ON = replace(BASE, skip_after_missed_dose=True)
START = DoseChange(datetime(2026, 9, 1, 12, 0), 10, 6, DoseRule.START)
MORNING, EVENING = DoseTarget.MORNING, DoseTarget.EVENING
TAKEN, MISSED = InjectionState.TAKEN, InjectionState.MISSED


def r(ts: str, mg: int, meal: Meal | None = None, note: ReadingNote | None = None) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()), meal=meal, note=note)


def missed_evening(*days: int) -> list[Injection]:
    return [Injection(date(2026, 9, d), EVENING, MISSED) for d in days]


class ExclusionTest(unittest.TestCase):
    def test_off_by_default_nothing_is_excluded(self):
        reading = r("2026-09-03 08:00", 180)
        missed = missed_doses(missed_evening(2))
        self.assertFalse(excluded_from_dosing(reading, BASE, missed))
        self.assertIsNone(missed_dose_behind(reading, BASE, missed))

    def test_morning_after_a_missed_evening_dose_is_excluded(self):
        reading = r("2026-09-03 08:00", 180)
        missed = missed_doses(missed_evening(2))
        self.assertTrue(excluded_from_dosing(reading, ON, missed))
        self.assertEqual(missed_dose_behind(reading, ON, missed), (date(2026, 9, 2), EVENING))
        self.assertEqual(missed_dose_why(reading, ON, missed), "dose du soir du 02/09 non prise")

    def test_only_the_day_after_is_excluded(self):
        missed = missed_doses(missed_evening(2))
        for day, expected in ((2, False), (3, True), (4, False)):
            self.assertEqual(excluded_from_dosing(r(f"2026-09-{day:02d} 08:00", 180), ON, missed), expected)

    def test_a_dose_not_filled_in_excludes_nothing(self):
        self.assertFalse(excluded_from_dosing(r("2026-09-03 08:00", 180), ON, missed_doses([])))
        taken = missed_doses([Injection(date(2026, 9, 2), EVENING, TAKEN)])
        self.assertFalse(excluded_from_dosing(r("2026-09-03 08:00", 180), ON, taken))

    def test_a_low_reading_is_never_excluded(self):
        missed = missed_doses(missed_evening(2))
        low = r("2026-09-03 08:00", 79)
        self.assertFalse(excluded_from_dosing(low, ON, missed))
        self.assertEqual(missed_dose_why(low, ON, missed), "")
        edge = r("2026-09-03 08:00", 80)
        self.assertTrue(excluded_from_dosing(edge, ON, missed))

    def test_only_reference_candidates_are_concerned(self):
        missed = missed_doses(missed_evening(2))
        self.assertFalse(excluded_from_dosing(r("2026-09-03 14:00", 180), ON, missed))
        self.assertFalse(excluded_from_dosing(r("2026-09-03 08:00", 180, Meal.AFTER_MEAL), ON, missed))

    def test_the_next_reading_of_the_window_is_excluded_too(self):
        missed = missed_doses(missed_evening(2))
        readings = [r("2026-09-03 07:00", 190, Meal.FASTING), r("2026-09-03 09:00", 170), r("2026-09-04 07:30", 120)]
        self.assertEqual([(m.day.day, m.reading.mg_dl) for m in morning_readings(readings, ON, missed)], [(4, 120)])
        self.assertEqual(len(morning_readings(readings, ON)), 2)

    def test_evening_glycemia_follows_the_same_day_morning_dose(self):
        settings = replace(ON, morning_titration=Titration(0.90, 1.60, 1, 2))
        missed = missed_doses([Injection(date(2026, 9, 3), MORNING, MISSED)])
        self.assertTrue(excluded_from_dosing(r("2026-09-03 19:00", 190, Meal.BEFORE_MEAL), settings, missed))
        self.assertFalse(excluded_from_dosing(r("2026-09-04 19:00", 190, Meal.BEFORE_MEAL), settings, missed))
        # une dose du matin non prise ne touche pas la glycémie du matin du lendemain
        self.assertFalse(excluded_from_dosing(r("2026-09-04 08:00", 190), settings, missed))

    def test_note_and_missed_dose_together(self):
        note = ReadingNote((NoteTag.LARGE_MEAL,), "", True)
        reading = r("2026-09-03 08:00", 180, note=note)
        missed = missed_doses(missed_evening(2))
        self.assertTrue(excluded_from_dosing(reading, BASE, frozenset()))
        self.assertEqual(fmt_excluded(reading, missed_dose_why(reading, ON, missed)),
                         "03/09/2026 08:00 : 1,80 g/L (Repas copieux · dose du soir du 02/09 non prise)")

    def test_a_note_that_does_not_exclude_is_not_a_motive(self):
        reading = r("2026-09-03 08:00", 180, note=ReadingNote((NoteTag.LARGE_MEAL,), "", False))
        self.assertEqual(fmt_excluded(reading, "dose du soir du 02/09 non prise"),
                         "03/09/2026 08:00 : 1,80 g/L (dose du soir du 02/09 non prise)")


class ProposalTest(unittest.TestCase):
    def mornings(self, values, first=2):
        return [r(f"2026-09-{first + i:02d} 08:00", mg) for i, mg in enumerate(values)]

    def test_missed_dose_breaks_the_high_streak(self):
        readings = self.mornings([180, 190])  # 2 et 3 septembre, série de 2 jours : hausse
        today = date(2026, 9, 3)
        self.assertEqual(propose(readings, [START], ON, today).evening.proposed_ui, 8)
        journal = missed_evening(2)  # le matin du 3 suit la dose du soir du 2, non prise
        proposal = propose(readings, [START], ON, today, journal)
        self.assertEqual(proposal.evening.proposed_ui, 6)
        self.assertEqual([x.day.day for x in proposal.evening.references], [2])
        self.assertEqual([x.mg_dl for x in proposal.evening.excluded], [190])
        self.assertEqual(proposal.evening.excluded_why, ("dose du soir du 02/09 non prise",))

    def test_setting_off_ignores_the_journal(self):
        readings = self.mornings([180, 190])
        proposal = propose(readings, [START], BASE, date(2026, 9, 3), missed_evening(2))
        self.assertEqual(proposal.evening.proposed_ui, 8)
        self.assertEqual(proposal.evening.excluded, ())

    def test_low_reading_still_lowers_the_dose(self):
        readings = self.mornings([180, 70])
        proposal = propose(readings, [START], ON, date(2026, 9, 3), missed_evening(2))
        self.assertEqual((proposal.evening.proposed_ui, proposal.evening.rule), (4, DoseRule.DECREASE_LOW_MORNING))
        self.assertEqual(proposal.evening.excluded, ())

    def test_info_alert_names_the_motive(self):
        readings = self.mornings([180, 190])
        proposal = propose(readings, [START], ON, date(2026, 9, 3), missed_evening(2))
        alert = next(a for a in proposal.alerts if a.code == "excluded")
        self.assertEqual(alert.level, AlertLevel.INFO)
        self.assertIn("dose du soir du 02/09 non prise", alert.message)

    def test_validated_dose_remembers_why(self):
        readings = self.mornings([180, 190, 200, 210], first=2)
        journal = missed_evening(2)  # écarte le matin du 3 ; les matins du 4 et du 5 suffisent pour la hausse
        proposal = propose(readings, [START], ON, date(2026, 9, 5), journal)
        self.assertTrue(proposal.evening.changes_dose)
        change = apply_proposal(proposal, datetime(2026, 9, 5, 20, 0))
        self.assertEqual(change.excluded, ("03/09/2026 08:00 : 1,90 g/L (dose du soir du 02/09 non prise)",))
        # même résultat par apply_adjustment
        self.assertEqual(apply_adjustment(proposal, EVENING, datetime(2026, 9, 5, 20, 0)).excluded, change.excluded)

    def test_unlogged_alerts_come_with_the_proposal(self):
        readings = self.mornings([110])
        proposal = propose(readings, [START], BASE, date(2026, 9, 3), [], datetime(2026, 9, 3, 9, 0))
        codes = [a.code for a in proposal.alerts]
        self.assertIn("unlogged_evening", codes)
        self.assertIn("unlogged_morning", codes)
        self.assertTrue(all(a.level is AlertLevel.WARNING for a in proposal.alerts if a.code.startswith("unlogged")))

    def test_without_a_journal_the_proposal_is_unchanged(self):
        readings = self.mornings([180, 190])
        old = propose(readings, [START], BASE, date(2026, 9, 3))
        self.assertEqual(old.evening.proposed_ui, 8)
        self.assertEqual(old.evening.excluded_why, ())


class TierTest(unittest.TestCase):
    def test_low_tier_reading_is_counted(self):
        settings = replace(ON, low_tiers=(LowTier(0.60, 4),))
        readings = [r("2026-09-02 08:00", 150), r("2026-09-03 08:00", 55)]
        proposal = propose(readings, [START], settings, date(2026, 9, 3), missed_evening(2))
        self.assertEqual(proposal.evening.proposed_ui, 2)


if __name__ == "__main__":
    unittest.main()
