"""Journal des injections côté application : état, carte d'Aujourd'hui, protocole, mesures (sans GTK)."""

import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from app.injections import CHOICES, INTRO, injections_view
from app.measures import measure_view
from app.protocol import protocol_diff, protocol_from_form, protocol_sections, protocol_to_form
from app.state import AppState, merge_message
from app.today import today_view
from contracts import DoseChange, DoseRule, DosingSettings, DoseTarget, InjectionState, Reading
from services.store import MergeSummary, Store

MORNING, EVENING = DoseTarget.MORNING, DoseTarget.EVENING
TAKEN, MISSED = InjectionState.TAKEN, InjectionState.MISSED
SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=2)


def r(ts: str, mg: int) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 9, 5, 9, 30)
        self.state = AppState(self.store, Path(self.tmp.name), today=lambda: self.now.date(), now=lambda: self.now)
        self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, 6, SETTINGS)


class StateTest(Base):
    def test_set_and_clear(self):
        self.state.set_injection(date(2026, 9, 4), EVENING, TAKEN)
        self.state.set_injection(date(2026, 9, 5), MORNING, MISSED)
        self.assertEqual([(i.day.day, i.target, i.state) for i in self.state.injections()],
                         [(4, EVENING, TAKEN), (5, MORNING, MISSED)])
        self.assertEqual(self.state.missed(), frozenset({(date(2026, 9, 5), MORNING)}))
        self.state.set_injection(date(2026, 9, 5), MORNING, None)
        self.assertEqual(self.state.missed(), frozenset())

    def test_a_future_dose_cannot_be_declared(self):
        with self.assertRaises(ValueError):
            self.state.set_injection(date(2026, 9, 6), EVENING, TAKEN)
        self.assertEqual(self.state.injections(), [])

    def test_proposal_sees_the_journal(self):
        protocol = replace(SETTINGS, skip_after_missed_dose=True)
        self.state.configure_protocol(protocol, "essai")
        self.store.import_readings([r("2026-09-04 08:00", 180), r("2026-09-05 08:00", 190)], "démo")
        self.assertEqual(self.state.proposal().evening.proposed_ui, 8)
        self.state.set_injection(date(2026, 9, 4), EVENING, MISSED)
        proposal = self.state.proposal()
        self.assertEqual(proposal.evening.proposed_ui, 6)
        self.assertEqual([x.mg_dl for x in proposal.evening.excluded], [190])

    def test_the_alert_appears_once_the_day_is_over(self):
        codes = lambda: [a.code for a in self.state.proposal().alerts]  # noqa: E731
        self.assertIn("unlogged_evening", codes())  # les jours d'avant
        for back in (1, 2, 3, 4):
            self.state.set_injection(date(2026, 9, 5 - back), EVENING, TAKEN)
            self.state.set_injection(date(2026, 9, 5 - back), MORNING, TAKEN)
        self.assertEqual([c for c in codes() if c.startswith("unlogged")], [])
        self.now = datetime(2026, 9, 5, 22, 5)
        self.assertEqual([c for c in codes() if c.startswith("unlogged")], ["unlogged_evening", "unlogged_morning"])
        self.state.set_injection(date(2026, 9, 5), EVENING, TAKEN)
        self.assertEqual([c for c in codes() if c.startswith("unlogged")], ["unlogged_morning"])

    def test_merge_message_counts_injections(self):
        quiet = MergeSummary("tablette.db", 0, 0, 0, 0, 0, 0, False, 0, False, (), None)
        busy = replace(quiet, injections_added=2, injections_updated=1)
        self.assertEqual(merge_message(busy), "Fusion de tablette.db : 3 injections renseignées.")
        self.assertEqual(merge_message(replace(busy, injections_added=1, injections_updated=0)),
                         "Fusion de tablette.db : 1 injection renseignée.")


class ViewTest(Base):
    def test_four_days_two_doses_newest_first(self):
        view = injections_view(self.state)
        self.assertEqual([row.title for row in view.rows][:2], ["Aujourd'hui", "Hier"])
        self.assertEqual(len(view.rows), 4)
        self.assertEqual([c.label for c in view.rows[0].cells], ["Matin", "Soir"])
        self.assertEqual([c.ui for c in view.rows[0].cells], [10, 6])

    def test_days_before_the_protocol_are_not_shown(self):
        self.state.store.db.execute("UPDATE dose_changes SET effective = '2026-09-04T12:00:00'")
        self.state.store.db.commit()
        self.assertEqual(len(injections_view(self.state).rows), 2)

    def test_status_text_and_attention(self):
        self.state.set_injection(date(2026, 9, 4), EVENING, TAKEN)
        self.state.set_injection(date(2026, 9, 4), MORNING, MISSED)
        yesterday = injections_view(self.state).rows[1]
        morning, evening = yesterday.cells
        self.assertEqual((morning.status, morning.attention, morning.state), ("Non prise", False, MISSED))
        self.assertEqual((evening.status, evening.attention), ("Prise", False))
        today = injections_view(self.state).rows[0]
        self.assertEqual([(c.status, c.attention) for c in today.cells], [("À renseigner", False)] * 2)
        older = injections_view(self.state).rows[2]
        self.assertTrue(all(c.attention and c.status == "À renseigner" for c in older.cells))

    def test_today_attention_follows_the_window_end(self):
        self.now = datetime(2026, 9, 5, 12, 1)
        morning, evening = injections_view(self.state).rows[0].cells
        self.assertEqual((morning.attention, evening.attention), (True, False))

    def test_a_dose_at_zero_has_no_pill(self):
        self.state.manual_change(0, 6, "consigne")
        row = injections_view(self.state).rows[0]
        self.assertEqual([c.label for c in row.cells], ["Soir"])

    def test_description_is_readable_by_a_screen_reader(self):
        self.state.set_injection(date(2026, 9, 4), EVENING, MISSED)
        cell = injections_view(self.state).rows[1].cells[1]
        self.assertEqual(cell.description, "Dose du soir, hier : 6 UI, non prise")
        self.assertEqual(cell.choice_title, "Dose du soir du 04/09")
        self.assertEqual(cell.choice_body, "6 UI. Cette dose a-t-elle été faite ?")
        self.assertEqual(cell.key, "2026-09-04|evening")

    def test_choices_cover_taken_missed_and_unset(self):
        self.assertEqual([state for state, _ in CHOICES], [TAKEN, MISSED, None])
        self.assertEqual([text for _, text in CHOICES], ["Prise", "Non prise", "Pas renseignée"])
        self.assertTrue(INTRO)

    def test_none_without_a_protocol(self):
        fresh = AppState(Store(":memory:"), Path(self.tmp.name))
        self.addCleanup(fresh.store.close)
        self.assertIsNone(injections_view(fresh))

    def test_today_view_carries_the_card_and_the_alert(self):
        view = today_view(self.state)
        self.assertIsNotNone(view.injections)
        self.assertTrue(any("pas cochée" in a.message for a in view.urgent))


