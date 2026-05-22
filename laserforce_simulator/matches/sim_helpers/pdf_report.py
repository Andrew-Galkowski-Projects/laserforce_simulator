"""RV-03: pure, in-memory Round-report PDF builder.

This module is the view <-> builder seam for the Round-report PDF export. It is
deliberately PURE: no Django / ORM imports, no settings access, no file I/O
beyond an internal ``io.BytesIO`` buffer. It consumes the ``report_data`` dict
assembled by the view (see the RV-03 seam contract) and returns the rendered
PDF as ``bytes``.

The diagonal "[Simulated]" watermark is drawn on EVERY page via a ReportLab
canvas page callback, gated through :func:`should_watermark`.
"""

from __future__ import annotations

import io
from typing import Callable

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Per-player columns: the RV-01 stat set, in the frozen seam-contract order.
# (header label, report_data["..._players"][i] key)
_PLAYER_COLUMNS: list[tuple[str, str]] = [
    ("Player", "name"),
    ("Role", "role"),
    ("Points", "points_scored"),
    ("MVP", "mvp"),
    ("Tags", "tags_made"),
    ("Tagged", "times_tagged"),
    ("Acc%", "accuracy"),
    ("Lives", "final_lives"),
    ("Resup", "resupplies_given"),
    ("Missiles", "missiles_landed"),
    ("Specials", "specials_used"),
    ("Follow-up", "follow_up_shots"),
    ("Reaction", "reaction_shots"),
    ("Combo", "combo_resupply_count"),
]

# Per-team resource summary block: (label, team_totals key).
_TOTALS_ROWS: list[tuple[str, str]] = [
    ("Team Points", "team_points"),
    ("Tags Made", "tags_made"),
    ("Resupplies Given", "resupplies_given"),
    ("Missiles Landed", "missiles_landed"),
    ("Specials Used", "specials_used"),
    ("Survivors", "survivors"),
]


def should_watermark(is_simulated: bool) -> bool:
    """Single decision point for whether the diagonal '[Simulated]' watermark
    is drawn. The page callback in :func:`build_round_report` consults this;
    tests assert on it directly without parsing compressed PDF streams.
    """
    return bool(is_simulated)


def _format_cell(value: object) -> str:
    """Render a player_row value for a table cell."""
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _make_watermark_callback(draw_watermark: bool) -> Callable[[object, object], None]:
    """Return an onPage callback that stamps the diagonal watermark.

    When ``draw_watermark`` is False the callback is a no-op, so no watermark
    is drawn on any page.
    """

    def _on_page(canvas, doc) -> None:
        if not draw_watermark:
            return
        canvas.saveState()
        width, height = doc.pagesize
        canvas.setFont("Helvetica-Bold", 72)
        canvas.setFillColor(colors.lightgrey)
        try:
            # Slight transparency where the backend supports it.
            canvas.setFillAlpha(0.25)
        except Exception:  # pragma: no cover - backend without alpha support
            pass
        canvas.translate(width / 2.0, height / 2.0)
        canvas.rotate(45)
        canvas.drawCentredString(0, 0, "[Simulated]")
        canvas.restoreState()

    return _on_page


def _scoreboard_table(players: list[dict]) -> Table:
    """Build a per-player scoreboard Table for one team."""
    header = [label for label, _key in _PLAYER_COLUMNS]
    rows = [header]
    for player in players:
        rows.append([_format_cell(player.get(key)) for _label, key in _PLAYER_COLUMNS])

    table = Table(rows, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                ("ALIGN", (0, 0), (0, -1), "LEFT"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f1f3f5")],
                ),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _totals_table(totals: dict) -> Table:
    """Build a per-team resource summary block."""
    rows = [["Metric", "Total"]]
    for label, key in _TOTALS_ROWS:
        rows.append([label, _format_cell(totals.get(key))])

    table = Table(rows, colWidths=[2.2 * inch, 1.2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#495057")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def build_round_report(report_data: dict, *, watermark: bool) -> bytes:
    """Render a Round-report PDF entirely in memory.

    PURE: no Django/ORM imports, no settings access, no file I/O beyond an
    internal io.BytesIO buffer. Consumes the ``report_data`` dict (shape in the
    RV-03 seam contract) and returns the PDF as bytes. The diagonal
    "[Simulated]" watermark is drawn on EVERY page via a ReportLab canvas page
    callback, gated by the ``watermark`` bool (routed through
    :func:`should_watermark`). When watermark is False, no watermark is drawn.

    Returns non-empty ``bytes`` starting with the literal PDF magic ``b"%PDF"``.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Round {report_data.get('round_id')} Report",
    )

    styles = getSampleStyleSheet()
    story: list = []

    # ----- Round summary block -----
    red_name = report_data.get("red_team_name", "")
    blue_name = report_data.get("blue_team_name", "")
    story.append(Paragraph(f"{red_name} vs {blue_name}", styles["Title"]))
    story.append(Paragraph(report_data.get("round_label", ""), styles["Heading2"]))

    summary_lines = [f"Round ID: {report_data.get('round_id')}"]
    if report_data.get("date_played"):
        summary_lines.append(f"Played: {report_data['date_played']}")
    # Map line omitted when map_name is None.
    if report_data.get("map_name") is not None:
        summary_lines.append(f"Map: {report_data['map_name']}")

    winner_name = report_data.get("winner_name")
    summary_lines.append(f"Winner: {winner_name if winner_name is not None else 'Tie'}")

    summary_lines.append(
        f"Final Score: {red_name} {report_data.get('red_points', 0)} - "
        f"{report_data.get('blue_points', 0)} {blue_name}"
    )
    if report_data.get("red_eliminated"):
        summary_lines.append(f"{red_name} eliminated")
    if report_data.get("blue_eliminated"):
        summary_lines.append(f"{blue_name} eliminated")

    for line in summary_lines:
        story.append(Paragraph(line, styles["Normal"]))
    story.append(Spacer(1, 0.25 * inch))

    # ----- Red scoreboard -----
    story.append(
        Paragraph(f"{red_name} (Red) - Player Performance", styles["Heading3"])
    )
    story.append(_scoreboard_table(report_data.get("red_players", [])))
    story.append(Spacer(1, 0.25 * inch))

    # ----- Blue scoreboard -----
    story.append(
        Paragraph(f"{blue_name} (Blue) - Player Performance", styles["Heading3"])
    )
    story.append(_scoreboard_table(report_data.get("blue_players", [])))
    story.append(Spacer(1, 0.3 * inch))

    # ----- Per-team resource summary -----
    story.append(Paragraph("Team Resource Summary", styles["Heading3"]))
    story.append(Paragraph(f"{red_name} (Red)", styles["Normal"]))
    story.append(_totals_table(report_data.get("red_totals", {})))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(f"{blue_name} (Blue)", styles["Normal"]))
    story.append(_totals_table(report_data.get("blue_totals", {})))

    on_page = _make_watermark_callback(should_watermark(watermark))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)

    return buffer.getvalue()
