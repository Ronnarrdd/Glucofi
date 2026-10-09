"""Types partagés entre les services Glucofi.

Toutes les heures sont des datetime naïfs exprimés dans l'horloge du lecteur
(champ "timestamp" de accuchek), qui fait référence. Le champ "epoch" est cette
heure interprétée dans le fuseau du PC, heure d'été comprise (local_epoch).
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum, IntEnum

MG_DL_PER_MMOL_L = 18.0
# lectures hors échelle du lecteur ("HI" / "LO"), codées comme Tidepool : juste au-delà de la plage 10-600
MG_DL_HIGH = 601
MG_DL_LOW = 9


class AccuchekExit(IntEnum):
    """Codes de sortie d'accuchek (ExitCode dans services/device/accuchek-src/session.h).

    En cas d'échec, stdout est vide et stderr contient "accuchek: <raison>".
    """

    OK = 0  # mesures écrites, éventuellement aucune (lecteur vide : "readings": [])
    USAGE = 1  # arguments, fichier de config, trace ou capture illisible
    NO_DEVICE = 2  # aucun lecteur connu sur le bus USB
    ACCESS_DENIED = 3  # lecteur trouvé mais ouverture refusée
    TRANSFER = 4  # transfert USB échoué : timeout, lecteur débranché
    PROTOCOL = 5  # le lecteur a interrompu l'échange ou répondu autre chose
    OUTPUT = 6  # stdout fermé ou non inscriptible (disque plein, pipe cassé) : ce qu'il contient est incomplet


def local_epoch(device_time: datetime) -> int:
    """Epoch de l'heure du lecteur dans le fuseau local, calculé comme accuchek (mktime, tm_isdst=-1)."""
    return int(_time.mktime(device_time.timetuple()))


class Meal(str, Enum):
    """Marqueur saisi sur le lecteur après une mesure (champ "meal" d'accuchek)."""

    FASTING = "fasting"
    BEFORE_MEAL = "before_meal"
    AFTER_MEAL = "after_meal"
    CASUAL = "casual"
    BEDTIME = "bedtime"
    OTHER = "other"


MEAL_LABELS_FR = {
    Meal.FASTING: "À jeun",
    Meal.BEFORE_MEAL: "Avant repas",
    Meal.AFTER_MEAL: "Après repas",
    Meal.CASUAL: "Autre moment",
    Meal.BEDTIME: "Coucher",
    Meal.OTHER: "Marqueur inconnu",
}


class NoteTag(str, Enum):
    """Étiquette rapide d'une note saisie dans Glucofi (pas sur le lecteur)."""

    LARGE_MEAL = "large_meal"
    EXERCISE = "exercise"
    ILLNESS = "illness"
    ALCOHOL = "alcohol"
    MISSED_DOSE = "missed_dose"
    SIDE_EFFECT = "side_effect"
    DOUBTFUL = "doubtful"


NOTE_TAG_LABELS_FR = {
    NoteTag.LARGE_MEAL: "Repas copieux",
    NoteTag.EXERCISE: "Activité physique",
    NoteTag.ILLNESS: "Malade",
    NoteTag.ALCOHOL: "Alcool",
    NoteTag.MISSED_DOSE: "Oubli d'injection",
    NoteTag.SIDE_EFFECT: "Effet secondaire",
    NoteTag.DOUBTFUL: "Mesure douteuse",
}

NOTE_MAX_CHARS = 500


@dataclass(frozen=True)
class ReadingNote:
    """Note sur une mesure : étiquettes (ordre de NoteTag), texte libre, demande d'écarter la mesure de l'ajustement.

    Écarter une mesure demande un motif (étiquette ou texte) : le médecin doit pouvoir le lire dans le rapport.
    """

    tags: tuple[NoteTag, ...] = ()
    text: str = ""
    exclude_from_dosing: bool = False

    def __post_init__(self) -> None:
        chosen = {NoteTag(tag) for tag in self.tags}
        object.__setattr__(self, "tags", tuple(tag for tag in NoteTag if tag in chosen))
        text = self.text.strip()
        if len(text) > NOTE_MAX_CHARS:
            raise ValueError(f"note trop longue ({len(text)} caractères, {NOTE_MAX_CHARS} au plus)")
        object.__setattr__(self, "text", text)
        if self.exclude_from_dosing and not self.tags and not text:
            raise ValueError("indiquez pourquoi la mesure est écartée (étiquette ou texte)")

    @property
    def empty(self) -> bool:
        return not self.tags and not self.text and not self.exclude_from_dosing

    @property
    def summary(self) -> str:
        """« Repas copieux, Malade · texte libre »."""
        parts = [", ".join(NOTE_TAG_LABELS_FR[tag] for tag in self.tags)] if self.tags else []
        if self.text:
            parts.append(self.text)
        return " · ".join(parts)


