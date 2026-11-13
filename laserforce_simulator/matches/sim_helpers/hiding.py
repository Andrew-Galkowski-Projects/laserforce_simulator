"""MECH-23 (spec #606): the walking hide.

A hiding player walks up to ``HIDE_MAX_FEET`` to a cell no enemy it remembers can
see, preferring a cell few places can see (the lowest ``MapContext.los_rank``), and
holds it. When a remembered enemy can see it again, the next tick's ``hide_cell``
is another cell and it walks again. ``BatchSimulator._advance_player`` takes
``hide_cell`` as the hider's goal each tick, unless ``holds_in_place`` keeps it on
its cell. The same hide runs for the Action ``hide``, the Downed option Stay hidden
and the arming Commander.

Pure: no Django, no RNG. Ties break by a fixed key ending in ``(row, col)``.
"""

from __future__ import annotations

from typing import Any

from .downed_option import _remembered_enemy_cells
from .resupply_visit import sees
from .support_survival import SUPPORT_ROLES

#: The hide search's reach by walking, in feet (``MapContext.squares``).
HIDE_MAX_FEET: float = 15.0

Cell = tuple[int, int]


def hide_cell(player: Any, tick: float, movement_ctx: Any) -> Cell:
    """The cell *player* walks to or holds while hiding.

    The watchers are the remembered enemies' cells (stale entries left out, as
    Shuffle to cover reads them). With none, the player's own cell. Otherwise the
    cell within ``HIDE_MAX_FEET`` by walking (own cell included) seen by the fewest
    watchers; then the lowest ``los_rank``; then the nearer; then ``(row, col)``.
    """
    here: Cell = (player.cell_row, player.cell_col)
    watchers = _remembered_enemy_cells(player, tick)
    if not watchers:
        return here
    distances = movement_ctx.walk_distances(here, movement_ctx.squares(HIDE_MAX_FEET))

    def key(cell: Cell) -> tuple[int, float, int, Cell]:
        seen = sum(1 for w in watchers if movement_ctx.can_see(w, cell))
        return (seen, movement_ctx.los_rank(cell), distances[cell], cell)

    return min(distances, key=key)


def holds_in_place(player: Any, all_alive: list, movement_ctx: Any) -> bool:
    """True when a hider stays on its cell without searching: it is a visitor
    waiting for or at its supports (CAL-04's visit ``wait`` / ``at``), or it is a
    Medic or Ammo that a teammate's arrived (``at``) visit sees."""
    visit = getattr(player, "visit", None)
    if visit is not None and visit.phase in ("wait", "at"):
        return True
    if player.role not in SUPPORT_ROLES:
        return False
    return any(
        p.team_color == player.team_color
        and getattr(p, "visit", None) is not None
        and p.visit.phase == "at"
        and sees(p, player, movement_ctx)
        for p in all_alive
    )
