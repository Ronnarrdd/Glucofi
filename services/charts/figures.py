"""Figures matplotlib partagées par l'interface (FigureCanvasGTK4Agg) et le PDF.

On construit des `Figure` sans pyplot : pas d'état global, utilisable hors du
thread principal pour l'export.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Sequence

from matplotlib.dates import AutoDateLocator, DateFormatter
from matplotlib.figure import Figure

from contracts import DoseChange, DosingSettings, Reading
from services.charts.stats import PERIODS, period_of, stats_by_period
from services.dosing import morning_readings

BLUE = "#3584e4"
GREEN = "#2ec27e"
RED = "#e01b24"
ORANGE = "#ff7800"
PURPLE = "#9141ac"
GREY = "#77767b"
GAP_HOURS = 36


def _legend_below(ax, handles=None, labels=None, ncols: int = 4) -> None:
    kwargs = {} if handles is None else {"handles": handles, "labels": labels}
    ax.legend(**kwargs, loc="upper center", bbox_to_anchor=(0.5, -0.1), fontsize=8, ncols=ncols, frameon=False)


def _break_gaps(times: list[datetime], values: list[float]) -> tuple[list, list]:
    """Insère une coupure dans la courbe entre deux mesures trop espacées."""
    xs, ys = [], []
    for i, (t, v) in enumerate(zip(times, values)):
        if i and (t - times[i - 1]).total_seconds() > GAP_HOURS * 3600:
            xs.append(times[i - 1] + (t - times[i - 1]) / 2)
            ys.append(float("nan"))
        xs.append(t)
        ys.append(v)
    return xs, ys


def figure_png(fig: Figure, dpi: int = 160) -> bytes:
    """Rendu Agg (paquet matplotlib de base) : sur Mageia, le backend GTK4 est dans python3-matplotlib-gtk3."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi)
    return buf.getvalue()


def _target_band(ax, settings: DosingSettings) -> None:
    ax.axhspan(settings.low_g_l, settings.high_g_l, color=GREEN, alpha=0.15, lw=0, label="Objectif")
    ax.axhline(settings.hypo_alert_g_l, color=RED, lw=0.8, ls="--", label="Hypoglycémie")


def _date_axis(ax) -> None:
    locator = AutoDateLocator(minticks=4, maxticks=10)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(DateFormatter("%d/%m"))
    ax.grid(True, alpha=0.3)


def _empty(fig: Figure, message: str = "Aucune mesure sur cette période") -> Figure:
    ax = fig.add_subplot()
    ax.text(0.5, 0.5, message, ha="center", va="center", color=GREY, fontsize=12)
    ax.set_axis_off()
    return fig


def _dose_changes_in(changes: Sequence[DoseChange], since: datetime | None, until: datetime | None):
    return [c for c in changes if (since is None or c.effective >= since) and (until is None or c.effective <= until)]


def timeline_figure(
    readings: Sequence[Reading],
    changes: Sequence[DoseChange],
    settings: DosingSettings,
    since: datetime | None = None,
    until: datetime | None = None,
    size: tuple[float, float] = (9, 4),
) -> Figure:
    fig = Figure(figsize=size, layout="constrained")
    if not readings:
        return _empty(fig)
    ax = fig.add_subplot()
    _target_band(ax, settings)
    times = [r.device_time for r in readings]
    values = [r.g_l for r in readings]
    ax.plot(*_break_gaps(times, values), color=BLUE, lw=1, alpha=0.5)
    colors = [RED if r.g_l < settings.low_g_l else ORANGE if r.g_l > settings.high_g_l else GREEN for r in readings]
    ax.scatter(times, values, c=colors, s=18, zorder=3, edgecolors="white", linewidths=0.5)
    mornings = morning_readings(readings, settings)
    if mornings:
        ax.scatter(
            [m.reading.device_time for m in mornings],
            [m.reading.g_l for m in mornings],
            s=60, facecolors="none", edgecolors=PURPLE, linewidths=1.2, zorder=4, label="Glycémie du matin",
        )
    for change in _dose_changes_in(changes, since or times[0], until):
        ax.axvline(change.effective, color=GREY, lw=0.8, ls=":")
        ax.annotate(
            f"soir {change.evening_ui} UI", (change.effective, 0.98), xycoords=("data", "axes fraction"),
            rotation=90, va="top", ha="left", fontsize=7, color=GREY, xytext=(2, 0), textcoords="offset points",
            bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "none", "alpha": 0.8},
        )
    ax.set_ylabel("Glycémie (g/L)")
    ax.set_ylim(0, max(3.2, max(values) + 0.2))
    if since and until:
        ax.set_xlim(since, until)
    _date_axis(ax)
    _legend_below(ax, ncols=3)
    return fig


