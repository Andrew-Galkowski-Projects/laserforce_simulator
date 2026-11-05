"""MOVE-08: the Downed option.

At every Down a player rolls one **Downed option** and keeps to it until
they are Active again (``last_downed_time + RESPAWN_TICKS``):

- **Stay hidden**: hide on the Down cell (the 50% hide-miss applies).
- **Shuffle to cover**: walk to the nearest cell no remembered enemy can see.
- **Make a trail**: walk to a cell ``TRAIL_FEET`` away and back to the
  spot, so an enemy who follows becomes a Chaser (MECH-10). Allowed only
  when the tagger is more than ``TAGGER_MIN_FEET`` away by walking.

Distances are feet, turned into the map's squares by ``MapContext.squares``.

A Resupply can also lead to Make a trail (``RESUPPLY_TRAIL_CHANCE``).

**Formula** (tunable; also written out in docs/agents/matches/sim-helpers.md):

    stay    = STAY_BASE_WEIGHT * (1.5 - p) * (1.5 - (speed + stamina) / 200)
    shuffle = SHUFFLE_WEIGHT
    trail   = TRAIL_WEIGHT if allowed else 0

``p`` is the Down cell's sight-line rank on the map (``MapContext.los_rank``,
0 = most hidden, 1 = most open). The weights are normalised and drawn with
one ``random.random()`` in the order stay, shuffle, trail.

Three entry points:

- ``choose_downed_option(player, tagger, tick, ctx)`` from ``record_down``.
- ``choose_resupply_trail(player, tick, ctx)`` from ``combat._reset_resupplied``.
- ``downed_goal(player, tick, movement_ctx)`` from ``choose_goal_cell``.

State is the transient ``PlayerState.downed_plan``. Map-less rounds: inert.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import hypot
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .map_context import MapContext
    from .round_context import RoundContext

from .time_constants import RESPAWN_TICKS

# ── Formula constants (MOVE-08 decisions 9, 15, 17) ──────────────────────
STAY_BASE_WEIGHT: float = 20.0
SHUFFLE_WEIGHT: float = 50.0
TRAIL_WEIGHT: float = 30.0
RESUPPLY_TRAIL_CHANCE: float = 0.25
# Distances are feet, turned into squares by the round's map
# (``MapContext.squares``; on Syracuse 5 ft is 1 square).
#: Feet out (by walking) the trail's turn cell lies from the spot.
TRAIL_FEET: float = 10.0
#: The tagger (or, after a Resupply, the nearest enemy) must be farther than
#: this by walking for Make a trail to be allowed.
TAGGER_MIN_FEET: float = 10.0
#: Cover farther than this by walking turns Shuffle to cover into Stay hidden.
SHUFFLE_MAX_FEET: float = 5.0

STAY_HIDDEN = "stay_hidden"
SHUFFLE_TO_COVER = "shuffle_to_cover"
MAKE_A_TRAIL = "make_a_trail"

Cell = tuple[int, int]


@dataclass
class DownedPlan:
    """One Downed option: ``spot`` is the Down (or Resupply) cell."""

    option: str
    spot: Cell
    turn_cell: Optional[Cell] = None
    outbound: bool = True
    cover: Optional[Cell] = None


def plan_is_live(player, tick: float) -> bool:
    """True while ``player`` has a Downed plan and is not yet Active."""
    return getattr(player, "downed_plan", None) is not None and not (
        player.is_active_at(tick)
    )


def active_before_nuke(player) -> bool:
    """True when the Downed player is Active at least one tick before the
    pending Nuke detonates, so the nuke reaction may override their option."""
    detonates = getattr(player, "nuke_detonates_at", None)
    if detonates is None or player.last_downed_time is None:
        return False
    return player.last_downed_time + RESPAWN_TICKS <= detonates - 1


def overrides_stay_hidden(player, tick: float, all_alive: list) -> bool:
    """True when a goal-selection override beats Stay hidden's hide: the nuke
    reaction while Active in time (decision 13), or the critical-resource
    seek for a non-support player with that support alive (decision 14,
    the same 30% rule as ``choose_goal_cell`` step 1)."""
    if getattr(player, "reacting_to_nuke", False) and active_before_nuke(player):
        return True
    if player.role in ("medic", "ammo"):
        return False
    if player.final_lives <= player.max_lives * 0.3:
        support = "medic"
    elif player.final_shots <= player.max_shots * 0.3:
        support = "ammo"
    else:
        return False
    return any(
        p.team_color == player.team_color
        and p.role == support
        and p.final_lives > 0
        and p.cell_row is not None
        for p in all_alive
    )


def _walk_distances(start: Cell, adjacency: dict, limit: int) -> dict[Cell, int]:
    """Walking distance from ``start`` to every cell within ``limit`` steps."""
    distances = {start: 0}
    frontier = [start]
    for step in range(1, limit + 1):
        nxt = []
        for cell in frontier:
            for neighbour in adjacency.get(cell, ()):
                if neighbour not in distances:
                    distances[neighbour] = step
                    nxt.append(neighbour)
        frontier = nxt
    return distances


def _cell(player) -> Cell:
    return (player.cell_row, player.cell_col)


def option_weights(
    player, trail_allowed: bool, movement_ctx: MapContext
) -> dict[str, float]:
    """The un-normalised weights of the three options (the tunable formula)."""
    p = movement_ctx.los_rank(_cell(player))
    mobility = (player.speed + player.stamina) / 200
    return {
        STAY_HIDDEN: STAY_BASE_WEIGHT * (1.5 - p) * (1.5 - mobility),
        SHUFFLE_TO_COVER: SHUFFLE_WEIGHT,
        MAKE_A_TRAIL: TRAIL_WEIGHT if trail_allowed else 0.0,
    }


def _remembered_enemy_cells(player, tick: float) -> list[Cell]:
    from .pathfinding import _known_enemies_from_memory  # avoids a cycle

    enemy_color = "blue" if player.team_color == "red" else "red"
    return _known_enemies_from_memory(player, enemy_color, tick, "ticks")


def _nearest_cover(
    player, from_cell: Cell, tick: float, movement_ctx: MapContext
) -> Optional[Cell]:
    """Nearest cell within ``SHUFFLE_MAX_FEET`` (ties by row, col) that no
    remembered enemy can see; None when none, or no enemy is remembered."""
    watchers = _remembered_enemy_cells(player, tick)
    if not watchers:
        return None
    distances = _walk_distances(
        from_cell,
        movement_ctx.get_adjacency(),
        movement_ctx.squares(SHUFFLE_MAX_FEET),
    )
    for cell in sorted(distances, key=lambda c: (distances[c], c)):
        if not any(movement_ctx.can_see(w, cell) for w in watchers):
            return cell
    return None


def _turn_cell(spot: Cell, away_from: Optional[Cell], movement_ctx: MapContext) -> Cell:
    """The cell ``TRAIL_FEET`` from ``spot`` by walking (the farthest
    within it when none is that far), farthest from ``away_from``: by walking,
    then straight line, then (row, col)."""
    adjacency = movement_ctx.get_adjacency()
    trail = movement_ctx.squares(TRAIL_FEET)
    distances = _walk_distances(spot, adjacency, trail)
    reach = max(distances.values())
    candidates = [c for c, d in distances.items() if d == reach]
    if away_from is None:
        return min(candidates)
    from_tagger = _walk_distances(away_from, adjacency, 10 * trail)
    far = 10 * trail + 1
    return min(
        candidates,
        key=lambda c: (
            -from_tagger.get(c, far),
            -hypot(c[0] - away_from[0], c[1] - away_from[1]),
            c,
        ),
    )


def _within_tagger_range(
    cell: Cell, others: list[Cell], movement_ctx: MapContext
) -> bool:
    """True when any of ``others`` is within ``TAGGER_MIN_FEET`` by walking."""
    distances = _walk_distances(
        cell, movement_ctx.get_adjacency(), movement_ctx.squares(TAGGER_MIN_FEET)
    )
    return any(o in distances for o in others)


def _trail_plan(
    spot: Cell, away_from: Optional[Cell], movement_ctx: MapContext
) -> DownedPlan:
    return DownedPlan(
        option=MAKE_A_TRAIL,
        spot=spot,
        turn_cell=_turn_cell(spot, away_from, movement_ctx),
    )


def choose_downed_option(
    player, tagger, tick: float, ctx: Optional[RoundContext]
) -> None:
    """Roll the Downed option at a Down (``record_down``, before the
    ``last_downed_time`` stamp). No map or no cell: no plan."""
    movement_ctx = getattr(ctx, "movement_ctx", None)
    if movement_ctx is None or player.cell_row is None:
        player.downed_plan = None
        return
    spot = _cell(player)
    tagger_cell = (
        _cell(tagger) if tagger is not None and tagger.cell_row is not None else None
    )
    trail_allowed = tagger_cell is None or not _within_tagger_range(
        spot, [tagger_cell], movement_ctx
    )
    weights = option_weights(player, trail_allowed, movement_ctx)
    draw = random.random() * sum(weights.values())
    option = MAKE_A_TRAIL if trail_allowed else SHUFFLE_TO_COVER
    for name in (STAY_HIDDEN, SHUFFLE_TO_COVER, MAKE_A_TRAIL):
        if draw < weights[name]:
            option = name
            break
        draw -= weights[name]

    if option == MAKE_A_TRAIL:
        player.downed_plan = _trail_plan(spot, tagger_cell, movement_ctx)
        return
    if option == SHUFFLE_TO_COVER:
        cover = _nearest_cover(player, spot, tick, movement_ctx)
        if cover is not None and cover != spot:
            player.downed_plan = DownedPlan(SHUFFLE_TO_COVER, spot, cover=cover)
            return
    player.downed_plan = DownedPlan(STAY_HIDDEN, spot)
    player.is_hiding = True


def choose_resupply_trail(player, tick: float, ctx: Optional[RoundContext]) -> None:
    """After a Resupply: Make a trail with ``RESUPPLY_TRAIL_CHANCE`` when no
    enemy is within ``TAGGER_MIN_FEET`` by walking. No roll otherwise."""
    # A combo Resupply resets the receiver twice on one tick: one roll only.
    if player.resupply_rolled_at == tick:
        return
    # The Resupply restarts the cooldown, which would bring a stale plan from
    # an earlier Down back to life: a Resupply starts from no plan.
    player.downed_plan = None
    movement_ctx = getattr(ctx, "movement_ctx", None)
    if ctx is None or movement_ctx is None or player.cell_row is None:
        return
    player.resupply_rolled_at = tick
    spot = _cell(player)
    enemies = [
        _cell(p)
        for p in ctx.all_alive
        if p.team_color != player.team_color
        and p.final_lives > 0
        and p.cell_row is not None
    ]
    if _within_tagger_range(spot, enemies, movement_ctx):
        return
    if random.random() >= RESUPPLY_TRAIL_CHANCE:
        return
    nearest = min(
        enemies,
        key=lambda c: (abs(c[0] - spot[0]) + abs(c[1] - spot[1]), c),
        default=None,
    )
    player.downed_plan = _trail_plan(spot, nearest, movement_ctx)
    player.is_hiding = False  # a pre-Resupply hide would freeze the trail


def downed_goal(player, tick: float, movement_ctx: MapContext) -> Optional[Cell]:
    """The goal cell while the Downed plan is live, else None.

    At Active the plan and the MOVE-04 goal commitment are cleared.
    """
    plan = getattr(player, "downed_plan", None)
    if plan is None:
        return None
    if player.is_active_at(tick):
        player.downed_plan = None
        player._committed_goal = None
        return None
    here = _cell(player)
    if plan.option == SHUFFLE_TO_COVER:
        watchers = _remembered_enemy_cells(player, tick)
        if any(movement_ctx.can_see(w, plan.cover) for w in watchers):
            cover = _nearest_cover(player, here, tick, movement_ctx)
            if cover is None:
                plan.option, plan.cover = STAY_HIDDEN, None
                player.is_hiding = True
                return here
            plan.cover = cover
        return plan.cover
    if plan.option == MAKE_A_TRAIL:
        if plan.outbound and here == plan.turn_cell:
            plan.outbound = False
        return plan.turn_cell if plan.outbound else plan.spot
    return here
