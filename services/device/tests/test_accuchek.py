import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import re

from contracts import AccuchekExit, ClockAction, Meal, SegmentCount
from services.device import (
    DeviceAccessDenied,
    DeviceNotConnected,
    DeviceProtocolError,
    DeviceReadFailed,
    InvalidOutput,
    fetch,
    fmt_offset,
    is_device_connected,
    parse_output,
)

VALID = """[
    { "id":     0, "epoch": 1617012720, "timestamp":"2021/03/29 11:12", "mg/dL":133, "mmol/L":  7.388889 },
    { "id":     1, "epoch": 1617098760, "timestamp":"2021/03/30 11:06", "mg/dL":192, "mmol/L": 10.666667 }
]
"""


METER = (
    '{"manufacturer":"Roche", "model":"925", "serial":"92500000042", "firmware":"v1.9.6", '
    '"hardware":"G", "software":"", "system_id":"0060190000000042"}'
)
CLOCK_SET = (
    '{"meter":"2026/10/01 21:06:58", "pc":"2026/10/01 20:42:52", "offset_s":1446, '
    '"settable":true, "pc_synchronized":true, "action":"set"}'
)
READINGS = """[
    { "id":     0, "epoch": 1617012720, "timestamp":"2021/03/29 11:12", "mg/dL":133, "mmol/L":  7.388889, "status":0, "meal":"fasting" },
    { "id":     1, "epoch": 1617098760, "timestamp":"2021/03/30 11:06", "mg/dL":192, "mmol/L": 10.666667, "status":0 }
  ]"""


def format2(
    meter: str = METER,
    clock: str = CLOCK_SET,
    glucose: str = '{"announced":2, "received":2}',
    meal: str = '{"announced":1, "received":1, "unmatched":0}',
    readings: str = READINGS,
) -> str:
    return (
        f'{{\n  "format": 2,\n  "meter": {meter},\n  "clock": {clock},\n  "glucose": {glucose},\n'
        f'  "meal": {meal},\n  "readings": {readings}\n}}\n'
    )


def clock(offset: int | None, action: str, synchronized: str = "true") -> str:
    return (
        f'{{"meter":"2026/10/01 21:06:58", "pc":"2026/10/01 20:42:52", "offset_s":{"null" if offset is None else offset}, '
        f'"settable":true, "pc_synchronized":{synchronized}, "action":"{action}"}}'
    )


