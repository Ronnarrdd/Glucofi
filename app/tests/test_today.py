"""Écran Aujourd'hui sans GTK (app/today.py) : mêmes phrases et mêmes choix que la tablette."""

import unittest
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

from app.today import (
    DISCLAIMER,
    MINUS,
    TAGLINE,
    DoseCard,
    chips,
    delta,
    long_date,
    reading_item,
    split_alerts,
    today_view,
    why_doses,
)
from contracts import Alert, AlertLevel, DoseTarget
from scripts.screenshots import demo_export_file, demo_state

EXPORT = demo_export_file(30)


def card(target=DoseTarget.EVENING, current=6, proposed=None, adjustment=None, label="Soir") -> DoseCard:
    return DoseCard(target, label, current, proposed, "inchangée" if proposed is None else f"au lieu de {current} UI", adjustment)


class TextTest(unittest.TestCase):
    def test_long_date_like_the_tablet(self):
        self.assertEqual(long_date(date(2026, 10, 1)), "jeudi 1er octobre")
        self.assertEqual(long_date(date(2026, 10, 6)), "mardi 6 octobre")

    def test_delta_uses_the_typographic_minus(self):
        self.assertEqual(delta(card(DoseTarget.MORNING, 10, 9, label="Matin")), f"Matin {MINUS}1 UI")
        self.assertEqual(delta(card(current=6, proposed=8)), "Soir +2 UI")
        self.assertIsNone(delta(card()))

    def test_footer_texts(self):
        self.assertEqual(TAGLINE, "Glucofi propose, vous validez.")
        self.assertIn("pas un dispositif médical", DISCLAIMER)


class AlertsTest(unittest.TestCase):
    def test_danger_first_then_warnings_and_info_apart(self):
        proposal = SimpleNamespace(alerts=(
            Alert(AlertLevel.INFO, "i", "info 1"),
            Alert(AlertLevel.WARNING, "w", "attention"),
            Alert(AlertLevel.DANGER, "d", "hypo"),
        ))
        urgent, info = split_alerts(proposal)
        self.assertEqual([(a.kind, a.message) for a in urgent], [("danger", "hypo"), ("warning", "attention")])
        self.assertEqual([(a.kind, a.message) for a in info], [("info", "info 1")])
        self.assertEqual(split_alerts(None), ((), ()))


class TodayViewTest(unittest.TestCase):
    def test_both_doses_proposed_in_titration(self):
        view = today_view(demo_state(EXPORT, "titration"))
        morning, evening = view.doses
        self.assertEqual((morning.target, evening.target), (DoseTarget.MORNING, DoseTarget.EVENING))
        for dose in view.doses:
            self.assertIsNotNone(dose.proposed_ui)
            self.assertEqual(dose.status, f"au lieu de {dose.current_ui} UI")
            self.assertEqual(dose.shown_ui, dose.proposed_ui)
        self.assertEqual(evening.detail_title, f"Pourquoi {evening.proposed_ui} UI le soir ?")
        self.assertEqual(morning.detail_title, f"Pourquoi {morning.proposed_ui} UI le matin ?")
        self.assertTrue(evening.adjustment.confirm_title.startswith("Passer la dose du soir de 6 à"))

    def test_morning_without_titration_is_not_adjusted_and_not_explained(self):
        view = today_view(demo_state(EXPORT, "default"))
        morning, evening = view.doses
        self.assertEqual(morning.status, "pas d'ajustement automatique")
        self.assertIsNone(morning.adjustment)
        featured, explained = view.why
        self.assertEqual([d.target for d in explained], [DoseTarget.EVENING])
        self.assertEqual(featured, explained)
        self.assertTrue(view.footer.startswith("Insuline de démonstration · "))

    def test_why_column_keeps_only_the_doses_that_change(self):
        adjustment = SimpleNamespace()
        moving, steady = card(proposed=8, adjustment=adjustment), card(DoseTarget.MORNING, 10, None, adjustment, "Matin")
        self.assertEqual(why_doses((steady, moving)), ((moving,), (moving, steady)))
        self.assertEqual(why_doses((steady,)), ((steady,), (steady,)))

    def test_chips_show_the_evidence_oldest_first_else_the_last_three(self):
        state = demo_state(EXPORT, "default")
        settings = state.settings.evening_titration
        readings = [r for r in state.readings() if r.mg_dl > 0][-5:]
        items = [reading_item(r, settings, evidence=i in (1, 3)) for i, r in enumerate(reversed(readings))]
        view = SimpleNamespace(references=tuple(items))
        self.assertEqual(chips(view), (items[3], items[1]))
        plain = SimpleNamespace(references=tuple(replace(i, evidence=False) for i in items))
        self.assertEqual([c.reading for c in chips(plain)], [i.reading for i in reversed(items[:3])])

    def test_onboarding_has_no_dose_and_no_footer(self):
        view = today_view(demo_state(EXPORT, "onboarding"))
        self.assertEqual(view.doses, ())
        self.assertIsNone(view.footer)
        self.assertIsNotNone(view.last_reading)

    def test_meter_section_names_the_last_import(self):
        rows = dict(today_view(demo_state(EXPORT, "default")).meter_rows)
        self.assertIn("Dernière récupération", rows)
        self.assertIn("Mesures enregistrées", rows)
        self.assertNotIn("Horloge du lecteur", rows, "import d'un fichier : pas d'horloge du lecteur")


if __name__ == "__main__":
    unittest.main()
