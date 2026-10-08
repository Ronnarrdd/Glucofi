import unittest

from dataclasses import replace
from unittest import mock

from contracts import DoseTarget, LowTier, Titration
from evals.replay_history import (
    FULL,
    FULL_SKIPPING,
    SKIPPING,
    changed_mornings,
    decisions_changed,
    evening_variety,
    replay,
    synthetic_journal,
    synthetic_patient,
    with_notes,
)


def full_failures(seeds=range(8)) -> tuple[list, list[str]]:
    rows, failures = [], []
    for seed in seeds:
        patient = evening_variety(synthetic_patient(seed, days=45), seed)
        for source, readings in ((f"c{seed}", patient), (f"cn{seed}", with_notes(patient, seed))):
            r, f = replay(source, readings, settings=FULL)
            rows += r
            failures += f
    return rows, failures


class ReplaySmokeTest(unittest.TestCase):
    """Version rapide de l'eval de rejeu, lancée à chaque commit."""

    def test_engine_matches_oracle_on_synthetic_patients(self):
        for seed in range(8):
            rows, failures = replay(f"synthetique-{seed}", synthetic_patient(seed, days=45))
            self.assertTrue(rows)
            self.assertEqual(failures, [], f"graine {seed}")

    def test_marked_patients_exercise_the_marker_rule(self):
        marked = synthetic_patient(1, days=45)
        self.assertTrue(any(r.meal is not None for r in marked))
        self.assertGreater(changed_mornings(marked), 0)
        self.assertEqual(changed_mornings(synthetic_patient(0, days=45)), 0)

    def test_engine_matches_oracle_with_notes(self):
        changed = low_marked = 0
        for seed in range(8):
            plain, _ = replay(f"synthetique-{seed}", synthetic_patient(seed, days=45))
            noted = with_notes(synthetic_patient(seed, days=45), seed)
            low_marked += sum(r.note is not None and r.note.exclude_from_dosing and r.mg_dl < 80 for r in noted)
            rows, failures = replay(f"synthetique-notes-{seed}", noted)
            self.assertEqual(failures, [], f"graine {seed}")
            changed += decisions_changed(plain, rows)
        self.assertGreater(changed, 0)
        self.assertGreater(low_marked, 0)

    def test_oracle_catches_an_engine_that_excludes_low_mornings(self):
        """Mutation : sans le garde-fou du seuil bas, une hypo écartée masque une baisse et l'eval échoue."""
        from unittest import mock

        with mock.patch("services.dosing.engine.can_exclude", lambda _r, _s: True):
            failures = [f for seed in range(8) for f in replay(f"n{seed}", with_notes(synthetic_patient(seed, days=45), seed))[1]]
        self.assertTrue(any("glycémie basse écartée" in f for f in failures))


    def test_full_protocol_matches_oracle(self):
        """Paliers et dose du matin : chaque dose suit l'oracle, et les deux règles du matin se déclenchent."""
        rows, failures = full_failures()
        self.assertEqual(failures, [])
        rules = {row.engine_rule for row in rows}
        self.assertLessEqual({"decrease_low_evening", "increase_high_evenings", "increase_high_mornings"}, rules)
        self.assertTrue(any(abs(row.ui_after - row.ui_before) > 2 for row in rows), "aucun palier exercé")

    def test_oracle_catches_a_shared_reset_of_both_doses(self):
        """Mutation : valider une dose remet aussi à zéro le décompte de l'autre."""
        from services.dosing import engine

        shared = lambda changes, _target: engine._ordered(changes)[-1]
        with mock.patch("services.dosing.engine.tracking_start", shared):
            _, failures = full_failures()
        self.assertTrue(failures)

    def test_oracle_catches_ignored_tiers(self):
        """Mutation : seuls le seuil bas et le pas de base comptent, les paliers sont oubliés."""
        base_only = property(lambda t: (LowTier(t.low_g_l, t.step_ui),))
        with mock.patch.object(Titration, "all_low_tiers", base_only):
            _, failures = full_failures()
        self.assertTrue(any("decrease" in f for f in failures))


def journal_run(seeds=range(8), full=False):
    """(lignes avec journal, échecs, lignes du même patient sans le réglage)."""
    rows, failures, plain_rows = [], [], []
    for seed in seeds:
        base = synthetic_patient(seed, days=45)
        patient = evening_variety(base, seed) if full else base
        journal = synthetic_journal(patient, seed)
        skipping = FULL_SKIPPING if full else SKIPPING
        r, f = replay(f"j{seed}", patient, settings=skipping, journal=journal)
        rows += r
        failures += f
        plain_rows += replay(f"p{seed}", patient, settings=FULL if full else replace(skipping, skip_after_missed_dose=False))[0]
    return rows, failures, plain_rows


class JournalReplaySmokeTest(unittest.TestCase):
    """Journal des injections : l'oracle relit la règle (dose du soir de la veille, jamais sous le seuil bas)."""

    def test_engine_matches_oracle_with_a_journal(self):
        rows, failures, plain = journal_run()
        self.assertEqual(failures, [])
        self.assertGreater(decisions_changed(plain, rows), 0, "le journal ne change aucune décision : eval sans effet")

    def test_full_protocol_with_a_journal_matches_oracle(self):
        rows, failures, _ = journal_run(full=True)
        self.assertEqual(failures, [])
        self.assertTrue(rows)

    def test_oracle_catches_an_engine_that_ignores_the_journal(self):
        with mock.patch("services.dosing.engine.missed_dose_behind", lambda *_a, **_k: None):
            _, failures, _ = journal_run()
        self.assertTrue(failures)

    def test_oracle_catches_an_engine_that_treats_unset_as_missed(self):
        real = __import__("services.adherence", fromlist=["missed_doses"]).missed_doses

        def lenient(injections):
            injections = list(injections)
            declared = {(i.day, i.target) for i in injections}
            days = {d for d, _t in declared}
            extra = {(d, t) for d in days for t in (DoseTarget.EVENING, DoseTarget.MORNING)} - declared
            return real(injections) | frozenset(extra)

        with mock.patch("services.dosing.engine.missed_doses", lenient):
            _, failures, _ = journal_run()
        self.assertTrue(failures)

    def test_oracle_catches_low_readings_set_aside_after_a_missed_dose(self):
        with mock.patch("services.dosing.engine.can_exclude", lambda _r, _s: True):
            _, failures, _ = journal_run()
        self.assertTrue(any("glycémie basse écartée" in f for f in failures))

    def test_oracle_catches_the_wrong_day(self):
        with mock.patch("services.dosing.engine.dose_before", lambda day, target: (day, target)):
            _, failures, _ = journal_run()
        self.assertTrue(failures)


if __name__ == "__main__":
    unittest.main()
