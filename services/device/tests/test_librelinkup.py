import hashlib
import locale
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contracts import local_epoch
from services.device.tests.fake_llu import PATIENT_ID, TOKEN, USER_ID, FakeLlu, graph_data, item
from services.device.librelinkup import (
    Credentials,
    LibreLinkUpError,
    fetch_cgm,
    load_credentials,
    parse_us_timestamp,
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


def french_time_locale(test: unittest.TestCase) -> None:
    """LC_TIME en fr_FR pour la durée du test (GTK met le processus dans la langue de l'utilisateur)."""
    saved = locale.setlocale(locale.LC_TIME)
    test.addCleanup(locale.setlocale, locale.LC_TIME, saved)
    for name in ("fr_FR.UTF-8", "fr_FR.utf8", "fr_FR"):
        try:
            locale.setlocale(locale.LC_TIME, name)
            return
        except locale.Error:
            continue
    test.skipTest("locale fr_FR absente")


class ParseUsTimestampTest(unittest.TestCase):
    def test_hours_around_noon_and_midnight(self):
        for text, expected in (
            ("10/29/2026 8:22:28 PM", datetime(2026, 10, 29, 20, 22, 28)),
            ("10/29/2026 12:00:00 AM", datetime(2026, 10, 29, 0, 0, 0)),
            ("10/29/2026 12:30:00 PM", datetime(2026, 10, 29, 12, 30, 0)),
            ("1/2/2026 1:05:09 AM", datetime(2026, 1, 2, 1, 5, 9)),
            ("12/31/2026 11:59:59 PM", datetime(2026, 12, 31, 23, 59, 59)),
        ):
            with self.subTest(text):
                self.assertEqual(parse_us_timestamp(text), expected)

    def test_garbage_and_impossible_values_are_refused(self):
        for text in ("", "pas une date", "10/29/2026 20:22:28", "10/29/2026 0:10:00 AM", "10/29/2026 13:10:00 PM", "13/45/2026 1:00:00 AM"):
            with self.subTest(text), self.assertRaises(ValueError):
                parse_us_timestamp(text)

    def test_french_locale_does_not_matter(self):
        french_time_locale(self)
        self.assertEqual(parse_us_timestamp("10/29/2026 8:22:28 PM"), datetime(2026, 10, 29, 20, 22, 28))

    def test_the_bug_is_real_in_strptime(self):
        """Pourquoi parse_us_timestamp existe : strptime ne lit pas « PM » en fr_FR."""
        french_time_locale(self)
        with self.assertRaises(ValueError):
            datetime.strptime("10/29/2026 8:22:28 PM", "%m/%d/%Y %I:%M:%S %p")


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


class RegionsTest(unittest.TestCase):
    def test_default_region_is_listed(self):
        from services.device.librelinkup import DEFAULT_REGION, REGIONS

        self.assertIn(DEFAULT_REGION, REGIONS)
        self.assertEqual(len(set(REGIONS)), len(REGIONS))


class FetchTest(unittest.TestCase):
    def fetch(self, fake, every_min=15, credentials=CREDENTIALS):
        return fetch_cgm(credentials, every_min, transport=fake)

    def test_combines_graph_and_latest_and_names_the_sensor(self):
        fake = FakeLlu(graph_data(
            [item("10/29/2026 7:00:10 PM", 150), item("10/29/2026 7:15:03 PM", 160)], item("10/29/2026 8:00:27 PM", 179)
        ))
        result = self.fetch(fake)
        self.assertEqual([(r.device_time.strftime("%H:%M"), r.mg_dl) for r in result.readings], [("19:00", 150), ("19:15", 160), ("20:00", 179)])
        self.assertEqual(result.rejected, ())
        self.assertEqual((result.meter.model, result.meter.serial), ("FreeStyle Libre 3", "3MH00ABC"))

    def test_the_account_region_is_the_host_and_the_headers_say_who_asks(self):
        fake = FakeLlu(graph_data([], item("10/29/2026 8:00:27 PM", 120)))
        self.fetch(fake, credentials=Credentials("suivi@example.org", "secret", "DE"))
        login, patients, graph = fake.calls
        self.assertEqual((login["method"], login["host"], login["path"]), ("POST", "api-de.libreview.io", "/llu/auth/login"))
        self.assertEqual(login["body"], {"email": "suivi@example.org", "password": "secret"})
        self.assertNotIn("authorization", login["headers"])
        for call in (patients, graph):
            self.assertEqual(call["headers"]["authorization"], f"Bearer {TOKEN}")
            self.assertEqual(call["headers"]["account-id"], hashlib.sha256(USER_ID.encode()).hexdigest())
            self.assertEqual((call["headers"]["product"], call["headers"]["version"]), ("llu.android", "4.16.0"))
        self.assertEqual(graph["path"], f"/llu/connections/{PATIENT_ID}/graph")

    def test_graph_empty_still_returns_the_latest_value(self):
        result = self.fetch(FakeLlu(graph_data([], item("10/29/2026 8:00:27 PM", 179))))
        self.assertEqual([r.mg_dl for r in result.readings], [179])

    def test_no_current_value_is_fine(self):
        result = self.fetch(FakeLlu(graph_data([item("10/29/2026 7:00:10 PM", 150)], None)))
        self.assertEqual([r.mg_dl for r in result.readings], [150])

    def test_latest_repeated_in_graph_is_one_reading(self):
        fake = FakeLlu(graph_data([item("10/29/2026 8:00:05 PM", 179)], item("10/29/2026 8:00:40 PM", 179)))
        self.assertEqual(len(self.fetch(fake, every_min=None).readings), 1)

    def test_invalid_measures_are_rejected_and_listed_not_fatal(self):
        fake = FakeLlu(graph_data(
            [item("10/29/2026 7:00:00 PM", 3.0), {"Timestamp": "n'importe quoi", "ValueInMgPerDl": 100}, {"Timestamp": "10/29/2026 7:05:00 PM"}],
            item("10/29/2026 8:00:00 PM", 120),
        ))
        result = self.fetch(fake)
        self.assertEqual([r.mg_dl for r in result.readings], [120])
        self.assertEqual(len(result.rejected), 3)
        self.assertIn("hors plage", result.rejected[0])

    def test_off_scale_values_use_the_meter_codes(self):
        result = self.fetch(FakeLlu(graph_data([item("10/29/2026 7:00:00 PM", 400, high=True), item("10/29/2026 7:15:00 PM", 40, low=True)], None)))
        self.assertEqual([(r.mg_dl, r.off_scale) for r in result.readings], [(601, "high"), (9, "low")])

    def test_french_process_reads_pm(self):
        french_time_locale(self)
        result = self.fetch(FakeLlu(graph_data([], item("10/29/2026 8:22:28 PM", 179))))
        self.assertEqual(result.readings[0].device_time, datetime(2026, 10, 29, 20, 22))

    def test_no_sensor_means_no_meter(self):
        self.assertIsNone(self.fetch(FakeLlu(graph_data([], item("10/29/2026 8:00:00 PM", 120), sensor=None))).meter)

    def test_gzip_answers_are_read(self):
        fake = FakeLlu(graph_data([], item("10/29/2026 8:00:00 PM", 120)))
        fake.gzip = True
        self.assertEqual(len(self.fetch(fake).readings), 1)

    def test_no_shared_patient(self):
        with self.assertRaisesRegex(LibreLinkUpError, "Aucun patient partagé"):
            self.fetch(FakeLlu(patients=[]))

    def test_wrong_password(self):
        fake = FakeLlu()
        fake.login_replies = [(200, {"status": 2, "error": {"message": "notAuthenticated"}})]
        with self.assertRaisesRegex(LibreLinkUpError, "Identifiants LibreLinkUp refusés : vérifiez l'e-mail et le mot de passe\\.$"):
            self.fetch(fake)

    def test_http_failures(self):
        for status, expected in ((401, "Identifiants LibreLinkUp refusés"), (429, "Trop de demandes"), (500, "LibreLinkUp a répondu 500")):
            fake = FakeLlu()
            fake.fail["/llu/connections"] = (status, {}, b"{}")
            with self.subTest(status), self.assertRaisesRegex(LibreLinkUpError, expected):
                self.fetch(fake)

    def test_network_error(self):
        fake = FakeLlu()
        fake.network_error = ConnectionError("réseau coupé")
        with self.assertRaisesRegex(LibreLinkUpError, "injoignable : réseau coupé"):
            self.fetch(fake)

    def test_unreadable_or_unexpected_answers_are_clear_messages(self):
        fake = FakeLlu()
        fake.fail["/llu/connections"] = (200, {}, b"<html>pas du json")
        with self.assertRaisesRegex(LibreLinkUpError, "Réponse illisible"):
            self.fetch(fake)
        fake = FakeLlu(patients=[{"pas_de_patientId": 1}])
        with self.assertRaisesRegex(LibreLinkUpError, "inattendue.*l'API a peut-être changé"):
            self.fetch(fake)

    def test_unknown_region(self):
        with self.assertRaisesRegex(LibreLinkUpError, "Région LibreLinkUp inconnue : 'MARS'"):
            self.fetch(FakeLlu(), credentials=Credentials("a", "b", "MARS"))

    def test_the_session_is_not_reused_between_calls(self):
        fake = FakeLlu(graph_data([], item("10/29/2026 8:00:00 PM", 120)))
        self.fetch(fake)
        self.fetch(fake)
        self.assertEqual([c["path"] for c in fake.calls].count("/llu/auth/login"), 2)


if __name__ == "__main__":
    unittest.main()