@dataclass(frozen=True, order=True)
class Reading:
    device_time: datetime
    mg_dl: int
    epoch: int
    device_id: int | None = field(default=None, compare=False)
    # même mesure avec ou sans marqueur : un réimport ne la duplique pas
    meal: Meal | None = field(default=None, compare=False)
    meter_serial: str | None = field(default=None, compare=False)
    note: ReadingNote | None = field(default=None, compare=False)

    @property
    def g_l(self) -> float:
        return self.mg_dl / 100.0

    @property
    def off_scale(self) -> str | None:
        """"high" pour HI (> 600 mg/dL), "low" pour LO (< 10 mg/dL), sinon None."""
        if self.mg_dl >= MG_DL_HIGH:
            return "high"
        if self.mg_dl <= MG_DL_LOW:
            return "low"
        return None

    @property
    def mmol_l(self) -> float:
        return self.mg_dl / MG_DL_PER_MMOL_L

    @property
    def day(self) -> date:
        return self.device_time.date()


# numéros de modèle Roche (table de Tidepool, lib/drivers/roche/models.js)
ROCHE_MODELS = {
    **dict.fromkeys(("483", "484", "497", "498", "499", "500", "502", "685"), "Accu-Chek Aviva Connect"),
    **dict.fromkeys(("479", "501", "503", "765"), "Accu-Chek Performa Connect"),
    **dict.fromkeys(("921", "922", "923", "925", "926", "929", "930", "932"), "Accu-Chek Guide"),
    **dict.fromkeys(("958", "959", "960", "961", "963", "964", "965"), "Accu-Chek Instant"),
    **dict.fromkeys(("897", "898", "901", "902", "903", "904", "905"), "Accu-Chek Guide Me"),
    **dict.fromkeys(("972", "973", "975", "976", "977", "978", "979", "980"), "Accu-Chek Instant"),
    **dict.fromkeys(("966", "967", "968", "969", "970", "971"), "Accu-Chek Instant S"),
    "982": "ReliOn Platinum",
}


@dataclass(frozen=True)
class MeterInfo:
    """Identité annoncée par le lecteur (attributs MDS)."""

    manufacturer: str = ""
    model: str = ""
    serial: str = ""
    firmware: str = ""
    hardware: str = ""
    software: str = ""
    system_id: str = ""

    @property
    def model_name(self) -> str:
        if self.model in ROCHE_MODELS:
            return f"{ROCHE_MODELS[self.model]} ({self.model})"
        return " ".join(part for part in (self.manufacturer, self.model) if part) or "Lecteur inconnu"


class ClockAction(str, Enum):
    """Ce qu'accuchek a fait de l'horloge du lecteur (clockActionName dans session.cpp)."""

    NOT_REQUESTED = "not_requested"
    SET = "set"
    WITHIN_TOLERANCE = "within_tolerance"
    NOT_SETTABLE = "not_settable"
    PC_NOT_SYNCHRONIZED = "pc_not_synchronized"
    PC_UNKNOWN = "pc_unknown"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


@dataclass(frozen=True)
class MeterClock:
    """Horloge du lecteur lue en début de session, avant une éventuelle remise à l'heure."""

    meter: datetime
    pc: datetime | None
    offset_s: int | None  # lecteur - PC, en secondes
    settable: bool
    pc_synchronized: bool | None
    action: ClockAction


@dataclass(frozen=True)
class SegmentCount:
    """Nombre d'entrées annoncé par le lecteur (None s'il ne le dit pas) et reçu."""

    announced: int | None
    received: int

    @property
    def complete(self) -> bool:
        return self.announced is None or self.announced == self.received


class DoseRule(str, Enum):
    START = "start"
    MANUAL = "manual"
    # dose du soir, d'après la glycémie du matin
    DECREASE_LOW_MORNING = "decrease_low_morning"
    INCREASE_HIGH_MORNINGS = "increase_high_mornings"
    # dose du matin, d'après la glycémie du soir (avant le dîner)
    DECREASE_LOW_EVENING = "decrease_low_evening"
    INCREASE_HIGH_EVENINGS = "increase_high_evenings"
    KEEP = "keep"


