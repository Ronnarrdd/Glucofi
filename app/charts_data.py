"""Données de l'onglet Graphiques, sans GTK : cinq tuiles de synthèse et les trois figures (PC et tablette).

Les graphiques sont des figures matplotlib (services/charts/figures.py) ; l'appelant choisit la palette
(thème clair ou sombre) et ce qu'il fait du PNG (texture GTK, fichier lu par l'app Android).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from app.measures import marker_of
from app.state import AppState
from app.today import DAYS_SHORT_FR
from contracts import DoseChange, DosingSettings, Reading
from services.dosing import fmt_g_l, morning_readings

PERIODS = ((14, "14 jours"), (30, "30 jours"), (90, "90 jours"))
CHART_TITLES = ("Courbe des glycémies", "Glycémies du matin et dose du soir", "Répartition")


@dataclass(frozen=True)
class ChartData:
    days: int
    since: datetime
    until: datetime
    settings: DosingSettings
    readings: Sequence[Reading]
    changes: Sequence[DoseChange]
    tiles: tuple[tuple[str, str], ...]


def chart_data(state: AppState, days: int) -> ChartData | None:
    """None tant que le protocole n'est pas saisi (pas d'objectif à tracer)."""
    from services.charts import compute_stats

    settings = state.settings
    if settings is None:
        return None
    since, until = state.period_bounds(days)
    readings = state.readings(days=days)
    stats = compute_stats(readings, settings)
    tiles = (
        ("Mesures", str(stats.count)),
        ("Moyenne", fmt_g_l(round(stats.mean_mg)) if stats.mean_mg is not None else "-"),
        ("Dans l'objectif", f"{stats.pct_in_range:.0f} %"),
        ("Sous l'objectif", f"{stats.pct_low:.0f} %"),
        ("Au-dessus", f"{stats.pct_high:.0f} %"),
    )
    return ChartData(days, since, until, settings, readings, state.dose_changes(), tiles)


def chart_figures(data: ChartData, palette=None) -> list[tuple[str, object]]:
    """(titre, figure) des trois graphiques, dans l'ordre de CHART_TITLES."""
    from services.charts.figures import distribution_figure, morning_trend_figure, timeline_figure

    args = (data.readings, data.changes, data.settings, data.since, data.until)
    return list(zip(CHART_TITLES, (
        timeline_figure(*args, palette=palette) if palette else timeline_figure(*args),
        morning_trend_figure(*args, palette=palette) if palette else morning_trend_figure(*args),
        distribution_figure(data.readings, data.settings, palette=palette) if palette else distribution_figure(data.readings, data.settings),
    )))


@dataclass(frozen=True)
class Hit:
    """Un point du graphique, en fraction de l'image (origine en haut à gauche), avec le texte de son infobulle."""

    x: float
    y: float
    text: str


def _when(moment: datetime) -> str:
    return f"{DAYS_SHORT_FR[moment.weekday()]} {moment:%d/%m}"


def chart_hits(data: ChartData, figures: Sequence[tuple[str, object]]) -> list[list[Hit]]:
    """Points survolables de chaque figure, dans l'ordre de `figures` (vide pour la répartition).

    À appeler APRÈS le rendu de la figure : la mise en page contrainte de matplotlib ne fixe la position des axes qu'au
    dessin. Les positions sont des fractions de la figure, donc valables à toute résolution du PNG."""
    from matplotlib.dates import date2num

    mornings = morning_readings(data.readings, data.settings)
    sources = (
        [(r.device_time, r.g_l, f"{_when(r.device_time)} {r.device_time:%H:%M} · {fmt_g_l(r.mg_dl)} · {marker_of(r).label}")
         for r in data.readings],
        [(datetime.combine(m.day, datetime.min.time()), m.reading.g_l,
          f"{_when(m.reading.device_time)} · glycémie du matin {fmt_g_l(m.reading.mg_dl)}") for m in mornings],
    )
    out: list[list[Hit]] = []
    for index, (_title, fig) in enumerate(figures):
        points = sources[index] if index < len(sources) else []
        if not points or not fig.axes or not fig.axes[0].has_data():
            out.append([])
            continue
        ax, to_fraction = fig.axes[0], fig.transFigure.inverted()
        # tous les points d'un coup : le capteur en donne près de 9 000 sur 90 jours
        fractions = to_fraction.transform(ax.transData.transform([(date2num(when), value) for when, value, _ in points]))
        out.append([
            Hit(float(fx), 1 - float(fy), text)
            for (fx, fy), (_when, _value, text) in zip(fractions, points)
            if 0 <= fx <= 1 and 0 <= fy <= 1
        ])
    return out


def image_rect(width: float, height: float, ratio: float) -> tuple[float, float, float, float]:
    """(x, y, largeur, hauteur) de l'image dans un Gtk.Picture CONTAIN de `width` x `height` (ratio = largeur / hauteur)."""
    shown_w = min(width, height * ratio)
    shown_h = shown_w / ratio
    return (width - shown_w) / 2, (height - shown_h) / 2, shown_w, shown_h


def nearest_hit(hits: Sequence[Hit], x: float, y: float, rect: tuple[float, float, float, float], radius: float = 14.0) -> Hit | None:
    """Le point le plus proche du pointeur (x, y en pixels du widget), à `radius` pixels au plus."""
    left, top, width, height = rect
    best, best_distance = None, radius * radius
    for hit in hits:
        distance = (left + hit.x * width - x) ** 2 + (top + hit.y * height - y) ** 2
        if distance <= best_distance:
            best, best_distance = hit, distance
    return best
