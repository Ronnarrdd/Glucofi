"""Présentation de l'onglet Mesures : filtres, résumé, jours, lignes.

Sans GTK. La glycémie du matin retenue est celle du moteur de dose
(morning_readings), calculée après le filtre de moment et avant le filtre
de marqueur : un filtre « Après repas » laisse la valeur du matin dans
l'en-tête du jour.

La couleur est réservée au niveau de la glycémie (sous / dans / au-dessus de
l'objectif, comparaisons en mg/dL entiers comme compute_stats) ; chaque niveau
hors objectif porte aussi une flèche, pour ne pas reposer sur la couleur seule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from contracts import MEAL_LABELS_FR, DosingSettings, MeterInfo, Reading
from services.charts import PERIODS, compute_stats, period_of
from services.dosing import fmt_g_l, fmt_mg_dl, morning_readings

WEEKDAYS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS_FR = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)

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

# icônes de app/icons, sur le modèle des marqueurs du lecteur (pomme, trognon...)
MARKER_ICONS = {
    "all": "view-list-symbolic",
    "fasting": "glucofi-meal-fasting-symbolic",
    "before_meal": "glucofi-meal-before-symbolic",
    "after_meal": "glucofi-meal-after-symbolic",
    "bedtime": "glucofi-meal-bedtime-symbolic",
    "casual": "glucofi-meal-casual-symbolic",
    "other": "glucofi-meal-other-symbolic",
    "none": "glucofi-meal-none-symbolic",
}

LEVELS = ("low", "in", "high")
LEVEL_CSS = {"low": "error", "in": "success", "high": "warning"}
LEVEL_ICONS = {"low": "go-down-symbolic", "in": None, "high": "go-up-symbolic"}
RETAINED_ICON = "daytime-sunrise-symbolic"
RETAINED_TOOLTIP = "Glycémie du matin retenue pour l'ajustement de la dose du soir"

_MEAL_KEYS = {key for key, _label in MEAL_FILTERS}


@dataclass(frozen=True)
class Marker:
    key: str
    label: str
    icon: str


@dataclass(frozen=True)
class MeasureLine:
    time: str
    marker: Marker
    period: str
    value: str
    mg: str
    level: str
    level_label: str
    retained: bool
    meter: str | None

    @property
    def title(self) -> str:
        return self.time

    @property
    def subtitle(self) -> str:
        parts = [self.marker.label, self.period]
        if self.meter is not None:
            parts.append(self.meter)
        return " · ".join(parts)

    @property
    def css(self) -> str:
        return LEVEL_CSS[self.level]

    @property
    def level_icon(self) -> str | None:
        return LEVEL_ICONS[self.level]


@dataclass(frozen=True)
class MorningChip:
    value: str
    level: str
    level_label: str


@dataclass(frozen=True)
class MeasureDay:
    day: date
    title: str
    detail: str | None
    count_text: str
    morning: MorningChip | None
    lines: tuple[MeasureLine, ...]


@dataclass(frozen=True)
class StatTile:
    label: str
    value: str
    detail: str


@dataclass(frozen=True)
class RangeSegment:
    level: str
    label: str
    bounds: str
    count: int
    pct: str
    span: int


@dataclass(frozen=True)
class MeasureSummary:
    count: int
    mean_mg: float | None
    pct_in_range: float
    pct_hypo: float
    hypo_count: int
    marked: int
    tiles: tuple[StatTile, ...]
    segments: tuple[RangeSegment, ...]


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
    today: date | None = None,
) -> MeasureView:
    """Jours du plus récent au plus ancien, lignes du plus récent au plus ancien, et résumé."""
    if period != "all" and period not in PERIODS:
        raise ValueError(f"moment inconnu : {period}")
    if meal not in _MEAL_KEYS:
        raise ValueError(f"marqueur inconnu : {meal}")
    today = today or date.today()
    by_period = [r for r in readings if period == "all" or period_of(r, settings) == period]
    retained = {item.day: item.reading for item in morning_readings(by_period, settings)}
    shown = [r for r in by_period if _matches_meal(r, meal)]
    names = _meter_names(meters)
    by_day: dict[date, list[Reading]] = {}
    for reading in shown:
        by_day.setdefault(reading.day, []).append(reading)
    days = []
    for day in sorted(by_day, reverse=True):
        group = sorted(by_day[day], key=lambda r: (r.device_time, r.epoch), reverse=True)
        morning = retained.get(day)
        title, detail = day_title(day, today)
        days.append(MeasureDay(
            day=day,
            title=title,
            detail=detail,
            count_text="1 mesure" if len(group) == 1 else f"{len(group)} mesures",
            morning=None if morning is None else MorningChip(
                fmt_g_l(morning.mg_dl), level_of(morning, settings), level_label(level_of(morning, settings), settings),
            ),
            lines=tuple(_line(r, settings, names, r == morning) for r in group),
        ))
    return MeasureView(_summary(shown, settings), tuple(days))


def day_title(day: date, today: date) -> tuple[str, str | None]:
    """« Aujourd'hui » / « Hier » avec la date en détail, sinon la date complète (année si autre que l'actuelle)."""
    written = f"{WEEKDAYS_FR[day.weekday()]} {day.day}{'er' if day.day == 1 else ''} {MONTHS_FR[day.month - 1]}"
    if day.year != today.year:
        written += f" {day.year}"
    delta = (today - day).days
    if delta == 0:
        return "Aujourd'hui", written
    if delta == 1:
        return "Hier", written
    return written[0].upper() + written[1:], None


def level_of(reading: Reading, settings: DosingSettings) -> str:
    if reading.mg_dl < round(settings.low_g_l * 100):
        return "low"
    if reading.mg_dl > round(settings.high_g_l * 100):
        return "high"
    return "in"


def level_label(level: str, settings: DosingSettings) -> str:
    low, high = fmt_g_l(round(settings.low_g_l * 100)), fmt_g_l(round(settings.high_g_l * 100))
    return {
        "low": f"Sous l'objectif (moins de {low})",
        "in": f"Dans l'objectif ({low} à {high})",
        "high": f"Au-dessus de l'objectif (plus de {high})",
    }[level]


def marker_of(reading: Reading) -> Marker:
    if reading.meal is None:
        return Marker("none", "Sans marqueur", MARKER_ICONS["none"])
    return Marker(reading.meal.value, MEAL_LABELS_FR[reading.meal], MARKER_ICONS[reading.meal.value])


def range_spans(counts: Sequence[int], total: int = 100) -> tuple[int, ...]:
    """Parts entières de `total` proportionnelles à `counts` (plus forts restes), au moins 1 par compte non nul."""
    n = sum(counts)
    if n == 0:
        return tuple(0 for _ in counts)
    exact = [total * c / n for c in counts]
    spans = [max(1, int(e)) if c else 0 for c, e in zip(counts, exact)]
    present = [i for i, c in enumerate(counts) if c]
    # le reste se mesure par rapport à la part déjà donnée : un niveau relevé à 1 part n'en reçoit pas une 2e
    while sum(spans) < total:
        spans[max(present, key=lambda i: exact[i] - spans[i])] += 1
    while sum(spans) > total:
        spans[min((i for i in present if spans[i] > 1), key=lambda i: exact[i] - spans[i])] -= 1
    return tuple(spans)


def _line(reading: Reading, settings: DosingSettings, names: dict[str, str] | None, retained: bool) -> MeasureLine:
    level = level_of(reading, settings)
    return MeasureLine(
        time=f"{reading.device_time:%H:%M}",
        marker=marker_of(reading),
        period=period_of(reading, settings),
        value=fmt_g_l(reading.mg_dl),
        mg=fmt_mg_dl(reading.mg_dl),
        level=level,
        level_label=level_label(level, settings),
        retained=retained,
        meter=None if names is None else names.get(reading.meter_serial or "", "Lecteur inconnu"),
    )


def _summary(shown: Sequence[Reading], settings: DosingSettings) -> MeasureSummary:
    stats = compute_stats(shown, settings)
    marked = sum(r.meal is not None for r in shown)
    hypo_mg = round(settings.hypo_alert_g_l * 100)
    hypo_count = sum(r.mg_dl < hypo_mg for r in shown)
    counts = {level: 0 for level in LEVELS}
    for r in shown:
        counts[level_of(r, settings)] += 1
    spans = range_spans([counts[level] for level in LEVELS])
    low, high = fmt_g_l(round(settings.low_g_l * 100)), fmt_g_l(round(settings.high_g_l * 100))
    pcts = {"low": stats.pct_low, "in": stats.pct_in_range, "high": stats.pct_high}
    segments = tuple(
        RangeSegment(level, label, bounds, counts[level], f"{pcts[level]:.0f} %", span)
        for level, label, bounds, span in zip(
            LEVELS,
            ("Sous l'objectif", "Dans l'objectif", "Au-dessus"),
            (f"< {low}", f"{low} à {high}", f"> {high}"),
            spans,
        )
    )
    tiles = (
        StatTile("Moyenne", fmt_g_l(round(stats.mean_mg)) if stats.mean_mg is not None else "-", _plural(stats.count, "mesure")),
        StatTile("Dans l'objectif", f"{stats.pct_in_range:.0f} %", f"{low} à {high}"),
        StatTile("Hypoglycémies", str(hypo_count), f"sous {fmt_g_l(hypo_mg)} · {stats.pct_hypo:.0f} %"),
        StatTile("Avec marqueur", str(marked), f"sur {stats.count}"),
    )
    return MeasureSummary(
        count=stats.count,
        mean_mg=stats.mean_mg,
        pct_in_range=stats.pct_in_range,
        pct_hypo=stats.pct_hypo,
        hypo_count=hypo_count,
        marked=marked,
        tiles=tiles,
        segments=segments,
    )


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _matches_meal(reading: Reading, meal: str) -> bool:
    if meal == "all":
        return True
    if meal == "none":
        return reading.meal is None
    return reading.meal is not None and reading.meal.value == meal


def _meter_names(meters: Sequence[MeterInfo | tuple]) -> dict[str, str] | None:
    infos = [item[0] if isinstance(item, tuple) else item for item in meters]
    if len(infos) <= 1:
        return None
    return {info.serial: info.model_name for info in infos}
