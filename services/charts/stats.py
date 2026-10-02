"""Statistiques glycémiques, sans dépendance graphique."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import time
from typing import Sequence

from contracts import DosingSettings, Reading

AFTERNOON_START = time(12, 0)
EVENING_START = time(18, 0)
PERIODS = ("Matin", "Après-midi", "Soir / nuit")


def period_of(reading: Reading, settings: DosingSettings) -> str:
    t = reading.device_time.time()
    if settings.morning_start <= t <= settings.morning_end:
        return PERIODS[0]
    if AFTERNOON_START <= t < EVENING_START:
        return PERIODS[1]
    return PERIODS[2]


@dataclass(frozen=True)
class Stats:
    count: int
    mean_mg: float | None
    sd_mg: float | None
    min_mg: int | None
    max_mg: int | None
    pct_hypo: float
    pct_low: float
    pct_in_range: float
    pct_high: float


def compute_stats(readings: Sequence[Reading], settings: DosingSettings) -> Stats:
    """pct_low inclut les hypoglycémies ; bornes de l'objectif incluses (une glycémie égale à un seuil est dans la cible)."""
    values = [r.mg_dl for r in readings]
    n = len(values)
    if n == 0:
        return Stats(0, None, None, None, None, 0.0, 0.0, 0.0, 0.0)
    low, high, hypo = round(settings.low_g_l * 100), round(settings.high_g_l * 100), round(settings.hypo_alert_g_l * 100)
    return Stats(
        count=n,
        mean_mg=statistics.fmean(values),
        sd_mg=statistics.stdev(values) if n > 1 else 0.0,
        min_mg=min(values),
        max_mg=max(values),
        pct_hypo=100 * sum(v < hypo for v in values) / n,
        pct_low=100 * sum(v < low for v in values) / n,
        pct_in_range=100 * sum(low <= v <= high for v in values) / n,
        pct_high=100 * sum(v > high for v in values) / n,
    )


def stats_by_period(readings: Sequence[Reading], settings: DosingSettings) -> dict[str, Stats]:
    groups: dict[str, list[Reading]] = {p: [] for p in PERIODS}
    for r in readings:
        groups[period_of(r, settings)].append(r)
    return {p: compute_stats(rs, settings) for p, rs in groups.items()}
