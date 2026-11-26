from datetime import datetime, timezone
from io import BytesIO
from uuid import uuid4
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def source_label(row):
    return f"{row['filename']} · {row['locator']} · version {row['number']}"


def response_pdf(turn):
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=48,
        bottomMargin=58,
        leftMargin=54,
        rightMargin=54,
    )
    ink = colors.HexColor("#111827")
    muted = colors.HexColor("#475569")
    title = ParagraphStyle(
        "GuideBot title",
        fontName="Helvetica-Bold",
        fontSize=23,
        leading=29,
        textColor=ink,
    )
    heading = ParagraphStyle(
        "GuideBot heading",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=16,
        textColor=ink,
        spaceBefore=15,
    )
    paragraph = ParagraphStyle(
        "GuideBot text",
        fontSize=10,
        leading=15,
        textColor=ink,
        alignment=TA_LEFT,
        spaceAfter=7,
    )
    metadata = ParagraphStyle(
        "GuideBot metadata", fontSize=9, leading=13, textColor=muted, spaceAfter=6
    )

    def block(value, style):
        return Paragraph(escape(value).replace("\n", "<br/>"), style)

    content = [
        block("GuideBot", title),
        block("Réponse et références documentaires", metadata),
        Spacer(1, 13),
        block("Question", heading),
        block(turn["question"], paragraph),
        block("Réponse", heading),
        block(turn["answer"], paragraph),
    ]
    if turn.get("explanation"):
        content.extend(
            [
                block("Interprétation automatique", heading),
                block(turn["explanation"], paragraph),
            ]
        )
    if turn["sources"]:
        content.append(block("Sources consultées", heading))
        for index, row in enumerate(turn["sources"], 1):
            content.extend(
                [
                    block(f"[{index}] {source_label(row)}", heading),
                    block(
                        f"Auteur : {row['author']} | Publication : {row['publication']} | Valide jusqu’au : {row['expiration']}",
                        metadata,
                    ),
                    block(row["text"], paragraph),
                ]
            )
    if turn.get("web_sources"):
        content.append(block("Sources Web publiques", heading))
        for index, row in enumerate(turn["web_sources"], len(turn["sources"]) + 1):
            headline = row.get("kind") == "press_headline"
            provenance = [row.get("publisher") or row["host"]]
            if headline:
                provenance.append("Titre de presse · via Google Actualités")
            if row.get("date"):
                provenance.append(row["date"])
            content.extend(
                [
                    block(f"[{index}] {row['title']}", heading),
                    block(" · ".join(provenance), metadata),
                    block(row["url"], metadata),
                ]
            )
            if not headline:
                content.append(block(row["text"], paragraph))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(muted)
        canvas.drawString(54, 35, "GuideBot")
        canvas.drawRightString(A4[0] - 54, 35, str(doc.page))
        canvas.restoreState()

    document.build(content, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def reminder_file(title, starts, domain):
    def ical(value):
        return (
            value.replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace(",", "\\,")
            .replace(";", "\\;")
        )

    def fold(line):
        chunks = [""]
        for char in line:
            if len((chunks[-1] + char).encode()) > 75:
                chunks.append(" ")
            chunks[-1] += char
        return "\r\n".join(chunks)

    return "\r\n".join(
        map(
            fold,
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//GuideBot//Rappel//FR",
                "BEGIN:VEVENT",
                f"UID:{uuid4().hex}@guidebot.local",
                f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
                f"DTSTART:{starts:%Y%m%dT%H%M%S}",
                "DURATION:PT15M",
                f"SUMMARY:{ical(title)}",
                f"DESCRIPTION:{ical(domain)}",
                "BEGIN:VALARM",
                "TRIGGER:-PT15M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{ical(title)}",
                "END:VALARM",
                "END:VEVENT",
                "END:VCALENDAR",
                "",
            ],
        )
    ).encode()
