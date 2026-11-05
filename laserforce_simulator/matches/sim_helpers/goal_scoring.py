"""MOVE-06+07: scored positioning goals (spec #540).

Positioning goals (Scout high-sight cells, a healthy Heavy's strong spots,
the Medic / Ammo cells the Heavy can see, the enemy-base default) are a
scored draw instead of "the nearest best cell", so teammates spread out.

**Formula** (tunable; also written out in docs/agents/matches/sim-helpers.md):

    score = VALUE_WEIGHT x value
            - DISTANCE_WEIGHT x (walk_ft / 100)
            - CROWDING_WEIGHT x crowding

``value`` (0..1) comes from the caller's role rule; ``walk_ft`` is the
walking distance in feet; ``crowding`` is the number of teammates whose cell
or committed goal is within ``CROWD_RADIUS_FEET`` (Chebyshev) of the cell.
The ``TOP_K`` best are drawn with weights ``exp((score - best) / T)``,
``T = T_MIN + T_SPAN x (1 - positioning / 100)``: one ``random.random()``.

Pure, Django-free; map-less rounds never reach it.
"""

from __future__ import annotations

import random
from math import exp
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from .map_context import MapContext

# ── Formula constants (MOVE-06 decisions 3, 4, 8) ────────────────────────
VALUE_WEIGHT: float = 1.0
#: Score lost per 100 ft of walking.
DISTANCE_WEIGHT: float = 0.5
#: Score lost per teammate on or heading to a cell within the radius.
CROWDING_WEIGHT: float = 0.5
CROWD_RADIUS_FEET: float = 15.0
TOP_K: int = 8
#: The enemy-base default: floor cells within this walk of the base cell.
BASE_RADIUS_FEET: float = 15.0
T_MIN: float = 0.05
T_SPAN: float = 0.45

Cell = tuple[int, int]


def temperature(player) -> float:
    """The draw's sharpness: low for high positioning (picks near the best)."""
    return T_MIN + T_SPAN * (1 - getattr(player, "positioning", 50) / 100)


def _teammate_marks(player, all_alive: list, tick: Optional[float]) -> list[list[Cell]]:
    """Per teammate, the cells that crowd: their cell and their committed goal
    while it is live (not expired at ``tick``). A teammate counts once."""
    marks = []
    for mate in all_alive:
        if mate is player or mate.team_color != player.team_color:
            continue
        cells = []
        if mate.cell_row is not None:
            cells.append((mate.cell_row, mate.cell_col))
        committed = getattr(mate, "_committed_goal", None)
        if committed is not None and (tick is None or tick < committed[2]):
            cells.append(committed[0])
        if cells:
            marks.append(cells)
    return marks


def score_candidates(
    player,
    candidates: list[Cell],
    value_of: Callable[[Cell], float],
    all_alive: list,
    movement_ctx: MapContext,
    tick: Optional[float] = None,
) -> list[tuple[float, Cell]]:
    """``(score, cell)`` for every reachable candidate, best first (ties by
    row, col). ``tick`` drops teammates' expired commitments from crowding."""
    if player.cell_row is None:
        return []
    start = (player.cell_row, player.cell_col)
    if start in movement_ctx.get_adjacency():
        distances = movement_ctx.walk_distances(start)
    else:
        # No movement graph at the player's cell (hand-built contexts):
        # straight-line squares, so a candidate is never dropped for it.
        distances = {c: abs(c[0] - start[0]) + abs(c[1] - start[1]) for c in candidates}
    radius = movement_ctx.squares(CROWD_RADIUS_FEET)
    marks = _teammate_marks(player, all_alive, tick)
    scored = []
    for cell in dict.fromkeys(candidates):
        if cell not in distances:
            continue
        walk_ft = distances[cell] * movement_ctx.feet_per_square
        crowding = sum(
            1
            for cells in marks
            if any(
                max(abs(m[0] - cell[0]), abs(m[1] - cell[1])) <= radius for m in cells
            )
        )
        score = (
            VALUE_WEIGHT * value_of(cell)
            - DISTANCE_WEIGHT * (walk_ft / 100)
            - CROWDING_WEIGHT * crowding
        )
        scored.append((score, cell))
    scored.sort(key=lambda sc: (-sc[0], sc[1]))
    return scored


def draw_goal(
    player,
    candidates: list[Cell],
    value_of: Callable[[Cell], float],
    all_alive: list,
    movement_ctx: MapContext,
    tick: Optional[float] = None,
) -> Optional[Cell]:
    """Draw one of the ``TOP_K`` best candidates; None when none is reachable."""
    scored = score_candidates(
        player, candidates, value_of, all_alive, movement_ctx, tick
    )
    top = scored[:TOP_K]
    if not top:
        return None
    t = temperature(player)
    best = top[0][0]
    weights = [exp((score - best) / t) for score, _ in top]
    draw = random.random() * sum(weights)
    for weight, (_, cell) in zip(weights, top):
        if draw < weight:
            return cell
        draw -= weight
    return top[-1][1]


def enemy_base_cells(enemy_color: str, movement_ctx: MapContext) -> list[Cell]:
    """Floor cells within ``BASE_RADIUS_FEET`` walk of the enemy base cell."""
    base = movement_ctx.get_spawn_cells().get(enemy_color)
    if base is None or base not in movement_ctx.get_adjacency():
        return []
    radius = movement_ctx.squares(BASE_RADIUS_FEET)
    distances = movement_ctx.walk_distances(base, radius)
    return sorted(c for c, d in distances.items() if d <= radius)
