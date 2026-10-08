import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, time
from pathlib import Path

from app.protocol import (
    FormError,
    evening_rule_text,
    parse_hhmm,
    protocol_diff,
    protocol_from_form,
    protocol_history,
    protocol_sections,
    protocol_to_form,
)
from app.state import AppState, StaleProposal, exclusion_option, merge_message
from contracts import DoseChange, DoseRule, DoseTarget, DosingSettings, HighTier, LowTier, ProtocolChange, Reading, Titration
from services.store import MergeRefused, MergeSummary, Store

SIMPLE = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
FULL = replace(
    SIMPLE,
    low_tiers=(LowTier(0.60, 4),),
    high_tiers=(HighTier(2.00, 4, 2),),
    morning_titration=Titration(0.90, 1.60, 1, 2, (LowTier(0.70, 2),), (HighTier(2.20, 3, 1),)),
    morning_start=time(6, 0),
    morning_end=time(10, 30),
    evening_start=time(18, 0),
    evening_end=time(20, 30),
)


def r(ts: str, mg: int) -> Reading:
    t = datetime.strptime(ts, "%Y-%m-%d %H:%M")
    return Reading(t, mg, int(t.timestamp()))


class FormRoundTripTest(unittest.TestCase):
    def test_every_setting_survives_the_form(self):
        for settings in (SIMPLE, FULL):
            with self.subTest(morning=settings.morning_titration is not None):
                form = protocol_to_form(settings)
                self.assertTrue(all(isinstance(v, (str, list)) for v in form.values()))
                self.assertEqual(protocol_from_form(form), settings)

    def test_form_values_are_french_strings(self):
        form = protocol_to_form(FULL)
        self.assertEqual((form["low_g_l"], form["morning_enabled"], form["evening_start"]), ("0,80", "1", "18:00"))
        self.assertEqual(form["m_high_tiers"], [{"above_g_l": "2,20", "step_ui": "3", "days": "1"}])

    def test_draft_prefills_without_a_protocol(self):
        form = protocol_to_form(None, {"low_g_l": 0.9, "morning_start": time(6, 0)})
        self.assertEqual((form["insulin"], form["low_g_l"], form["morning_start"], form["evening_end"]), ("", "0,90", "06:00", "21:59"))
        self.assertEqual(form["morning_enabled"], "")

    def test_blank_tier_rows_are_ignored(self):
        form = dict(protocol_to_form(SIMPLE), low_tiers=[{"below_g_l": " ", "step_ui": ""}, {"below_g_l": "0,6", "step_ui": "4"}])
        self.assertEqual(protocol_from_form(form).low_tiers, (LowTier(0.60, 4),))

    def test_disabling_the_morning_dose_drops_its_titration(self):
        form = dict(protocol_to_form(FULL), morning_enabled="")
        self.assertIsNone(protocol_from_form(form).morning_titration)

    def test_missing_keys_keep_the_base(self):
        """Ancien dialogue (cinq champs) : paliers, dose du matin et plages restent ceux de la base."""
        base = {k: getattr(FULL, k) for k in FULL.__dataclass_fields__}
        old_form = {"insulin": "Autre", "low_g_l": "0,85", "high_g_l": "1,5", "step_ui": "2", "high_streak_days": "3"}
        got = protocol_from_form(old_form, base)
        self.assertEqual(got, replace(FULL, insulin="Autre", low_g_l=0.85))

    def test_hhmm_variants(self):
        self.assertEqual([parse_hhmm("x", t) for t in ("7:30", "07:30", "7h30", "18h")], [time(7, 30)] * 3 + [time(18, 0)])


