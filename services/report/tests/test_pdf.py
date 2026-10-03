import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from contracts import DoseChange, DoseRule, DosingSettings, NoteTag, Reading, ReadingNote

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

    def test_notes_appear_with_the_morning_status_and_the_excluded_readings(self):
        from services.dosing import apply_proposal, propose
        from services.report.pdf import ReportInput, build_report

        settings = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
        readings = []
        for day, mg in ((1, 190), (2, 185), (3, 180), (4, 175), (5, 60)):
            t = datetime(2026, 9, day, 8)
            readings.append(Reading(t, mg, int(t.timestamp())))
        readings[0] = replace(readings[0], note=ReadingNote((NoteTag.LARGE_MEAL,), "raclette & <vin>", True))
        readings[4] = replace(readings[4], note=ReadingNote((NoteTag.DOUBTFUL,), exclude_from_dosing=True))
        start = DoseChange(datetime(2026, 8, 31, 20), 10, 6, DoseRule.START)
        increase = apply_proposal(propose(readings[:4], [start], settings, date(2026, 9, 4)), datetime(2026, 9, 4, 20))
        self.assertEqual(increase.excluded, ("01/09/2026 08:00 : 1,90 g/L (Repas copieux · raclette & <vin>)",))
        changes = [start, increase]
        proposal = propose(readings, changes, settings, date(2026, 9, 5))
        with tempfile.TemporaryDirectory() as tmp:
            out = build_report(
                ReportInput(readings, changes, settings, datetime(2026, 9, 1), datetime(2026, 9, 6), "Jean", proposal),
                Path(tmp) / "rapport.pdf",
                dpi=40,
            )
            if shutil.which("pdftotext") is None:
                self.skipTest("pdftotext absent : texte du rapport non vérifié")
            text = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True, check=True).stdout
        flat = " ".join(text.split())
        for shown in (
            "raclette & <vin>", "Écartées", "01/09/2026 08:00 : 1,90 g/L", "2 mesure(s) avec une note : Repas copieux 1, "
            "Mesure douteuse 1, texte libre 1.", "1 glycémie(s) du matin écartée(s)", "écartée", "comptée", "retenue",
        ):
            self.assertIn(shown, flat)


@unittest.skipUnless(HAS_DEPS, "matplotlib ou reportlab absent")
class NotesSummaryTest(unittest.TestCase):
    SETTINGS = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)

    def r(self, hour: int, mg: int, note: ReadingNote | None = None) -> Reading:
        t = datetime(2026, 9, 2, hour)
        return Reading(t, mg, int(t.timestamp()), note=note)

    def test_no_note_no_summary(self):
        from services.report.pdf import notes_summary

        self.assertEqual(notes_summary([self.r(8, 120)], self.SETTINGS), "")

    def test_counts_per_tag_and_exclusions(self):
        from services.report.pdf import notes_summary

        readings = [
            self.r(8, 200, ReadingNote((NoteTag.ILLNESS, NoteTag.ALCOHOL), exclude_from_dosing=True)),
            self.r(14, 250, ReadingNote((NoteTag.ILLNESS,), "fièvre", exclude_from_dosing=True)),
            self.r(20, 120),
        ]
        self.assertEqual(
            notes_summary(readings, self.SETTINGS),
            "2 mesure(s) avec une note : Malade 2, Alcool 1, texte libre 1. "
            "1 glycémie(s) du matin écartée(s) de l'ajustement de la dose.",
        )

    def test_morning_status(self):
        from services.dosing import morning_readings
        from services.report.pdf import morning_status

        excluded = self.r(7, 200, ReadingNote((NoteTag.LARGE_MEAL,), exclude_from_dosing=True))
        kept = self.r(9, 120)
        low = replace(self.r(8, 60, ReadingNote((NoteTag.DOUBTFUL,), exclude_from_dosing=True)), device_time=datetime(2026, 9, 3, 8))
        readings = [excluded, kept, low]
        retained = {m.reading for m in morning_readings(readings, self.SETTINGS)}
        self.assertEqual([morning_status(x, retained, self.SETTINGS) for x in readings], ["écartée", "retenue", "comptée"])
        self.assertEqual(morning_status(self.r(15, 120), retained, self.SETTINGS), "")


if __name__ == "__main__":
    unittest.main()