RULE_LABELS_FR = {
    DoseRule.START: "Début du protocole",
    DoseRule.MANUAL: "Modification manuelle",
    DoseRule.DECREASE_LOW_MORNING: "Baisse du soir : glycémie du matin trop basse",
    DoseRule.INCREASE_HIGH_MORNINGS: "Hausse du soir : glycémie du matin trop haute plusieurs jours de suite",
    DoseRule.DECREASE_LOW_EVENING: "Baisse du matin : glycémie du soir trop basse",
    DoseRule.INCREASE_HIGH_EVENINGS: "Hausse du matin : glycémie du soir trop haute plusieurs jours de suite",
    DoseRule.KEEP: "Dose inchangée",
}


class DoseTarget(str, Enum):
    """Dose ajustée : le soir d'après la glycémie du matin, le matin d'après la glycémie du soir."""

    EVENING = "evening"
    MORNING = "morning"


DOSE_TARGET_LABELS_FR = {DoseTarget.EVENING: "Soir", DoseTarget.MORNING: "Matin"}
# glycémie de référence de chaque dose, pour les textes (« glycémie du matin »)
REFERENCE_LABELS_FR = {DoseTarget.EVENING: "matin", DoseTarget.MORNING: "soir"}


def count_fr(n: int, singular: str, plural: str) -> str:
    """« 1 glycémie écartée », « 3 glycémies écartées » : accord au nombre réel, sans « (s) »."""
    return f"{n} {singular if n == 1 else plural}"


@dataclass(frozen=True)
class DoseChange:
    """Dose validée, en vigueur à partir de `effective`."""

    effective: datetime
    morning_ui: int
    evening_ui: int
    rule: DoseRule
    evidence: tuple[str, ...] = ()
    note: str = ""
    id: int | None = None
    # glycémies du matin écartées de l'ajustement à la validation : « JJ/MM/AAAA HH:MM : x g/L (motif) »
    excluded: tuple[str, ...] = ()


# marqueurs qui peuvent être la glycémie du matin : "à jeun" passe avant, puis "avant repas" ou sans marqueur
MORNING_FIRST_CHOICE = Meal.FASTING
MORNING_ALLOWED_MEALS = frozenset({Meal.FASTING, Meal.BEFORE_MEAL, None})
# glycémie du soir (avant le dîner) : "avant repas" passe avant, puis sans marqueur
EVENING_FIRST_CHOICE = Meal.BEFORE_MEAL
EVENING_ALLOWED_MEALS = frozenset({Meal.BEFORE_MEAL, None})


# champs que seule l'ordonnance peut fixer : aucun n'a de valeur par défaut
PROTOCOL_FIELDS = ("insulin", "low_g_l", "high_g_l", "step_ui", "high_streak_days")


@dataclass(frozen=True)
class LowTier:
    """Palier de baisse : glycémie de référence sous `below_g_l` -> dose diminuée de `step_ui`."""

    below_g_l: float
    step_ui: int


@dataclass(frozen=True)
class HighTier:
    """Palier de hausse : glycémie de référence au-dessus de `above_g_l` `days` jours de suite -> dose + `step_ui`."""

    above_g_l: float
    step_ui: int
    days: int


def _low_tiers(values) -> tuple[LowTier, ...]:
    tiers = tuple(t if isinstance(t, LowTier) else LowTier(**t) for t in values)
    return tuple(sorted(tiers, key=lambda t: -t.below_g_l))


def _high_tiers(values) -> tuple[HighTier, ...]:
    tiers = tuple(t if isinstance(t, HighTier) else HighTier(**t) for t in values)
    return tuple(sorted(tiers, key=lambda t: t.above_g_l))


@dataclass(frozen=True)
class Titration:
    """Règle d'ajustement d'une dose : objectif (seuils bas et haut), pas, jours, paliers supplémentaires.

    Les paliers supplémentaires sont plus sévères que la règle de base : un palier de baisse est sous le
    seuil bas, un palier de hausse au-dessus du seuil haut. Ils sont rangés du plus proche au plus loin
    de l'objectif, quel que soit l'ordre de saisie.
    """

    low_g_l: float
    high_g_l: float
    step_ui: int
    high_streak_days: int
    low_tiers: tuple[LowTier, ...] = ()
    high_tiers: tuple[HighTier, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "low_tiers", _low_tiers(self.low_tiers))
        object.__setattr__(self, "high_tiers", _high_tiers(self.high_tiers))
        _check_titration(self)

    @property
    def all_low_tiers(self) -> tuple[LowTier, ...]:
        """Règle de base puis paliers, du seuil le plus haut au plus bas."""
        return (LowTier(self.low_g_l, self.step_ui),) + self.low_tiers

    @property
    def all_high_tiers(self) -> tuple[HighTier, ...]:
        """Règle de base puis paliers, du seuil le plus bas au plus haut."""
        return (HighTier(self.high_g_l, self.step_ui, self.high_streak_days),) + self.high_tiers


