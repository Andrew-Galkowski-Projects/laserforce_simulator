"""MECH-25 (#617): the Board flip.

A **Tag streak** (three Tags in a row on the Medic while it is Active, each at
most ``TAG_STREAK_GAP_TICKS`` after the one before) makes a flip *pending*. It
starts on the Medic's next Active tick (``tick_flip``): the flip cell is chosen
once, far from the remembered enemies and out of their sight, and the Medic
crosses to it keeping out of their sight where it can (``flip_goal``), stays
``FLIP_STAY_TICKS``, and the normal rules take it back. Its Ammo comes along on
the existing Ammo leash. At most ``MAX_FLIPS_PER_ROUND`` flips per Round.

MECH-26c: a streak may start a Base run instead (``base_run.streak_roll``, its
one draw, at the flip start). Pure and Django-free; this module itself draws no
RNG. Every ending goes through ``end_flip``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, get_args

from .downed_option import remembered_enemy_cells
from .map_context import zone_from_cell
from .resupply_visit import manhattan

Cell = tuple[int, int]

TAG_STREAK_LENGTH = 3
TAG_STREAK_GAP_TICKS = 60
MAX_FLIPS_PER_ROUND = 3
FLIP_MIN_SQUARES = 40
FLIP_ARRIVE_TICKS = 60
FLIP_STAY_TICKS = 120
FLIP_CROSS_FEET = 15.0

FlipEndReason = Literal["down", "eliminated", "not_arrived", "stayed"]
FLIP_END_REASONS: tuple[str, ...] = get_args(FlipEndReason)


@dataclass
class Flip:
    """One Board flip. ``from_cell`` is the Medic's cell at the streak's third
    Tag; ``target`` is None while the flip is pending. ``to_target`` holds the
    walk distances from the target, computed once at the start."""

    from_cell: Cell
    target: Cell | None = None
    started: int | None = None
    arrived: int | None = None
    to_target: dict[Cell, int] | None = None


def note_tag(defender: Any, tick: int) -> None:
    """The Tag streak: called at each Tag, before ``record_down``.

    Counts only a Tag on the Medic while it is Active, with no flip pending and
    fewer than ``MAX_FLIPS_PER_ROUND`` flips started. Hiding or not, pinned by a
    visit or not, does not matter. The third Tag in a row makes a flip pending;
    the streak is kept until the flip starts, so a flip with no cell retries
    on the next Tag in the window (``tick_flip``).
    """
    if defender.role != "medic" or not defender.is_active_at(tick):
        return
    if defender.flip is not None and defender.flip.target is None:
        return
    if defender.flips_started >= MAX_FLIPS_PER_ROUND:
        return
    streak = defender.tag_streak
    if not streak or tick - streak[-1] > TAG_STREAK_GAP_TICKS:
        streak = []
    defender.tag_streak = [*streak, tick]
    # A started flip ends only through ``end_flip``; the streak waits for it.
    if len(defender.tag_streak) >= TAG_STREAK_LENGTH and defender.flip is None:
        defender.flip = Flip(from_cell=(defender.cell_row, defender.cell_col))


def end_flip(player: Any, tick: int, reason: FlipEndReason) -> None:
    """End *player*'s flip for *reason* (one of ``FLIP_END_REASONS``)."""
    if reason not in FLIP_END_REASONS:
        raise ValueError(f"unknown board flip end reason {reason!r}")
    player.flip = None


def end_on_down(player: Any, tick: int) -> None:
    """At a Down (from ``record_down``): an Elimination ends any flip, pending
    or started; another Down ends a started flip and leaves a pending one."""
    flip = player.flip
    if flip is None:
        return
    if player.final_lives <= 0:
        end_flip(player, tick, "eliminated")
    elif flip.target is not None:
        end_flip(player, tick, "down")


