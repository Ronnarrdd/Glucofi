#!/usr/bin/env python3
"""Polices de l'interface (Fredoka et Nunito, licence SIL OFL 1.1) -> app/fonts/*.ttf.

    python3 -m scripts.fonts              télécharge les polices variables de google/fonts et régénère app/fonts/
    python3 -m scripts.fonts --from DIR   même chose depuis des fichiers déjà téléchargés (DIR/Fredoka[wdth,wght].ttf...)

Mêmes sources, mêmes faces et même sous-ensemble que Glucofi pour Android (scripts/fonts.py de GlucofiAndroid) :
Fredoka (ronde, titres et chiffres) et Nunito (ronde, texte), figées à un commit de google/fonts et vérifiées par
SHA-256, une police statique par graisse, réduite aux alphabets latins et à la ponctuation française.

Différence avec Android : les noms internes suivent la graisse (« Fredoka SemiBold », famille typographique
« Fredoka »). Android charge chaque fichier avec sa graisse et ignore ces noms ; fontconfig et Pango, eux,
rangent les faces par nom de style, et sans ce renommage ne voient qu'une graisse par famille.
scripts/tests/test_fonts.py vérifie les noms, les graisses et que chaque caractère des textes existe.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "app" / "fonts"
LICENCES = FONT_DIR / "licences"
URL = "https://raw.githubusercontent.com/google/fonts/{commit}/ofl/{folder}/{file}"


@dataclass(frozen=True)
class Source:
    folder: str
    file: str
    commit: str
    sha256: str
    licence: str


SOURCES = {
    "Fredoka": Source(
        "fredoka", "Fredoka[wdth,wght].ttf", "a60a77e14f28abd4ef243a1b5dfc48df0cec5205",
        "2ba02e68b152868aef9ba28e24b3648c7d457fe6f25c761f2c2c53fb61a73fc8", "Fredoka-OFL.txt",
    ),
    "Nunito": Source(
        "nunito", "Nunito[wght].ttf", "8b0a1d0f5983c89bc2b93f1b5fb55f9e252744b5",
        "bb55a5ca5c2042335b3991af27c4d0705d0ef41cac6164ac737fd8f2a1e85207", "Nunito-OFL.txt",
    ),
}

# fichier (app/fonts/<nom>.ttf) -> famille, position sur les axes et nom de style attendu
FACES = {
    "fredoka_medium": ("Fredoka", {"wght": 500, "wdth": 100}, "Medium"),
    "fredoka_semibold": ("Fredoka", {"wght": 600, "wdth": 100}, "SemiBold"),
    # 500 pour le texte courant : le 400 de Nunito est trop maigre sur les fonds pastel
    "nunito_medium": ("Nunito", {"wght": 500}, "Medium"),
    "nunito_bold": ("Nunito", {"wght": 700}, "Bold"),
}

# latin de base, Latin-1, Latin étendu A (Œ, œ), ponctuation (espaces fines, tirets, guillemets, …),
# €, flèches, signe moins et comparaisons ; le reste retombe sur la police du système, caractère par caractère
UNICODES = [
    *range(0x20, 0x7F),
    *range(0xA0, 0x100),
    *range(0x100, 0x180),
    *range(0x2000, 0x2070),
    0x20AC,
    *range(0x2190, 0x2194),
    0x2212,
    0x2264,
    0x2265,
]


class ChecksumMismatch(Exception):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(family: str, into: Path) -> None:
    """Télécharge la police variable et sa licence au commit figé."""
    source = SOURCES[family]
    for name, target in ((source.file, into / source.file), ("OFL.txt", into / f"{family}-OFL.txt")):
        url = URL.format(commit=source.commit, folder=source.folder, file=urllib.request.quote(name))
        with urllib.request.urlopen(url, timeout=60) as response:
            target.write_bytes(response.read())


def verify(family: str, font: Path) -> None:
    expected = SOURCES[family].sha256
    actual = sha256(font)
    if actual != expected:
        raise ChecksumMismatch(f"{font.name} : SHA-256 {actual}, attendu {expected} (commit {SOURCES[family].commit})")


def build_face(variable: Path, axes: dict[str, int], out: Path) -> None:
    """Police statique à la position [axes], nommée d'après sa graisse, réduite à UNICODES, sans hinting."""
    font = TTFont(variable, recalcTimestamp=False)
    instancer.instantiateVariableFont(font, axes, inplace=True, updateFontNames=True)
    options = subset.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.hinting = False
    options.desubroutinize = True
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=UNICODES)
    subsetter.subset(font)
    font.save(out)


def generate(sources: Path, check_sums: bool = True) -> list[Path]:
    LICENCES.mkdir(parents=True, exist_ok=True)
    written = []
    for family, source in SOURCES.items():
        if check_sums:
            verify(family, sources / source.file)
        shutil.copyfile(sources / f"{family}-OFL.txt", LICENCES / source.licence)
        written.append(LICENCES / source.licence)
    for name, (family, axes, _style) in FACES.items():
        out = FONT_DIR / f"{name}.ttf"
        build_face(sources / SOURCES[family].file, axes, out)
        written.append(out)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path, help="dossier des polices variables déjà téléchargées")
    parser.add_argument("--print-sums", action="store_true", help="affiche les SHA-256 téléchargés (mise à jour des commits)")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        sources = args.source or Path(tmp)
        if args.source is None:
            for family in SOURCES:
                fetch(family, sources)
        if args.print_sums:
            for family, source in SOURCES.items():
                print(f"{family} {sha256(sources / source.file)}")
            return 0
        for path in generate(sources):
            print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
