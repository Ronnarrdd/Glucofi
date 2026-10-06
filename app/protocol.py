"""Protocole : formulaire (PC et tablette), textes lisibles, différences entre deux versions. Sans GTK.

Le formulaire est un dict de chaînes, plus quatre listes de paliers (dicts de chaînes) :
- insulin, low_g_l, high_g_l, step_ui, high_streak_days : dose du soir, réglée sur la glycémie du matin ;
- low_tiers [{below_g_l, step_ui}], high_tiers [{above_g_l, step_ui, days}] : paliers de la dose du soir ;
- morning_enabled ("1" ou "") puis m_low_g_l, m_high_g_l, m_step_ui, m_high_streak_days, m_low_tiers,
  m_high_tiers : dose du matin, réglée sur la glycémie du soir ;
- morning_start, morning_end, evening_start, evening_end : plages horaires, HH:MM.
Une clé absente garde la valeur de `base` (ancien formulaire, réglage non affiché).
Les erreurs (FormError) désignent le champ fautif : "low_g_l", "m_high_tiers.1.days", ...
"""

from __future__ import annotations

from datetime import time

from contracts import DoseTarget, DosingSettings, HighTier, LowTier, ProtocolChange, Titration
from services.dosing import fmt_g_l

TIME_FIELDS = ("morning_start", "morning_end", "evening_start", "evening_end")
TRUE_WORDS = {"1", "true", "on", "oui", "yes"}
DOSE_NAMES = {DoseTarget.EVENING: "dose du soir", DoseTarget.MORNING: "dose du matin"}


class FormError(ValueError):
    """Champ de formulaire invalide : `field` désigne la ligne à signaler."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def parse_g_l(field: str, text: str) -> float:
    """« 0,80 » ou « 0.8 » en g/L, entre 0,20 et 5,00."""
    try:
        value = float(str(text).strip().replace(",", "."))
    except ValueError:
        raise FormError(field, f"« {text} » n'est pas une glycémie en g/L (ex. 1,20)") from None
    if not 0.2 <= value <= 5.0:
        raise FormError(field, f"{text} g/L est hors de la plage 0,20 à 5,00 g/L")
    return round(value, 2)


def parse_count(field: str, text: str, minimum: int, maximum: int) -> int:
    try:
        value = int(str(text).strip())
    except ValueError:
        raise FormError(field, f"« {text} » n'est pas un nombre entier") from None
    if not minimum <= value <= maximum:
        raise FormError(field, f"{value} est hors de la plage {minimum} à {maximum}")
    return value


def parse_hhmm(field: str, text: str) -> time:
    """« 7:30 », « 07:30 » ou « 7h30 »."""
    raw = str(text).strip().lower().replace("h", ":")
    if raw.endswith(":"):
        raw += "00"
    try:
        hours, minutes = (int(part) for part in raw.split(":"))
        return time(hours, minutes)
    except ValueError:
        raise FormError(field, f"« {text} » n'est pas une heure (ex. 07:30)") from None


def fmt_form_g_l(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}".replace(".", ",")


def fmt_hhmm(value: time) -> str:
    return f"{value:%H:%M}"


def _blank(row: dict) -> bool:
    return not any(str(v).strip() for v in row.values())


def _titration(form: dict, prefix: str, base: Titration | None) -> Titration:
    """Seuils, pas, série et paliers d'une dose ; `prefix` = "" (soir) ou "m_" (matin)."""
    low = parse_g_l(f"{prefix}low_g_l", form.get(f"{prefix}low_g_l", ""))
    high = parse_g_l(f"{prefix}high_g_l", form.get(f"{prefix}high_g_l", ""))
    if low >= high:
        raise FormError(f"{prefix}high_g_l", "le seuil haut doit être au-dessus du seuil bas")
    step = parse_count(f"{prefix}step_ui", form.get(f"{prefix}step_ui", ""), 1, 20)
    days = parse_count(f"{prefix}high_streak_days", form.get(f"{prefix}high_streak_days", ""), 1, 14)

    if f"{prefix}low_tiers" in form:
        low_tiers = []
        for i, row in enumerate(form[f"{prefix}low_tiers"] or []):
            if _blank(row):
                continue
            field = f"{prefix}low_tiers.{i}"
            below = parse_g_l(f"{field}.below_g_l", row.get("below_g_l", ""))
            if below >= low:
                raise FormError(f"{field}.below_g_l", f"un palier de baisse doit être sous le seuil bas ({fmt_form_g_l(low)} g/L)")
            if any(t.below_g_l == below for t in low_tiers):
                raise FormError(f"{field}.below_g_l", f"deux paliers de baisse à {fmt_form_g_l(below)} g/L")
            low_tiers.append(LowTier(below, parse_count(f"{field}.step_ui", row.get("step_ui", ""), 1, 40)))
    else:
        low_tiers = list(base.low_tiers) if base else []
    if f"{prefix}high_tiers" in form:
        high_tiers = []
        for i, row in enumerate(form[f"{prefix}high_tiers"] or []):
            if _blank(row):
                continue
            field = f"{prefix}high_tiers.{i}"
            above = parse_g_l(f"{field}.above_g_l", row.get("above_g_l", ""))
            if above <= high:
                raise FormError(f"{field}.above_g_l", f"un palier de hausse doit être au-dessus du seuil haut ({fmt_form_g_l(high)} g/L)")
            if any(t.above_g_l == above for t in high_tiers):
                raise FormError(f"{field}.above_g_l", f"deux paliers de hausse à {fmt_form_g_l(above)} g/L")
            high_tiers.append(HighTier(
                above,
                parse_count(f"{field}.step_ui", row.get("step_ui", ""), 1, 40),
                parse_count(f"{field}.days", row.get("days", ""), 1, 14),
            ))
    else:
        high_tiers = list(base.high_tiers) if base else []
    return Titration(low, high, step, days, tuple(low_tiers), tuple(high_tiers))