def morning_trend_figure(
    readings: Sequence[Reading],
    changes: Sequence[DoseChange],
    settings: DosingSettings,
    since: datetime | None = None,
    until: datetime | None = None,
    size: tuple[float, float] = (9, 4),
) -> Figure:
    fig = Figure(figsize=size, layout="constrained")
    mornings = morning_readings(readings, settings)
    if not mornings:
        return _empty(fig, "Aucune glycémie du matin sur cette période")
    ax = fig.add_subplot()
    _target_band(ax, settings)
    days = [datetime.combine(m.day, datetime.min.time()) for m in mornings]
    values = [m.reading.g_l for m in mornings]
    colors = [RED if v < settings.low_g_l else ORANGE if v > settings.high_g_l else GREEN for v in values]
    ax.bar(days, values, width=0.7, color=colors, alpha=0.85)
    ax.set_ylabel("Glycémie du matin (g/L)")
    ax.set_ylim(0, max(3.2, max(values) + 0.2))

    if changes:
        dose_ax = ax.twinx()
        start = since or days[0]
        end = until or datetime.combine(mornings[-1].day, datetime.max.time())
        steps = [c for c in changes if c.effective <= end]
        xs, ys = [], []
        for c in steps:
            xs.append(max(c.effective, start))
            ys.append(c.evening_ui)
        if xs:
            xs.append(end)
            ys.append(ys[-1])
            dose_ax.step(xs, ys, where="post", color=PURPLE, lw=2, label="Dose du soir (UI)")
            dose_ax.set_ylabel("Dose du soir (UI)", color=PURPLE)
            dose_ax.set_ylim(0, max(ys) + 4)
            dose_ax.tick_params(axis="y", colors=PURPLE)
    handles, labels = ax.get_legend_handles_labels()
    if changes:
        extra_h, extra_l = dose_ax.get_legend_handles_labels()
        handles, labels = handles + extra_h, labels + extra_l
    if since and until:
        ax.set_xlim(since, until)
    _date_axis(ax)
    _legend_below(ax, handles, labels, ncols=3)
    return fig


def distribution_figure(
    readings: Sequence[Reading], settings: DosingSettings, size: tuple[float, float] = (9, 3.6)
) -> Figure:
    fig = Figure(figsize=size, layout="constrained")
    if not readings:
        return _empty(fig)
    box_ax, pct_ax = fig.subplots(1, 2, width_ratios=(3, 2))

    groups = {p: [r.g_l for r in readings if period_of(r, settings) == p] for p in PERIODS}
    labels = [p for p in PERIODS if groups[p]]
    box_ax.axhspan(settings.low_g_l, settings.high_g_l, color=GREEN, alpha=0.15, lw=0)
    box_ax.boxplot([groups[p] for p in labels], tick_labels=labels, widths=0.5, patch_artist=True,
                   boxprops={"facecolor": BLUE, "alpha": 0.5}, medianprops={"color": "black"})
    box_ax.set_ylabel("Glycémie (g/L)")
    box_ax.set_title("Répartition par moment de la journée", fontsize=10)
    box_ax.grid(True, axis="y", alpha=0.3)

    stats = stats_by_period(readings, settings)
    bottom = [0.0] * len(labels)
    for key, label, color in (("pct_low", "Sous l'objectif", RED), ("pct_in_range", "Dans l'objectif", GREEN),
                              ("pct_high", "Au-dessus", ORANGE)):
        heights = [getattr(stats[p], key) for p in labels]
        pct_ax.bar(labels, heights, bottom=bottom, color=color, label=label)
        for i, h in enumerate(heights):
            if h >= 8:
                pct_ax.text(i, bottom[i] + h / 2, f"{h:.0f} %", ha="center", va="center", fontsize=8, color="white")
        bottom = [b + h for b, h in zip(bottom, heights)]
    pct_ax.set_ylim(0, 100)
    pct_ax.set_ylabel("% des mesures")
    pct_ax.set_title("Temps dans l'objectif", fontsize=10)
    fig.legend(*pct_ax.get_legend_handles_labels(), loc="outside lower right", ncols=3, fontsize=8, frameon=False)
    pct_ax.tick_params(axis="x", labelsize=8)
    box_ax.tick_params(axis="x", labelsize=8)
    return fig