def _check_titration(t) -> None:
    if not 0 < t.low_g_l < t.high_g_l:
        raise ValueError("seuils incohérents : il faut 0 < bas < haut")
    if t.step_ui <= 0 or t.high_streak_days <= 0:
        raise ValueError("le pas et la durée de série doivent être positifs")
    lows = [tier.below_g_l for tier in t.low_tiers]
    if len(set(lows)) != len(lows) or any(not 0 < v < t.low_g_l for v in lows):
        raise ValueError("chaque palier de baisse doit avoir son propre seuil, sous le seuil bas")
    highs = [tier.above_g_l for tier in t.high_tiers]
    if len(set(highs)) != len(highs) or any(v <= t.high_g_l for v in highs):
        raise ValueError("chaque palier de hausse doit avoir son propre seuil, au-dessus du seuil haut")
    if any(tier.step_ui <= 0 for tier in (*t.low_tiers, *t.high_tiers)) or any(tier.days <= 0 for tier in t.high_tiers):
        raise ValueError("le pas et la durée de chaque palier doivent être positifs")


@dataclass(frozen=True)
class DosingSettings:
    """Protocole saisi par l'utilisateur depuis l'ordonnance.

    Dose du soir d'après la glycémie du matin (champs à plat, `low_tiers`, `high_tiers`) ; dose du matin
    d'après la glycémie du soir si l'ordonnance le prévoit (`morning_titration`, None sinon).
    """

    insulin: str
    low_g_l: float
    high_g_l: float
    step_ui: int
    high_streak_days: int
    morning_start: time = time(5, 0)
    morning_end: time = time(11, 59)
    hypo_alert_g_l: float = 0.70
    hyper_alert_g_l: float = 3.00
    stale_days: int = 2
    low_tiers: tuple[LowTier, ...] = ()
    high_tiers: tuple[HighTier, ...] = ()
    morning_titration: Titration | None = None
    evening_start: time = time(17, 0)
    evening_end: time = time(21, 59)
    # écarter la glycémie de référence qui suit une dose cochée « non prise » (voir Injection) ; désactivé par défaut
    skip_after_missed_dose: bool = False

    def __post_init__(self) -> None:
        if not self.insulin.strip():
            raise ValueError("indiquez le nom de l'insuline")
        if self.morning_start > self.morning_end:
            raise ValueError("la plage du matin doit commencer avant de finir")
        object.__setattr__(self, "low_tiers", _low_tiers(self.low_tiers))
        object.__setattr__(self, "high_tiers", _high_tiers(self.high_tiers))
        if isinstance(self.morning_titration, dict):
            object.__setattr__(self, "morning_titration", Titration(**self.morning_titration))
        _check_titration(self)
        if self.evening_start > self.evening_end:
            raise ValueError("la plage du soir doit commencer avant de finir")
        if self.morning_titration is not None and self.evening_start <= self.morning_end:
            raise ValueError("la plage du soir doit commencer après la fin de la plage du matin")

    @property
    def evening_titration(self) -> Titration:
        """Ajustement de la dose du soir, d'après la glycémie du matin."""
        return Titration(self.low_g_l, self.high_g_l, self.step_ui, self.high_streak_days, self.low_tiers, self.high_tiers)

    def titration(self, target: DoseTarget) -> Titration | None:
        return self.evening_titration if target is DoseTarget.EVENING else self.morning_titration

    @property
    def targets(self) -> tuple[DoseTarget, ...]:
        """Doses que le protocole ajuste : toujours le soir, le matin si l'ordonnance le prévoit."""
        return (DoseTarget.EVENING,) if self.morning_titration is None else (DoseTarget.EVENING, DoseTarget.MORNING)


class InjectionState(str, Enum):
    """Ce que le patient a dit d'une dose un jour donné ; une dose sans ligne dans le journal est « non renseignée »."""

    TAKEN = "taken"
    MISSED = "missed"


