import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from app.state import (
    AppState,
    FormError,
    StaleProposal,
    clock_text,
    fmt_form_g_l,
    import_message,
    meter_text,
    morning_rule_text,
    protocol_from_form,
)
from contracts import ClockAction, DoseChange, DoseRule, DosingSettings, Meal, MeterClock, MeterInfo, Reading, SegmentCount
from services.device import FetchResult
from services.store import ImportSummary, Store

SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)


def r(ts: str, mg: int) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()))


class AppStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.today = date(2026, 9, 5)
        self.state = AppState(
            self.store, Path(self.tmp.name), today=lambda: self.today, now=lambda: datetime(2026, 9, 5, 20, 3, 17)
        )

    def fetched(self, *readings):
        return FetchResult(tuple(readings), (), None)

    def test_onboarding_then_proposal(self):
        self.assertTrue(self.state.needs_onboarding)
        self.assertIsNone(self.state.proposal())
        self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, 6, SETTINGS)
        self.assertFalse(self.state.needs_onboarding)
        self.assertEqual(self.state.patient_name, "Jean")
        self.assertEqual(self.state.proposal().rule, DoseRule.KEEP)

    def test_protocol_comes_only_from_the_user(self):
        self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, 6, SETTINGS)
        self.assertEqual(self.state.settings, SETTINGS)
        self.assertEqual(self.store.current_dose().evening_ui, 6)
        with self.assertRaises(ValueError):
            self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, -1, SETTINGS)

    def test_database_without_protocol_asks_for_it(self):
        """Base d'avant le protocole saisi : une dose de départ mais aucun réglage enregistré."""
        self.store.add_dose_change(DoseChange(datetime(2026, 9, 1, 12), 10, 6, DoseRule.START))
        self.state.import_fetch(self.fetched(r("2026-09-04 08:00", 60)))
        self.assertIsNone(self.state.settings)
        self.assertFalse(self.state.needs_start_dose)
        self.assertTrue(self.state.needs_onboarding)
        self.assertIsNone(self.state.proposal())
        self.state.configure_protocol(SETTINGS)
        self.assertFalse(self.state.needs_onboarding)
        self.assertEqual(self.state.proposal().rule, DoseRule.DECREASE_LOW_MORNING)

    def test_full_increase_flow(self):
        self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, 6, SETTINGS)
        summary = self.state.import_fetch(self.fetched(r("2026-09-03 08:00", 180), r("2026-09-04 08:00", 190), r("2026-09-05 08:00", 200)))
        self.assertEqual(summary.added, 3)
        proposal = self.state.proposal()
        self.assertEqual(proposal.proposed_evening_ui, 8)
        change = self.state.validate(proposal)
        self.assertEqual((change.evening_ui, change.effective), (8, datetime(2026, 9, 5, 20, 3)))
        self.assertEqual(self.state.proposal().rule, DoseRule.KEEP)

    def test_validate_rejects_outdated_proposal(self):
        self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, 6, SETTINGS)
        self.state.import_fetch(self.fetched(r("2026-09-04 08:00", 70)))
        self.today = date(2026, 9, 4)
        shown = self.state.proposal()
        self.assertEqual(shown.rule, DoseRule.DECREASE_LOW_MORNING)
        self.state.import_fetch(self.fetched(r("2026-09-05 08:00", 120)))
        self.today = date(2026, 9, 5)
        with self.assertRaises(StaleProposal):
            self.state.validate(shown)

    def test_manual_change(self):
        self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, 6, SETTINGS)
        change = self.state.manual_change(10, 6, "consigne du médecin")
        self.assertEqual((change.rule, change.morning_ui, change.evening_ui), (DoseRule.MANUAL, 10, 6))
        with self.assertRaises(ValueError):
            self.state.manual_change(8, -1, "")

    def test_import_file_and_period(self):
        path = Path(self.tmp.name) / "m.json"
        path.write_text(
            '[{"id":0,"epoch":1,"timestamp":"2026/08/01 08:00","mg/dL":100},'
            '{"id":1,"epoch":2,"timestamp":"2026/09/05 08:00","mg/dL":110},'
            '{"id":2,"epoch":3,"timestamp":"nope","mg/dL":110}]'
        )
        summary = self.state.import_file(path)
        self.assertEqual((summary.received, summary.added, summary.rejected), (2, 2, 1))
        self.assertEqual([x.mg_dl for x in self.state.readings(days=14)], [110])
        self.assertEqual(self.state.period_bounds(14), (datetime(2026, 8, 23), datetime(2026, 9, 6)))

    def test_reimport_with_markers_changes_the_morning_reading(self):
        """Avant les marqueurs, la mesure d'après petit-déjeuner pouvait devenir la glycémie du matin."""
        self.state.start_protocol("Jean", datetime(2026, 9, 1, 12), 10, 6, SETTINGS)
        days = ("2026-09-03", "2026-09-04", "2026-09-05")
        plain = [r(f"{d} 06:30", 220) for d in days] + [r(f"{d} 09:00", 120) for d in days]
        self.state.import_fetch(self.fetched(*plain))
        self.assertEqual(self.state.proposal().rule, DoseRule.INCREASE_HIGH_MORNINGS)
        marked = [Reading(x.device_time, x.mg_dl, x.epoch, meal=Meal.AFTER_MEAL if x.mg_dl == 220 else Meal.FASTING) for x in plain]
        summary = self.state.import_fetch(FetchResult(tuple(marked), (), None, METER, CLOCK, SegmentCount(6, 6)))
        self.assertEqual((summary.added, summary.markers_added), (0, 6))
        self.assertEqual(self.state.proposal().rule, DoseRule.KEEP)
        self.assertEqual([m.reading.mg_dl for m in self.state.proposal().mornings], [120, 120, 120])
        self.assertEqual([m for m, _, _ in self.state.meters()], [METER])

    def test_new_format_file_keeps_meter_and_markers(self):
        path = Path(self.tmp.name) / "accuchek.json"
        path.write_text(
            '{"format": 2, "meter": null, "clock": null, "glucose": {"announced":null, "received":1}, "meal": null,'
            ' "readings": [{"id":0,"epoch":1,"timestamp":"2026/09/05 08:00","mg/dL":110,"meal":"fasting"}]}'
        )
        summary = self.state.import_file(path)
        self.assertEqual((summary.added, summary.markers_added), (1, 1))


