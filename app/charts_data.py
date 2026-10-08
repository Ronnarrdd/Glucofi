"""Données de l'onglet Graphiques, sans GTK : cinq tuiles de synthèse et les trois figures (PC et tablette).

Les graphiques sont des figures matplotlib (services/charts/figures.py) ; l'appelant choisit la palette
(thème clair ou sombre) et ce qu'il fait du PNG (texture GTK, fichier lu par l'app Android).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from app.state import AppState
from contracts import DoseChange, DosingSettings, Reading
from services.dosing import fmt_g_l

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
