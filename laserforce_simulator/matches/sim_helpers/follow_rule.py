"""MECH-10: the Follow rule.

A Downed or Resupplied player leaves a **Trail**: the cells they walk
through until they are Active again (``last_downed_time +
RESPAWN_TICKS``), widened to every cell within
``TRAIL_HALF_WIDTH_FEET`` of walking (in the map's squares) of that path.
The walk uses the map's 4-connected movement adjacency, so the band never
passes through a wall.

An enemy who advances ``FOLLOW_LIMIT_FEET`` along the path while inside
the band has **Chased** that player and may not Tag them until
``FOLLOW_GRACE_TICKS`` after they are Active again. Moving through the
area is never blocked, and the Chaser may Tag anyone else. MOVE-08: the
distances are feet, turned into squares by ``movement_ctx.squares``.

Three entry points, each given the round's map (``MapContext``):

- ``start_trail(player, tick, movement_ctx)``: a Down (``record_down``) or a
  Resupply (``attempt_resupply``) at ``tick``. Call it BEFORE
  ``last_downed_time`` is stamped: a player who is not yet Active keeps
  their open Trail (it grows until the later Active tick, and Chases and
  bans on it carry over); otherwise a fresh Trail starts at their cell.
- ``record_step(player, step_cells, tick, all_alive, movement_ctx)``: after
  each Advance, with the cell the player started the tick on followed by
  the cells they passed through. Grows the mover's own open Trail and
  advances the mover's Chase progress on every enemy's open Trail.
- ``may_tag(attacker, target, tick)``: False while ``attacker`` holds a
  ban on ``target``'s current Trail.

State lives on the ``PlayerState`` (``_trail`` on the Downed player,
``_chases`` on the attacker), transient and never flushed. No RNG, no
Django imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NamedTuple, Optional

if TYPE_CHECKING:
    from .map_context import MapContext
    from .round_context import RoundContext

from .time_constants import RESPAWN_TICKS

#: Feet advanced along a Trail that make a Chase (2 squares on Syracuse).
FOLLOW_LIMIT_FEET: float = 10.0
#: Feet of walking on either side of the path the Trail covers (1 square on
#: Syracuse).
TRAIL_HALF_WIDTH_FEET: float = 6.0
#: Ticks after the Chased player is Active again that the ban still holds (2 s).
FOLLOW_GRACE_TICKS: int = 4

Cell = tuple[int, int]


class BandCell(NamedTuple):
    """A cell the Trail covers: its walk ``distance`` to the nearest path
    cell, and that path cell's ``path_index``."""

    distance: int
    path_index: int


@dataclass
class Trail:
    """One Trail: ``start_tick`` identifies it; ``band`` maps each covered
    cell to its ``BandCell``. Path cells are 4-adjacent, so a
    ``path_index`` difference is squares along the path."""

    start_tick: float
    path: list[Cell] = field(default_factory=list)
    band: dict[Cell, BandCell] = field(default_factory=dict)


@dataclass
class Chase:
    """An attacker's progress along one enemy's Trail."""

    trail_start: float
    progress: int = 0
    banned: bool = False
    banned_at: Optional[float] = None


# Ticks are typed ``float`` like the engine's ``second`` arguments, whose
# values are whole ticks in the tick domain (BatchSimulator).


def _open_until(player) -> float:
    """The tick the player is Active again; their Trail grows until then."""
    return player.last_downed_time + RESPAWN_TICKS


def _trail_is_open(player, tick: float) -> bool:
    return (
        player._trail is not None
        and player.last_downed_time is not None
        and tick < _open_until(player)
    )


def open_trail(player, tick: float) -> Optional[Trail]:
    """``player``'s Trail while it is open (until they are Active), else None."""
    return player._trail if _trail_is_open(player, tick) else None


