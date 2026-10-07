#!/usr/bin/env python3
"""Illustrations de Glucofi pour Android (renard au lecteur, tache de pinceau) -> PNG pour Glucofi PC.

    python3 -m scripts.illustrations              lit app/src/main/res/drawable-nodpi/ de ../GlucofiAndroid (ou du dépôt parent)
    python3 -m scripts.illustrations --from DIR   lit DIR/<nom>.webp

GTK n'a pas de chargeur webp sur ce poste (gdk-pixbuf sans webp-pixbuf-loader) : les deux images sont converties
une fois en PNG par Pillow et versionnées dans app/illustrations/. Le pinceau est un masque blanc (seul son alpha
compte, l'app le teinte) : il est enregistré en niveaux de gris + alpha, quatre fois plus léger.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIR = ROOT / "app" / "illustrations"
DRAWABLES = Path("app") / "src" / "main" / "res" / "drawable-nodpi"
# dépôt GlucofiAndroid à côté de celui-ci, ou dépôt parent quand ce code est son sous-arbre glucofi/
SOURCE_CANDIDATES = (ROOT.parent / "GlucofiAndroid" / DRAWABLES, ROOT.parent / DRAWABLES)
DEFAULT_SOURCE = next((path for path in SOURCE_CANDIDATES if path.is_dir()), SOURCE_CANDIDATES[0])

# PNG produit -> (webp source, mode Pillow)
ILLUSTRATIONS = {
    "fox-banner.png": ("illus_fox_banner.webp", "RGBA"),
    "brush.png": ("illus_brush.webp", "LA"),
}


def convert(source: Path, target: Path, mode: str) -> Path:
    with Image.open(source) as image:
        image.load()
        converted = image.convert(mode)
    converted.save(target, format="PNG", optimize=True)
    return target


def generate(source_dir: Path = DEFAULT_SOURCE, target_dir: Path = TARGET_DIR) -> list[Path]:
    missing = [name for name, _ in ILLUSTRATIONS.values() if not (source_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"{source_dir} : {', '.join(missing)} introuvable(s) (dépôt GlucofiAndroid à côté ?)")
    target_dir.mkdir(parents=True, exist_ok=True)
    return [convert(source_dir / name, target_dir / png, mode) for png, (name, mode) in ILLUSTRATIONS.items()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path, default=DEFAULT_SOURCE, help="dossier des .webp")
    args = parser.parse_args(argv)
    try:
        paths = generate(args.source)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    for path in paths:
        print(f"{path.relative_to(ROOT)} ({path.stat().st_size // 1024} Ko)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
