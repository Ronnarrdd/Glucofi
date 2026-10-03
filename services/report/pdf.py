"""Export PDF du suivi glycémique (reportlab + figures matplotlib)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from contracts import (
    MEAL_LABELS_FR,
    NOTE_TAG_LABELS_FR,
    RULE_LABELS_FR,
    AlertLevel,
    DoseChange,
    DoseProposal,
    DosingSettings,
    NoteTag,
    Reading,
)
from services.charts import PERIODS, Stats, compute_stats, period_of, stats_by_period
from services.charts.figures import distribution_figure, figure_png, morning_trend_figure, timeline_figure
from services.dosing import excluded_from_dosing, exclusion_refused, fmt_g_l, fmt_mg_dl, morning_readings

DISCLAIMER = (
    "Document généré par Glucofi à partir des mesures du lecteur Accu-Chek Guide. "
    "Les propositions de dose appliquent le protocole prescrit par le médecin ; "
    "elles ne remplacent pas son avis. En cas de doute, d'hypoglycémie répétée ou de "
    "glycémie très élevée, contactez le médecin."
)
CONTENT_WIDTH = A4[0] - 3 * cm
HEADER_BG = colors.HexColor("#3584e4")
ROW_ALT = colors.HexColor("#f2f6fc")
LOW_BG = colors.HexColor("#fbd5d7")
HIGH_BG = colors.HexColor("#ffe3c7")
DAYS_FR = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
MORNING_LEGEND = (
    "Matin : « retenue » = glycémie du matin utilisée pour l'ajustement de la dose ; « écartée » = retirée de "
    "l'ajustement par une note du patient ; « comptée » = marquée à écarter mais sous le seuil bas, donc utilisée "
    "quand même (une glycémie basse n'est jamais écartée)."
)


@dataclass(frozen=True)
class ReportInput:
    readings: Sequence[Reading]
    changes: Sequence[DoseChange]
    settings: DosingSettings
    since: datetime
    until: datetime
    patient_name: str = ""
    proposal: DoseProposal | None = None
    generated_at: datetime | None = None


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontSize=18, spaceAfter=4),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=13, spaceBefore=10, spaceAfter=6),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontSize=9.5, leading=13),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#5e5c64")),
        "alert": ParagraphStyle("alert", parent=base["BodyText"], fontSize=9.5, leading=13, textColor=colors.HexColor("#c01c28")),
    }


def _table(rows: list[list], widths: list[float], extra: list | None = None) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c0bfbc")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    table.setStyle(TableStyle(style + (extra or [])))
    return table


def _figure_image(fig, dpi: int, width: float = CONTENT_WIDTH) -> Image:
    buf = io.BytesIO(figure_png(fig, dpi))
    w_in, h_in = fig.get_size_inches()
    return Image(buf, width=width, height=width * h_in / w_in)


def _fmt_mean(stats: Stats) -> str:
    return fmt_g_l(round(stats.mean_mg)) if stats.mean_mg is not None else "-"


def _stats_rows(overall: Stats, by_period: dict[str, Stats]) -> list[list[str]]:
    rows = [["", "Mesures", "Moyenne", "Min", "Max", "Sous l'objectif", "Dans l'objectif", "Au-dessus", "Hypo"]]
    for label, s in [("Toutes", overall)] + [(p, by_period[p]) for p in PERIODS]:
        if s.count == 0:
            rows.append([label, "0", "-", "-", "-", "-", "-", "-", "-"])
            continue
        rows.append([
            label, str(s.count), _fmt_mean(s), fmt_g_l(s.min_mg), fmt_g_l(s.max_mg),
            f"{s.pct_low:.0f} %", f"{s.pct_in_range:.0f} %", f"{s.pct_high:.0f} %", f"{s.pct_hypo:.0f} %",
        ])
    return rows


def morning_status(reading: Reading, retained: set[Reading], settings: DosingSettings) -> str:
    """Colonne « Matin » : retenue pour l'ajustement, écartée par une note, ou comptée malgré la note (sous le seuil bas)."""
    if reading in retained:
        return "comptée" if exclusion_refused(reading, settings) else "retenue"
    if excluded_from_dosing(reading, settings):
        return "écartée"
    return ""


