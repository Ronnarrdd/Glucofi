"""Les figures (matplotlib) sont dans services.charts.figures, importé à la demande."""

from services.charts.stats import PERIODS, Stats, compute_stats, period_of, stats_by_period

__all__ = ["PERIODS", "Stats", "compute_stats", "period_of", "stats_by_period"]
