import unittest
from dataclasses import replace
from datetime import date, datetime, time, timedelta

from contracts import DoseChange, DoseRule, DosingSettings, DoseTarget, Injection, InjectionState
from services.adherence import (
    UNLOGGED_LOOKBACK_DAYS,
    adherence,
    day_word,
    dose_before,
    dose_ui,
    due_days,
    missed_doses,
    states,
    unlogged_alerts,
    unlogged_days,
)

MORNING, EVENING = DoseTarget.MORNING, DoseTarget.EVENING
TAKEN, MISSED = InjectionState.TAKEN, InjectionState.MISSED
SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
START = DoseChange(datetime(2026, 9, 1, 12, 0), 10, 6, DoseRule.START)
D = date(2026, 9, 1)


def inj(day: int, target: DoseTarget, state: InjectionState) -> Injection:
    return Injection(date(2026, 9, day), target, state)


class DoseInForceTest(unittest.TestCase):
    def test_none_before_the_protocol_starts(self):
        self.assertIsNone(dose_ui([START], date(2026, 8, 31), EVENING))

    def test_a_change_applies_from_its_day(self):
        changes = [START, DoseChange(datetime(2026, 9, 5, 21, 0), 10, 8, DoseRule.INCREASE_HIGH_MORNINGS)]
        self.assertEqual([dose_ui(changes, date(2026, 9, d), EVENING) for d in (1, 4, 5, 6)], [6, 6, 8, 8])
        self.assertEqual(dose_ui(changes, date(2026, 9, 5), MORNING), 10)

    def test_same_instant_changes_use_the_later_id(self):
        a = DoseChange(datetime(2026, 9, 2, 8, 0), 10, 6, DoseRule.MANUAL, id=1)
        b = DoseChange(datetime(2026, 9, 2, 8, 0), 10, 7, DoseRule.MANUAL, id=2)
        self.assertEqual(dose_ui([START, b, a], date(2026, 9, 2), EVENING), 7)


class DueDaysTest(unittest.TestCase):
    def test_today_is_never_due(self):
        days = due_days([START], EVENING, date(2026, 9, 1), date(2026, 9, 10), today=date(2026, 9, 4))
        self.assertEqual(days, [date(2026, 9, d) for d in (1, 2, 3)])

    def test_period_end_caps_before_today(self):
        days = due_days([START], EVENING, date(2026, 9, 1), date(2026, 9, 3), today=date(2026, 9, 20))
        self.assertEqual(days, [date(2026, 9, 1), date(2026, 9, 2)])

    def test_days_before_the_protocol_are_not_due(self):
        days = due_days([START], EVENING, date(2026, 8, 25), date(2026, 9, 3), today=date(2026, 9, 3))
        self.assertEqual(days[0], date(2026, 9, 1))

    def test_a_dose_at_zero_is_not_due(self):
        changes = [START, DoseChange(datetime(2026, 9, 3, 8, 0), 0, 6, DoseRule.MANUAL)]
        days = due_days(changes, MORNING, date(2026, 9, 1), date(2026, 9, 6), today=date(2026, 9, 6))
        self.assertEqual(days, [date(2026, 9, 1), date(2026, 9, 2)])


class AdherenceTest(unittest.TestCase):
    def test_counts_and_rate(self):
        journal = [inj(1, EVENING, TAKEN), inj(2, EVENING, TAKEN), inj(3, EVENING, MISSED), inj(1, MORNING, TAKEN)]
        report = adherence(journal, [START], date(2026, 9, 1), date(2026, 9, 5), today=date(2026, 9, 5))
        evening, morning = report.dose(EVENING), report.dose(MORNING)
        self.assertEqual((evening.due, evening.taken, evening.missed, evening.unset), (4, 2, 1, 1))
        self.assertEqual(evening.missed_days, (date(2026, 9, 3),))
        self.assertEqual(evening.unset_days, (date(2026, 9, 4),))
        self.assertEqual(evening.rate, 50.0)
        self.assertEqual(evening.rate_text, "50 %")
        self.assertEqual((morning.due, morning.taken, morning.unset), (4, 1, 3))
        self.assertEqual(report.declared, 4)
        self.assertEqual(report.due, 8)

    def test_today_is_left_out_even_if_declared(self):
        report = adherence([inj(4, EVENING, TAKEN)], [START], date(2026, 9, 1), date(2026, 9, 5), today=date(2026, 9, 4))
        self.assertEqual(report.dose(EVENING).due, 3)
        self.assertEqual(report.dose(EVENING).taken, 0)

    def test_nothing_due_has_no_rate(self):
        report = adherence([], [START], date(2026, 9, 1), date(2026, 9, 1), today=date(2026, 9, 9))
        self.assertIsNone(report.dose(EVENING).rate)
        self.assertEqual(report.dose(EVENING).rate_text, "-")
        self.assertEqual(report.declared, 0)

    def test_declarations_outside_the_period_do_not_count(self):
        report = adherence([inj(1, EVENING, TAKEN)], [START], date(2026, 9, 2), date(2026, 9, 5), today=date(2026, 9, 9))
        self.assertEqual(report.dose(EVENING).taken, 0)


