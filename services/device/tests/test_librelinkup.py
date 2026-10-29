import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from unittest import mock

from contracts import local_epoch
from services.device.librelinkup import (
    Credentials,
    LibreLinkUpError,
    LibreLinkUpNotInstalled,
    fetch_cgm as fetch,
    load_credentials,
    save_credentials,
    thin,
    to_reading,
)

CREDENTIALS = Credentials("suivi@example.org", "secret")


class ToReadingTest(unittest.TestCase):
    def test_rounds_and_truncates_to_the_minute(self):
        reading = to_reading(datetime(2026, 10, 29, 20, 0, 27), 178.6)
        self.assertEqual(reading.mg_dl, 179)
        self.assertEqual(reading.device_time, datetime(2026, 10, 29, 20, 0))

    def test_epoch_is_the_local_epoch_of_the_minute(self):
        reading = to_reading(datetime(2026, 10, 29, 20, 0, 27), 100)
        self.assertEqual(reading.epoch, local_epoch(datetime(2026, 10, 29, 20, 0)))

    def test_timezone_is_dropped_without_shifting_the_clock(self):
        reading = to_reading(datetime(2026, 10, 29, 20, 0, tzinfo=timezone(timedelta(hours=2))), 100)
        self.assertIsNone(reading.device_time.tzinfo)
        self.assertEqual(reading.device_time.hour, 20)

    def test_same_minute_same_value_is_one_reading(self):
        self.assertEqual(
            to_reading(datetime(2026, 10, 29, 20, 0, 5), 120), to_reading(datetime(2026, 10, 29, 20, 0, 55), 120)
        )

    def test_off_scale_uses_the_accuchek_codes(self):
        high = to_reading(datetime(2026, 10, 29, 20, 0), 0, is_high=True)
        low = to_reading(datetime(2026, 10, 29, 20, 0), 0, is_low=True)
        self.assertEqual((high.mg_dl, high.off_scale), (601, "high"))
        self.assertEqual((low.mg_dl, low.off_scale), (9, "low"))

    def test_out_of_range_is_refused(self):
        for value in (0, 9.4, 600.6, 1000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                to_reading(datetime(2026, 10, 29, 20, 0), value)


class ThinTest(unittest.TestCase):
    def readings(self, minutes):
        base = datetime(2026, 10, 29, 6, 0)
        return [to_reading(base + timedelta(minutes=m), 100 + m) for m in minutes]

    def test_keeps_the_first_of_each_bucket(self):
        kept = thin(self.readings(range(0, 31)), 15)
        self.assertEqual([r.device_time.minute for r in kept], [0, 15, 30])

    def test_none_keeps_everything_sorted(self):
        shuffled = self.readings([5, 1, 3])
        self.assertEqual([r.device_time.minute for r in thin(shuffled, None)], [1, 3, 5])

    def test_same_bucket_on_two_days_is_not_merged(self):
        day2 = to_reading(datetime(2026, 10, 30, 6, 2), 110)
        kept = thin([*self.readings([2]), day2], 15)
        self.assertEqual(len(kept), 2)

    def test_empty(self):
        self.assertEqual(thin([], 15), ())


class CredentialsTest(unittest.TestCase):
    def env_file(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_environment_wins(self):
        env = {"LIBRELINKUP_EMAIL": "a@b.c", "LIBRELINKUP_PASSWORD": "pw", "LIBRELINKUP_REGION": "eu"}
        file = self.env_file("LIBRELINKUP_EMAIL=autre@b.c\nLIBRELINKUP_PASSWORD=autre\n")
        self.assertEqual(load_credentials(env, (file,)), Credentials("a@b.c", "pw", "eu"))

    def test_env_file_with_quotes_and_default_region(self):
        file = self.env_file("GEMINI_API_KEY=x\nLIBRELINKUP_EMAIL='a@b.c'\nLIBRELINKUP_PASSWORD=\"pw\"\n")
        self.assertEqual(load_credentials({}, (file,)), Credentials("a@b.c", "pw", "FR"))

    def test_first_file_wins(self):
        first = self.env_file("LIBRELINKUP_EMAIL=un@b.c\nLIBRELINKUP_PASSWORD=1\n")
        second = self.env_file("LIBRELINKUP_EMAIL=deux@b.c\nLIBRELINKUP_PASSWORD=2\n")
        self.assertEqual(load_credentials({}, (first, second)).email, "un@b.c")

    def test_missing_file_or_half_configured_is_none(self):
        file = self.env_file("LIBRELINKUP_EMAIL=a@b.c\n")
        self.assertIsNone(load_credentials({}, (file, Path("/nonexistent/.env"))))
        self.assertIsNone(load_credentials({}, ()))


class RegionsTest(unittest.TestCase):
    def test_default_region_is_listed(self):
        from services.device.librelinkup import DEFAULT_REGION, REGIONS

        self.assertIn(DEFAULT_REGION, REGIONS)

    def test_regions_match_the_installed_library(self):
        try:
            from pylibrelinkup import APIUrl
        except ImportError:
            self.skipTest("pylibrelinkup n'est pas installée")
        from services.device.librelinkup import REGIONS

        self.assertEqual(set(REGIONS), {member.name for member in APIUrl})


class SaveCredentialsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "glucofi" / ".env"

    def test_round_trip_with_awkward_passwords(self):
        for password in ("p w=1#", "abc'", "'abc'", '"abc"', "a\\b $x", "é€"):
            with self.subTest(password):
                save_credentials(Credentials("a@b.c", password, "EU"), self.path)
                self.assertEqual(load_credentials({}, (self.path,)), Credentials("a@b.c", password, "EU"))

    def test_file_is_private_even_when_it_already_existed_wide_open(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("GEMINI_API_KEY=abc\n")
        self.path.chmod(0o644)
        save_credentials(CREDENTIALS, self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_other_lines_are_kept_and_old_values_replaced(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("GEMINI_API_KEY=abc\nLIBRELINKUP_EMAIL=ancien\n# note\n")
        save_credentials(CREDENTIALS, self.path)
        lines = self.path.read_text().splitlines()
        self.assertEqual(lines[0], "GEMINI_API_KEY=abc")
        self.assertIn("# note", lines)
        self.assertEqual(sum(line.startswith("LIBRELINKUP_EMAIL=") for line in lines), 1)

    def test_none_removes_the_account_and_keeps_the_rest(self):
        save_credentials(CREDENTIALS, self.path)
        self.path.write_text("GEMINI_API_KEY=abc\n" + self.path.read_text())
        save_credentials(None, self.path)
        self.assertEqual(self.path.read_text(), "GEMINI_API_KEY=abc\n")
        self.assertIsNone(load_credentials({}, (self.path,)))

    def test_newline_in_a_value_is_refused(self):
        with self.assertRaises(ValueError):
            save_credentials(Credentials("a@b.c", "x\nGEMINI_API_KEY=pirate"), self.path)
        self.assertFalse(self.path.exists())


def fake_library(*, patients=1, graph=(), latest=None, sensors=("3MH00ABC",), error=None):
    """Remplace pylibrelinkup : mêmes noms que ceux dont fetch() se sert."""
    exceptions = types.SimpleNamespace(
        PyLibreLinkUpError=type("PyLibreLinkUpError", (Exception,), {}),
    )
    exceptions.AuthenticationError = type("AuthenticationError", (exceptions.PyLibreLinkUpError,), {})

    class FakeUrl(Enum):
        FR = "https://api-fr.libreview.io"
        US = "https://api-us.libreview.io"

    def measurement(timestamp, value, high=False, low=False):
        return types.SimpleNamespace(
            timestamp=timestamp, value_in_mg_per_dl=value, is_high=high, is_low=low
        )

    class FakeClient:
        instances = []

        def __init__(self, email, password, api_url):
            self.args = (email, password, api_url)
            FakeClient.instances.append(self)

        def authenticate(self):
            if error is not None:
                raise error(exceptions)

        def get_patients(self):
            return [object()] * patients

        def read(self, _patient):
            data = types.SimpleNamespace(
                graph_data=list(graph),
                active_sensors=[types.SimpleNamespace(sensor=types.SimpleNamespace(sn=sn)) for sn in sensors],
            )
            return types.SimpleNamespace(data=data)

        def latest(self, _patient):
            return latest

    module = types.ModuleType("pylibrelinkup")
    module.APIUrl, module.PyLibreLinkUp, module.exceptions = FakeUrl, FakeClient, exceptions
    module.measurement, module.FakeClient = measurement, FakeClient
    return module


def installed(module):
    return mock.patch.dict(sys.modules, {"pylibrelinkup": module})


class FetchTest(unittest.TestCase):
    def test_combines_graph_and_latest_and_names_the_sensor(self):
        m = fake_library().measurement
        graph = [m(datetime(2026, 10, 29, 19, 0, 10), 150), m(datetime(2026, 10, 29, 19, 15, 3), 160)]
        lib = fake_library(graph=graph, latest=m(datetime(2026, 10, 29, 20, 0, 27), 179.0))
        with installed(lib):
            result = fetch(CREDENTIALS, every_min=15)
        self.assertEqual([r.mg_dl for r in result.readings], [150, 160, 179])
        self.assertEqual(result.rejected, ())
        self.assertEqual((result.meter.model, result.meter.serial), ("FreeStyle Libre 3", "3MH00ABC"))
        self.assertEqual(lib.FakeClient.instances[0].args[2].name, "FR")

    def test_graph_empty_still_returns_the_latest_value(self):
        lib = fake_library(latest=fake_library().measurement(datetime(2026, 10, 29, 20, 0, 27), 179.0))
        with installed(lib):
            result = fetch(CREDENTIALS)
        self.assertEqual([r.mg_dl for r in result.readings], [179])

    def test_latest_repeated_in_graph_is_one_reading(self):
        m = fake_library().measurement
        point = m(datetime(2026, 10, 29, 20, 0, 5), 179.0)
        lib = fake_library(graph=[point], latest=m(datetime(2026, 10, 29, 20, 0, 40), 179.0))
        with installed(lib):
            self.assertEqual(len(fetch(CREDENTIALS, every_min=None).readings), 1)

    def test_invalid_measure_is_rejected_and_listed(self):
        m = fake_library().measurement
        lib = fake_library(graph=[m(datetime(2026, 10, 29, 19, 0), 3.0)], latest=m(datetime(2026, 10, 29, 20, 0), 120))
        with installed(lib):
            result = fetch(CREDENTIALS)
        self.assertEqual([r.mg_dl for r in result.readings], [120])
        self.assertEqual(len(result.rejected), 1)
        self.assertIn("hors plage", result.rejected[0])

    def test_no_sensor_means_no_meter(self):
        m = fake_library().measurement
        lib = fake_library(sensors=(), latest=m(datetime(2026, 10, 29, 20, 0), 120))
        with installed(lib):
            self.assertIsNone(fetch(CREDENTIALS).meter)

    def test_no_shared_patient(self):
        with installed(fake_library(patients=0)), self.assertRaisesRegex(LibreLinkUpError, "Aucun patient partagé"):
            fetch(CREDENTIALS)

    def test_wrong_password(self):
        lib = fake_library(error=lambda ex: ex.AuthenticationError("bad"))
        with installed(lib), self.assertRaisesRegex(LibreLinkUpError, "Identifiants LibreLinkUp refusés"):
            fetch(CREDENTIALS)

    def test_other_api_error_and_network_error(self):
        for error, expected in (
            (lambda ex: ex.PyLibreLinkUpError("boom"), "a refusé la demande : boom"),
            (lambda ex: ConnectionError("réseau coupé"), "injoignable : réseau coupé"),
        ):
            with self.subTest(expected=expected), installed(fake_library(error=error)):
                with self.assertRaisesRegex(LibreLinkUpError, expected):
                    fetch(CREDENTIALS)

    def test_unknown_region(self):
        with installed(fake_library()), self.assertRaisesRegex(LibreLinkUpError, "Région LibreLinkUp inconnue"):
            fetch(Credentials("a", "b", "MARS"))

    def test_library_not_installed(self):
        with mock.patch.dict(sys.modules, {"pylibrelinkup": None}), self.assertRaises(LibreLinkUpNotInstalled):
            fetch(CREDENTIALS)


if __name__ == "__main__":
    unittest.main()
