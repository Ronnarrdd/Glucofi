import base64
import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from contracts import DoseChange, DoseRule, DosingSettings, Meal, NoteTag, Reading, ReadingNote
from contracts.tablet_sync import PC_FILE, SYNC_VERSION, TABLET_FILE
from services.store import Store
from services.tablet import Adb, TabletError, TabletLink, find_adb, parse_devices, parse_reply, pick_device, sync
from services.tablet.tests.fake_tablet import FakeTablet, reply_output

PROTOCOL = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.30, step_ui=2, high_streak_days=3)


def reading(ts: str, mg: int, meal: Meal | None = None) -> Reading:
    t = datetime.fromisoformat(ts)
    return Reading(t, mg, int(t.timestamp()), meal=meal)


class PcStore:
    """Côté PC réduit à ce que sync() utilise (AppState.merge_db / export_db)."""

    def __init__(self, store: Store):
        self.store = store

    def merge_db(self, path: Path):
        return self.store.merge_from(path)

    def export_db(self, path: Path) -> Path:
        return self.store.export_to(path)


def content(store: Store):
    return (
        [(r.device_time, r.mg_dl, r.meal, r.note) for r in store.readings()],
        [(c.effective, c.morning_ui, c.evening_ui, c.rule) for c in store.dose_changes()],
        [(c.effective, c.settings) for c in store.protocol_changes()],
        store.dosing_settings(),
    )


class ParseTest(unittest.TestCase):
    def test_devices(self):
        text = (
            "* daemon started successfully\nList of devices attached\n"
            "R92X2035LTN            device usb:1-4.1 product:gta9pwifieea model:SM_X210 device:gta9pwifi transport_id:3\n"
            "emulator-5580          device product:sdk model:sdk_gphone64_x86_64 transport_id:1\n"
            "ABC                    unauthorized usb:1-2 transport_id:4\n\n"
        )
        devices = parse_devices(text)
        self.assertEqual([(d.serial, d.state, d.model) for d in devices], [
            ("R92X2035LTN", "device", "SM X210"), ("emulator-5580", "device", "sdk gphone64 x86 64"), ("ABC", "unauthorized", ""),
        ])
        self.assertEqual([d.emulator for d in devices], [False, True, False])

    def test_reply_survives_accents_quotes_and_newlines(self):
        reply = {"sync": SYNC_VERSION, "ok": True, "message": "Fusion de « x » : 1 note ajoutée,\n}] {fin}", "warnings": []}
        self.assertEqual(parse_reply(reply_output(reply)), reply)

    def test_reply_errors(self):
        cases = {
            "Error while accessing provider:x\njava.lang.IllegalArgumentException: Unknown authority x": "dernière version de l'app",
            "Error while accessing provider\njava.lang.SecurityException: refusé": "refusé",
            "": "Réponse inattendue",
            "Result: Bundle[{reply=pas du base64!}]": "illisible",
            reply_output({"sync": SYNC_VERSION, "ok": True}): "incomplète",
            reply_output({"sync": SYNC_VERSION + 1, "ok": True, "message": ""}): "mettez à jour Glucofi",
        }
        for output, expected in cases.items():
            with self.subTest(output=output[:40]):
                with self.assertRaises(TabletError) as raised:
                    parse_reply(output)
                self.assertIn(expected, str(raised.exception))

    def test_find_adb(self):
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp) / "Android" / "Sdk" / "platform-tools"
            sdk.mkdir(parents=True)
            adb = sdk / "adb"
            adb.write_text("#!/bin/sh\n")
            empty = {"PATH": str(Path(tmp) / "nothing")}
            self.assertIsNone(find_adb(empty, home=Path(tmp)))
            adb.chmod(0o755)
            self.assertEqual(find_adb(empty, home=Path(tmp)), str(adb))
            self.assertEqual(find_adb({**empty, "ANDROID_HOME": str(Path(tmp) / "Android" / "Sdk")}, home=Path("/nowhere")), str(adb))
            on_path = Path(tmp) / "bin"
            on_path.mkdir()
            (on_path / "adb").write_text("#!/bin/sh\n")
            (on_path / "adb").chmod(0o755)
            self.assertEqual(find_adb({"PATH": str(on_path)}, home=Path(tmp)), str(on_path / "adb"))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        (root / "pc").mkdir()
        (root / "work").mkdir()
        self.work = root / "work"
        self.tablet = FakeTablet(root / "tablette")
        self.addCleanup(self.tablet.close)
        self.pc_store = Store(root / "pc" / "glucofi.db")
        self.addCleanup(self.pc_store.close)
        self.pc = PcStore(self.pc_store)
        self.adb = Adb("/usr/bin/adb", run=self.tablet)


