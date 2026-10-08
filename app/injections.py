"""Journal des injections, sans GTK : la carte « Injections » d'Aujourd'hui (PC et tablette).

Quatre jours (aujourd'hui et les trois précédents, depuis le début du protocole), une pastille par dose due ce
jour-là : « Prise », « Non prise » ou « À renseigner ». Un clic ouvre le choix entre les trois.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.measures import day_title
from app.state import AppState
from contracts import (
    DOSE_TARGET_LABELS_FR,
    INJECTION_STATE_LABELS_FR,
    UNSET_LABEL_FR,
    DoseTarget,
    InjectionState,
)
from services.adherence import UNLOGGED_LOOKBACK_DAYS, day_word, dose_ui, states, unlogged_days

# (état à enregistrer, libellé du bouton) ; None = revenir à « à renseigner »
CHOICES: tuple[tuple[InjectionState | None, str], ...] = (
    (InjectionState.TAKEN, "Prise"),
    (InjectionState.MISSED, "Non prise"),
    (None, "Pas renseignée"),
)
TITLE = "Injections"
INTRO = "Dites si vous avez fait chaque injection. Un jour sans réponse est signalé."
DOSE_PHRASES = {DoseTarget.MORNING: "dose du matin", DoseTarget.EVENING: "dose du soir"}


@dataclass(frozen=True)
class InjectionCell:
    day: date
    target: DoseTarget
    label: str
    ui: int
    state: InjectionState | None
    status: str
    attention: bool
    description: str

    @property
    def key(self) -> str:
        return f"{self.day.isoformat()}|{self.target.value}"

    @property
    def choice_title(self) -> str:
        return f"{DOSE_PHRASES[self.target].capitalize()} du {self.day:%d/%m}"

    @property
    def choice_body(self) -> str:
        return f"{self.ui} UI. Cette dose a-t-elle été faite ?"


@dataclass(frozen=True)
class InjectionRow:
    day: date
    title: str
    detail: str | None
    cells: tuple[InjectionCell, ...]


@dataclass(frozen=True)
class InjectionsView:
    rows: tuple[InjectionRow, ...]

    @property
    def pending(self) -> int:
        return sum(cell.attention for row in self.rows for cell in row.cells)


def status_of(state: InjectionState | None) -> str:
    return UNSET_LABEL_FR if state is None else INJECTION_STATE_LABELS_FR[state]


def injections_view(state: AppState) -> InjectionsView | None:
    """None tant qu'il n'y a ni protocole ni dose de départ."""
    settings = state.settings
    changes = state.dose_changes()
    if settings is None or not changes:
        return None
    today = state._today()
    declared = states(state.injections(today - timedelta(days=UNLOGGED_LOOKBACK_DAYS), today + timedelta(days=1)))
    pending = {
        target: set(unlogged_days(state.injections(), changes, settings, target, today, state._now()))
        for target in (DoseTarget.MORNING, DoseTarget.EVENING)
    }
    first = changes[0].effective.date()
    rows = []
    for back in range(UNLOGGED_LOOKBACK_DAYS + 1):
        day = today - timedelta(days=back)
        if day < first:
            break
        cells = []
        for target in (DoseTarget.MORNING, DoseTarget.EVENING):
            ui = dose_ui(changes, day, target) or 0
            if ui == 0:
                continue
            current = declared.get((day, target))
            label = DOSE_TARGET_LABELS_FR[target]
            status = status_of(current)
            cells.append(InjectionCell(
                day, target, label, ui, current, status, day in pending[target],
                f"{DOSE_PHRASES[target].capitalize()}, {day_word(day, today)} : {ui} UI, {status.lower()}",
            ))
        title, detail = day_title(day, today)
        if cells:
            rows.append(InjectionRow(day, title, detail, tuple(cells)))
    return InjectionsView(tuple(rows))
