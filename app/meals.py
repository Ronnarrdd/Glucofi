"""Journal alimentaire, sans GTK : l'onglet Repas (PC et tablette).

Un jour = trois repas (matin, midi, soir), chacun écrit en texte libre puis estimé par Gemini (calories, glucides,
fourchette) ou corrigé à la main. Les totaux du jour ne comptent que les repas estimés. Ces chiffres sont des
estimations : ils n'entrent jamais dans le calcul de la dose d'insuline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.measures import day_title
from app.state import AppState
from contracts import MEAL_SLOT_LABELS_FR, MealEntry, MealSlot

TITLE = "Repas"
INTRO = "Écrivez ce que vous avez mangé : Gemini estime les calories et les glucides. Vous pouvez corriger les chiffres."
DISCLAIMER = "Estimations approximatives. Elles ne servent jamais au calcul de la dose d'insuline."
DAYS_SHOWN = 14
SOURCE_LABELS = {"gemini": "Estimé par Gemini", "manual": "Corrigé à la main"}
SLOTS = tuple(MealSlot)


def fmt_carbs(grams: float) -> str:
    """« 96 g », « 5,5 g » : virgule décimale, pas de « ,0 »."""
    rounded = round(grams, 1)
    text = f"{rounded:.0f}" if rounded == int(rounded) else f"{rounded:.1f}".replace(".", ",")
    return f"{text} g"


def fmt_kcal(kcal: int) -> str:
    return f"{kcal:,} kcal".replace(",", " ")


def range_text(entry: MealEntry) -> str | None:
    """« 85 à 110 g de glucides » ; None sans fourchette ou si elle se réduit à l'estimation."""
    low, high = entry.carbs_low_g, entry.carbs_high_g
    if low is None or high is None or round(low) == round(high):
        return None
    return f"{round(low)} à {fmt_carbs(high)} de glucides"


@dataclass(frozen=True)
class MealCell:
    day: date
    slot: MealSlot
    label: str
    entry: MealEntry | None

    @property
    def key(self) -> str:
        return f"{self.day.isoformat()}|{self.slot.value}"

    @property
    def text(self) -> str:
        return self.entry.text if self.entry else ""

    @property
    def status(self) -> str:
        if self.entry is None:
            return "À renseigner"
        if not self.entry.estimated:
            return "À estimer"
        return SOURCE_LABELS.get(self.entry.source or "", "Estimé")

    @property
    def summary(self) -> str | None:
        """« 96 g de glucides · 660 kcal » ; None tant que le repas n'est pas estimé."""
        e = self.entry
        if e is None or not e.estimated:
            return None
        return f"{fmt_carbs(e.carbs_g)} de glucides · {fmt_kcal(e.calories_kcal)}"

    @property
    def range(self) -> str | None:
        return range_text(self.entry) if self.entry and self.entry.estimated else None

    @property
    def description(self) -> str:
        """Phrase lue par un lecteur d'écran : « Midi, aujourd'hui : pâtes, 96 g de glucides, 660 kcal »."""
        if self.entry is None:
            return f"{self.label} : à renseigner"
        parts = [f"{self.label} : {self.entry.text}"]
        parts.append(self.summary.replace(" · ", ", ") if self.summary else self.status.lower())
        return ", ".join(parts)


@dataclass(frozen=True)
class MealDay:
    day: date
    title: str
    detail: str | None
    cells: tuple[MealCell, ...]

    @property
    def estimated(self) -> list[MealEntry]:
        return [c.entry for c in self.cells if c.entry is not None and c.entry.estimated]

    @property
    def carbs_g(self) -> float:
        return sum(e.carbs_g for e in self.estimated)

    @property
    def calories_kcal(self) -> int:
        return sum(e.calories_kcal for e in self.estimated)

    @property
    def total(self) -> str | None:
        """« 140 g de glucides · 1 520 kcal » sur les repas estimés ; None si aucun."""
        if not self.estimated:
            return None
        return f"{fmt_carbs(self.carbs_g)} de glucides · {fmt_kcal(self.calories_kcal)}"

    @property
    def pending(self) -> int:
        """Repas écrits mais pas encore estimés."""
        return sum(c.entry is not None and not c.entry.estimated for c in self.cells)


@dataclass(frozen=True)
class MealsView:
    days: tuple[MealDay, ...]

    @property
    def logged(self) -> int:
        return sum(c.entry is not None for d in self.days for c in d.cells)


def meals_view(state: AppState, days: int = DAYS_SHOWN) -> MealsView:
    """Les `days` derniers jours, aujourd'hui d'abord ; chaque jour montre toujours ses trois repas."""
    today = state._today()
    first = today - timedelta(days=days - 1)
    logged = {(m.day, m.slot): m for m in state.meals(first, today + timedelta(days=1))}
    out = []
    for back in range(days):
        day = today - timedelta(days=back)
        title, detail = day_title(day, today)
        cells = tuple(MealCell(day, slot, MEAL_SLOT_LABELS_FR[slot], logged.get((day, slot))) for slot in SLOTS)
        out.append(MealDay(day, title, detail, cells))
    return MealsView(tuple(out))


def _number(text: str, name: str, ceiling: float) -> float | None:
    text = text.strip().replace(",", ".").replace(" ", "").replace(" ", "")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{name} : un nombre est attendu") from None
    if value != value or value < 0 or value > ceiling:
        raise ValueError(f"{name} : entre 0 et {ceiling:g}")
    return value


def entry_from_form(day: date, slot: MealSlot, text: str, kcal_text: str, carbs_text: str, estimate=None) -> MealEntry:
    """Le repas que le formulaire décrit. `estimate` est l'estimation affichée (Gemini ou déjà enregistrée).

    Chiffres identiques à l'estimation : source « gemini », avec sa fourchette. Chiffres changés ou saisis à la main :
    source « manual », sans fourchette. Aucun chiffre : le repas est enregistré tel quel, à estimer plus tard.
    Raises ValueError (message pour le patient) : texte vide, un seul des deux chiffres, nombre invalide.
    """
    from contracts import MEAL_TEXT_MAX_CHARS

    text = text.strip()
    if not text:
        raise ValueError("décrivez le repas")
    if len(text) > MEAL_TEXT_MAX_CHARS:
        raise ValueError(f"le texte dépasse {MEAL_TEXT_MAX_CHARS} caractères")
    kcal = _number(kcal_text, "Calories", 5000)
    carbs = _number(carbs_text, "Glucides", 400)
    if kcal is None and carbs is None:
        return MealEntry(day, slot, text)
    if kcal is None or carbs is None:
        raise ValueError("renseignez les calories et les glucides, ou aucun des deux")
    kcal = round(kcal)
    if estimate is not None and kcal == estimate.calories_kcal and round(carbs, 1) == round(estimate.carbs_g, 1):
        return MealEntry(day, slot, text, kcal, estimate.carbs_g, estimate.carbs_low_g, estimate.carbs_high_g, "gemini")
    return MealEntry(day, slot, text, kcal, round(carbs, 1), None, None, "manual")


def baseline_of(entry: MealEntry | None):
    """L'estimation de Gemini déjà enregistrée pour ce repas (pour reconnaître des chiffres non modifiés), sinon None."""
    from services.meals import MealEstimate

    if entry is None or not entry.estimated or entry.source != "gemini":
        return None
    return MealEstimate(entry.calories_kcal, entry.carbs_g, entry.carbs_low_g or entry.carbs_g, entry.carbs_high_g or entry.carbs_g)
