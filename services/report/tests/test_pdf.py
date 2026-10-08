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
            "raclette & <vin>", "Écartées", "01/09/2026 08:00 : 1,90 g/L", "2 mesures avec une note : Repas copieux 1, "
            "Mesure douteuse 1, texte libre 1.", "1 glycémie du matin écartée", "écartée", "comptée", "retenue",
        ):
            self.assertIn(shown, flat)

    def test_morning_dose_protocol_and_history_are_reported(self):
        from contracts import Titration
        from services.dosing import propose
        from services.report.pdf import ReportInput, build_report

        simple = DosingSettings(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)
        settings = replace(simple, morning_titration=Titration(0.90, 1.60, 1, 2))
        readings = [Reading(datetime(2026, 9, 5, 8), 120, int(datetime(2026, 9, 5, 8).timestamp()))]
        evening = datetime(2026, 9, 5, 19)
        readings.append(Reading(evening, 150, int(evening.timestamp()), note=ReadingNote((NoteTag.ILLNESS,), exclude_from_dosing=True)))
        late = datetime(2026, 9, 5, 20)
        readings.append(Reading(late, 80, int(late.timestamp())))
        changes = [DoseChange(datetime(2026, 9, 1, 12), 10, 6, DoseRule.START)]
        proposal = propose(readings, changes, settings, date(2026, 9, 5))
        sections = [("Dose du matin, selon la glycémie du soir (17:00-21:59)", ["Sous 0,90 g/L : baisser de 1 UI"])]
        history = [("03/09/2026 · Dr Test", ["Ajustement de la dose du matin : aucun → automatique"]), ("01/09/2026", [])]
        with tempfile.TemporaryDirectory() as tmp:
            out = build_report(
                ReportInput(
                    readings, changes, settings, datetime(2026, 9, 1), datetime(2026, 9, 6), "Patient fictif", proposal,
                    protocol=sections,
                    protocol_history=history,
                ),
                Path(tmp) / "rapport.pdf",
                dpi=40,
            )
            if shutil.which("pdftotext") is None:
                self.skipTest("pdftotext absent : texte du rapport non vérifié")
            text = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True, check=True).stdout
        flat = " ".join(text.split())
        for shown in (
            "Proposition : passer la dose du matin à 9 UI", "Proposition : dose du soir inchangée",
            "Dose du matin, selon la glycémie du soir (17:00-21:59)", "Historique du protocole", "03/09/2026 · Dr Test",
            "Ajustement de la dose du matin : aucun → automatique", "1 glycémie du soir écartée",
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
            "2 mesures avec une note : Malade 2, Alcool 1, texte libre 1. "
            "1 glycémie du matin écartée de l'ajustement de la dose.",
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


@unittest.skipUnless(HAS_DEPS, "matplotlib ou reportlab absent")
class InjectionsInTheReportTest(unittest.TestCase):
    """Observance et glycémies écartées après une dose non prise, dans le texte du rapport."""

    SETTINGS = DosingSettings(
        insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3, skip_after_missed_dose=True
    )

    def text(self, injections, generated=datetime(2026, 9, 11, 9, 0), readings=()):
        from services.report.pdf import ReportInput, build_report

        changes = [DoseChange(datetime(2026, 9, 1, 12), 10, 6, DoseRule.START)]
        with tempfile.TemporaryDirectory() as tmp:
            out = build_report(
                ReportInput(
                    list(readings), changes, self.SETTINGS, datetime(2026, 9, 1), datetime(2026, 9, 11), "Jean",
                    injections=injections, generated_at=generated,
                ),
                Path(tmp) / "rapport.pdf",
                dpi=40,
            )
            if shutil.which("pdftotext") is None:
                self.skipTest("pdftotext absent : texte du rapport non vérifié")
            raw = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True, check=True).stdout
        return " ".join(raw.split())

    def test_unused_journal_says_so_instead_of_a_zero_rate(self):
        flat = self.text([])
        self.assertIn("Journal des injections non renseigné sur cette période", flat)
        self.assertNotIn("Observance", flat)

    def test_adherence_table_and_days(self):
        from contracts import DoseTarget, Injection, InjectionState

        journal = [Injection(date(2026, 9, d), DoseTarget.EVENING, InjectionState.TAKEN) for d in (1, 2, 3, 4, 5, 6, 7)]
        journal += [Injection(date(2026, 9, 8), DoseTarget.EVENING, InjectionState.MISSED)]
        journal += [Injection(date(2026, 9, 1), DoseTarget.MORNING, InjectionState.TAKEN)]
        flat = self.text(journal)
        # dues : 1er au 10 septembre = 10 jours par dose (le 11, jour du rapport, n'est pas compté)
        for shown in (
            "Injections", "Observance", "Dose du soir 10 7 1 2 70 %", "Dose du matin 10 1 0 9 10 %",
            "Non prises, dose du soir : 08/09.", "Non renseignées, dose du soir : 09/09, 10/09.",
            "une dose non renseignée compte comme non prise",
        ):
            self.assertIn(shown, flat)

    def test_readings_set_aside_after_a_missed_dose_are_counted_and_marked(self):
        from contracts import DoseTarget, Injection, InjectionState

        readings = []
        for day, mg in ((2, 190), (3, 185), (4, 120)):
            t = datetime(2026, 9, day, 8)
            readings.append(Reading(t, mg, int(t.timestamp())))
        journal = [Injection(date(2026, 9, 2), DoseTarget.EVENING, InjectionState.MISSED)]
        flat = self.text(journal, readings=readings)
        self.assertIn("1 glycémie du matin écartée après une dose du soir non prise de l'ajustement.", flat)
        self.assertIn("écartée", flat)
        self.assertIn("ou parce que la dose qui précède est déclarée non prise", flat)
