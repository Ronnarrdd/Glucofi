import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from contracts import DoseChange, DoseRule, DosingSettings, Reading

HAS_DEPS = all(importlib.util.find_spec(m) for m in ("matplotlib", "reportlab"))


def sample() -> list[Reading]:
    out = []
    start = datetime(2026, 9, 1)
    for d in range(14):
        for hour, mg in ((8, 60 + d * 12), (13, 140), (20, 210)):
            t = start + timedelta(days=d, hours=hour)
            out.append(Reading(t, mg, int(t.timestamp())))
    return out


@unittest.skipUnless(HAS_DEPS, "matplotlib ou reportlab absent")
class PdfTest(unittest.TestCase):
    def test_report_is_valid_pdf(self):
        from services.dosing import propose
        from services.report.pdf import ReportInput, build_report

        settings = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
        readings = sample()
        changes = [
            DoseChange(datetime(2026, 8, 31, 20), 10, 6, DoseRule.START),
            DoseChange(datetime(2026, 9, 1, 20), 10, 4, DoseRule.DECREASE_LOW_MORNING, ("01/09/2026 08:00 : 0,60 g/L",)),
        ]
        proposal = propose(readings, changes, settings, date(2026, 9, 14))
        with tempfile.TemporaryDirectory() as tmp:
            out = build_report(
                ReportInput(readings, changes, settings, datetime(2026, 9, 1), datetime(2026, 9, 15), "Jean Test", proposal),
                Path(tmp) / "rapport.pdf",
                dpi=40,
            )
            data = out.read_bytes()
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertIn(b"%%EOF", data[-1024:])
        self.assertGreater(len(data), 10_000)

    def test_empty_period(self):
        from services.report.pdf import ReportInput, build_report

        with tempfile.TemporaryDirectory() as tmp:
            out = build_report(
                ReportInput([], [], DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3), datetime(2026, 9, 1), datetime(2026, 9, 15)),
                Path(tmp) / "vide.pdf",
                dpi=40,
            )
            self.assertTrue(out.read_bytes().startswith(b"%PDF-"))

    def test_user_text_and_off_scale_values_are_escaped(self):
        """« LO (< 0,10 g/L) », un « & » ou un « < » saisis cassaient le balisage ReportLab."""
        from services.dosing import propose
        from services.report.pdf import ReportInput, build_report

        settings = DosingSettings(insulin="Insuline <test> & co", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
        lo = Reading(datetime(2026, 9, 14, 8), 9, int(datetime(2026, 9, 14, 8).timestamp()))
        changes = [
            DoseChange(datetime(2026, 8, 31, 20), 10, 6, DoseRule.START),
            DoseChange(datetime(2026, 9, 1, 20), 10, 4, DoseRule.DECREASE_LOW_MORNING, ("01/09/2026 08:00 : LO (< 0,10 g/L)",)),
            DoseChange(datetime(2026, 9, 2, 20), 10, 4, DoseRule.MANUAL, note="Dr A & B <urgent>"),
        ]
        proposal = propose([lo], changes, settings, date(2026, 9, 14))
        self.assertIn("<", proposal.reason)
        with tempfile.TemporaryDirectory() as tmp:
            out = build_report(
                ReportInput([lo], changes, settings, datetime(2026, 9, 1), datetime(2026, 9, 15), "Jean & <Marie>", proposal),
                Path(tmp) / "rapport.pdf",
                dpi=40,
            )
            self.assertTrue(out.read_bytes().startswith(b"%PDF-"))
            if shutil.which("pdftotext") is None:
                self.skipTest("pdftotext absent : texte du rapport non vérifié")
            text = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True, check=True).stdout
        for shown in ("Jean & <Marie>", "Insuline <test> & co", "Dr A & B <urgent>", "LO (< 0,10 g/L)"):
            self.assertIn(shown, text)


if __name__ == "__main__":
    unittest.main()