INJECTION_STATE_LABELS_FR = {InjectionState.TAKEN: "Prise", InjectionState.MISSED: "Non prise"}
UNSET_LABEL_FR = "À renseigner"


@dataclass(frozen=True)
class Injection:
    """Journal des injections : la dose `target` du jour `day` a été prise ou non. Pas de ligne = non renseignée."""

    day: date
    target: DoseTarget
    state: InjectionState


class MealSlot(str, Enum):
    """Les trois repas du journal alimentaire (à ne pas confondre avec Meal, le marqueur saisi sur le lecteur)."""

    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"


MEAL_SLOT_LABELS_FR = {MealSlot.BREAKFAST: "Matin", MealSlot.LUNCH: "Midi", MealSlot.DINNER: "Soir"}
MEAL_TEXT_MAX_CHARS = 500


@dataclass(frozen=True)
class MealEntry:
    """Journal alimentaire : ce qui a été mangé au repas `slot` du jour `day`, et l'estimation qui va avec.

    `carbs_g` est l'estimation centrale, `carbs_low_g` et `carbs_high_g` sa fourchette ; None tant que le repas n'a
    pas été estimé. `source` dit d'où viennent les chiffres : "gemini" (estimés) ou "manual" (saisis ou corrigés).
    """

    day: date
    slot: MealSlot
    text: str
    calories_kcal: int | None = None
    carbs_g: float | None = None
    carbs_low_g: float | None = None
    carbs_high_g: float | None = None
    source: str | None = None

    @property
    def estimated(self) -> bool:
        return self.calories_kcal is not None and self.carbs_g is not None


@dataclass(frozen=True)
class ProtocolChange:
    """Version du protocole en vigueur à partir de `effective` (la plus récente est le protocole en cours)."""

    effective: datetime
    settings: DosingSettings
    note: str = ""
    id: int | None = None


@dataclass(frozen=True)
class MorningReading:
    """Glycémie de référence d'un jour (du matin pour la dose du soir, du soir pour la dose du matin)."""

    day: date
    reading: Reading


ReferenceReading = MorningReading


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    DANGER = "danger"


@dataclass(frozen=True)
class Alert:
    level: AlertLevel
    code: str
    message: str


@dataclass(frozen=True)
class DoseAdjustment:
    """Proposition pour une dose (`target`), d'après ses glycémies de référence depuis son dernier changement."""

    target: DoseTarget
    current_ui: int
    proposed_ui: int
    rule: DoseRule
    reason: str
    # début du suivi : dernier changement de cette dose (ou début du protocole)
    since: datetime
    evidence: tuple[Reading, ...] = ()
    references: tuple[ReferenceReading, ...] = ()
    # glycémies de référence possibles postérieures à `since`, écartées par une note ou par une dose non prise
    excluded: tuple[Reading, ...] = ()
    # pour chaque mesure de `excluded`, le motif tiré du journal des injections (« dose du soir du 05/10 non prise »),
    # vide si seule sa note l'écarte
    excluded_why: tuple[str, ...] = ()

    @property
    def changes_dose(self) -> bool:
        return self.proposed_ui != self.current_ui


@dataclass(frozen=True)
class DoseProposal:
    """Propositions du jour : la dose du soir toujours, la dose du matin si le protocole l'ajuste."""

    current: DoseChange
    evening: DoseAdjustment
    morning: DoseAdjustment | None = None
    alerts: tuple[Alert, ...] = ()

    @property
    def adjustments(self) -> tuple[DoseAdjustment, ...]:
        return (self.evening,) if self.morning is None else (self.evening, self.morning)

    def adjustment(self, target: DoseTarget) -> DoseAdjustment | None:
        return self.evening if target is DoseTarget.EVENING else self.morning

    @property
    def changes_dose(self) -> bool:
        return any(a.changes_dose for a in self.adjustments)

    # dose du soir : noms d'avant la dose du matin, gardés pour le rapport PDF, les graphiques et les evals

    @property
    def proposed_evening_ui(self) -> int:
        return self.evening.proposed_ui

    @property
    def rule(self) -> DoseRule:
        return self.evening.rule

    @property
    def reason(self) -> str:
        return self.evening.reason

    @property
    def evidence(self) -> tuple[Reading, ...]:
        return self.evening.evidence

    @property
    def mornings(self) -> tuple[MorningReading, ...]:
        return self.evening.references

    @property
    def excluded(self) -> tuple[Reading, ...]:
        return self.evening.excluded
