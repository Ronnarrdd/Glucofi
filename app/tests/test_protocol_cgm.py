"""Formulaire du protocole : glycémie de référence donnée par le capteur (LibreLinkUp)."""

import unittest
from dataclasses import replace
from datetime import time

from app.protocol import (
    FormError,
    evening_rule_text,
    morning_rule_text,
    protocol_diff,
    protocol_from_form,
    protocol_sections,
    protocol_to_form,
)
from contracts import DosingSettings, Titration
from services.store.store import settings_from_json, settings_json

METER = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
BOTH = replace(
    METER, cgm_morning=time(7, 0), cgm_evening=time(18, 30), cgm_window_min=15, morning_titration=Titration(0.9, 1.4, 1, 2)
)


def form_for(settings: DosingSettings, **changes) -> dict:
    return {**protocol_to_form(settings), **changes}


class FormTest(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(protocol_from_form(protocol_to_form(BOTH), None), BOTH)

    def test_meter_protocol_has_the_sensor_off_with_defaults_to_fill(self):
        form = protocol_to_form(METER)
        self.assertEqual((form["cgm_enabled"], form["cgm_morning"], form["cgm_evening"], form["cgm_window_min"]), ("", "07:00", "18:30", "20"))
        self.assertFalse(protocol_from_form(form).uses_cgm)

    def test_turning_it_on(self):
        settings = protocol_from_form(form_for(METER, cgm_enabled="1", cgm_morning="7h15"))
        self.assertEqual((settings.cgm_morning, settings.cgm_evening, settings.cgm_window_min), (time(7, 15), None, 20))

    def test_turning_it_off_clears_both_times(self):
        self.assertFalse(protocol_from_form(form_for(BOTH, cgm_enabled="")).uses_cgm)
        self.assertIsNone(protocol_from_form(form_for(BOTH, cgm_enabled="")).cgm_evening)

    def test_evening_time_is_only_kept_when_the_morning_dose_is_adjusted(self):
        settings = protocol_from_form(form_for(BOTH, morning_enabled=""))
        self.assertEqual((settings.cgm_morning, settings.cgm_evening), (time(7, 0), None))

    def test_a_form_without_the_sensor_keys_keeps_the_base(self):
        form = {k: v for k, v in protocol_to_form(BOTH).items() if not k.startswith("cgm_")}
        base = settings_from_json(settings_json(BOTH))
        self.assertEqual(protocol_from_form(form, base).cgm_morning, time(7, 0))

    def test_errors_name_the_field(self):
        for field, changes in (
            ("cgm_morning", {"cgm_morning": "bonjour"}),
            ("cgm_morning", {"cgm_morning": "15:00"}),
            ("cgm_evening", {"cgm_evening": "09:00"}),
            ("cgm_evening", {"cgm_evening": ""}),
            ("cgm_window_min", {"cgm_window_min": "0"}),
            ("cgm_window_min", {"cgm_window_min": "90"}),
        ):
            with self.subTest(changes), self.assertRaises(FormError) as caught:
                protocol_from_form(form_for(BOTH, **changes))
            self.assertEqual(caught.exception.field, field)


class TextTest(unittest.TestCase):
    def test_rule_texts_describe_the_sensor_not_the_markers(self):
        self.assertIn("la plus proche de 07:00, à 15 min près", morning_rule_text(BOTH))
        self.assertIn("la plus proche de 18:30", evening_rule_text(BOTH))
        self.assertNotIn("à jeun", morning_rule_text(BOTH))

    def test_meter_texts_are_unchanged(self):
        self.assertIn("première mesure « à jeun »", morning_rule_text(METER))

    def test_sections_only_name_the_reference_for_the_sensor(self):
        self.assertNotIn("Glycémie de référence", [t for t, _ in protocol_sections(METER)])
        self.assertIn("Glycémie de référence", dict(protocol_sections(BOTH)))

    def test_diff_shows_the_switch(self):
        self.assertEqual(
            protocol_diff(METER, replace(METER, cgm_morning=time(7, 0))),
            ["Glycémie de référence : lecteur Accu-Chek → capteur Libre 3 à 07:00 (±20 min)"],
        )


if __name__ == "__main__":
    unittest.main()