class FormErrorTest(unittest.TestCase):
    def error(self, **changes) -> FormError:
        form = protocol_to_form(FULL) | changes
        with self.assertRaises(FormError) as ctx:
            protocol_from_form(form)
        return ctx.exception

    def test_errors_point_at_the_field(self):
        cases = {
            "low_tiers.0.below_g_l": dict(low_tiers=[{"below_g_l": "0,9", "step_ui": "4"}]),
            "low_tiers.1.below_g_l": dict(low_tiers=[{"below_g_l": "0,6", "step_ui": "4"}, {"below_g_l": "0,60", "step_ui": "6"}]),
            "high_tiers.0.above_g_l": dict(high_tiers=[{"above_g_l": "1,2", "step_ui": "4", "days": "2"}]),
            "high_tiers.0.days": dict(high_tiers=[{"above_g_l": "2", "step_ui": "4", "days": "0"}]),
            "m_low_tiers.0.step_ui": dict(m_low_tiers=[{"below_g_l": "0,5", "step_ui": "x"}]),
            "m_high_g_l": dict(m_low_g_l="1,6", m_high_g_l="1,6"),
            "m_step_ui": dict(m_step_ui=""),
            "morning_end": dict(morning_start="11:00", morning_end="10:00"),
            "evening_end": dict(evening_start="21:00", evening_end="19:00"),
            "evening_start": dict(evening_start="10:00"),
            "morning_start": dict(morning_start="25:00"),
        }
        for field, changes in cases.items():
            with self.subTest(field=field):
                self.assertEqual(self.error(**changes).field, field)

    def test_messages_are_readable(self):
        self.assertIn("sous le seuil bas (0,80 g/L)", str(self.error(low_tiers=[{"below_g_l": "0,9", "step_ui": "4"}])))
        self.assertIn("après la fin de la plage du matin (10:30)", str(self.error(evening_start="10:00")))

    def test_evening_window_is_free_without_morning_dose(self):
        form = protocol_to_form(SIMPLE) | {"evening_start": "10:00", "evening_end": "11:00"}
        self.assertEqual(protocol_from_form(form).evening_start, time(10, 0))