def flip_cell(player: Any, tick: int, ctx: Any, from_cell: Cell) -> Cell | None:
    """The flip cell: at least ``FLIP_MIN_SQUARES`` walk from the Medic, seen
    by no watcher, not in the enemy base's zone. The least of (farthest from
    the nearest watcher, ``los_rank``, walk, cell). Watchers are the remembered
    enemies, or *from_cell* with none. None when no cell qualifies."""
    watchers = remembered_enemy_cells(player, tick) or [from_cell]
    enemy_zone = 2 if player.team_color == "red" else 0
    spawn = ctx.get_spawn_cells()
    distances = ctx.walk_distances((player.cell_row, player.cell_col))
    candidates = [
        (-min(manhattan(cell, w) for w in watchers), ctx.los_rank(cell), walk, cell)
        for cell, walk in distances.items()
        if walk >= FLIP_MIN_SQUARES
        and zone_from_cell(cell[0], cell[1], spawn) != enemy_zone
        and not any(ctx.can_see(w, cell) for w in watchers)
    ]
    return min(candidates)[-1] if candidates else None


def tick_flip(player: Any, tick: int, ctx: Any, events: Any) -> None:
    """Once per tick for a player with a flip, while it is Active and placed:
    start a pending flip (``_start_flip``), mark the arrival, or end it
    (``not_arrived``, ``stayed``)."""
    flip = player.flip
    if flip is None or player.cell_row is None or not player.is_active_at(tick):
        return
    if flip.target is None:
        _start_flip(player, flip, tick, ctx, events)
        return
    if flip.arrived is None:
        if (player.cell_row, player.cell_col) == flip.target:
            flip.arrived = tick
        elif tick - flip.started > FLIP_ARRIVE_TICKS:
            end_flip(player, tick, "not_arrived")
        return
    if tick - flip.arrived > FLIP_STAY_TICKS:
        end_flip(player, tick, "stayed")


def _start_flip(player: Any, flip: Flip, tick: int, ctx: Any, events: Any) -> None:
    """Start a pending flip: the cell is chosen once and a ``board_flip`` row
    written. With no cell there is no flip, and the streak stays for a retry.
    MECH-26: ``base_run.streak_roll`` first; a live or new Base run means no
    flip."""
    from . import base_run  # base_run imports this module

    if base_run.streak_roll(player, tick, ctx, events):
        return
    target = flip_cell(player, tick, ctx, flip.from_cell)
    if target is None:
        player.flip = None  # no flip; the streak stays for a retry
        return
    flip.target, flip.started = target, tick
    flip.to_target = ctx.walk_distances(target)
    player.flips_started += 1
    player.tag_streak = []
    events.board_flip(player, tick, target)


def is_flipping(player: Any) -> bool:
    """A flip has started (pending flips change nothing)."""
    flip = player.flip
    return flip is not None and flip.target is not None


def crossing(player: Any) -> bool:
    """A started flip whose target is not reached yet."""
    return is_flipping(player) and player.flip.arrived is None


def flip_goal(player: Any, tick: int, ctx: Any) -> Cell:
    """The goal of a started flip. Crossing with remembered enemies: among the
    cells within ``FLIP_CROSS_FEET`` walk that no remembered enemy sees, the
    least of (walk to the target, walk from here, cell). The target itself
    once arrived, with no remembered enemy, or when no cell is better."""
    flip = player.flip
    target: Cell = flip.target
    watchers = remembered_enemy_cells(player, tick)
    if flip.arrived is not None or not watchers or not flip.to_target:
        return target
    here: Cell = (player.cell_row, player.cell_col)
    near = ctx.walk_distances(here, ctx.squares(FLIP_CROSS_FEET))
    to = flip.to_target
    best = min(
        (
            (to[cell], walk, cell)
            for cell, walk in near.items()
            if cell in to and not any(ctx.can_see(w, cell) for w in watchers)
        ),
        default=None,
    )
    if best is None or best[-1] == here:
        return target
    return best[-1]
