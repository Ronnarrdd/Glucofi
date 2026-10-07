"""Figures matplotlib partagées par l'interface (FigureCanvasGTK4Agg) et le PDF.

On construit des `Figure` sans pyplot : pas d'état global, utilisable hors du
thread principal pour l'export. Les couleurs viennent d'une ChartPalette posée
élément par élément (jamais dans rcParams) : PDF_PALETTE par défaut pour le
rapport, la palette du thème clair ou sombre pour l'écran.
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Sequence

from matplotlib.dates import AutoDateLocator, DateFormatter
from matplotlib.figure import Figure

from contracts import DoseChange, DosingSettings, Reading
from services.charts.palette import PDF_PALETTE, ChartPalette
from services.charts.stats import PERIODS, period_of, stats_by_period
from services.dosing import morning_readings

GAP_HOURS = 36


def _text(palette: ChartPalette, color: str | None = None) -> dict:
    """Couleur et police d'un texte ; rien pour la palette du PDF, qui garde les valeurs par défaut."""
    kw = {}
    color = color or palette.text
    if color:
        kw["color"] = color
    if palette.fonts:
        kw["fontfamily"] = list(palette.fonts)
    return kw


def _figure(palette: ChartPalette, size: tuple[float, float]) -> Figure:
    return Figure(figsize=size, layout="constrained", facecolor=palette.background)


def _frame(palette: ChartPalette, *axes) -> None:
    """Fond, cadre, graduations et quadrillage des axes. Les graduations naissent au dessin : réglées par tick_params."""
    ticks = {}
    if palette.text:
        ticks["colors"] = palette.text
    if palette.grid:
        ticks["grid_color"] = palette.grid
    if palette.fonts:
        ticks["labelfontfamily"] = list(palette.fonts)
    for ax in axes:
        if palette.background:
            ax.set_facecolor(palette.background)
        if palette.edge:
            for spine in ax.spines.values():
                spine.set_edgecolor(palette.edge)
        if palette.grid_below:
            ax.set_axisbelow(True)
        if ticks:
            ax.tick_params(which="both", **ticks)


def _legend_kw(palette: ChartPalette, size: float = 8) -> dict:
    kw = {"prop": {"family": list(palette.fonts), "size": size}} if palette.fonts else {"fontsize": size}
    if palette.text:
        kw["labelcolor"] = palette.text
    return kw


def _legend_below(ax, palette: ChartPalette, handles=None, labels=None, ncols: int = 4) -> None:
    kwargs = {} if handles is None else {"handles": handles, "labels": labels}
    ax.legend(**kwargs, loc="upper center", bbox_to_anchor=(0.5, -0.1), ncols=ncols, frameon=False, **_legend_kw(palette))


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


def _level_color(value: float, settings: DosingSettings, palette: ChartPalette) -> str:
    if value < settings.low_g_l:
        return palette.low
    return palette.high if value > settings.high_g_l else palette.in_range


def _target_band(ax, settings: DosingSettings, palette: ChartPalette) -> None:
    ax.axhspan(settings.low_g_l, settings.high_g_l, color=palette.band, alpha=palette.band_alpha, lw=0, label="Objectif")
    ax.axhline(settings.hypo_alert_g_l, color=palette.hypo, lw=0.8, ls="--", label="Hypoglycémie")


def _date_axis(ax, palette: ChartPalette) -> None:
    locator = AutoDateLocator(minticks=4, maxticks=10)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(DateFormatter("%d/%m"))
    ax.grid(True, alpha=palette.grid_alpha)


def _empty(fig: Figure, palette: ChartPalette, message: str = "Aucune mesure sur cette période") -> Figure:
    ax = fig.add_subplot()
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12, **_text(palette, palette.muted))
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
    palette: ChartPalette = PDF_PALETTE,
) -> Figure:
    fig = _figure(palette, size)
    if not readings:
        return _empty(fig, palette)
    ax = fig.add_subplot()
    _frame(palette, ax)
    _target_band(ax, settings, palette)
    times = [r.device_time for r in readings]
    values = [r.g_l for r in readings]
    ax.plot(*_break_gaps(times, values), color=palette.line, lw=1, alpha=0.5)
    colors = [_level_color(r.g_l, settings, palette) for r in readings]
    ax.scatter(times, values, c=colors, s=18, zorder=3, edgecolors=palette.point_edge, linewidths=0.5)
    mornings = morning_readings(readings, settings)
    if mornings:
        ax.scatter(
            [m.reading.device_time for m in mornings],
            [m.reading.g_l for m in mornings],
            s=60, facecolors="none", edgecolors=palette.morning, linewidths=1.2, zorder=4, label="Glycémie du matin",
        )
    for change in _dose_changes_in(changes, since or times[0], until):
        ax.axvline(change.effective, color=palette.muted, lw=0.8, ls=":")
        ax.annotate(
            f"soir {change.evening_ui} UI", (change.effective, 0.98), xycoords=("data", "axes fraction"),
            rotation=90, va="top", ha="left", fontsize=7, xytext=(2, 0), textcoords="offset points",
            bbox={"boxstyle": "round,pad=0.2", "fc": palette.annotation_bg, "ec": "none", "alpha": 0.8},
            **_text(palette, palette.muted),
        )
    ax.set_ylabel("Glycémie (g/L)", **_text(palette))
    ax.set_ylim(0, max(3.2, max(values) + 0.2))
    if since and until:
        ax.set_xlim(since, until)
    _date_axis(ax, palette)
    _legend_below(ax, palette, ncols=3)
    return fig