def _base_settings(base: dict | None) -> DosingSettings | None:
    try:
        return DosingSettings(**base) if base else None
    except (TypeError, ValueError):
        return None


def protocol_from_form(form: dict, base: dict | None = None) -> DosingSettings:
    """Protocole saisi depuis l'ordonnance ; `base` garde les réglages absents du formulaire (alertes...)."""
    insulin = str(form.get("insulin", "")).strip()
    if not insulin:
        raise FormError("insulin", "indiquez le nom de l'insuline prescrite")
    previous = _base_settings(base)
    evening = _titration(form, "", previous.evening_titration if previous else None)
    values = dict(base or {})
    values.update(
        insulin=insulin,
        low_g_l=evening.low_g_l,
        high_g_l=evening.high_g_l,
        step_ui=evening.step_ui,
        high_streak_days=evening.high_streak_days,
        low_tiers=evening.low_tiers,
        high_tiers=evening.high_tiers,
    )
    for key in TIME_FIELDS:
        if key in form:
            values[key] = parse_hhmm(key, form[key])
    if "morning_enabled" in form:
        enabled = str(form["morning_enabled"]).strip().lower() in TRUE_WORDS
        values["morning_titration"] = (
            _titration(form, "m_", previous.morning_titration if previous else None) if enabled else None
        )

    defaults = DosingSettings(insulin="x", low_g_l=0.8, high_g_l=1.5, step_ui=1, high_streak_days=1)
    start, end = (values.get(k, getattr(defaults, k)) for k in ("morning_start", "morning_end"))
    if start >= end:
        raise FormError("morning_end", "la fin de la plage du matin doit être après son début")
    e_start, e_end = (values.get(k, getattr(defaults, k)) for k in ("evening_start", "evening_end"))
    if e_start >= e_end:
        raise FormError("evening_end", "la fin de la plage du soir doit être après son début")
    if values.get("morning_titration") is not None and e_start <= end:
        raise FormError("evening_start", f"la plage du soir doit commencer après la fin de la plage du matin ({fmt_hhmm(end)})")
    try:
        return DosingSettings(**values)
    except (TypeError, ValueError) as exc:
        raise FormError("protocol", str(exc)) from None


