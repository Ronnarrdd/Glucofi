import json
import unittest
from datetime import date

from scripts.demo_export import build
from services.device import parse_output


class DemoExportTest(unittest.TestCase):
    def test_deterministic_and_readable_by_the_parser(self):
        a = build(30, date(2026, 9, 30))
        self.assertEqual(a, build(30, date(2026, 9, 30)))
        parsed = parse_output(json.dumps(a))
        self.assertEqual(parsed.rejected, ())
        self.assertEqual(len(parsed.readings), len(a["readings"]))
        days = {r.device_time.date() for r in parsed.readings}
        self.assertEqual((min(days), max(days), len(days)), (date(2026, 9, 1), date(2026, 9, 30), 30))
        self.assertTrue(any(r.meal is not None for r in parsed.readings))


if __name__ == "__main__":
    unittest.main()