class Format2Test(unittest.TestCase):
    def test_meter_clock_counts_and_markers(self):
        result = parse_output(format2())
        self.assertEqual(result.meter.serial, "92500000042")
        self.assertEqual(result.meter.firmware, "v1.9.6")
        self.assertEqual(result.meter.model_name, "Accu-Chek Guide (925)")
        self.assertEqual(result.clock.meter, datetime(2026, 10, 1, 21, 6, 58))
        self.assertEqual(result.clock.pc, datetime(2026, 10, 1, 20, 42, 52))
        self.assertEqual(result.clock.offset_s, 1446)
        self.assertEqual(result.clock.action, ClockAction.SET)
        self.assertEqual(result.glucose, SegmentCount(2, 2))
        self.assertEqual(result.meal, SegmentCount(1, 1))
        self.assertEqual([r.meal for r in result.readings], [Meal.FASTING, None])
        self.assertEqual(result.markers, 1)
        self.assertEqual(result.warnings, ())

    def test_meter_that_describes_nothing(self):
        result = parse_output(format2(meter="null", clock="null", glucose='{"announced":null, "received":2}', meal="null"))
        self.assertIsNone(result.meter)
        self.assertIsNone(result.clock)
        self.assertIsNone(result.meal)
        self.assertTrue(result.glucose.complete)
        self.assertEqual(len(result.readings), 2)

    def test_replay_without_pc_clock(self):
        text = format2(clock='{"meter":"2026/10/01 21:06:58", "pc":null, "offset_s":null, "settable":true, '
                             '"pc_synchronized":null, "action":"not_requested"}')
        result = parse_output(text)
        self.assertIsNone(result.clock.pc)
        self.assertIsNone(result.clock.offset_s)
        self.assertEqual(result.warnings, ())

    def test_incomplete_download_is_reported(self):
        result = parse_output(format2(glucose='{"announced":3, "received":2}', meal='{"announced":2, "received":1, "unmatched":1}'))
        self.assertEqual(
            result.warnings,
            (
                "Le lecteur annonce 3 mesures, 2 reçues.",
                "Le lecteur annonce 2 marqueurs repas, 1 reçus.",
                "1 marqueur(s) repas sans mesure à la même seconde, ignoré(s).",
            ),
        )

    def test_clock_warnings(self):
        cases = {
            clock(1446, "set"): (),
            clock(300, "not_requested"): (),
            clock(301, "not_requested"): ("L'horloge du lecteur a 5 min d'avance sur le PC.",),
            clock(-3900, "not_settable"): (
                "L'horloge du lecteur a 1 h 05 min de retard sur le PC. "
                "Ce lecteur ne se règle pas par USB : corrigez l'heure sur le lecteur.",
            ),
            clock(1446, "pc_not_synchronized", "false"): (
                "L'horloge du lecteur a 24 min d'avance sur le PC. "
                "L'heure du PC n'est pas synchronisée (NTP), elle n'a pas été copiée sur le lecteur.",
            ),
            clock(1446, "rejected"): (
                "Le lecteur a refusé la mise à l'heure (horloge du lecteur : 24 min d'avance sur le PC) : "
                "corrigez l'heure sur le lecteur.",
            ),
            clock(None, "pc_unknown", "null"): (),
        }
        for text, expected in cases.items():
            with self.subTest(clock=text):
                self.assertEqual(parse_output(format2(clock=text)).warnings, expected)

    def test_offset_wording(self):
        self.assertEqual(fmt_offset(40), "40 s d'avance")
        self.assertEqual(fmt_offset(-90), "2 min de retard")
        self.assertEqual(fmt_offset(7200), "2 h 00 min d'avance")

    def test_invalid_header_is_fatal(self):
        cases = [
            format2(meter='{"model":"925"}'),
            format2(clock=clock(1446, "teleport")),
            format2(clock=clock(1446, "set").replace('"settable":true', '"settable":1')),
            format2(glucose='{"announced":-1, "received":2}'),
            format2(glucose='{"announced":2, "received":null}'),
            format2(meal='{"announced":1, "received":1, "unmatched":true}'),
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(InvalidOutput) as ctx:
                parse_output(text)
            self.assertIn("En-tête de la sortie accuchek invalide", str(ctx.exception))

    def test_unknown_marker_rejects_the_reading(self):
        text = format2(readings="""[
          {"id":0,"epoch":1,"timestamp":"2024/01/01 08:00","mg/dL":100,"meal":"brunch"},
          {"id":1,"epoch":2,"timestamp":"2024/01/01 09:00","mg/dL":110,"meal":"other"}
        ]""")
        result = parse_output(text)
        self.assertEqual([(r.mg_dl, r.meal) for r in result.readings], [(110, Meal.OTHER)])
        self.assertIn("brunch", result.rejected[0])

    def test_marker_does_not_change_reading_identity(self):
        a = parse_output(format2()).readings[0]
        b = parse_output(VALID).readings[0]
        self.assertEqual(a, b)
        self.assertNotEqual(a.meal, b.meal)


def fake(stdout: str, code: int = 0, stderr: str = "") -> list[str]:
    script = f"import sys; sys.stdout.write({stdout!r}); sys.stderr.write({stderr!r}); sys.exit({code})"
    return [sys.executable, "-c", script]


class FakeSysfs:
    def __init__(self, vendors: list[str]):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        for i, vendor in enumerate(vendors):
            dev = self.root / f"1-{i}"
            dev.mkdir()
            (dev / "idVendor").write_text(vendor + "\n")

    def cleanup(self):
        self.dir.cleanup()


class ParseOutputTest(unittest.TestCase):
    def test_valid_output_uses_device_clock(self):
        result = parse_output(VALID)
        self.assertEqual(len(result.readings), 2)
        first = result.readings[0]
        self.assertEqual(first.device_time, datetime(2021, 3, 29, 11, 12))
        self.assertEqual(first.mg_dl, 133)
        self.assertAlmostEqual(first.g_l, 1.33)
        self.assertEqual(first.device_id, 0)
        self.assertEqual(result.rejected, ())

    def test_only_opening_bracket_is_no_data(self):
        with self.assertRaises(InvalidOutput):
            parse_output("[")

    def test_empty_output(self):
        with self.assertRaises(InvalidOutput):
            parse_output("  \n")

    def test_truncated_json(self):
        with self.assertRaises(InvalidOutput):
            parse_output(VALID[:120])

    def test_unknown_object(self):
        for text in ('{"mg/dL": 100}', '{"format": 3, "readings": []}', '{"format": 2, "readings": {}}', "{"):
            with self.subTest(text=text), self.assertRaises(InvalidOutput):
                parse_output(text)

    def test_old_array_has_no_meter_report(self):
        result = parse_output(VALID)
        self.assertIsNone(result.meter)
        self.assertIsNone(result.clock)
        self.assertIsNone(result.glucose)
        self.assertEqual(result.warnings, ())

    def test_invalid_rows_are_rejected_not_fatal(self):
        text = """[
          {"id":0,"epoch":1,"timestamp":"2024/01/01 08:00","mg/dL":100},
          {"id":1,"epoch":2,"timestamp":"2024/01/01 09:00","mg/dL":5},
          {"id":2,"epoch":3,"timestamp":"pas une date","mg/dL":100},
          {"id":3,"epoch":4,"timestamp":"2024/01/01 10:00"},
          {"id":4,"epoch":5,"timestamp":"2024/01/01 11:00","mg/dL":"120"},
          {"id":5,"epoch":6,"timestamp":"2024/01/01 12:00","mg/dL":true}
        ]"""
        result = parse_output(text)
        self.assertEqual([r.mg_dl for r in result.readings], [100])
        self.assertEqual(len(result.rejected), 5)

    def test_off_scale_readings_are_kept(self):
        text = """[
          {"id":0,"epoch":1,"timestamp":"2026/09/01 12:00","mg/dL":601,"mmol/L":33.4,"status":0,"range":"high"},
          {"id":1,"epoch":2,"timestamp":"2026/09/02 03:15","mg/dL":9,"mmol/L":0.5,"status":1024,"range":"low"}
        ]"""
        result = parse_output(text)
        self.assertEqual(result.rejected, ())
        self.assertEqual([(r.mg_dl, r.off_scale) for r in result.readings], [(601, "high"), (9, "low")])

    def test_flagged_status_is_rejected_with_reason(self):
        text = """[
          {"id":0,"epoch":1,"timestamp":"2026/09/02 08:00","mg/dL":140,"status":1},
          {"id":1,"epoch":2,"timestamp":"2026/09/02 09:00","mg/dL":150,"status":0}
        ]"""
        result = parse_output(text)
        self.assertEqual([r.mg_dl for r in result.readings], [150])
        self.assertEqual(len(result.rejected), 1)
        self.assertIn("statut 0x0001", result.rejected[0])

    def test_unreadable_date_is_rejected_with_reason(self):
        text = """[
          {"id":0,"epoch":null,"timestamp":null,"mg/dL":120,"status":0,"error":"invalid date"},
          {"id":1,"epoch":2,"timestamp":"2026/09/02 09:00","mg/dL":150,"status":0}
        ]"""
        result = parse_output(text)
        self.assertEqual([r.mg_dl for r in result.readings], [150])
        self.assertIn("mesure illisible sur le lecteur (invalid date)", result.rejected[0])

    def test_inconsistent_range_is_rejected(self):
        text = """[
          {"id":0,"epoch":1,"timestamp":"2026/09/02 08:00","mg/dL":450,"status":0,"range":"high"},
          {"id":1,"epoch":2,"timestamp":"2026/09/02 09:00","mg/dL":650,"status":0}
        ]"""
        result = parse_output(text)
        self.assertEqual(result.readings, ())
        self.assertEqual(len(result.rejected), 2)

    def test_readings_are_sorted(self):
        text = """[
          {"id":1,"epoch":20,"timestamp":"2024/01/02 08:00","mg/dL":110},
          {"id":0,"epoch":10,"timestamp":"2024/01/01 08:00","mg/dL":100}
        ]"""
        result = parse_output(text)
        self.assertEqual([r.mg_dl for r in result.readings], [100, 110])


class DeviceDetectionTest(unittest.TestCase):
    def test_roche_present(self):
        sysfs = FakeSysfs(["1d6b", "173a"])
        self.addCleanup(sysfs.cleanup)
        self.assertTrue(is_device_connected(sysfs.root))

    def test_roche_absent(self):
        sysfs = FakeSysfs(["1d6b", "046d"])
        self.addCleanup(sysfs.cleanup)
        self.assertFalse(is_device_connected(sysfs.root))


REPO = Path(__file__).resolve().parents[3]

ACCUCHEK_BUILD = REPO / "services/device/accuchek-src/accuchek"


UDEV_RULE = REPO / "packaging/udev/70-glucofi-accuchek.rules"


def udev_rule_devices() -> set[str]:
    """vendor:product couverts par la règle udev (alternatives "a|b" de ATTR{idProduct})."""
    devices = set()
    for line in UDEV_RULE.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        vendor = re.search(r'ATTR\{idVendor\}=="([0-9a-f]{4})"', line).group(1)
        products = re.search(r'ATTR\{idProduct\}=="([0-9a-f|]+)"', line).group(1)
        devices |= {f"{vendor}:{product}" for product in products.split("|")}
    return devices


class AccuchekConfigTest(unittest.TestCase):
    def test_installer_cleans_old_install(self):
        installer = (REPO / "packaging/install-system.sh").read_text()
        self.assertIn("/usr/local/share/glucofi/config.txt", installer.split("rm -f", 1)[1])
        self.assertNotIn("accuchek-config.txt", installer)
        self.assertIn("/usr/local/libexec/glucofi-accuchek", installer.split("rm -f", 1)[1])
        self.assertIn("/usr/share/polkit-1/actions/fr.librenard.glucofi.accuchek.policy", installer.split("rm -f", 1)[1])

    @unittest.skipUnless(ACCUCHEK_BUILD.exists(), "accuchek non compilé (scripts/gate.sh le compile)")
    def test_accuchek_accepts_guide_from_any_directory(self):
        with tempfile.TemporaryDirectory() as empty:
            proc = subprocess.run(
                [str(ACCUCHEK_BUILD), "--known-devices"], cwd=empty, capture_output=True, text=True, check=True
            )
        self.assertIn("173a:21d5", proc.stdout.split())

    def test_installer_builds_vendored_accuchek(self):
        installer = (REPO / "packaging/install-system.sh").read_text()
        self.assertIn('ACCUCHEK_SRC="$SRC/../services/device/accuchek-src"', installer)
        self.assertIn('cp -r "$ACCUCHEK_SRC/." "$BUILD/"', installer)
        self.assertIn('make -C "$BUILD" all', installer)
        self.assertIn('"$BUILD/accuchek" /usr/local/bin/accuchek', installer)
        self.assertIn("lib64usb1.0-devel", installer)
        self.assertTrue((REPO / "services/device/accuchek-src/main.cpp").is_file())

    def test_installer_leaves_no_root_files_in_the_repo(self):
        """sudo install-system.sh compilait dans accuchek-src : .objs/ et .deps/ root bloquaient le hook pre-commit."""
        installer = (REPO / "packaging/install-system.sh").read_text()
        self.assertNotRegex(installer, r'make -C "\$ACCUCHEK_SRC"')
        self.assertIn('BUILD="$(mktemp -d', installer)
        self.assertIn('rm -rf "$ACCUCHEK_SRC/.objs" "$ACCUCHEK_SRC/.deps"', installer)

    def test_missing_binary_is_explained(self):
        from services.device import NotInstalled
        from services.device import accuchek

        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(accuchek, "ACCUCHEK_BIN", "/nonexistent/accuchek"):
            os.environ.pop("GLUCOFI_ACCUCHEK_CMD", None)
            with self.assertRaises(NotInstalled) as ctx:
                accuchek.build_command()
        self.assertIn("sudo packaging/install-system.sh", str(ctx.exception))


class NoRootTest(unittest.TestCase):
    """accuchek exigeait root (pkexec + lanceur + polkit) ; l'accès USB vient maintenant d'udev."""

    def test_command_runs_accuchek_directly(self):
        from services.device import accuchek

        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "accuchek"
            fake_bin.write_text("#!/bin/sh\n")
            fake_bin.chmod(0o755)
            with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(accuchek, "ACCUCHEK_BIN", str(fake_bin)):
                os.environ.pop("GLUCOFI_ACCUCHEK_CMD", None)
                self.assertEqual(accuchek.build_command(), [str(fake_bin), "--set-time"])

    def test_no_privilege_escalation_left(self):
        sources = [REPO / "services/device/accuchek.py", REPO / "packaging/install-system.sh", REPO / "install.sh"]
        for path in sources:
            text = path.read_text()
            self.assertNotIn("pkexec", text, path.name)
            self.assertNotRegex(text, r"install .*polkit", path.name)
        self.assertNotIn("geteuid", (REPO / "services/device/accuchek-src/main.cpp").read_text())
        self.assertFalse((REPO / "packaging/polkit").exists())
        self.assertFalse((REPO / "packaging/accuchek").exists())

    def test_udev_rule_grants_session_access(self):
        rule = UDEV_RULE.read_text()
        self.assertIn('SUBSYSTEM=="usb"', rule)
        self.assertIn('ENV{DEVTYPE}=="usb_device"', rule)
        self.assertIn('TAG+="uaccess"', rule)
        self.assertNotIn("MODE=", rule)
        # uaccess n'est appliqué que par 73-seat-late.rules : la règle doit passer avant
        self.assertLess(UDEV_RULE.name, "73-seat-late.rules")

    def test_installer_installs_and_reloads_udev_rule(self):
        installer = (REPO / "packaging/install-system.sh").read_text()
        self.assertIn('"$SRC/udev/70-glucofi-accuchek.rules" /etc/udev/rules.d/70-glucofi-accuchek.rules', installer)
        self.assertIn("udevadm control --reload-rules", installer)
        self.assertIn("udevadm trigger --subsystem-match=usb --attr-match=idVendor=173a", installer)
        from services.device import accuchek

        self.assertEqual(accuchek.UDEV_RULE, "/etc/udev/rules.d/70-glucofi-accuchek.rules")

    @unittest.skipUnless(ACCUCHEK_BUILD.exists(), "accuchek non compilé (scripts/gate.sh le compile)")
    def test_udev_rule_matches_known_devices(self):
        proc = subprocess.run([str(ACCUCHEK_BUILD), "--known-devices"], capture_output=True, text=True, check=True)
        self.assertEqual(udev_rule_devices(), set(proc.stdout.split()))

    @unittest.skipUnless(shutil.which("udevadm"), "udevadm absent")
    def test_udev_rule_syntax(self):
        proc = subprocess.run(
            ["udevadm", "verify", "--no-style", str(UDEV_RULE)], capture_output=True, text=True, check=False
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_access_denied_message_points_to_udev(self):
        from services.device import accuchek

        with mock.patch.object(accuchek, "UDEV_RULE", "/nonexistent/70-glucofi-accuchek.rules"):
            error = accuchek.exit_error(AccuchekExit.ACCESS_DENIED, "accuchek: permission denied on USB meter\n")
        self.assertIsInstance(error, DeviceAccessDenied)
        self.assertIn("sudo packaging/install-system.sh", str(error))
        with tempfile.NamedTemporaryFile() as rule, mock.patch.object(accuchek, "UDEV_RULE", rule.name):
            error = accuchek.exit_error(AccuchekExit.ACCESS_DENIED, "")
        self.assertIn("rebranchez le lecteur", str(error))


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.sysfs = FakeSysfs(["173a"])
        self.addCleanup(self.sysfs.cleanup)
        self.raw = tempfile.TemporaryDirectory()
        self.addCleanup(self.raw.cleanup)

    def run_fetch(self, command):
        return fetch(raw_dir=Path(self.raw.name), command=command, sysfs_root=self.sysfs.root, timeout_s=10)

    def test_success_saves_raw_copy(self):
        result = self.run_fetch(fake(VALID))
        self.assertEqual(len(result.readings), 2)
        self.assertIsNotNone(result.raw_path)
        self.assertEqual(result.raw_path.read_text(), VALID)

    def test_not_connected(self):
        sysfs = FakeSysfs([])
        self.addCleanup(sysfs.cleanup)
        with self.assertRaises(DeviceNotConnected):
            fetch(command=fake(VALID), sysfs_root=sysfs.root)

    def test_device_not_found_by_tool(self):
        with self.assertRaises(DeviceNotConnected) as ctx:
            self.run_fetch(fake("", code=2, stderr="accuchek: no Accu-Chek meter found on the USB bus\n"))
        self.assertIn("Détail : no Accu-Chek meter found on the USB bus.", str(ctx.exception))
        self.assertEqual(list(Path(self.raw.name).iterdir()), [])

    def test_must_be_root(self):
        with self.assertRaises(DeviceAccessDenied):
            self.run_fetch(fake("", code=3, stderr="accuchek: must be root, euid is 1000\n"))

    def test_exit_codes_map_to_typed_errors(self):
        cases = {
            AccuchekExit.USAGE: DeviceReadFailed,
            AccuchekExit.NO_DEVICE: DeviceNotConnected,
            AccuchekExit.ACCESS_DENIED: DeviceAccessDenied,
            AccuchekExit.TRANSFER: DeviceReadFailed,
            AccuchekExit.PROTOCOL: DeviceProtocolError,
            AccuchekExit.OUTPUT: DeviceReadFailed,
            134: DeviceReadFailed,
        }
        for code, error in cases.items():
            with self.subTest(code=code), self.assertRaises(error) as ctx:
                self.run_fetch(fake("", code=int(code), stderr="log line\naccuchek: the reason\n"))
            self.assertIn("Détail : the reason.", str(ctx.exception))
            self.assertNotIsInstance(ctx.exception, InvalidOutput)

    def test_transfer_error_says_nothing_was_imported(self):
        with self.assertRaises(DeviceReadFailed) as ctx:
            self.run_fetch(fake("", code=4, stderr="accuchek: failed to receive message data segment: Operation timed out\n"))
        self.assertIn("Aucune mesure n'a été importée", str(ctx.exception))
        self.assertIn("Operation timed out", str(ctx.exception))

    # code 6 tombait dans le message générique "Relancez sudo packaging/install-system.sh"
    def test_output_error_does_not_blame_the_installation(self):
        with self.assertRaises(DeviceReadFailed) as ctx:
            self.run_fetch(fake("", code=6, stderr="accuchek: cannot write on stdout: No space left on device\n"))
        self.assertNotIsInstance(ctx.exception, DeviceProtocolError)
        self.assertIn("Aucune mesure n'a été importée", str(ctx.exception))
        self.assertIn("espace disque", str(ctx.exception))
        self.assertIn("No space left on device", str(ctx.exception))
        self.assertNotIn("install-system.sh", str(ctx.exception))

    # accuchek 2.1 écrit des "accuchek: warning: ..." avant l'erreur fatale
    def test_reason_skips_warning_lines(self):
        from services.device import accuchek

        stderr = (
            "accuchek: warning: cannot write trace t.trace, it is incomplete\n"
            "accuchek: failed to receive message data segment: Operation timed out\n"
        )
        self.assertEqual(accuchek.accuchek_reason(stderr), "failed to receive message data segment: Operation timed out")
        stderr = "accuchek: failed to receive message data segment: No such device\naccuchek: warning: late\n"
        self.assertEqual(accuchek.accuchek_reason(stderr), "failed to receive message data segment: No such device")

    def test_empty_meter_is_not_an_error(self):
        for text in ("[\n]\n", format2(readings="[]", glucose='{"announced":0, "received":0}')):
            with self.subTest(text=text):
                result = self.run_fetch(fake(text))
                self.assertEqual(result.readings, ())
                self.assertEqual(result.rejected, ())

    def test_fetch_carries_the_meter_report(self):
        result = self.run_fetch(fake(format2()))
        self.assertEqual(result.meter.serial, "92500000042")
        self.assertEqual(result.clock.action, ClockAction.SET)
        self.assertEqual(result.markers, 1)
        self.assertEqual(result.glucose, SegmentCount(2, 2))

    def test_missing_program(self):
        with self.assertRaises(DeviceReadFailed):
            self.run_fetch(["/nonexistent/accuchek"])

    def test_timeout(self):
        with self.assertRaises(DeviceReadFailed):
            fetch(
                command=[sys.executable, "-c", "import time; time.sleep(5)"],
                sysfs_root=self.sysfs.root,
                timeout_s=0.3,
            )



SESSION_H = REPO / "services/device/accuchek-src/session.h"


class ExitCodeContractTest(unittest.TestCase):
    def test_contract_matches_cpp_enum(self):
        cpp = dict(re.findall(r"kExit(\w+) = (\d+),", SESSION_H.read_text()))
        expected = {re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper(): int(v) for name, v in cpp.items()}
        self.assertEqual(expected, {e.name: e.value for e in AccuchekExit})


FIXTURE = REPO / "services/device/accuchek-src/tests/fixtures/two_segments.trace"
FIRST_SEGMENT_LINE = 12


@unittest.skipUnless(ACCUCHEK_BUILD.exists(), "accuchek non compilé (scripts/gate.sh le compile)")
class RealBinaryTest(unittest.TestCase):
    """fetch() avec le vrai accuchek rejouant une trace."""

    def fetch_trace(self, text: str):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        trace = Path(tmp.name) / "t.trace"
        trace.write_text(text)
        args = next((line.removeprefix("# args: ") for line in text.splitlines() if line.startswith("# args: ")), "")
        command = [str(ACCUCHEK_BUILD), "--replay", str(trace), *shlex.split(args)]
        return fetch(command=command, sysfs_root=Path(tmp.name) / "nosysfs")

    def test_replayed_meal_markers(self):
        result = self.fetch_trace((FIXTURE.parent / "meals.trace").read_text())
        self.assertEqual(
            [r.meal for r in result.readings], [Meal.FASTING, Meal.AFTER_MEAL, Meal.BEDTIME, None]
        )
        self.assertEqual(result.meal, SegmentCount(4, 4))
        self.assertEqual(result.meals_unmatched, 1)
        self.assertEqual(result.meter.model_name, "Accu-Chek Guide (925)")

    def test_replayed_clock_setting(self):
        result = self.fetch_trace((FIXTURE.parent / "set_time.trace").read_text())
        self.assertEqual(result.clock.action, ClockAction.SET)
        self.assertEqual(result.clock.offset_s, 1446)
        self.assertEqual(result.warnings, ())

    def test_replayed_undescribed_meter(self):
        result = self.fetch_trace((FIXTURE.parent / "undescribed_meter.trace").read_text())
        self.assertIsNone(result.meter)
        self.assertEqual(len(result.readings), 3)

    def broken(self, line: str) -> str:
        lines = FIXTURE.read_text().splitlines(keepends=True)
        lines[FIRST_SEGMENT_LINE] = line + "\n"
        return "".join(lines)

    def test_replayed_session(self):
        self.assertEqual(len(self.fetch_trace(FIXTURE.read_text()).readings), 3)

    def test_timeout_is_a_read_failure_with_reason(self):
        with self.assertRaises(DeviceReadFailed) as ctx:
            self.fetch_trace(self.broken("< !-7"))
        self.assertNotIsInstance(ctx.exception, DeviceProtocolError)
        self.assertIn("Operation timed out", str(ctx.exception))

    def test_abort_is_a_protocol_error(self):
        with self.assertRaises(DeviceProtocolError) as ctx:
            self.fetch_trace(self.broken("< E60000020000"))
        self.assertIn("association abort", str(ctx.exception))

    def test_unwritable_stdout_is_an_output_error(self):
        from services.device import accuchek

        with open("/dev/full", "w") as full:
            proc = subprocess.run(
                [str(ACCUCHEK_BUILD), "--replay", str(FIXTURE)], stdout=full, stderr=subprocess.PIPE, text=True, check=False
            )
        self.assertEqual(proc.returncode, AccuchekExit.OUTPUT)
        error = accuchek.exit_error(proc.returncode, proc.stderr)
        self.assertIs(type(error), DeviceReadFailed)
        self.assertIn("No space left on device", str(error))

    def test_debug_logs_do_not_break_json(self):
        with mock.patch.dict(os.environ, {"ACCUCHEK_DBG": "1"}):
            self.assertEqual(len(self.fetch_trace(FIXTURE.read_text()).readings), 3)


if __name__ == "__main__":
    unittest.main()