def morning_trend_figure(
    readings: Sequence[Reading],
    changes: Sequence[DoseChange],
    settings: DosingSettings,
    since: datetime | None = None,
    until: datetime | None = None,
    size: tuple[float, float] = (9, 4),
    palette: ChartPalette = PDF_PALETTE,
) -> Figure:
    fig = _figure(palette, size)
    mornings = morning_readings(readings, settings)
    if not mornings:
        return _empty(fig, palette, "Aucune glycémie du matin sur cette période")
    ax = fig.add_subplot()
    _frame(palette, ax)
    _target_band(ax, settings, palette)
    days = [datetime.combine(m.day, datetime.min.time()) for m in mornings]
    values = [m.reading.g_l for m in mornings]
    colors = [_level_color(v, settings, palette) for v in values]
    ax.bar(days, values, width=0.7, color=colors, alpha=0.85)
    ax.set_ylabel("Glycémie du matin (g/L)", **_text(palette))
    ax.set_ylim(0, max(3.2, max(values) + 0.2))

    if changes:
        dose_ax = ax.twinx()
        _frame(palette, dose_ax)
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
            dose_ax.step(xs, ys, where="post", color=palette.dose, lw=2, label="Dose du soir (UI)")
            dose_ax.set_ylabel("Dose du soir (UI)", **_text(palette, palette.dose))
            dose_ax.set_ylim(0, max(ys) + 4)
            dose_ax.tick_params(axis="y", colors=palette.dose)
    handles, labels = ax.get_legend_handles_labels()
    if changes:
        extra_h, extra_l = dose_ax.get_legend_handles_labels()
        handles, labels = handles + extra_h, labels + extra_l
    if since and until:
        ax.set_xlim(since, until)
    _date_axis(ax, palette)
    _legend_below(ax, palette, handles, labels, ncols=3)
    return fig


def distribution_figure(
    readings: Sequence[Reading],
    settings: DosingSettings,
    size: tuple[float, float] = (9, 3.6),
    palette: ChartPalette = PDF_PALETTE,
) -> Figure:
    fig = _figure(palette, size)
    if not readings:
        return _empty(fig, palette)
    box_ax, pct_ax = fig.subplots(1, 2, width_ratios=(3, 2))
    _frame(palette, box_ax, pct_ax)

    groups = {p: [r.g_l for r in readings if period_of(r, settings) == p] for p in PERIODS}
    labels = [p for p in PERIODS if groups[p]]
    box_ax.axhspan(settings.low_g_l, settings.high_g_l, color=palette.band, alpha=palette.band_alpha, lw=0)
    ink = {"color": palette.median}
    box_ax.boxplot([groups[p] for p in labels], tick_labels=labels, widths=0.5, patch_artist=True,
                   boxprops={"facecolor": palette.box_face, "edgecolor": palette.median, "alpha": palette.box_alpha},
                   medianprops=ink, whiskerprops=ink, capprops=ink, flierprops={"markeredgecolor": palette.median})
    box_ax.set_ylabel("Glycémie (g/L)", **_text(palette))
    box_ax.set_title("Répartition par moment de la journée", fontsize=10, **_text(palette, palette.title))
    box_ax.grid(True, axis="y", alpha=palette.grid_alpha)

    stats = stats_by_period(readings, settings)
    bottom = [0.0] * len(labels)
    for key, label, color in (("pct_low", "Sous l'objectif", palette.low), ("pct_in_range", "Dans l'objectif", palette.in_range),
                              ("pct_high", "Au-dessus", palette.high)):
        heights = [getattr(stats[p], key) for p in labels]
        pct_ax.bar(labels, heights, bottom=bottom, color=color, label=label)
        for i, h in enumerate(heights):
            if h >= 8:
                pct_ax.text(i, bottom[i] + h / 2, f"{h:.0f} %", ha="center", va="center", fontsize=8,
                            **_text(palette, palette.bar_text))
        bottom = [b + h for b, h in zip(bottom, heights)]
    pct_ax.set_ylim(0, 100)
    pct_ax.set_ylabel("% des mesures", **_text(palette))
    pct_ax.set_title("Temps dans l'objectif", fontsize=10, **_text(palette, palette.title))
    fig.legend(*pct_ax.get_legend_handles_labels(), loc="outside lower right", ncols=3, frameon=False, **_legend_kw(palette))
    pct_ax.tick_params(axis="x", labelsize=8)
    box_ax.tick_params(axis="x", labelsize=8)
    return fig