def _titration_form(t: Titration | None, prefix: str) -> dict:
    if t is None:
        return {f"{prefix}{k}": "" for k in ("low_g_l", "high_g_l", "step_ui", "high_streak_days")} | {
            f"{prefix}low_tiers": [], f"{prefix}high_tiers": [],
        }
    return {
        f"{prefix}low_g_l": fmt_form_g_l(t.low_g_l),
        f"{prefix}high_g_l": fmt_form_g_l(t.high_g_l),
        f"{prefix}step_ui": str(t.step_ui),
        f"{prefix}high_streak_days": str(t.high_streak_days),
        f"{prefix}low_tiers": [{"below_g_l": fmt_form_g_l(x.below_g_l), "step_ui": str(x.step_ui)} for x in t.low_tiers],
        f"{prefix}high_tiers": [
            {"above_g_l": fmt_form_g_l(x.above_g_l), "step_ui": str(x.step_ui), "days": str(x.days)} for x in t.high_tiers
        ],
    }


def protocol_to_form(settings: DosingSettings | None, draft: dict | None = None) -> dict:
    """Formulaire prérempli : le protocole en cours, sinon le brouillon (préférences d'avant le protocole)."""
    if settings is not None:
        form = {"insulin": settings.insulin, **_titration_form(settings.evening_titration, "")}
        form |= _titration_form(settings.morning_titration, "m_")
        form["morning_enabled"] = "1" if settings.morning_titration is not None else ""
        form |= {key: fmt_hhmm(getattr(settings, key)) for key in TIME_FIELDS}
        return form
    draft = draft or {}
    defaults = DosingSettings(insulin="x", low_g_l=0.8, high_g_l=1.5, step_ui=1, high_streak_days=1)
    form = {
        "insulin": draft.get("insulin") or "",
        "low_g_l": fmt_form_g_l(draft.get("low_g_l")),
        "high_g_l": fmt_form_g_l(draft.get("high_g_l")),
        "step_ui": "" if draft.get("step_ui") is None else str(draft["step_ui"]),
        "high_streak_days": "" if draft.get("high_streak_days") is None else str(draft["high_streak_days"]),
        "low_tiers": [],
        "high_tiers": [],
        **_titration_form(None, "m_"),
        "morning_enabled": "",
    }
    for key in TIME_FIELDS:
        value = draft.get(key) or getattr(defaults, key)
        form[key] = fmt_hhmm(value) if isinstance(value, time) else str(value)
    return form


# Textes


def _rules(t: Titration) -> list[str]:
    lines = [f"Sous {fmt_g_l(round(x.below_g_l * 100))} : baisser de {x.step_ui} UI" for x in t.all_low_tiers]
    lines += [
        f"Au-dessus de {fmt_g_l(round(x.above_g_l * 100))} {x.days} jour{'s' if x.days > 1 else ''} de suite : "
        f"augmenter de {x.step_ui} UI"
        for x in t.all_high_tiers
    ]
    return lines


def morning_rule_text(settings: DosingSettings) -> str:
    return (
        f"Entre {settings.morning_start:%H:%M} et {settings.morning_end:%H:%M} : première mesure « à jeun », "
        "sinon première mesure « avant repas » ou sans marqueur. "
        "Les mesures « après repas », « coucher » et « autre moment » ne comptent pas."
    )


def evening_rule_text(settings: DosingSettings) -> str:
    return (
        f"Entre {settings.evening_start:%H:%M} et {settings.evening_end:%H:%M}, avant le dîner : première mesure "
        "« avant repas », sinon première mesure sans marqueur. "
        "Les mesures « à jeun », « après repas », « coucher » et « autre moment » ne comptent pas."
    )


