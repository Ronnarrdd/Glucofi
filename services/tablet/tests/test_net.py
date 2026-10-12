import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from contracts import DoseChange, DoseRule, DosingSettings, Reading
from contracts.tablet_sync import NET_MAX_FAILURES, NET_PORT
from services.store import Store
from services.tablet import NetLink, Pairing, TabletError, TabletLink, connect_link, forget_pairing, load_pairing, save_pairing, secure, sync
from services.tablet.net import Found, PairingRefused, discover, pair, parse_address, parse_avahi
from services.tablet.tests.fake_net_tablet import FakeNetTablet
from services.tablet.tests.test_sync import PROTOCOL, PcStore, content, reading

VECTOR = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "tablet_net_vector.json").read_text())


class SecureTest(unittest.TestCase):
    def test_vector_is_what_the_tablet_computes(self):
        """Mêmes octets que le test Kotlin NetCryptoTest : le fichier de contrat fait foi des deux côtés."""
        secret = bytes.fromhex(VECTOR["secret"])
        self.assertEqual(secure.format_code(secret), VECTOR["code"])
        self.assertEqual(secure.parse_code(VECTOR["code"].lower().replace("-", " ")), secret)
        key = secure.derive_key(secret)
        self.assertEqual(key.hex(), VECTOR["key"])
        challenge = bytes.fromhex(VECTOR["challenge"])
        sealed = secure.seal(key, challenge, VECTOR["method"], VECTOR["direction"], bytes.fromhex(VECTOR["frame"]), bytes.fromhex(VECTOR["nonce"]))
        self.assertEqual(sealed.hex(), VECTOR["sealed"])
        self.assertEqual(secure.unseal(key, challenge, VECTOR["method"], VECTOR["direction"], sealed).hex(), VECTOR["frame"])

    def test_wrong_key_challenge_method_direction_or_byte_are_refused(self):
        key, challenge = secure.derive_key(b"0123456789"), b"c" * 16
        sealed = secure.seal(key, challenge, "export", "rep", b"x" * 20)
        for args in (
            (secure.derive_key(b"9876543210"), challenge, "export", "rep", sealed),
            (key, b"d" * 16, "export", "rep", sealed),
            (key, challenge, "merge", "rep", sealed),
            (key, challenge, "export", "req", sealed),
            (key, challenge, "export", "rep", sealed[:-1] + bytes([sealed[-1] ^ 1])),
            (key, challenge, "export", "rep", b"court"),
        ):
            with self.assertRaises(secure.BadMessage):
                secure.unseal(*args)

    def test_codes(self):
        secret = secure.new_secret()
        self.assertEqual(secure.parse_code(secure.format_code(secret)), secret)
        for bad in ("", "ABCD-EFGH", "AAAA-AAAA-AAAA-AAA0", "AAAA-AAAA-AAAA-AAAA-AAAA"):
            with self.assertRaises(ValueError, msg=bad):
                secure.parse_code(bad)

    def test_frames(self):
        self.assertEqual(secure.unpack(secure.pack({"a": "é"}, b"\x00\xff")), ({"a": "é"}, b"\x00\xff"))
        for bad in (b"", b"\x00\x00\x00\x09{}", b"\x00\x00\x00\x02[]", b"\x00\x00\x00\x01{"):
            with self.assertRaises(secure.BadMessage):
                secure.unpack(bad)


class WithoutCryptographyTest(unittest.TestCase):
    """python3-cryptography manque : Glucofi démarre et le câble marche ; seul le Wi-Fi le dit."""

    def test_package_imports_and_wifi_explains(self):
        import importlib
        import sys

        blocked = {name: None for name in ("cryptography", "cryptography.exceptions", "cryptography.hazmat.primitives",
                                           "cryptography.hazmat.primitives.ciphers.aead", "cryptography.hazmat.primitives.kdf.hkdf")}
        fresh = {n: m for n, m in sys.modules.items() if n.startswith("services.tablet")}
        for name in fresh:
            del sys.modules[name]
        try:
            from unittest import mock

            with mock.patch.dict(sys.modules, blocked):
                tablet = importlib.import_module("services.tablet")
                pairing = tablet.Pairing("00" * 10, "127.0.0.1", 9)
                with self.assertRaisesRegex(tablet.TabletError, "python3-cryptography"):
                    tablet.NetLink(pairing).hello()
        finally:
            for name in [n for n in sys.modules if n.startswith("services.tablet")]:
                del sys.modules[name]
            sys.modules.update(fresh)


class NetSyncTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        (root / "pc").mkdir()
        self.pc = Store(root / "pc" / "glucofi.db")
        self.addCleanup(self.pc.close)
        self.pc.set_setting("patient_name", "Patient fictif")
        self.pc.save_dosing_settings(PROTOCOL, effective=datetime(2026, 9, 1, 9))
        self.pc.add_dose_change(DoseChange(datetime(2026, 9, 1, 12), 10, 6, DoseRule.START))
        self.pc.import_readings([reading("2026-09-02T07:30", 150)], "lecteur")
        self.tablet = FakeNetTablet(root / "tablette")
        self.addCleanup(self.tablet.close)
        self.tablet.store.import_readings([reading("2026-09-03T07:20", 160)], "lecteur")
        self.pairing = Pairing(self.tablet.secret.hex(), "127.0.0.1", self.tablet.port)
        self.workdir = root / "work"
        self.workdir.mkdir()

    def link(self) -> NetLink:
        return NetLink.connect(self.pairing, find=lambda: [])

    def test_sync_merges_both_ways_and_leaves_nothing(self):
        result = sync(PcStore(self.pc), self.link(), self.workdir)
        self.assertEqual(self.pc.count_readings(), 2)
        self.assertEqual(self.tablet.store.count_readings(), 2)
        self.assertEqual(content(self.pc), content(self.tablet.store))
        self.assertTrue(result.changed)
        self.assertEqual(result.device.model, "Wi-Fi")
        self.assertEqual(self.tablet.calls.count("export"), 1)
        self.assertEqual(self.tablet.calls[-1], "merge")

    def test_second_sync_changes_nothing(self):
        sync(PcStore(self.pc), self.link(), self.workdir)
        again = sync(PcStore(self.pc), self.link(), self.workdir)
        self.assertFalse(again.changed)
        self.assertEqual(self.pc.count_readings(), 2)

    def test_wrong_code_is_refused_without_detail_then_locks(self):
        wrong = Pairing(secure.new_secret().hex(), "127.0.0.1", self.tablet.port)
        with self.assertRaises(PairingRefused) as ctx:
            NetLink(wrong).hello()
        self.assertIn("couplage", str(ctx.exception))
        self.assertEqual(self.tablet.calls, [])
        for _ in range(NET_MAX_FAILURES):
            try:
                NetLink(wrong).hello()
            except TabletError:
                pass
        with self.assertRaises(TabletError) as ctx:
            self.link()  # même la bonne clé attend la fin du verrou
        self.assertIn("minute", str(ctx.exception))

    def test_replayed_challenge_is_refused(self):
        import urllib.request

        from contracts.tablet_sync import NET_CALL_PATH, NET_CHALLENGE_HEADER, NET_CHALLENGE_PATH

        base = f"http://127.0.0.1:{self.tablet.port}"
        challenge = bytes.fromhex(urllib.request.urlopen(base + NET_CHALLENGE_PATH).read().decode())
        body = secure.seal(self.tablet.key, challenge, "hello", "req", secure.pack({}))

        def post():
            request = urllib.request.Request(base + NET_CALL_PATH + "hello", data=body, headers={NET_CHALLENGE_HEADER: challenge.hex()}, method="POST")
            try:
                return urllib.request.urlopen(request).status
            except urllib.error.HTTPError as exc:
                return exc.code

        self.assertEqual(post(), 200)
        self.assertEqual(post(), 403)

    def test_tablet_refusal_is_shown(self):
        self.tablet.fail["merge"] = "La base reçue est celle d'une version trop récente."
        link = self.link()
        db = self.pc.export_to(self.workdir / "pc.db")
        with self.assertRaisesRegex(TabletError, "trop récente"):
            link.send(db)

    def test_version_mismatch(self):
        self.tablet.sync_version = 99
        with self.assertRaisesRegex(TabletError, "version 99"):
            NetLink(self.pairing).hello()

    def test_unreachable_tablet_says_what_to_do(self):
        self.tablet.server.shutdown()
        self.tablet.server.server_close()
        with self.assertRaisesRegex(TabletError, "Ouvrez Glucofi sur la tablette"):
            NetLink.connect(self.pairing, find=lambda: [])

    def test_address_changed_is_found_again(self):
        stale = self.pairing.at("127.0.0.2", 9)  # rien n'écoute là
        link = NetLink.connect(stale, find=lambda: [Found("127.0.0.1", self.tablet.port, "Glucofi")])
        self.assertEqual((link.pairing.host, link.pairing.port), ("127.0.0.1", self.tablet.port))


class PairingFileTest(unittest.TestCase):
    def test_roundtrip_private_file_and_forget(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_pairing(Path(tmp)))
            pairing = Pairing("00" * 10, "192.168.1.20", NET_PORT)
            save_pairing(Path(tmp), pairing)
            self.assertEqual(load_pairing(Path(tmp)), pairing)
            self.assertEqual((Path(tmp) / "tablet-wifi.json").stat().st_mode & 0o777, 0o600)
            (Path(tmp) / "tablet-wifi.json").write_text("{pas du json")
            self.assertIsNone(load_pairing(Path(tmp)))
            save_pairing(Path(tmp), pairing)
            forget_pairing(Path(tmp))
            forget_pairing(Path(tmp))
            self.assertIsNone(load_pairing(Path(tmp)))


