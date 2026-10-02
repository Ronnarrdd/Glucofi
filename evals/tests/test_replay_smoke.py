import unittest

from evals.replay_history import changed_mornings, replay, synthetic_patient


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


if __name__ == "__main__":
    unittest.main()
