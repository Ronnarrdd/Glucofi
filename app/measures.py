"""Présentation de l'onglet Mesures : filtres, résumé, jours, lignes.

Sans GTK. La glycémie du matin retenue est celle du moteur de dose
(morning_readings), calculée après le filtre de moment et avant le filtre
de marqueur : un filtre « Après repas » laisse la valeur du matin dans
l'en-tête du jour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from contracts import MEAL_LABELS_FR, DosingSettings, MeterInfo, Reading
from services.charts import PERIODS, compute_stats, period_of
from services.dosing import fmt_g_l, fmt_mg_dl, morning_readings

WEEKDAYS_FR = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")

MEAL_FILTERS = (
    ("all", "Tous"),
    ("fasting", "À jeun"),
    ("before_meal", "Avant repas"),
    ("after_meal", "Après repas"),
    ("bedtime", "Coucher"),
    ("casual", "Autre moment"),
    ("other", "Marqueur inconnu"),
    ("none", "Sans marqueur"),
)

_MEAL_KEYS = {key for key, _label in MEAL_FILTERS}


@dataclass(frozen=True)
class MeasureLine:
    title: str
    subtitle: str
    value: str
    css: str
    retained: bool
    header: str | None


@dataclass(frozen=True)
class MeasureDay:
    header: str
    lines: tuple[MeasureLine, ...]


@dataclass(frozen=True)
class MeasureSummary:
    count: int
    mean_mg: float | None
    pct_in_range: float
    pct_hypo: float
    marked: int
    cards: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MeasureView:
    summary: MeasureSummary
    days: tuple[MeasureDay, ...]

    @property
    def lines(self) -> tuple[MeasureLine, ...]:
        return tuple(line for day in self.days for line in day.lines)


def measure_view(
    readings: Sequence[Reading],
    settings: DosingSettings,
    period: str,
    meal: str,
    meters: Sequence[MeterInfo | tuple],
) -> MeasureView:
    """Liste affichable, du plus récent au plus ancien, et cartes du résumé."""
    if period != "all" and period not in PERIODS:
        raise ValueError(f"moment inconnu : {period}")
    if meal not in _MEAL_KEYS:
        raise ValueError(f"marqueur inconnu : {meal}")
    by_period = [r for r in readings if period == "all" or period_of(r, settings) == period]
    retained = {item.day: item.reading for item in morning_readings(by_period, settings)}
    shown = [r for r in by_period if _matches_meal(r, meal)]
    stats = compute_stats(shown, settings)
    marked = sum(r.meal is not None for r in shown)
    names = _meter_names(meters)
    by_day: dict[date, list[Reading]] = {}
    for reading in shown:
        by_day.setdefault(reading.day, []).append(reading)
    days = []
    for day in sorted(by_day, reverse=True):
        group = sorted(by_day[day], key=lambda r: (r.device_time, r.epoch), reverse=True)
        header = _day_header(day, len(group), retained.get(day))
        lines = tuple(
            MeasureLine(
                title=f"{reading.device_time:%H:%M}",
                subtitle=_subtitle(reading, settings, names),
                value=fmt_g_l(reading.mg_dl),
                css=_css(reading, settings),
                retained=retained.get(day) == reading,
                header=header if index == 0 else None,
            )
            for index, reading in enumerate(group)
        )
        days.append(MeasureDay(header, lines))
    summary = MeasureSummary(
        count=stats.count,
        mean_mg=stats.mean_mg,
        pct_in_range=stats.pct_in_range,
        pct_hypo=stats.pct_hypo,
        marked=marked,
        cards=(
            ("Mesures", str(stats.count)),
            ("Moyenne", fmt_g_l(round(stats.mean_mg)) if stats.mean_mg is not None else "-"),
            ("Dans l'objectif", f"{stats.pct_in_range:.0f} %"),
            ("Hypoglycémies", f"{stats.pct_hypo:.0f} %"),
            ("Avec marqueur", str(marked)),
        ),
    )
    return MeasureView(summary, tuple(days))


def _matches_meal(reading: Reading, meal: str) -> bool:
    if meal == "all":
        return True
    if meal == "none":
        return reading.meal is None
    return reading.meal is not None and reading.meal.value == meal


def _css(reading: Reading, settings: DosingSettings) -> str:
    if reading.g_l < settings.low_g_l:
        return "error"
    if reading.g_l > settings.high_g_l:
        return "warning"
    return "success"


def _meter_names(meters: Sequence[MeterInfo | tuple]) -> dict[str, str] | None:
    infos = [item[0] if isinstance(item, tuple) else item for item in meters]
    if len(infos) <= 1:
        return None
    return {info.serial: info.model_name for info in infos}


def _subtitle(reading: Reading, settings: DosingSettings, names: dict[str, str] | None) -> str:
    marker = MEAL_LABELS_FR[reading.meal] if reading.meal is not None else "sans marqueur"
    parts = [period_of(reading, settings), fmt_mg_dl(reading.mg_dl), marker]
    if names is not None:
        parts.append(names.get(reading.meter_serial or "", "Lecteur inconnu"))
    return " · ".join(parts)


def _day_header(day: date, count: int, morning: Reading | None) -> str:
    measures = "1 mesure" if count == 1 else f"{count} mesures"
    text = f"{WEEKDAYS_FR[day.weekday()]} {day:%d/%m} · {measures}"
    if morning is not None:
        text += f" · matin {fmt_g_l(morning.mg_dl)}"
    return text