class StatesTest(unittest.TestCase):
    def test_last_declaration_of_a_dose_wins(self):
        journal = [inj(1, EVENING, TAKEN), inj(1, EVENING, MISSED)]
        self.assertEqual(states(journal), {(date(2026, 9, 1), EVENING): MISSED})

    def test_only_missed_doses_are_listed(self):
        journal = [inj(1, EVENING, TAKEN), inj(2, EVENING, MISSED), inj(2, MORNING, MISSED)]
        self.assertEqual(missed_doses(journal), frozenset({(date(2026, 9, 2), EVENING), (date(2026, 9, 2), MORNING)}))


class DoseBeforeTest(unittest.TestCase):
    def test_morning_glycemia_follows_last_evening_dose(self):
        self.assertEqual(dose_before(date(2026, 9, 5), EVENING), (date(2026, 9, 4), EVENING))

    def test_evening_glycemia_follows_the_same_day_morning_dose(self):
        self.assertEqual(dose_before(date(2026, 9, 5), MORNING), (date(2026, 9, 5), MORNING))


class UnloggedTest(unittest.TestCase):
    today = date(2026, 9, 10)

    def days(self, target, journal=(), now=None, changes=(START,)):
        return unlogged_days(list(journal), list(changes), SETTINGS, target, self.today, now)

    def test_last_three_days_without_today(self):
        self.assertEqual(
            self.days(EVENING), [self.today - timedelta(days=n) for n in range(UNLOGGED_LOOKBACK_DAYS, 0, -1)]
        )

    def test_declared_days_are_not_listed(self):
        journal = [Injection(self.today - timedelta(days=1), EVENING, TAKEN), Injection(self.today - timedelta(days=2), EVENING, MISSED)]
        self.assertEqual(self.days(EVENING, journal), [self.today - timedelta(days=3)])

    def test_today_counts_after_the_reference_window_of_the_dose(self):
        before_end, after_end = datetime.combine(self.today, time(21, 59)), datetime.combine(self.today, time(22, 0))
        self.assertNotIn(self.today, self.days(EVENING, now=before_end))
        self.assertIn(self.today, self.days(EVENING, now=after_end))
        self.assertNotIn(self.today, self.days(MORNING, now=datetime.combine(self.today, time(11, 59))))
        self.assertIn(self.today, self.days(MORNING, now=datetime.combine(self.today, time(12, 0))))

    def test_days_before_the_protocol_are_not_listed(self):
        late = DoseChange(datetime(2026, 9, 9, 8, 0), 10, 6, DoseRule.START)
        self.assertEqual(self.days(EVENING, changes=(late,)), [date(2026, 9, 9)])

    def test_a_dose_at_zero_is_never_asked(self):
        zero = DoseChange(datetime(2026, 9, 1, 12, 0), 0, 6, DoseRule.START)
        self.assertEqual(self.days(MORNING, changes=(zero,)), [])

    def test_alert_wording_and_order(self):
        alerts = unlogged_alerts([], [START], SETTINGS, self.today)
        self.assertEqual([a.code for a in alerts], ["unlogged_evening", "unlogged_morning"])
        self.assertEqual(
            alerts[0].message,
            "Doses du soir pas cochées : "
            f"{day_word(date(2026, 9, 7), self.today)}, avant-hier, hier. Dites si vous les avez prises (Aujourd'hui, Injections).",
        )

    def test_single_day_uses_the_singular(self):
        journal = [Injection(self.today - timedelta(days=n), EVENING, TAKEN) for n in (2, 3)]
        alert = unlogged_alerts(journal, [START], replace(SETTINGS), self.today)[0]
        self.assertTrue(alert.message.startswith("Dose du soir pas cochée : hier."))

    def test_nothing_to_say_when_everything_is_declared(self):
        journal = [Injection(self.today - timedelta(days=n), t, TAKEN) for n in (1, 2, 3) for t in (MORNING, EVENING)]
        self.assertEqual(unlogged_alerts(journal, [START], SETTINGS, self.today), [])

    def test_day_words(self):
        self.assertEqual([day_word(self.today - timedelta(days=n), self.today) for n in range(4)],
                         ["aujourd'hui", "hier", "avant-hier", "lun. 07/09"])


if __name__ == "__main__":
    unittest.main()
