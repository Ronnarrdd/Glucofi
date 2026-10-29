"""Mesures du capteur (source librelinkup) et heures du capteur dans le protocole."""

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, time
from pathlib import Path

from contracts import CGM_SOURCE, DosingSettings, MeterInfo, Reading, Titration
from services.store import Store
from services.store.store import settings_from_json, settings_json

PROTOCOL = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.30, step_ui=2, high_streak_days=3)


def reading(ts: str, mg: int) -> Reading:
    t = datetime.fromisoformat(ts)
    return Reading(t, mg, int(t.timestamp()))


class CgmReadingsTest(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def test_only_sensor_imports_are_flagged(self):
        self.store.import_readings([reading("2026-10-29 07:00", 120)], CGM_SOURCE)
        self.store.import_readings([reading("2026-10-29 08:00", 130)], "lecteur")
        self.assertEqual([(r.mg_dl, r.cgm) for r in self.store.readings()], [(120, True), (130, False)])

    def test_reimport_does_not_duplicate(self):
        for _ in range(3):
            summary = self.store.import_readings([reading("2026-10-29 07:00", 120)], CGM_SOURCE)
        self.assertEqual((summary.received, summary.added, self.store.count_readings()), (1, 0, 1))

    def test_a_meter_reading_is_not_turned_into_a_sensor_one_by_the_same_minute_and_value(self):
        self.store.import_readings([reading("2026-10-29 07:00", 120)], "lecteur")
        self.store.import_readings([reading("2026-10-29 07:00", 120)], CGM_SOURCE)
        self.assertEqual([r.cgm for r in self.store.readings()], [False])

    def test_sensor_is_listed_among_the_meters(self):
        sensor = MeterInfo(manufacturer="Abbott", model="FreeStyle Libre 3", serial="0UFCE8AKP0")
        self.store.import_readings([reading("2026-10-29 07:00", 120)], CGM_SOURCE, meter=sensor)
        self.assertEqual(self.store.meters()[0][0].model_name, "Abbott FreeStyle Libre 3")

    def test_flag_survives_a_merge(self):
        self.store.import_readings([reading("2026-10-29 07:00", 120)], CGM_SOURCE)
        with tempfile.TemporaryDirectory() as folder:
            path = self.store.export_to(Path(folder) / "pc.db")
            other = Store(":memory:")
            self.addCleanup(other.close)
            other.merge_from(path)
        self.assertEqual([r.cgm for r in other.readings()], [True])


class CgmSettingsTest(unittest.TestCase):
    SENSOR = replace(
        PROTOCOL, cgm_morning=time(7, 0), cgm_evening=time(18, 30), cgm_window_min=15,
        morning_titration=Titration(0.9, 1.4, 1, 2),
    )

    def test_json_round_trip(self):
        values = settings_json(self.SENSOR)
        self.assertEqual((values["cgm_morning"], values["cgm_evening"]), ("07:00", "18:30"))
        self.assertEqual(DosingSettings(**settings_from_json(values)), self.SENSOR)

    def test_meter_protocol_keeps_null_times(self):
        values = settings_json(PROTOCOL)
        self.assertEqual((values["cgm_morning"], values["cgm_evening"]), (None, None))
        self.assertEqual(DosingSettings(**settings_from_json(values)), PROTOCOL)

    def test_a_protocol_saved_before_the_sensor_still_loads(self):
        old = settings_json(PROTOCOL)
        for key in ("cgm_morning", "cgm_evening", "cgm_window_min"):
            del old[key]
        self.assertEqual(DosingSettings(**settings_from_json(old)), PROTOCOL)

    def test_switching_to_the_sensor_enters_the_history(self):
        store = Store(":memory:")
        self.addCleanup(store.close)
        store.save_dosing_settings(PROTOCOL, effective=datetime(2026, 9, 1, 9, 0))
        change = store.save_dosing_settings(replace(PROTOCOL, cgm_morning=time(7, 0)), effective=datetime(2026, 10, 29, 9, 0))
        self.assertIsNotNone(change)
        self.assertTrue(store.dosing_settings().uses_cgm)


if __name__ == "__main__":
    unittest.main()