def _widen(trail: Trail, index: int, movement_ctx: MapContext) -> None:
    """Add every cell within the half-width walk of ``trail.path[index]``.

    A cell already in the band keeps the nearer path cell (the first one
    on a tie).
    """
    adjacency = movement_ctx.get_adjacency()
    half_width = movement_ctx.squares(TRAIL_HALF_WIDTH_FEET)
    origin = trail.path[index]
    frontier = [origin]
    seen = {origin}
    distance = 0
    while frontier:
        for cell in frontier:
            known = trail.band.get(cell)
            if known is None or distance < known.distance:
                trail.band[cell] = BandCell(distance, index)
        if distance == half_width:
            break
        distance += 1
        nxt = []
        for cell in frontier:
            for neighbour in adjacency.get(cell, ()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    nxt.append(neighbour)
        frontier = nxt


def _extend_path(trail: Trail, cells: list[Cell], movement_ctx: MapContext) -> None:
    for cell in cells:
        if trail.path and trail.path[-1] == cell:
            continue
        trail.path.append(cell)
        _widen(trail, len(trail.path) - 1, movement_ctx)


def round_map(ctx: Optional[RoundContext]) -> Optional[MapContext]:
    """The round's ``MapContext`` from a ``RoundContext``, or None when there
    is no context or no map (the rule is then inert)."""
    if ctx is None:
        return None
    return ctx.movement_ctx


def start_trail(player, tick: float, movement_ctx: Optional[MapContext]) -> None:
    """A Down or a Resupply happened to ``player`` at ``tick``.

    Must run before ``last_downed_time`` is stamped. No map
    (``movement_ctx`` is None) or no cell: nothing to measure, the rule
    stays inert.
    """
    if movement_ctx is None or player.cell_row is None:
        return
    if _trail_is_open(player, tick):
        return  # not yet Active: the same Trail keeps growing
    trail = Trail(start_tick=tick)
    _extend_path(trail, [(player.cell_row, player.cell_col)], movement_ctx)
    player._trail = trail


def record_step(
    player,
    step_cells: list[Cell],
    tick: float,
    all_alive: list,
    movement_ctx: MapContext,
) -> None:
    """Record one Advance: ``step_cells`` is the start cell, then the cells
    passed through, in order."""
    if _trail_is_open(player, tick):
        _extend_path(player._trail, step_cells, movement_ctx)
    limit = movement_ctx.squares(FOLLOW_LIMIT_FEET)

    for enemy in all_alive:
        if enemy.team_color == player.team_color or not _trail_is_open(enemy, tick):
            continue
        trail = enemy._trail
        chase = player._chases.get(enemy.tag_id_key)
        if chase is None or chase.trail_start != trail.start_tick:
            chase = Chase(trail_start=trail.start_tick)
            player._chases[enemy.tag_id_key] = chase
        previous: Optional[int] = None
        for cell in step_cells:
            covered = trail.band.get(cell)
            if covered is None:
                previous = None  # distance outside the Trail never counts
                continue
            index = covered.path_index
            if previous is not None and index > previous:
                chase.progress += index - previous
            previous = index
        if chase.progress >= limit and not chase.banned:
            chase.banned = True
            chase.banned_at = tick


def chased_tag_ids(attacker, all_alive: list, tick: float) -> set[str]:
    """The ``tag_id_key`` of every player ``attacker`` may not Tag now (for
    goal selection, whose memory entries are keyed by tag id)."""
    return {p.tag_id_key for p in all_alive if not may_tag(attacker, p, tick)}


def may_tag(attacker, target, tick: float) -> bool:
    """False while ``attacker`` has Chased ``target`` on their current Trail.

    Duck-typed players without Follow rule state (``PlayerRoundState`` in
    the weight tests) are never barred.
    """
    chase = getattr(attacker, "_chases", {}).get(target.tag_id_key)
    if chase is None or not chase.banned:
        return True
    trail = getattr(target, "_trail", None)
    if trail is None or trail.start_tick != chase.trail_start:
        return True  # a fresh Trail ended the old ban
    return tick >= _open_until(target) + FOLLOW_GRACE_TICKS
