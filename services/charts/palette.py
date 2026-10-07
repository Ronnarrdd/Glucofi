"""Couleurs des figures. Sans matplotlib : l'interface construit sa palette sans charger la bibliothèque."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChartPalette:
    """Couleurs et police d'une figure, posées sur chaque élément (jamais dans rcParams, partagé entre threads).

    Les champs du cadre (`background` à `fonts`) valent None ou () pour garder la valeur par défaut de matplotlib.
    """

    low: str
    in_range: str
    high: str
    band: str
    band_alpha: float
    hypo: str
    line: str
    morning: str
    dose: str
    muted: str
    point_edge: str
    annotation_bg: str
    bar_text: str
    box_face: str
    box_alpha: float
    median: str
    background: str | None = None
    text: str | None = None
    title: str | None = None
    edge: str | None = None
    grid: str | None = None
    grid_alpha: float = 0.3
    grid_below: bool = False
    fonts: tuple[str, ...] = ()


# Le rapport PDF pour le médecin : couleurs d'origine, fond blanc, police par défaut de matplotlib.
PDF_PALETTE = ChartPalette(
    low="#e01b24",
    in_range="#2ec27e",
    high="#ff7800",
    band="#2ec27e",
    band_alpha=0.15,
    hypo="#e01b24",
    line="#3584e4",
    morning="#9141ac",
    dose="#9141ac",
    muted="#77767b",
    point_edge="white",
    annotation_bg="white",
    bar_text="white",
    box_face="#3584e4",
    box_alpha=0.5,
    median="black",
)