class ProtocolTest(unittest.TestCase):
    def test_form_roundtrip(self):
        form = protocol_to_form(SETTINGS)
        self.assertEqual(form["skip_missed_dose"], "")
        on = protocol_from_form(form | {"skip_missed_dose": "1"})
        self.assertTrue(on.skip_after_missed_dose)
        self.assertEqual(protocol_to_form(on)["skip_missed_dose"], "1")
        self.assertFalse(protocol_from_form(protocol_to_form(on) | {"skip_missed_dose": ""}).skip_after_missed_dose)

    def test_old_form_without_the_field_keeps_the_saved_value(self):
        form = protocol_to_form(SETTINGS)
        form.pop("skip_missed_dose")
        base = {"insulin": "x", "low_g_l": 0.8, "high_g_l": 1.5, "step_ui": 2, "high_streak_days": 2, "skip_after_missed_dose": True}
        self.assertTrue(protocol_from_form(form, base).skip_after_missed_dose)

    def test_draft_before_the_protocol_keeps_it(self):
        form = protocol_to_form(None, {"skip_after_missed_dose": True})
        self.assertEqual(form["skip_missed_dose"], "1")

    def test_sections_and_diff_say_it(self):
        on = replace(SETTINGS, skip_after_missed_dose=True)
        self.assertIn("écartées de l'ajustement", dict(protocol_sections(on))["Doses non prises"][0])
        self.assertIn("n'écarte aucune", dict(protocol_sections(SETTINGS))["Doses non prises"][0])
        self.assertEqual(protocol_diff(SETTINGS, on), ["Écarter après une dose non prise : non → oui"])
        self.assertEqual(protocol_diff(SETTINGS, SETTINGS), [])

    def test_stored_protocol_without_the_field_reads_as_off(self):
        from services.store import settings_json
        values = settings_json(SETTINGS)
        values.pop("skip_after_missed_dose")
        store = Store(":memory:")
        self.addCleanup(store.close)
        store.set_setting("dosing", values)
        self.assertFalse(store.dosing_settings().skip_after_missed_dose)
        store.set_setting("dosing", settings_json(replace(SETTINGS, skip_after_missed_dose=True)))
        self.assertTrue(store.dosing_settings().skip_after_missed_dose)

    def test_changing_it_enters_the_protocol_history(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        store.save_dosing_settings(SETTINGS, effective=datetime(2026, 9, 1, 9))
        self.assertIsNotNone(store.save_dosing_settings(replace(SETTINGS, skip_after_missed_dose=True)))
        self.assertEqual(len(store.protocol_changes()), 2)


class MeasuresTest(unittest.TestCase):
    def test_line_excluded_with_its_motive(self):
        on = replace(SETTINGS, skip_after_missed_dose=True)
        readings = [r("2026-09-04 08:00", 180), r("2026-09-05 08:00", 190), r("2026-09-05 14:00", 120)]
        missed = frozenset({(date(2026, 9, 4), EVENING)})
        view = measure_view(readings, on, "all", "all", [], today=date(2026, 9, 5), missed=missed)
        today = {line.time: line for line in view.days[0].lines}
        self.assertTrue(today["08:00"].excluded)
        self.assertEqual(today["08:00"].excluded_why, "dose du soir du 04/09 non prise")
        self.assertFalse(today["08:00"].retained)
        self.assertFalse(today["14:00"].excluded)
        self.assertIsNone(view.days[0].morning)
        self.assertFalse(view.days[1].lines[0].excluded)
        self.assertTrue(view.days[1].lines[0].retained)

    def test_without_the_journal_nothing_changes(self):
        readings = [r("2026-09-05 08:00", 190)]
        view = measure_view(readings, replace(SETTINGS, skip_after_missed_dose=True), "all", "all", [], today=date(2026, 9, 5))
        self.assertFalse(view.days[0].lines[0].excluded)


if __name__ == "__main__":
    unittest.main()
