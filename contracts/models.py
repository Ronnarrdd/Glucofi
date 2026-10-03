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


@dataclass(frozen=True, order=True)
class Reading:
    device_time: datetime
    mg_dl: int
    epoch: int
    device_id: int | None = field(default=None, compare=False)
    # même mesure avec ou sans marqueur : un réimport ne la duplique pas
    meal: Meal | None = field(default=None, compare=False)
    meter_serial: str | None = field(default=None, compare=False)

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
    DECREASE_LOW_MORNING = "decrease_low_morning"
    INCREASE_HIGH_MORNINGS = "increase_high_mornings"
    KEEP = "keep"


RULE_LABELS_FR = {
    DoseRule.START: "Début du protocole",
    DoseRule.MANUAL: "Modification manuelle",
    DoseRule.DECREASE_LOW_MORNING: "Baisse : glycémie du matin trop basse",
    DoseRule.INCREASE_HIGH_MORNINGS: "Hausse : glycémie du matin trop haute plusieurs jours de suite",
    DoseRule.KEEP: "Dose inchangée",
}


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


# marqueurs qui peuvent être la glycémie du matin : "à jeun" passe avant, puis "avant repas" ou sans marqueur
MORNING_FIRST_CHOICE = Meal.FASTING
MORNING_ALLOWED_MEALS = frozenset({Meal.FASTING, Meal.BEFORE_MEAL, None})


# champs que seule l'ordonnance peut fixer : aucun n'a de valeur par défaut
PROTOCOL_FIELDS = ("insulin", "low_g_l", "high_g_l", "step_ui", "high_streak_days")


@dataclass(frozen=True)
class DosingSettings:
    """Protocole de titration de la dose du soir, saisi par l'utilisateur depuis l'ordonnance."""

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

    def __post_init__(self) -> None:
        if not self.insulin.strip():
            raise ValueError("indiquez le nom de l'insuline")
        if self.morning_start > self.morning_end:
            raise ValueError("la plage du matin doit commencer avant de finir")
        if not 0 < self.low_g_l < self.high_g_l:
            raise ValueError("seuils incohérents : il faut 0 < bas < haut")
        if self.step_ui <= 0 or self.high_streak_days <= 0:
            raise ValueError("le pas et la durée de série doivent être positifs")


@dataclass(frozen=True)
class MorningReading:
    day: date
    reading: Reading


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
class DoseProposal:
    current: DoseChange
    proposed_evening_ui: int
    rule: DoseRule
    reason: str
    evidence: tuple[Reading, ...] = ()
    mornings: tuple[MorningReading, ...] = ()
    alerts: tuple[Alert, ...] = ()

    @property
    def changes_dose(self) -> bool:
        return self.proposed_evening_ui != self.current.evening_ui
