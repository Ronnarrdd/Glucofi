import unittest

from evals.replay_history import changed_mornings, decisions_changed, replay, synthetic_patient, with_notes


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


if __name__ == "__main__":
    unittest.main()
