import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from evals.store_migration import main
from services.store.tests.test_store import V1_ROWS, V1_SCHEMA, V2_SCHEMA, TimezoneParis


class StoreMigrationSmokeTest(TimezoneParis, unittest.TestCase):
    """Le rapport de migration réussit sur une base v1 et ne touche pas l'original."""

    def test_report_on_v1_copy(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source = Path(tmp.name) / "glucofi.db"
        db = sqlite3.connect(source)
        db.executescript(V1_SCHEMA)
        db.executemany(
            "INSERT INTO readings (epoch, device_time, mg_dl, source, imported_at) VALUES (?, ?, ?, 'lecteur', 'x')",
            V1_ROWS,
        )
        db.commit()
        db.close()
        original = source.read_bytes()

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--db", str(source), "--out", str(Path(tmp.name) / "rapport")])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("| corrigée -1 h (été) | 2 |", out.getvalue())
        self.assertIn("Migration v2 -> v3", out.getvalue())
        self.assertEqual(source.read_bytes(), original)

    def test_reimport_fills_markers_without_duplicates(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        source = Path(tmp.name) / "glucofi.db"
        db = sqlite3.connect(source)
        db.executescript(V2_SCHEMA)
        db.close()
        export = Path(tmp.name) / "accuchek.json"
        export.write_text(
            '{"format": 2, "meter": null, "clock": null, "glucose": {"announced":2, "received":2}, '
            '"meal": {"announced":1, "received":1, "unmatched":0}, "readings": ['
            '{"id":0,"epoch":1782970560,"timestamp":"2026/07/02 07:36","mg/dL":120,"meal":"fasting"},'
            '{"id":1,"epoch":1782992160,"timestamp":"2026/07/02 13:36","mg/dL":180}]}'
        )
        original = source.read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--db", str(source), "--out", str(Path(tmp.name) / "rapport"), "--reimport", str(export)])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("| marqueur ajouté (fasting) | 1 |", out.getvalue())
        self.assertIn("| mesure nouvelle | 1 |", out.getvalue())
        self.assertIn("1 nouvelles, 1 marqueurs ajoutés", out.getvalue())
        self.assertEqual(source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
