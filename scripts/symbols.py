#!/usr/bin/env python3
"""Icônes de l'interface (Material Symbols Outlined, licence Apache 2.0) -> icônes symboliques GTK.

    python3 -m scripts.symbols              télécharge les SVG au commit figé et régénère app/icons/.../glucofi-*-symbolic.svg
    python3 -m scripts.symbols --from DIR   même chose depuis des SVG déjà téléchargés (DIR/<nom>.svg)

Mêmes icônes que Glucofi pour Android (scripts/symbols_to_vector.py de GlucofiAndroid), plus celles propres au PC
(graphiques, PDF, tablette, menu). Source : github.com/google/material-design-icons, au commit COMMIT,
symbols/web/<nom>/materialsymbolsoutlined/<nom>_24px.svg. Ces SVG sont faits d'un seul <path> sur le viewBox
« 0 -960 960 960 » ; tout autre format fait échouer la conversion plutôt que de dessiner une icône fausse.
Le tracé est réécrit en 16 x 16 (voir to_symbolic) et en gris Adwaita, comme les icônes des marqueurs repas :
GTK recolore les icônes « -symbolic » à la couleur du texte.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON_DIR = ROOT / "app" / "icons" / "hicolor" / "scalable" / "actions"
COMMIT = "737e3324305806514d7909874fa1818ae1808232"
URL = (
    "https://raw.githubusercontent.com/google/material-design-icons/{commit}"
    "/symbols/web/{base}/materialsymbolsoutlined/{name}_24px.svg"
)
PREFIX = "glucofi-"

# icône GTK (glucofi-<clé>-symbolic) -> nom Material Symbols (suffixe _fill1 : variante pleine)
SYMBOLS = {
    "home": "home",
    "home-fill": "home_fill1",
    "measures": "show_chart",
    "charts": "bar_chart",
    "doses": "vaccines",
    "doses-fill": "vaccines_fill1",
    "morning": "wb_sunny",
    "evening": "dark_mode",
    "arrow-up": "arrow_upward",
    "arrow-down": "arrow_downward",
    "sync": "sync",
    "usb": "usb",
    "tablet": "tablet_android",
    "pdf": "picture_as_pdf",
    "menu": "menu",
    "check": "check",
    "warning": "warning",
    "error": "error",
    "info": "info",
    "edit": "edit",
    "add": "add",
    "remove": "remove",
    "close": "close",
    "history": "history",
}

SVG = re.compile(
    r'^<svg xmlns="http://www.w3.org/2000/svg" height="24" viewBox="0 -960 960 960" width="24">'
    r'<path d="(?P<d>[^"]+)"/></svg>\s*$'
)


class UnsupportedSvg(ValueError):
    pass


SIZE = 16
SOURCE_SIZE = 960
TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
# arguments par commande, position des ordonnées (décalées, commandes absolues seulement)
# et des arguments qui ne sont pas des longueurs (angle et drapeaux de l'arc, jamais mis à l'échelle)
ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
Y_INDEX = {"M": (1,), "L": (1,), "H": (), "V": (0,), "C": (1, 3, 5), "S": (1, 3), "Q": (1, 3), "T": (1,), "A": (6,), "Z": ()}
UNSCALED = {"A": (2, 3, 4)}


def _number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def transform(d: str, dy: float, scale: float = 1.0) -> str:
    """Tracé `d` décalé de `dy` en ordonnée puis mis à l'échelle `scale`.

    Seules les coordonnées absolues sont décalées, les relatives suivent ; un « m » en tête de tracé part de
    l'origine, sa première paire est donc absolue."""
    tokens = TOKEN.findall(d)
    if "".join(tokens) != re.sub(r"[\s,]", "", d):
        raise UnsupportedSvg(f"tracé illisible : {d[:40]}…")
    out: list[str] = []
    command = ""
    args: list[str] = []

    def flush() -> None:
        if not command:
            return
        upper = command.upper()
        n = ARITY[upper]
        if n == 0:
            out.append(command)
            return
        if len(args) % n:
            raise UnsupportedSvg(f"commande {command} : {len(args)} nombres")
        for start in range(0, len(args), n):
            group = [float(a) for a in args[start:start + n]]
            if command.isupper() or (command == "m" and start == 0 and not out):
                for i in Y_INDEX[upper]:
                    group[i] += dy
            group = [v if i in UNSCALED.get(upper, ()) else v * scale for i, v in enumerate(group)]
            out.append((command if start == 0 else "") + " ".join(_number(v) for v in group))

    for token in tokens:
        if token.isalpha():
            flush()
            command, args = token, []
        else:
            args.append(token)
    flush()
    return " ".join(out)


def icon_name(key: str) -> str:
    return f"{PREFIX}{key}-symbolic"


def to_symbolic(svg: str, source: str) -> str:
    """Le moteur SVG interne de GTK 4.20 ignore le viewBox et dessine en unités de width/height : le tracé est
    donc réécrit directement en 16 x 16, origine en haut à gauche."""
    match = SVG.match(svg)
    if match is None:
        raise UnsupportedSvg(f"{source} : format inattendu (un seul <path> sur viewBox 0 -960 960 960 attendu)")
    d = transform(match["d"], SOURCE_SIZE, SIZE / SOURCE_SIZE)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}">\n'
        f"  <!-- Material Symbols Outlined « {source} » (Apache 2.0), généré par scripts/symbols.py -->\n"
        f'  <path fill="#2e3436" d="{d}"/>\n'
        "</svg>\n"
    )


def fetch(name: str, source_dir: Path | None) -> str:
    if source_dir is not None:
        return (source_dir / f"{name}.svg").read_text(encoding="utf-8")
    url = URL.format(commit=COMMIT, base=name.removesuffix("_fill1"), name=name)
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read().decode("utf-8")


def generate(source_dir: Path | None = None) -> list[Path]:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for key, name in SYMBOLS.items():
        target = ICON_DIR / f"{icon_name(key)}.svg"
        target.write_text(to_symbolic(fetch(name, source_dir), name), encoding="utf-8")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="source", type=Path, help="dossier des SVG déjà téléchargés")
    args = parser.parse_args(argv)
    for path in generate(args.source):
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
