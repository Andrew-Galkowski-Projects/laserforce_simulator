"""RV-05: pure, in-memory chart renderer for the Round-report PDF (#283, #285).

Renders the four per-player charts the events page shows (shots, lives,
points, SP over time) as PNG bytes the PDF builder (and later HX-03) can embed.
Deliberately PURE: no Django / ORM imports, no settings access, no file I/O
beyond an internal ``io.BytesIO`` buffer, and no global matplotlib state --
only the object-oriented API (a ``Figure`` driven by the Agg canvas), never
``matplotlib.pyplot`` and never ``matplotlib.use``.
"""

from __future__ import annotations

import colorsys
import io
from dataclasses import dataclass

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image

#: The events-page SP ceiling: the SP chart is fixed 0..99 with a dashed cap.
SP_CAP = 99

#: Figure size in inches: the letter content width (8.5in minus two 0.5in
#: margins) by a height that fits two charts per page with room to spare.
FIGURE_SIZE: tuple[float, float] = (7.5, 3.6)

#: Events-page hue bases: red team ``hsl(350 + 40*i/n, 70%, 45%)``, blue team
#: ``hsl(210 + 40*i/n, 70%, 45%)`` where ``i`` is the player's index within
#: the team and ``n`` the team size.
_TEAM_HUE_BASE: dict[str, float] = {"red": 350.0, "blue": 210.0}
_HUE_SPREAD = 40.0
_SATURATION = 0.70
_LIGHTNESS = 0.45


@dataclass(frozen=True)
class ChartDef:
    """One chart in the PDF section.

    ``y_min`` / ``y_max`` fix the y range (``None`` = no ceiling, matplotlib
    autoscale); ``cap`` draws a dashed horizontal line at that value.
    """

    key: str
    title: str
    y_min: float = 0.0
    y_max: float | None = None
    cap: float | None = None


#: The four charts in events-page card order; titles match the page cards.
CHARTS: tuple[ChartDef, ...] = (
    ChartDef(key="shots", title="Shots Over Time (per player)"),
    ChartDef(key="lives", title="Lives Over Time (per player)"),
    ChartDef(key="points", title="Points Over Time (per player)"),
    ChartDef(
        key="sp",
        title="Special Points (SP) Over Time (per player)",
        y_max=SP_CAP,
        cap=SP_CAP,
    ),
)

_CHARTS_BY_KEY: dict[str, ChartDef] = {chart.key: chart for chart in CHARTS}


def player_colour(team: str, index: int, team_size: int) -> tuple[float, float, float]:
    """The events-page line colour for the ``index``-th of ``team_size`` players
    on ``team`` (``"red"`` / ``"blue"``), as an RGB triple in 0..1.

    Ports the page's ``hsl(base + 40*i/n, 70%, 45%)`` rule; the hue wraps past
    360 (red at 350 + spread crosses it) exactly as CSS does.
    """
    base = _TEAM_HUE_BASE[team]
    hue = (base + _HUE_SPREAD * index / max(team_size, 1)) % 360.0
    return colorsys.hls_to_rgb(hue / 360.0, _LIGHTNESS, _SATURATION)


def _player_colours(players: list[dict]) -> dict[str, tuple[float, float, float]]:
    """Colour per ``str(player id)`` using each player's index within their team."""
    by_team: dict[str, list[dict]] = {}
    for player in players:
        by_team.setdefault(player["team"], []).append(player)
    colours: dict[str, tuple[float, float, float]] = {}
    for team, members in by_team.items():
        for index, player in enumerate(members):
            colours[str(player["id"])] = player_colour(team, index, len(members))
    return colours


def render_chart_png(chart_key: str, series: dict, *, dpi: int = 150) -> bytes:
    """Draw the ``chart_key`` chart of ``series`` and return it as PNG bytes.

    ``series`` is the #283 series-port shape: ``players`` (``{id, name, team}``
    in order) plus ``shots`` / ``lives`` / ``points`` / ``sp``, each keyed by
    ``str(player id)`` with a list of ``(seconds, value)`` points.

    Every player is a stepped line in their team hue. ``drawstyle="steps-post"``
    holds each value forward in time until the next point, which is what the
    page's Chart.js ``stepped: 'before'`` draws (Chart.js moves to
    ``(p1.x, p0.y)`` before rising to ``p1``). A legend of player names and the
    chart title are drawn; the x axis is seconds. An empty series (no players)
    renders titled empty axes.
    """
    chart = _CHARTS_BY_KEY.get(chart_key)
    if chart is None:
        known = ", ".join(_CHARTS_BY_KEY)
        raise ValueError(f"unknown chart key {chart_key!r}; expected one of {known}")
    players: list[dict] = series.get("players", [])
    points_by_player: dict = series.get(chart_key, {})
    colours = _player_colours(players)

    figure = Figure(figsize=FIGURE_SIZE, dpi=dpi, layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.add_subplot(1, 1, 1)

    lowest = 0.0
    for player in players:
        key = str(player["id"])
        points = points_by_player.get(key, [])
        xs = [float(x) for x, _ in points]
        ys = [float(y) for _, y in points]
        lowest = min([lowest, *ys])
        axes.plot(
            xs,
            ys,
            drawstyle="steps-post",
            color=colours[key],
            linewidth=1.5,
            label=player["name"],
        )

    axes.set_title(chart.title)
    axes.set_xlabel("Seconds")
    axes.set_xlim(left=0)
    # Chart.js ``beginAtZero``: the floor is ``y_min`` unless the data dips
    # below it (negative points), in which case autoscale keeps both in view.
    axes.set_ylim(
        bottom=chart.y_min if lowest >= chart.y_min else None, top=chart.y_max
    )
    if chart.cap is not None:
        axes.axhline(chart.cap, color="0.4", linestyle="--", linewidth=1)
    axes.grid(True, color="0.9")
    # No top / right frame, so a cap line on the ceiling stays visible.
    axes.spines["top"].set_visible(False)
    axes.spines["right"].set_visible(False)
    if players:
        figure.legend(loc="outside right center", fontsize="small")

    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=dpi)
    return buffer.getvalue()


def chart_flowable(png: bytes, width: float) -> Image:
    """Wrap ``png`` bytes as a ReportLab ``Image`` flowable ``width`` points
    wide, with the height scaled to keep the PNG's pixel aspect ratio.
    """
    reader = ImageReader(io.BytesIO(png))
    pixel_w, pixel_h = reader.getSize()
    height = width * pixel_h / pixel_w
    return Image(io.BytesIO(png), width=width, height=height)
