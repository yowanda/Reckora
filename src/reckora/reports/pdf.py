"""PDF dossier export.

Layout mirrors the markdown / HTML dossier so a reader can swap between the
formats without losing context: header → identifiers → traces → correlation
edges → optional AI summary / hypotheses. Built on reportlab Platypus so we
stay pure-Python (no system libs like cairo / pango), keeping CI portable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..models.entity import Edge, Subject, Trace


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReckoraTitle",
        parent=base["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        spaceAfter=2,
    )
    meta = ParagraphStyle(
        "ReckoraMeta",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=9,
        textColor=colors.HexColor("#555555"),
        spaceAfter=12,
    )
    h2 = ParagraphStyle(
        "ReckoraH2",
        parent=base["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        spaceBefore=14,
        spaceAfter=6,
        textColor=colors.HexColor("#222222"),
    )
    body = ParagraphStyle(
        "ReckoraBody",
        parent=base["BodyText"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
    )
    mono = ParagraphStyle(
        "ReckoraMono",
        parent=body,
        fontName="Courier",
        fontSize=9,
        leading=12,
    )
    empty = ParagraphStyle(
        "ReckoraEmpty",
        parent=body,
        fontSize=9,
        textColor=colors.HexColor("#888888"),
    )
    card_title = ParagraphStyle(
        "ReckoraCardTitle",
        parent=body,
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=14,
        spaceAfter=2,
    )
    return {
        "title": title,
        "meta": meta,
        "h2": h2,
        "body": body,
        "mono": mono,
        "empty": empty,
        "card_title": card_title,
    }


def _confidence_band(value: float) -> str:
    if value >= 0.7:
        return "HIGH"
    if value >= 0.4:
        return "MEDIUM"
    return "LOW"


def _confidence_color(value: float) -> colors.Color:
    if value >= 0.7:
        return colors.HexColor("#1f7a4d")
    if value >= 0.4:
        return colors.HexColor("#b88600")
    return colors.HexColor("#99312a")


def _esc(value: object) -> str:
    """Escape a value for inclusion inside a reportlab Paragraph."""
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _kv_table(rows: list[tuple[str, str]], styles: dict[str, ParagraphStyle]) -> Table:
    data = [
        [
            Paragraph(_esc(k), styles["body"]),
            Paragraph(v, styles["body"]),
        ]
        for k, v in rows
    ]
    table = Table(data, colWidths=[35 * mm, 130 * mm], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _trace_card(trace: Trace, styles: dict[str, ParagraphStyle]) -> KeepTogether:
    title = Paragraph(
        f"<b>{_esc(trace.source.value)}</b> &middot; <font face='Courier'>"
        f"{_esc(trace.identifier.value)}</font>",
        styles["card_title"],
    )
    rows: list[tuple[str, str]] = []
    short = trace.evidence.payload_sha256[:16]
    rows.append(
        (
            "evidence",
            f"<font face='Courier'>{_esc(short)}…</font> "
            f"fetched {_esc(trace.evidence.fetched_at.isoformat())}",
        )
    )
    rows.append(
        (
            "source",
            f"<link href='{_esc(trace.evidence.source_url)}'>"
            f"{_esc(trace.evidence.source_url)}</link>",
        )
    )
    if trace.evidence.archive_url:
        rows.append(
            (
                "archive",
                f"<link href='{_esc(trace.evidence.archive_url)}'>"
                f"{_esc(trace.evidence.archive_url)}</link>",
            )
        )
    for k, v in trace.fields.items():
        if v in (None, "", []):
            continue
        rows.append((str(k), f"<font face='Courier'>{_esc(v)}</font>"))

    return KeepTogether([title, _kv_table(rows, styles), Spacer(1, 6)])


def _edge_table(edges: list[Edge], styles: dict[str, ParagraphStyle]) -> Table:
    header = [
        Paragraph("<b>edge</b>", styles["body"]),
        Paragraph("<b>kind</b>", styles["body"]),
        Paragraph("<b>confidence</b>", styles["body"]),
        Paragraph("<b>reasons</b>", styles["body"]),
    ]
    data: list[list[Paragraph]] = [header]
    for edge in edges:
        edge_cell = Paragraph(
            f"<font face='Courier'>{_esc(edge.source.value)}</font> &harr; "
            f"<font face='Courier'>{_esc(edge.target.value)}</font>",
            styles["body"],
        )
        kind_cell = Paragraph(_esc(edge.kind.value), styles["body"])
        band = _confidence_band(edge.confidence)
        color = _confidence_color(edge.confidence).hexval()[2:]
        conf_cell = Paragraph(
            f"<font color='#{color}'><b>{band}</b></font> ({edge.confidence:.0%})",
            styles["body"],
        )
        reasons = "<br/>".join(f"&bull; {_esc(r)}" for r in edge.reasons) if edge.reasons else ""
        reasons_cell = Paragraph(reasons or "&nbsp;", styles["body"])
        data.append([edge_cell, kind_cell, conf_cell, reasons_cell])

    table = Table(
        data,
        colWidths=[55 * mm, 30 * mm, 25 * mm, 55 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef1f5")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#cccccc")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#eeeeee")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def to_dossier_pdf(
    *,
    subject: Subject,
    traces: list[Trace],
    edges: list[Edge],
    summary: str | None = None,
    hypotheses: str | None = None,
) -> bytes:
    """Render a complete dossier as a PDF document and return raw bytes."""
    styles = _styles()
    seed = subject.seed_identifier
    story: list[object] = []

    story.append(
        Paragraph(
            f"Reckora dossier &mdash; <font face='Courier'>{_esc(seed.type.value)}:"
            f"{_esc(seed.value)}</font>",
            styles["title"],
        )
    )
    story.append(
        Paragraph(
            f"generated {datetime.now(UTC).isoformat()} &middot; subject "
            f"<font face='Courier'>{_esc(subject.id)}</font>",
            styles["meta"],
        )
    )

    story.append(Paragraph("Identifiers", styles["h2"]))
    if subject.identifiers:
        items = "&nbsp; &nbsp;".join(
            f"<font face='Courier'>{_esc(i.type.value)}</font>:"
            f"<font face='Courier'>{_esc(i.value)}</font>"
            for i in subject.identifiers
        )
        story.append(Paragraph(items, styles["body"]))
    else:
        story.append(Paragraph("none", styles["empty"]))

    story.append(Paragraph("Traces", styles["h2"]))
    if traces:
        for t in traces:
            story.append(_trace_card(t, styles))
    else:
        story.append(Paragraph("no traces", styles["empty"]))

    story.append(Paragraph("Correlation edges", styles["h2"]))
    if edges:
        story.append(_edge_table(edges, styles))
    else:
        story.append(Paragraph("no edges", styles["empty"]))

    if summary:
        story.append(PageBreak())
        story.append(Paragraph("AI summary", styles["h2"]))
        story.append(Paragraph(_esc(summary).replace("\n", "<br/>"), styles["body"]))
    if hypotheses:
        story.append(Paragraph("AI hypotheses", styles["h2"]))
        story.append(Paragraph(_esc(hypotheses).replace("\n", "<br/>"), styles["body"]))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Reckora dossier — {seed.type.value}:{seed.value}",
        author="Reckora",
    )
    doc.build(story)
    return buffer.getvalue()