class PickDeviceTest(Fixture):
    def test_the_tablet_with_glucofi(self):
        self.assertEqual(pick_device(self.adb).serial, self.tablet.serial)

    def test_real_tablet_wins_over_an_emulator(self):
        self.tablet.others.append(("emulator-5580", "sdk", True))
        self.assertEqual(pick_device(self.adb, environ={}).serial, self.tablet.serial)

    def test_an_emulator_alone_is_never_picked(self):
        """07/10/2026 : tablette débranchée, émulateur ouvert : ses données de démonstration sont parties dans la vraie base."""
        self.tablet.serial = "emulator-5580"
        with self.assertRaises(TabletError) as raised:
            pick_device(self.adb, environ={})
        self.assertIn("Seul un émulateur", str(raised.exception))
        with self.assertRaises(TabletError):
            TabletLink.connect(self.adb)
        self.assertEqual(self.tablet.calls, [], "aucun échange avec l'émulateur")
        self.assertEqual(pick_device(self.adb, environ={"GLUCOFI_SYNC_EMULATOR": "1"}).serial, "emulator-5580")

    def test_unauthorized_tablet_is_reported_even_with_an_emulator(self):
        self.tablet.state = "unauthorized"
        self.tablet.others.append(("emulator-5580", "sdk", True))
        with self.assertRaises(TabletError) as raised:
            pick_device(self.adb, environ={})
        self.assertIn("Autoriser le débogage USB", str(raised.exception))

    def test_messages(self):
        cases = []
        self.tablet.state = "unauthorized"
        cases.append("Autoriser le débogage USB")
        for expected in cases:
            with self.assertRaises(TabletError) as raised:
                pick_device(self.adb)
            self.assertIn(expected, str(raised.exception))
        self.tablet.state = "offline"
        with self.assertRaises(TabletError) as raised:
            pick_device(self.adb)
        self.assertIn("pas prête", str(raised.exception))
        self.tablet.state = "device"
        self.tablet.installed = False
        with self.assertRaises(TabletError) as raised:
            pick_device(self.adb)
        self.assertIn("pas installée", str(raised.exception))
        self.tablet.installed = True
        self.tablet.others.append(("R92X9999", "SM_X220", True))
        with self.assertRaises(TabletError) as raised:
            pick_device(self.adb)
        self.assertIn("Plusieurs appareils", str(raised.exception))

    def test_nothing_plugged(self):
        adb = Adb("/usr/bin/adb", run=lambda args, timeout: subprocess.CompletedProcess(args, 0, "List of devices attached\n\n", ""))
        with self.assertRaises(TabletError) as raised:
            pick_device(adb)
        self.assertIn("débogage USB", str(raised.exception))

    def test_adb_failures_become_messages(self):
        def timeout(args, seconds):
            raise subprocess.TimeoutExpired(args, seconds)

        def missing(args, seconds):
            raise FileNotFoundError("adb")

        for runner, expected in ((timeout, "ne répond plus"), (missing, "n'a pas pu être lancé")):
            with self.subTest(expected=expected):
                with self.assertRaises(TabletError) as raised:
                    Adb("/usr/bin/adb", run=runner).devices()
                self.assertIn(expected, str(raised.exception))

    def test_old_app_without_sync(self):
        self.tablet.provider = False
        with self.assertRaises(TabletError) as raised:
            TabletLink.connect(self.adb)
        self.assertIn("dernière version de l'app", str(raised.exception))