class TextsTest(unittest.TestCase):
    def test_sections(self):
        titles = [title for title, _ in protocol_sections(FULL)]
        self.assertEqual(titles, [
            "Insuline",
            "Dose du soir, selon la glycémie du matin (06:00-10:30)",
            "Dose du matin, selon la glycémie du soir (18:00-20:30)",
            "Doses non prises",
            "Alertes",
        ])
        evening = dict(protocol_sections(FULL))[titles[1]]
        self.assertEqual(evening, [
            "Sous 0,80 g/L : baisser de 2 UI",
            "Sous 0,60 g/L : baisser de 4 UI",
            "Au-dessus de 1,50 g/L 3 jours de suite : augmenter de 2 UI",
            "Au-dessus de 2,00 g/L 2 jours de suite : augmenter de 4 UI",
        ])
        self.assertIn("Pas d'ajustement automatique", dict(protocol_sections(SIMPLE))["Dose du matin"][0])

    def test_diff(self):
        self.assertEqual(protocol_diff(None, SIMPLE), ["Premier protocole saisi"])
        self.assertEqual(protocol_diff(SIMPLE, SIMPLE), [])
        self.assertEqual(protocol_diff(SIMPLE, replace(SIMPLE, high_g_l=1.4)), ["Dose du soir, seuil haut : 1,50 g/L → 1,40 g/L"])
        lines = protocol_diff(SIMPLE, FULL)
        self.assertIn("Ajustement de la dose du matin : aucun → automatique", lines)
        self.assertIn("Dose du soir, palier sous 0,60 g/L : baisser de 4 UI (nouveau)", lines)
        self.assertIn("Plage du matin : 05:00-11:59 → 06:00-10:30", lines)
        self.assertIn("Dose du soir, palier sous 0,60 g/L : supprimé (était baisser de 4 UI)", protocol_diff(FULL, replace(FULL, low_tiers=())))

    def test_history_is_newest_first_with_its_changes(self):
        changes = [
            ProtocolChange(datetime(2026, 9, 1), SIMPLE, "", 1),
            ProtocolChange(datetime(2026, 10, 1), replace(SIMPLE, step_ui=1), "Dr Test", 2),
        ]
        history = protocol_history(changes)
        self.assertEqual([c.id for c, _ in history], [2, 1])
        self.assertEqual(history[0][1], ["Dose du soir, baisse : 2 UI → 1 UI", "Dose du soir, hausse : 2 UI après 3 jours → 1 UI après 3 jours"])
        self.assertEqual(history[1][1], ["Premier protocole saisi"])

    def test_evening_rule_text(self):
        self.assertIn("Entre 18:00 et 20:30, avant le dîner : première mesure « avant repas »", evening_rule_text(FULL))

    def test_merge_message(self):
        quiet = MergeSummary("tablette.db", 0, 0, 0, 0, 0, 0, False, 0, False, (), None)
        self.assertIn("déjà à jour", merge_message(quiet))
        busy = replace(quiet, readings_added=12, notes_added=1, doses_added=2, protocol_versions_added=1, protocol_changed=True)
        self.assertEqual(
            merge_message(busy),
            "Fusion de tablette.db : 12 mesures ajoutées, 1 note ajoutée, 2 doses validées ajoutées, "
            "1 version du protocole ajoutée ; protocole mis à jour.",
        )


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "glucofi.db")
        self.addCleanup(self.store.close)
        self.now = datetime(2026, 9, 3, 20, 3, 17)
        self.state = AppState(self.store, Path(self.tmp.name), today=lambda: self.now.date(), now=lambda: self.now)
        self.state.start_protocol("Patient fictif", datetime(2026, 9, 1, 12), 10, 6, FULL)

    def test_each_dose_is_validated_on_its_own(self):
        self.store.import_readings([r("2026-09-03 08:00", 70), r("2026-09-03 19:00", 80)], "lecteur")
        shown = self.state.proposal()
        self.assertEqual((shown.evening.proposed_ui, shown.morning.proposed_ui), (4, 9))
        evening = self.state.validate(shown, target=DoseTarget.EVENING)
        self.assertEqual((evening.morning_ui, evening.evening_ui), (10, 4))
        with self.assertRaises(StaleProposal, msg="la proposition affichée avant la 1re validation a changé"):
            self.state.validate(shown, target=DoseTarget.MORNING)
        morning = self.state.validate(self.state.proposal(), "vu avec le médecin", DoseTarget.MORNING)
        self.assertEqual((morning.morning_ui, morning.evening_ui, morning.rule), (9, 4, DoseRule.DECREASE_LOW_EVENING))

    def test_new_reading_makes_the_shown_morning_proposal_stale(self):
        self.store.import_readings([r("2026-09-03 19:00", 80)], "lecteur")
        shown = self.state.proposal()
        self.store.import_readings([r("2026-09-03 18:30", 120)], "lecteur")
        with self.assertRaises(StaleProposal):
            self.state.validate(shown, target=DoseTarget.MORNING)

    def test_protocol_history_through_the_state(self):
        self.assertEqual(self.state.protocol_changes()[0].effective, datetime(2026, 9, 1, 12), "date de début du protocole")
        self.assertIsNone(self.state.configure_protocol(FULL))
        change = self.state.configure_protocol(replace(FULL, high_g_l=1.4), "consultation")
        self.assertEqual((change.effective, change.note), (datetime(2026, 9, 3, 20, 3), "consultation"))
        self.assertEqual(len(self.state.protocol_changes()), 2)

    def test_export_then_merge_into_another_device(self):
        exported = self.state.export_db(Path(self.tmp.name) / "export" / "glucofi-pc.db")
        with tempfile.TemporaryDirectory() as other_dir:
            other = Store(Path(other_dir) / "glucofi.db")
            try:
                summary = AppState(other, Path(other_dir)).merge_db(exported)
                self.assertEqual((summary.doses_added, summary.protocol_changed), (1, True))
                self.assertEqual(other.dosing_settings(), FULL)
                with self.assertRaises(MergeRefused):
                    AppState(other, Path(other_dir)).merge_db(Path(other_dir) / "glucofi.db")
            finally:
                other.close()

    def test_exclusion_option_for_an_evening_reading(self):
        allowed, text = exclusion_option(r("2026-09-03 19:00", 150), FULL)
        self.assertTrue(allowed)
        self.assertIn("glycémie du soir pour la dose du matin", text)
        allowed, text = exclusion_option(r("2026-09-03 19:00", 85), FULL)
        self.assertFalse(allowed)
        self.assertIn("0,90 g/L", text)
        self.assertIn("du matin ou du soir", exclusion_option(r("2026-09-03 14:00", 150), FULL)[1])
        self.assertFalse(exclusion_option(r("2026-09-03 19:00", 150), SIMPLE)[0])


if __name__ == "__main__":
    unittest.main()
