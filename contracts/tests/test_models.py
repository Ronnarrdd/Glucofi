import dataclasses
import unittest

from contracts import PROTOCOL_FIELDS, DosingSettings


class DosingSettingsTest(unittest.TestCase):
    PROTOCOL = dict(insulin="Insuline test", low_g_l=0.80, high_g_l=1.50, step_ui=2, high_streak_days=3)

    def test_no_prescription_has_a_default(self):
        """Seule l'ordonnance fixe ces valeurs : aucune ne doit venir du code."""
        fields = {f.name: f for f in dataclasses.fields(DosingSettings)}
        for name in PROTOCOL_FIELDS:
            with self.subTest(field=name):
                self.assertIs(fields[name].default, dataclasses.MISSING)
                self.assertIs(fields[name].default_factory, dataclasses.MISSING)
        with self.assertRaises(TypeError):
            DosingSettings()

    def test_validation(self):
        DosingSettings(**self.PROTOCOL)
        for bad in (dict(insulin=" "), dict(low_g_l=1.5), dict(step_ui=0), dict(high_streak_days=0)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                DosingSettings(**dict(self.PROTOCOL, **bad))


if __name__ == "__main__":
    unittest.main()