class DiscoverTest(unittest.TestCase):
    OUTPUT = (
        "+;wlan0;IPv4;Glucofi;_glucofi._tcp;local\n"
        "=;wlan0;IPv6;Glucofi;_glucofi._tcp;local;android.local;fe80::1;47831;\n"
        "=;wlan0;IPv4;Glucofi;_glucofi._tcp;local;android.local;192.168.1.20;47831;\n"
        "=;wlan0;IPv4;Glucofi;_glucofi._tcp;local;android.local;192.168.1.20;47831;\n"
        "=;wlan0;IPv4;cassé;_glucofi._tcp;local;x;1.2.3.4;port\n"
    )

    def test_parse_keeps_ipv4_once(self):
        self.assertEqual(parse_avahi(self.OUTPUT), [Found("192.168.1.20", 47831, "Glucofi")])

    def test_discover_without_avahi_is_empty(self):
        def missing(args, timeout):
            raise FileNotFoundError("avahi-browse")

        self.assertEqual(discover(run=missing), [])
        self.assertEqual(discover(run=lambda a, t: subprocess.CompletedProcess(a, 0, self.OUTPUT, "")), [Found("192.168.1.20", 47831, "Glucofi")])
        self.assertEqual(discover(run=lambda a, t: (_ for _ in ()).throw(subprocess.TimeoutExpired(a, t))), [])


class ConnectLinkTest(unittest.TestCase):
    """Wi-Fi d'abord s'il y a un couplage, USB en secours, messages des deux côtés."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.tablet = FakeNetTablet(self.root / "tablette")
        self.addCleanup(self.tablet.close)

    def test_no_pairing_uses_usb_and_hints_at_wifi_on_failure(self):
        sentinel = object()
        self.assertIs(connect_link(self.root, lambda: sentinel), sentinel)

        def no_cable():
            raise TabletError("Aucune tablette trouvée.")

        with self.assertRaisesRegex(TabletError, r"Aucune tablette trouvée\.\n\nSans câble : menu > Coupler"):
            connect_link(self.root, no_cable)

    def test_pairing_prefers_wifi_and_never_touches_usb(self):
        save_pairing(self.root, Pairing(self.tablet.secret.hex(), "127.0.0.1", self.tablet.port))
        link = connect_link(self.root, lambda: self.fail("USB ne doit pas servir"))
        self.assertIsInstance(link, NetLink)

    def test_wifi_failure_falls_back_to_usb_then_reports_both(self):
        save_pairing(self.root, Pairing(self.tablet.secret.hex(), "127.0.0.1", 9))
        sentinel = object()
        self.assertIs(connect_link(self.root, lambda: sentinel, find=lambda: []), sentinel)

        def no_cable():
            raise TabletError("Aucune tablette trouvée.")

        with self.assertRaises(TabletError) as ctx:
            connect_link(self.root, no_cable, find=lambda: [])
        self.assertIn("Par le Wi-Fi", str(ctx.exception))
        self.assertIn("Par USB : Aucune tablette trouvée.", str(ctx.exception))

    def test_new_address_is_remembered(self):
        save_pairing(self.root, Pairing(self.tablet.secret.hex(), "127.0.0.2", 9))
        connect_link(self.root, lambda: self.fail("USB ne doit pas servir"), find=lambda: [Found("127.0.0.1", self.tablet.port, "Glucofi")])
        self.assertEqual(load_pairing(self.root).host, "127.0.0.1")


class PairTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.tablet = FakeNetTablet(self.root / "tablette")
        self.addCleanup(self.tablet.close)
        self.code = secure.format_code(self.tablet.secret)

    def test_pairs_with_typed_address_and_keeps_it_privately(self):
        pairing = pair(self.root, self.code.lower(), f"127.0.0.1:{self.tablet.port}")
        self.assertEqual(load_pairing(self.root), pairing)
        self.assertEqual(self.tablet.calls, ["hello"])

    def test_pairs_with_discovered_tablet(self):
        pairing = pair(self.root, self.code, find=lambda: [Found("127.0.0.1", self.tablet.port, "Glucofi")])
        self.assertEqual((pairing.host, pairing.port), ("127.0.0.1", self.tablet.port))

    def test_tries_next_discovered_tablet(self):
        pair(self.root, self.code, find=lambda: [Found("127.0.0.1", 9, "autre"), Found("127.0.0.1", self.tablet.port, "Glucofi")])
        self.assertEqual(load_pairing(self.root).port, self.tablet.port)

    def test_wrong_code_keeps_nothing(self):
        wrong = secure.format_code(secure.new_secret())
        with self.assertRaisesRegex(TabletError, "Code de couplage refusé"):
            pair(self.root, wrong, f"127.0.0.1:{self.tablet.port}")
        self.assertIsNone(load_pairing(self.root))

    def test_bad_inputs_have_clear_messages(self):
        with self.assertRaisesRegex(TabletError, "incomplet"):
            pair(self.root, "ABCD", "127.0.0.1")
        with self.assertRaisesRegex(TabletError, "Aucune tablette Glucofi trouvée"):
            pair(self.root, self.code, find=lambda: [])
        with self.assertRaisesRegex(TabletError, "Port"):
            pair(self.root, self.code, "127.0.0.1:abc")
        self.assertEqual(parse_address(" 10.0.0.2 "), ("10.0.0.2", NET_PORT))
        self.assertEqual(parse_address("10.0.0.2:5000"), ("10.0.0.2", 5000))
        with self.assertRaises(ValueError):
            parse_address(":5000")