def protocol_sections(settings: DosingSettings) -> list[tuple[str, list[str]]]:
    """Le protocole en clair, par section (écran Protocole, rapport PDF)."""
    sections = [
        ("Insuline", [settings.insulin]),
        (
            f"Dose du soir, selon la glycémie du matin ({settings.morning_start:%H:%M}-{settings.morning_end:%H:%M})",
            _rules(settings.evening_titration),
        ),
    ]
    if settings.morning_titration is not None:
        sections.append((
            f"Dose du matin, selon la glycémie du soir ({settings.evening_start:%H:%M}-{settings.evening_end:%H:%M})",
            _rules(settings.morning_titration),
        ))
    else:
        sections.append(("Dose du matin", ["Pas d'ajustement automatique : elle ne change que si vous la modifiez."]))
    sections.append(("Alertes", [
        f"Hypoglycémie sous {fmt_g_l(round(settings.hypo_alert_g_l * 100))}",
        f"Hyperglycémie au-dessus de {fmt_g_l(round(settings.hyper_alert_g_l * 100))}",
        f"Pas de proposition si la dernière glycémie de référence a plus de {settings.stale_days} jours",
    ]))
    return sections


def _flatten(settings: DosingSettings) -> dict[str, str]:
    """Libellé lisible -> valeur, dans l'ordre d'affichage ; base des différences entre versions."""
    out = {"Insuline": settings.insulin, "Plage du matin": f"{settings.morning_start:%H:%M}-{settings.morning_end:%H:%M}"}
    for target, titration in ((DoseTarget.EVENING, settings.evening_titration), (DoseTarget.MORNING, settings.morning_titration)):
        dose = DOSE_NAMES[target]
        if titration is None:
            out[f"Ajustement de la {dose}"] = "aucun"
            continue
        out[f"Ajustement de la {dose}"] = "automatique"
        name = dose.capitalize()
        out[f"{name}, seuil bas"] = fmt_g_l(round(titration.low_g_l * 100))
        out[f"{name}, baisse"] = f"{titration.step_ui} UI"
        out[f"{name}, seuil haut"] = fmt_g_l(round(titration.high_g_l * 100))
        out[f"{name}, hausse"] = (
            f"{titration.step_ui} UI après {titration.high_streak_days} jour{'s' if titration.high_streak_days > 1 else ''}"
        )
        for x in titration.low_tiers:
            out[f"{name}, palier sous {fmt_g_l(round(x.below_g_l * 100))}"] = f"baisser de {x.step_ui} UI"
        for x in titration.high_tiers:
            out[f"{name}, palier au-dessus de {fmt_g_l(round(x.above_g_l * 100))}"] = (
                f"augmenter de {x.step_ui} UI après {x.days} jour{'s' if x.days > 1 else ''}"
            )
    if settings.morning_titration is not None:
        out["Plage du soir"] = f"{settings.evening_start:%H:%M}-{settings.evening_end:%H:%M}"
    out["Alerte hypoglycémie"] = f"sous {fmt_g_l(round(settings.hypo_alert_g_l * 100))}"
    out["Alerte hyperglycémie"] = f"au-dessus de {fmt_g_l(round(settings.hyper_alert_g_l * 100))}"
    out["Données trop anciennes"] = f"après {settings.stale_days} jours"
    return out


def protocol_diff(old: DosingSettings | None, new: DosingSettings) -> list[str]:
    """Ce qui change d'une version à l'autre, une ligne par réglage."""
    if old is None:
        return ["Premier protocole saisi"]
    before, after = _flatten(old), _flatten(new)
    lines = []
    for label, value in after.items():
        if label not in before:
            lines.append(f"{label} : {value} (nouveau)")
        elif before[label] != value:
            lines.append(f"{label} : {before[label]} → {value}")
    lines += [f"{label} : supprimé (était {value})" for label, value in before.items() if label not in after]
    return lines


def protocol_history(changes: list[ProtocolChange]) -> list[tuple[ProtocolChange, list[str]]]:
    """Versions du protocole, de la plus récente à la plus ancienne, avec ce que chacune a changé."""
    out = []
    previous = None
    for change in changes:
        out.append((change, protocol_diff(previous, change.settings)))
        previous = change.settings
    return list(reversed(out))