def notes_summary(readings: Sequence[Reading], settings: DosingSettings) -> str:
    """« 5 mesures avec une note : Repas copieux 3, Malade 1, texte libre 2. 2 glycémies du matin écartées de l'ajustement. »"""
    noted = [r for r in readings if r.note is not None and not r.note.empty]
    if not noted:
        return ""
    counts = [(NOTE_TAG_LABELS_FR[tag], sum(tag in r.note.tags for r in noted)) for tag in NoteTag]
    counts.append(("texte libre", sum(bool(r.note.text) for r in noted)))
    text = f"{len(noted)} mesure(s) avec une note : " + ", ".join(f"{name} {n}" for name, n in counts if n) + "."
    excluded = sum(excluded_from_dosing(r, settings) for r in noted)
    if excluded:
        text += f" {excluded} glycémie(s) du matin écartée(s) de l'ajustement de la dose."
    return text


def _dose_details(change: DoseChange) -> str:
    details = "<br/>".join(map(escape, change.evidence)) or escape(change.note or "-")
    if change.excluded:
        details += "<br/><i>Écartées :</i><br/>" + "<br/>".join(map(escape, change.excluded))
    return details


def build_report(data: ReportInput, path: Path | str, dpi: int = 160) -> Path:
    path = Path(path)
    st = _styles()
    generated = data.generated_at or datetime.now()
    period = [r for r in data.readings if data.since <= r.device_time < data.until]
    settings = data.settings
    current = max(data.changes, key=lambda c: c.effective) if data.changes else None

    story: list = [Paragraph("Suivi glycémique", st["title"])]
    who = f"<b>{escape(data.patient_name)}</b> - " if data.patient_name else ""
    story.append(Paragraph(
        f"{who}Période du {data.since:%d/%m/%Y} au {data.until:%d/%m/%Y} - {escape(settings.insulin)}<br/>"
        f"Objectif avant les repas : {fmt_g_l(round(settings.low_g_l * 100))} à {fmt_g_l(round(settings.high_g_l * 100))}. "
        f"Généré le {generated:%d/%m/%Y à %H:%M}.",
        st["body"],
    ))

    story.append(Paragraph("Traitement", st["h2"]))
    if current:
        story.append(Paragraph(
            f"Dose en cours depuis le {current.effective:%d/%m/%Y} : <b>{current.morning_ui} UI le matin</b>, "
            f"<b>{current.evening_ui} UI le soir</b>.",
            st["body"],
        ))
    if data.proposal:
        p = data.proposal
        verdict = (
            f"<b>Proposition : passer la dose du soir à {p.proposed_evening_ui} UI</b> (à valider)."
            if p.changes_dose else "<b>Proposition : dose inchangée.</b>"
        )
        story.append(Paragraph(f"{verdict} {escape(p.reason)}", st["body"]))
        for alert in p.alerts:
            style = st["alert"] if alert.level is AlertLevel.DANGER else st["body"]
            story.append(Paragraph(f"Alerte : {escape(alert.message)}", style))

    story.append(Paragraph("Statistiques", st["h2"]))
    overall = compute_stats(period, settings)
    widths = [2.4 * cm, 1.5 * cm, 1.9 * cm, 1.7 * cm, 1.7 * cm, 2.3 * cm, 2.3 * cm, 1.9 * cm, 1.3 * cm]
    story.append(_table(_stats_rows(overall, stats_by_period(period, settings)), widths))
    story.append(Paragraph(
        f"Matin : entre {settings.morning_start:%H:%M} et {settings.morning_end:%H:%M}, première mesure « à jeun », "
        "sinon « avant repas » ou sans marqueur (« après repas » et « coucher » exclus). "
        f"Hypo : sous {fmt_g_l(round(settings.hypo_alert_g_l * 100))}.",
        st["small"],
    ))

    story.append(Paragraph("Courbe des glycémies", st["h2"]))
    story.append(_figure_image(timeline_figure(period, data.changes, settings, data.since, data.until), dpi))
    story.append(KeepTogether([
        Paragraph("Glycémies du matin et dose du soir", st["h2"]),
        _figure_image(morning_trend_figure(period, data.changes, settings, data.since, data.until), dpi),
    ]))
    story.append(KeepTogether([
        Paragraph("Répartition", st["h2"]),
        _figure_image(distribution_figure(period, settings), dpi),
    ]))

    story.append(Paragraph("Historique des doses", st["h2"]))
    if data.changes:
        rows = [["Depuis le", "Matin", "Soir", "Motif", "Mesures justificatives"]]
        for c in sorted(data.changes, key=lambda c: c.effective):
            rows.append([
                f"{c.effective:%d/%m/%Y %H:%M}", f"{c.morning_ui} UI", f"{c.evening_ui} UI",
                Paragraph(RULE_LABELS_FR[c.rule], st["small"]),
                Paragraph(_dose_details(c), st["small"]),
            ])
        story.append(_table(rows, [2.8 * cm, 1.5 * cm, 1.5 * cm, 5 * cm, 7.2 * cm]))
    else:
        story.append(Paragraph("Aucune dose enregistrée.", st["body"]))

    story.append(PageBreak())
    story.append(Paragraph("Détail des mesures", st["h2"]))
    if period:
        notes = notes_summary(period, settings)
        if notes:
            story.append(Paragraph(escape(notes), st["body"]))
            story.append(Spacer(1, 6))
        morning_keys = {m.reading for m in morning_readings(period, settings)}
        low, high = round(settings.low_g_l * 100), round(settings.high_g_l * 100)
        rows = [["Date", "Heure", "Glycémie", "mg/dL", "Moment", "Marqueur", "Matin", "Note"]]
        extra = []
        for i, r in enumerate(sorted(period, reverse=True), start=1):
            rows.append([
                f"{DAYS_FR[r.device_time.weekday()]} {r.device_time:%d/%m/%Y}", f"{r.device_time:%H:%M}", fmt_g_l(r.mg_dl), fmt_mg_dl(r.mg_dl),
                period_of(r, settings), MEAL_LABELS_FR[r.meal] if r.meal is not None else "",
                morning_status(r, morning_keys, settings),
                Paragraph(escape(r.note.summary), st["small"]) if r.note is not None and r.note.summary else "",
            ])
            if r.mg_dl < low:
                extra.append(("BACKGROUND", (2, i), (3, i), LOW_BG))
            elif r.mg_dl > high:
                extra.append(("BACKGROUND", (2, i), (3, i), HIGH_BG))
        widths = [2.6 * cm, 1.1 * cm, 2.3 * cm, 1.8 * cm, 1.9 * cm, 2.4 * cm, 1.5 * cm, 4.4 * cm]
        story.append(_table(rows, widths, extra + [("ALIGN", (7, 1), (7, -1), "LEFT")]))
        story.append(Paragraph(MORNING_LEGEND, st["small"]))
    else:
        story.append(Paragraph("Aucune mesure sur la période.", st["body"]))

    story.append(Spacer(1, 12))
    story.append(Paragraph(DISCLAIMER, st["small"]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#77767b"))
        canvas.drawString(1.5 * cm, 1 * cm, f"Glucofi - {data.patient_name or 'suivi glycémique'} - {generated:%d/%m/%Y}")
        canvas.drawRightString(A4[0] - 1.5 * cm, 1 * cm, f"Page {doc.page}")
        canvas.restoreState()

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.5 * cm, bottomMargin=1.6 * cm,
        title="Suivi glycémique", author="Glucofi", subject=data.patient_name,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