class SyncTest(Fixture):
    def setUp(self):
        super().setUp()
        self.pc_store.set_setting("patient_name", "Patient fictif")
        self.pc_store.save_dosing_settings(PROTOCOL, effective=datetime(2026, 9, 1, 9))
        self.pc_store.add_dose_change(DoseChange(datetime(2026, 9, 1, 12), 10, 6, DoseRule.START))
        self.pc_store.import_readings([reading("2026-09-02T07:30", 150, Meal.FASTING), reading("2026-09-02T19:00", 140)], "lecteur")
        self.tablet.store.import_readings([reading("2026-09-03T07:20", 160)], "lecteur")
        self.tablet.store.set_note(self.tablet.store.readings()[0], ReadingNote((NoteTag.ILLNESS,), "fièvre", True))

    def sync(self):
        return sync(self.pc, TabletLink.connect(self.adb), self.work)

    def test_both_sides_end_up_identical(self):
        result = self.sync()
        self.assertEqual(content(self.pc_store), content(self.tablet.store))
        self.assertEqual(self.pc_store.count_readings(), 3)
        self.assertEqual(self.tablet.store.dosing_settings(), PROTOCOL)
        self.assertTrue(result.pc.changed and result.tablet.changed and result.changed)
        self.assertEqual(self.tablet.calls, ["hello", "export", "cleanup", "merge", "cleanup"])
        self.assertEqual(self.tablet.files(), [], "aucune copie de base ne reste sur la tablette")

    def test_a_sync_folder_left_by_adb_does_not_block_the_tablet(self):
        self.tablet.leave_folder_created_by_adb()
        self.sync()
        self.assertEqual(content(self.pc_store), content(self.tablet.store))
        self.assertEqual(self.tablet.files(), [])
        self.assertEqual(self.tablet.remote_owner, "app")

    def test_second_sync_changes_nothing_and_leaves_no_backup(self):
        self.sync()
        backups = sorted(self.pc_store.path.parent.glob("*.bak")), sorted(self.tablet.root.glob("*.bak"))
        again = self.sync()
        self.assertFalse(again.changed)
        self.assertEqual((sorted(self.pc_store.path.parent.glob("*.bak")), sorted(self.tablet.root.glob("*.bak"))), backups)

    def test_changes_on_each_side_meet(self):
        self.sync()
        self.pc_store.add_dose_change(DoseChange(datetime(2026, 9, 5, 20), 10, 8, DoseRule.INCREASE_HIGH_MORNINGS, ("x",)))
        self.tablet.store.import_readings([reading("2026-09-06T07:10", 120)], "lecteur")
        result = self.sync()
        self.assertEqual(content(self.pc_store), content(self.tablet.store))
        self.assertEqual((result.pc.readings_added, result.tablet.changed), (1, True))

    def test_refused_merge_on_the_tablet_is_reported_and_cleaned(self):
        self.tablet.fail["merge"] = "La base reçue est abîmée."
        with self.assertRaises(TabletError) as raised:
            self.sync()
        self.assertEqual(str(raised.exception), "La base reçue est abîmée.")
        self.assertEqual(self.tablet.files(), [])

    def test_export_failure_stops_before_touching_the_pc(self):
        self.tablet.fail["export"] = "Base de la tablette illisible."
        before = content(self.pc_store)
        with self.assertRaises(TabletError):
            self.sync()
        self.assertEqual(content(self.pc_store), before)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_files_cross_in_the_right_place(self):
        self.sync()
        self.assertTrue((self.work / TABLET_FILE).exists())
        self.assertTrue((self.work / PC_FILE).exists())

    def test_warnings_say_which_side(self):
        self.sync()
        self.pc_store.add_dose_change(DoseChange(datetime(2026, 9, 5, 20), 10, 8, DoseRule.INCREASE_HIGH_MORNINGS, ("x",)))
        self.tablet.store.add_dose_change(DoseChange(datetime(2026, 9, 5, 21), 9, 6, DoseRule.DECREASE_LOW_MORNING, ("y",)))
        result = self.sync()
        self.assertTrue(result.warnings)
        self.assertTrue(all(w.startswith(("Sur le PC : ", "Sur la tablette : ")) for w in result.warnings), result.warnings)
        self.assertEqual(content(self.pc_store), content(self.tablet.store))


class ReplyEncodingTest(unittest.TestCase):
    def test_kotlin_side_encoding(self):
        """L'app encode en base64 standard sans retour à la ligne (Base64.NO_WRAP) : même chose que b64encode."""
        payload = json.dumps({"sync": SYNC_VERSION, "ok": True, "message": "é"}, ensure_ascii=False).encode()
        self.assertNotIn(b"\n", base64.b64encode(payload))


if __name__ == "__main__":
    unittest.main()
