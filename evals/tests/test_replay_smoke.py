import unittest

from unittest import mock

from contracts import LowTier, Titration
from evals.replay_history import FULL, changed_mornings, decisions_changed, evening_variety, replay, synthetic_patient, with_notes


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


if __name__ == "__main__":
    unittest.main()