METER = MeterInfo("Roche", "925", "92500000042", "v1.9.6", "G", "", "0060190000000042")
CLOCK = MeterClock(datetime(2026, 10, 1, 21, 6, 58), datetime(2026, 10, 1, 20, 42, 52), 1446, True, True, ClockAction.SET)


def summary(**kw) -> ImportSummary:
    values = dict(source="lecteur", received=638, added=3, rejected=0, at=datetime(2026, 10, 1, 20, 43))
    values.update(kw)
    return ImportSummary(**values)


class TextsTest(unittest.TestCase):
    def test_import_message(self):
        self.assertEqual(import_message(summary()), "3 nouvelle(s) mesure(s) sur 638 lue(s)")
        self.assertEqual(
            import_message(summary(rejected=1, markers_added=576, clock_action=ClockAction.SET, clock_offset_s=1446)),
            "3 nouvelle(s) mesure(s) sur 638 lue(s), 1 ignorée(s), 576 marqueur(s) repas ajouté(s), "
            "lecteur remis à l'heure (24 min d'avance corrigée)",
        )
        self.assertNotIn("heure", import_message(summary(clock_action=ClockAction.WITHIN_TOLERANCE, clock_offset_s=7)))

    def test_meter_text(self):
        self.assertEqual(meter_text(METER), "Accu-Chek Guide (925) · n° 92500000042 · logiciel v1.9.6")
        self.assertEqual(meter_text(MeterInfo("Acme", "X1")), "Acme X1")
        self.assertEqual(MeterInfo().model_name, "Lecteur inconnu")

    def test_clock_text(self):
        self.assertEqual(
            clock_text(summary(clock_action=ClockAction.SET, clock_offset_s=1446)),
            "Remise à l'heure du PC le 01/10/2026 (24 min d'avance corrigée)",
        )
        self.assertEqual(clock_text(summary(clock_action=ClockAction.WITHIN_TOLERANCE, clock_offset_s=7)), "7 s d'avance le 01/10/2026")
        self.assertEqual(clock_text(summary(clock_action=ClockAction.WITHIN_TOLERANCE, clock_offset_s=0)), "À l'heure le 01/10/2026")
        self.assertEqual(
            clock_text(summary(clock_action=ClockAction.PC_NOT_SYNCHRONIZED, clock_offset_s=-600)),
            "10 min de retard le 01/10/2026 ; heure du PC non synchronisée, lecteur laissé tel quel",
        )
        self.assertEqual(clock_text(summary()), "Inconnue (lecteur ancien ou import de fichier)")
        self.assertEqual(clock_text(summary(clock_action=ClockAction.PC_UNKNOWN)), "Écart avec le PC inconnu")

    def test_morning_rule_text(self):
        self.assertIn("Entre 05:00 et 11:59 : première mesure « à jeun »", morning_rule_text(SETTINGS))



class ProtocolFormTest(unittest.TestCase):
    FORM = {"insulin": " Insuline X ", "low_g_l": "0,9", "high_g_l": "1.4", "step_ui": "1", "high_streak_days": "2"}

    def test_parses_french_and_dot_decimals(self):
        s = protocol_from_form(self.FORM)
        self.assertEqual((s.insulin, s.low_g_l, s.high_g_l, s.step_ui, s.high_streak_days), ("Insuline X", 0.9, 1.4, 1, 2))
        self.assertEqual(fmt_form_g_l(s.low_g_l), "0,90")
        self.assertEqual(fmt_form_g_l(None), "")

    def test_keeps_the_other_settings(self):
        s = protocol_from_form(self.FORM, {"stale_days": 5, "insulin": "ancienne"})
        self.assertEqual((s.stale_days, s.insulin), (5, "Insuline X"))

    def test_every_field_is_required_and_checked(self):
        cases = {
            "insulin": "  ",
            "low_g_l": "",
            "high_g_l": "abc",
            "step_ui": "0",
            "high_streak_days": "2.5",
        }
        for field, bad in cases.items():
            with self.subTest(field=field):
                with self.assertRaises(FormError) as ctx:
                    protocol_from_form(dict(self.FORM, **{field: bad}))
                self.assertEqual(ctx.exception.field, field)

    def test_thresholds_must_be_ordered_and_plausible(self):
        with self.assertRaises(FormError) as ctx:
            protocol_from_form(dict(self.FORM, low_g_l="1,5", high_g_l="1,5"))
        self.assertEqual(ctx.exception.field, "high_g_l")
        with self.assertRaises(FormError) as ctx:
            protocol_from_form(dict(self.FORM, low_g_l="80"))
        self.assertEqual(ctx.exception.field, "low_g_l")


if __name__ == "__main__":
    unittest.main()
