import io
import re
import tokenize
import unittest
from pathlib import Path

from contracts import count_fr

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ("app", "contracts", "services")
# « mesure(s) », « ajouté(es) », « lue(e)s » : accord à remplacer par count_fr
PLACEHOLDER = re.compile(r"\w\((?:s|es|e)\)", re.UNICODE)
STRING_TOKENS = {tokenize.STRING} | {getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}


def placeholders(source: str) -> list[tuple[int, str]]:
    """(ligne, chaîne) de chaque littéral contenant un « (s) » ; les appels comme int(e) ne comptent pas."""
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return [(t.start[0], t.string.strip()) for t in tokens if t.type in STRING_TOKENS and PLACEHOLDER.search(t.string)]


class CountTest(unittest.TestCase):
    def test_agrees_with_the_number(self):
        self.assertEqual(count_fr(0, "mesure", "mesures"), "0 mesures")
        self.assertEqual(count_fr(1, "mesure", "mesures"), "1 mesure")
        self.assertEqual(count_fr(2, "mesure", "mesures"), "2 mesures")

    def test_no_plural_placeholder_in_shown_texts(self):
        """Les textes vus par le patient s'accordent au nombre réel : jamais « mesure(s) » ni « ajouté(es) »."""
        files = [p for top in SOURCES for p in (ROOT / top).rglob("*.py") if "tests" not in p.parts]
        self.assertTrue(files)
        found = [
            f"{path.relative_to(ROOT)}:{line}: {text[:80]}"
            for path in files
            for line, text in placeholders(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(found, [], "\n".join(found))

    def test_detector_sees_strings_not_calls(self):
        sample = 'x = int(e)\ny = f"{n} mesure(s)"\nz = "ajouté(es)"\n'
        self.assertEqual([line for line, _ in placeholders(sample)], [2, 3])


if __name__ == "__main__":
    unittest.main()
