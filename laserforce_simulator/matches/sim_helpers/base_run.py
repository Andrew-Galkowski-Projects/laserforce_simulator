"""MECH-26 (#623): the Base run by the supports.

A Medic or Ammo leaves its place to **Capture** every Base it has not taken,
the farther first and the nearer on the way back, crossing out of the
remembered enemies' sight as a Board flip does. When its Commander fires a
nuke, one roll per team (``start_on_nuke_fire``); the runner waits out the
Fuse window at a staging cell and steps out at detonation. After the last
capture the Medic may stay over there as a Board flip, or the runner walks
back to where it last saw its Heavy.

Pure and Django-free. Every roll draws from the module-level ``random`` the
engine seeds per round. Every ending goes through ``end_base_run``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Literal, get_args

from . import board_flip
from .downed_option import remembered_enemy_cells
from .map_context import _NEUTRAL_BASE_TYPES
from .modes import BASE_CAPTURE_SHOTS
from .resupply_visit import chebyshev, manhattan
from .support_survival import own_teammate
from .time_constants import RESPAWN_TICKS, TICK_SECONDS

Cell = tuple[int, int]

NUKE_RUN_CHANCE = 0.39
BOTH_RUN_CHANCE = 0.8
STREAK_RUN_CHANCE = 0.07
RUN_STAY_CHANCE = 0.5
RUN_LEG_ARRIVE_TICKS = 60
RETURN_ARRIVE_TICKS = 120
RETURN_NEAR_SQUARES = 3
RUN_CROSS_FEET = 15.0
NUKE_WINDOW_TICKS = RESPAWN_TICKS
PAIR_SQUARES = 4
PRE_DRAW_MIN_TICKS = 4
PRE_DRAW_MAX_TICKS = 8
DRAW_AMMO_TO_MEDIC = 0.6
DRAW_MUTUAL = 0.2  # the remaining 0.2 of the draw is nothing

BaseRunEndReason = Literal[
    "down",
    "eliminated",
    "not_arrived",
    "nuke_cancelled",
    "partner_down",
    "stayed",
    "returned",
]
BASE_RUN_END_REASONS: tuple[str, ...] = get_args(BaseRunEndReason)
#: MECH-26c: what a pair's Resupply draw did (``_pair_draw``).
PairDrawOutcome = Literal["ammo_to_medic", "mutual", "nothing", "skipped"]
PAIR_DRAW_OUTCOMES: tuple[str, ...] = get_args(PairDrawOutcome)


@dataclass
class BaseRun:
    """One Base run. ``bases`` is the planned order, ``leg`` the index of the
    current one; ``taken`` the bases captured on the run."""

    trigger: Literal["nuke", "streak"]
    bases: list[str]
    started: int
    stays: bool = False
    leg: int = 0
    leg_started: int = 0
    taken: list[str] = field(default_factory=list)
    returning_to: Cell | None = None
    return_started: int | None = None
    nuker: Any = None
    detonation: int | None = None
    cancelled: bool = False
    partner: Any = None
    follow: Any = None
    pre_draw_at: int | None = None
    return_draw: bool = False
    staging: Cell | None = None
    staged_at: int | None = None
    to_target: dict[Cell, int] | None = None
    #: The current leg's nearest range cell, chosen once per leg (None: not yet).
    leg_target: Cell | None = None
    #: The log the start row went to; the end row goes there too.
    events: Any = None
    #: MECH-26b, on the paired Ammo: the Medic's run it follows.
    lead: BaseRun | None = None
    #: MECH-26b, on the paired Ammo: the tick it found the Medic (None: not yet).
    found_at: int | None = None
    #: The reason ``end_base_run`` gave (None while the run lives); the
    #: follower reads its leader's.
    ended: BaseRunEndReason | None = None


def squares_per_tick(player: Any, ctx: Any) -> float:
    """*player*'s top speed in this map's squares a tick."""
    from .pathfinding import feet_per_second  # avoids a cycle

    return feet_per_second(player) * TICK_SECONDS / ctx.feet_per_square


def _range_cells(ctx: Any, base: str) -> list[Cell]:
    """The cells in range of *base*, sorted (a frozenset's order is not fixed)."""
    cells = []
    for key in ctx.base_sight_data.get(base, ()):
        row, col = key.split(",")
        cells.append((int(row), int(col)))
    return sorted(cells)


def _nearest_range_cell(
    ctx: Any, base: str, walk: dict[Cell, int]
) -> tuple[int, Cell] | None:
    """(walk, cell) of *base*'s range cell nearest by *walk*; None unreachable."""
    return min(
        ((walk[cell], cell) for cell in _range_cells(ctx, base) if cell in walk),
        default=None,
    )


def _opposing(player: Any) -> str:
    return "blue" if player.team_color == "red" else "red"


def _destroyed(player: Any, base: str) -> bool:
    """*player* has destroyed *base* (one flag covers every neutral)."""
    if base.startswith("neutral"):
        return bool(player.neutral_base_destroyed)
    return bool(player.opposing_base_destroyed)


def run_order(
    player: Any, ctx: Any, window_ticks: int | None, start: Cell | None = None
) -> list[str]:
    """The Bases *player* can still take, farther first by walk from *start*
    (default its cell) to the nearest range cell: the first neutral with a
    range, then the opposing base. With *window_ticks* and two bases, the
    nearer goes first when the farther range is out of reach in the window. A
    Medic plans one base per ``BASE_CAPTURE_SHOTS`` Shots (at least one), and
    none under that. Empty unplaced or with no ``base_sight_data``."""
    if player.cell_row is None or not ctx.base_sight_data:
        return []
    if player.role == "medic" and player.final_shots < BASE_CAPTURE_SHOTS:
        return []
    candidates = []
    if not player.neutral_base_destroyed:
        candidates += [n for n in _NEUTRAL_BASE_TYPES if ctx.base_sight_data.get(n)][:1]
    if not player.opposing_base_destroyed:
        candidates.append(_opposing(player))
    walk = ctx.walk_distances(start or (player.cell_row, player.cell_col))
    scored = []
    for base in candidates:
        near = _nearest_range_cell(ctx, base, walk)
        if near is not None:
            scored.append((near[0], base))
    scored.sort(reverse=True)
    if window_ticks is not None and len(scored) == 2:
        if scored[0][0] > window_ticks * squares_per_tick(player, ctx):
            scored.reverse()
    order = [base for _, base in scored]
    if player.role == "medic":
        order = order[: max(1, player.final_shots // BASE_CAPTURE_SHOTS)]
    return order


def is_running(player: Any) -> bool:
    """*player* has a live Base run."""
    return getattr(player, "base_run", None) is not None


def _here(player: Any) -> Cell:
    return (player.cell_row, player.cell_col)


def _begin(player: Any, run: BaseRun, tick: int, events: Any) -> None:
    """Start *run*: the runner leaves its hide, Hold and committed goal."""
    run.events = events
    player.base_run = run
    player.is_hiding = False
    player.is_holding = False
    player.hold_until = None
    player._committed_goal = None
    events.base_run(player, tick, "start", run.trigger, run.bases)


def start_on_nuke_fire(
    nuker: Any, teammates: list, tick: int, detonation: int, ctx: Any
) -> None:
    """When *nuker* fires a nuke (the tick loop's dispatch, where the nuke is
    scheduled): one roll for its team. *ctx* is the ``RoundContext`` (its
    ``movement_ctx`` is the map, its ``events`` the log).

    The eligible supports are the team's Medic and Ammo alive, placed, with no
    run, no started flip and a Base to take (``run_order``); none means no
    draw. The roll is ``NUKE_RUN_CHANCE`` x their mean ``game_awareness / 50``.
    With both eligible, ``BOTH_RUN_CHANCE`` sends both, else one goes, the
    Medic or the Ammo 50/50; then one ``stays`` roll shared by the run(s). The
    leader is the Medic when it goes, else the lone Ammo, which never stays.
    A pair's Ammo gets a follower run (``follow``, ``lead``), and the Medic's
    run its ``partner`` and ``pre_draw_at``, ``PRE_DRAW_MIN_TICKS`` to
    ``PRE_DRAW_MAX_TICKS`` before detonation (the next tick at the earliest)."""
    mc = ctx.movement_ctx
    if mc is None:
        return
    window = detonation - tick + NUKE_WINDOW_TICKS
    eligible = [
        p
        for p in teammates
        if p.role in ("medic", "ammo")
        and p.final_lives > 0
        and p.base_run is None
        and not board_flip.is_flipping(p)
        and run_order(p, mc, window)  # empty for an unplaced player
    ]
    if not eligible:
        return
    awareness = sum(p.game_awareness / 50 for p in eligible) / len(eligible)
    if random.random() >= NUKE_RUN_CHANCE * awareness:
        return
    eligible.sort(key=lambda p: p.role != "medic")  # the Medic first
    if len(eligible) == 2 and random.random() >= BOTH_RUN_CHANCE:
        eligible = [eligible[0] if random.random() < 0.5 else eligible[1]]
    stays = random.random() < RUN_STAY_CHANCE
    leader = eligible[0]
    run = BaseRun(
        trigger="nuke",
        bases=[],
        started=tick,
        stays=stays and leader.role == "medic",
        leg_started=detonation,
        nuker=nuker,
        detonation=detonation,
    )
    _plan_staging(leader, run, tick, window, mc)
    _begin(leader, run, tick, ctx.events)
    if len(eligible) == 2:
        _start_follower(eligible[1], leader, run, tick, ctx.events)


def _start_follower(
    ammo: Any, medic: Any, lead: BaseRun, tick: int, events: Any
) -> None:
    """A pair's Ammo: a follower run on the Medic's *lead*, which gets its
    ``partner`` and its ``pre_draw_at``, ``PRE_DRAW_MIN_TICKS`` to
    ``PRE_DRAW_MAX_TICKS`` before detonation (the next tick at the earliest)."""
    assert lead.detonation is not None
    follower = BaseRun(
        trigger="nuke",
        bases=[],
        started=tick,
        stays=lead.stays,  # the shared roll (the leader of a pair is the Medic)
        leg_started=lead.detonation,
        nuker=lead.nuker,
        detonation=lead.detonation,
        follow=medic,
        lead=lead,
    )
    lead.partner = ammo
    pre = random.randint(PRE_DRAW_MIN_TICKS, PRE_DRAW_MAX_TICKS)
    lead.pre_draw_at = max(tick + 1, lead.detonation - pre)
    _begin(ammo, follower, tick, events)


def streak_roll(player: Any, tick: int, ctx: Any, events: Any) -> bool:
    """MECH-26c: called by ``board_flip._start_flip`` before the flip cell is
    chosen (*ctx* the map, *events* the log for the start row). True means no
    flip: a live run blocks it (the pending flip clears, no draw); a draw
    under ``STREAK_RUN_CHANCE`` with a Base to take (``run_order``, no window)
    starts a ``"streak"`` run, with its ``stays`` roll, and clears the flip
    and the streak. A streak run has no window, no pair and no staging; the
    Ammo comes on its leash. False: the flip goes on."""
    if is_running(player):
        player.flip = None
        return True
    if random.random() >= STREAK_RUN_CHANCE:
        return False
    order = run_order(player, ctx, None)
    if not order:
        return False
    run = BaseRun(
        trigger="streak",
        bases=order,
        started=tick,
        stays=random.random() < RUN_STAY_CHANCE,
        leg_started=tick,
    )
    player.flip = None
    player.tag_streak = []
    _begin(player, run, tick, events)
    return True


def _staging_cell(player: Any, base: str, tick: int, reach: int, mc: Any) -> Cell:
    """The cell within *reach* walk seen by no remembered enemy with the least
    walk to *base*'s nearest range cell; the player's own cell with none."""
    here = _here(player)
    near = _nearest_range_cell(mc, base, mc.walk_distances(here))
    if near is None:
        return here
    to_range = mc.walk_distances(near[1])
    watchers = remembered_enemy_cells(player, tick)
    best = min(
        (
            (to_range[cell], walk, cell)
            for cell, walk in mc.walk_distances(here, reach).items()
            if cell in to_range and not any(mc.can_see(w, cell) for w in watchers)
        ),
        default=None,
    )
    return best[-1] if best is not None else here


def _plan_staging(player: Any, run: BaseRun, tick: int, window: int, mc: Any) -> None:
    """The staging cell for the first leg, then the order measured from it (a
    new first leg gets its own staging cell)."""
    assert run.detonation is not None
    reach = math.ceil((run.detonation - tick) * squares_per_tick(player, mc))
    order = run_order(player, mc, window)
    staging = _staging_cell(player, order[0], tick, reach, mc)
    from_staging = run_order(player, mc, window, start=staging) or order
    if from_staging[0] != order[0]:
        staging = _staging_cell(player, from_staging[0], tick, reach, mc)
    run.bases, run.staging = from_staging, staging


def tick_base_run(
    player: Any, tick: int, ctx: Any, all_alive: list, events: Any
) -> None:
    """Once a tick in ``_advance_player`` for a placed player with a run,
    Downed or not, from the tick after the start: a nuke cancelled in the fuse
    starts the return; captured bases move the legs on; a leg longer than
    ``RUN_LEG_ARRIVE_TICKS`` ends ``not_arrived``; after the last leg the
    Medic stays as a Board flip or the runner returns (``_tick_return``)."""
    run = player.base_run
    if run is None or player.cell_row is None or tick <= run.started:
        return
    if run.lead is not None:
        _tick_follower(player, run, tick, ctx, all_alive)
        return
    if run.returning_to is not None:
        _tick_return(player, run, tick)
        return
    if run.detonation is not None and not run.cancelled and tick <= run.detonation:
        if run.staged_at is None and _here(player) == run.staging:
            run.staged_at = tick
        nuker = run.nuker
        if nuker.final_lives <= 0 or nuker.special_active_until < run.detonation:
            run.cancelled = True
            _start_return(player, run, tick, all_alive)
            return
    _move_legs(player, run, tick)
    if run.leg < len(run.bases):
        if tick - run.leg_started > RUN_LEG_ARRIVE_TICKS:
            end_base_run(player, tick, "not_arrived")
        return
    if not _stay(player, run, tick, ctx, events):
        run.return_draw = True
        _start_return(player, run, tick, all_alive)


def _sees_partner(player: Any, other: Any, ctx: Any) -> bool:
    """*player* is within ``PAIR_SQUARES`` (Chebyshev) of *other* and sees it."""
    if player.cell_row is None or other.cell_row is None:
        return False
    here, there = _here(player), _here(other)
    return chebyshev(here, there) <= PAIR_SQUARES and bool(ctx.can_see(here, there))


def _tick_follower(
    player: Any, run: BaseRun, tick: int, ctx: Any, all_alive: list
) -> None:
    """The paired Ammo: the Medic's run ended by a Down or an Elimination ends
    it ``partner_down``, by a stay ``stayed``; the Medic's return (or another
    ending) starts its own return, cancelled with the Medic's. Outbound it has
    no legs; it has found the Medic once within reach and in sight."""
    lead = run.lead
    assert lead is not None
    if lead.ended in ("down", "eliminated"):
        end_base_run(player, tick, "partner_down")
        return
    if lead.ended == "stayed":
        end_base_run(player, tick, "stayed")
        return
    if run.returning_to is not None:
        _tick_return(player, run, tick)
        return
    if lead.returning_to is not None or lead.ended is not None:
        run.cancelled = lead.cancelled
        _start_return(player, run, tick, all_alive)
        return
    if run.found_at is None and _sees_partner(player, run.follow, ctx):
        run.found_at = tick


def _move_legs(player: Any, run: BaseRun, tick: int) -> None:
    """Every planned base now destroyed is taken (mid-route too); the leg is
    the first one not taken, and a new leg restarts its clock (never before
    detonation: a capture in the fuse leaves the next leg to start there)."""
    for base in run.bases:
        if base not in run.taken and _destroyed(player, base):
            run.taken.append(base)
    leg = next((i for i, b in enumerate(run.bases) if b not in run.taken), None)
    leg = len(run.bases) if leg is None else leg
    if leg != run.leg:
        start = tick if run.detonation is None else max(tick, run.detonation)
        run.leg, run.leg_started, run.leg_target = leg, start, None


def _stay(player: Any, run: BaseRun, tick: int, ctx: Any, events: Any) -> bool:
    """A stays Medic with flips left, Active, with a flip cell from here hands
    over to a started Board flip (as ``board_flip._start_flip`` does); the run
    ends ``stayed``. False when it does not stay."""
    if not (
        run.stays
        and player.role == "medic"
        and player.flips_started < board_flip.MAX_FLIPS_PER_ROUND
        and player.is_active_at(tick)
    ):
        return False
    here = _here(player)
    target = board_flip.flip_cell(player, tick, ctx, here)
    if target is None:
        return False
    end_base_run(player, tick, "stayed")
    player.flip = board_flip.Flip(
        from_cell=here,
        target=target,
        started=tick,
        to_target=ctx.walk_distances(target),
    )
    player.flips_started += 1
    player.tag_streak = []
    events.board_flip(player, tick, target)
    return True


def _start_return(player: Any, run: BaseRun, tick: int, all_alive: list) -> None:
    """Walk back to the cell *player* remembers for its Heavy, however stale,
    eliminated or not (an eliminated Heavy is read by its engine tag id,
    ``<team>_heavy``); with none the run ends at once."""
    heavy = own_teammate(player, "heavy", all_alive)
    key = heavy.tag_id_key if heavy is not None else f"{player.team_color}_heavy"
    entry = player.player_memory.get(key)
    if not entry or entry.get("cell") is None:
        end_base_run(player, tick, _cancelled_or(run, "returned"))
        return
    run.returning_to = (int(entry["cell"][0]), int(entry["cell"][1]))
    run.return_started = tick


def _tick_return(player: Any, run: BaseRun, tick: int) -> None:
    """Back within ``RETURN_NEAR_SQUARES`` ends ``returned``; more than
    ``RETURN_ARRIVE_TICKS`` ends ``not_arrived``; a cancelled nuke's run ends
    ``nuke_cancelled`` either way."""
    assert run.returning_to is not None and run.return_started is not None
    if manhattan(_here(player), run.returning_to) <= RETURN_NEAR_SQUARES:
        end_base_run(player, tick, _cancelled_or(run, "returned"))
    elif tick - run.return_started > RETURN_ARRIVE_TICKS:
        end_base_run(player, tick, _cancelled_or(run, "not_arrived"))


def _cancelled_or(run: BaseRun, reason: BaseRunEndReason) -> BaseRunEndReason:
    """A cancelled nuke's run ends ``nuke_cancelled`` whatever ends it."""
    return "nuke_cancelled" if run.cancelled else reason


def _in_fuse(run: BaseRun, tick: int) -> bool:
    """Before detonation, with a staging cell to wait at."""
    return bool(run.detonation is not None and tick < run.detonation and run.staging)


def _target(player: Any, run: BaseRun, ctx: Any, tick: int) -> Cell | None:
    """The leader's target before the crossing: the return cell, the staging
    cell in the fuse, else the current leg's nearest range cell (chosen once
    per leg); None after the last leg."""
    if run.returning_to is not None:
        return run.returning_to
    if _in_fuse(run, tick):
        return run.staging
    if run.leg >= len(run.bases):
        return None
    if run.leg_target is None:
        walk = ctx.walk_distances(_here(player))
        near = _nearest_range_cell(ctx, run.bases[run.leg], walk)
        run.leg_target = near[1] if near is not None else None
    return run.leg_target


def _follower_goal(player: Any, run: BaseRun, ctx: Any, tick: int) -> Cell:
    """The paired Ammo's goal: the Medic's cell until found; then, of the
    cells within ``PAIR_SQUARES`` (Chebyshev) of the Medic that see it, the
    one nearest by walk the Medic's target; the Medic's cell with none."""
    medic = run.follow
    there = _here(medic)
    if run.found_at is None:
        return there
    assert run.lead is not None
    target = _target(medic, run.lead, ctx, tick)
    if target is None:
        return there
    if run.to_target is None or run.to_target.get(target) != 0:
        run.to_target = ctx.walk_distances(target)  # cached per target
    to = run.to_target
    best = min(
        (
            (to[cell], cell)
            for cell in _box(there, PAIR_SQUARES)
            if cell in to and ctx.can_see(cell, there)
        ),
        default=None,
    )
    return best[1] if best is not None else there


def _box(centre: Cell, radius: int) -> list[Cell]:
    """The cells within *radius* (Chebyshev) of *centre*, in order."""
    row, col = centre
    return [
        (r, c)
        for r in range(row - radius, row + radius + 1)
        for c in range(col - radius, col + radius + 1)
    ]


def base_run_goal(player: Any, ctx: Any, tick: int) -> Cell | None:
    """The run's goal: the staging cell in the fuse, then the current leg's
    nearest range cell, then the return cell. Crossing with remembered
    enemies: among the cells within ``RUN_CROSS_FEET`` walk no remembered
    enemy sees, the least of (walk to the target, walk from here, cell); the
    target itself with none, or when the best is the runner's own cell."""
    run = player.base_run
    if run is None:
        return None
    if run.lead is not None and run.returning_to is None:
        return _follower_goal(player, run, ctx, tick)
    if run.returning_to is None and _in_fuse(run, tick):
        return run.staging
    target = _target(player, run, ctx, tick)
    if target is None:
        return None
    watchers = remembered_enemy_cells(player, tick)
    if not watchers:
        return target
    if run.to_target is None or run.to_target.get(target) != 0:
        run.to_target = ctx.walk_distances(target)  # cached per target
    to = run.to_target
    here = _here(player)
    near_cells = ctx.walk_distances(here, ctx.squares(RUN_CROSS_FEET))
    best = min(
        (
            (to[cell], walk, cell)
            for cell, walk in near_cells.items()
            if cell in to and not any(ctx.can_see(w, cell) for w in watchers)
        ),
        default=None,
    )
    if best is None or best[-1] == here:
        return target
    return best[-1]


def tick_draws(tick: int, players: list, rctx: Any) -> None:
    """Once a tick after the Advance: each pair's Resupply draws. A Medic whose
    run has a ``partner`` draws at ``pre_draw_at`` (the pre-run draw) and,
    with ``return_draw`` set, once on the way back. Each draw is spent whether
    it is made or skipped out of reach (``_pair_draw``)."""
    for medic in players:
        run = medic.base_run
        if run is None or run.partner is None:
            continue
        if run.pre_draw_at is not None and tick >= run.pre_draw_at:
            run.pre_draw_at = None
            _pair_draw(medic, run.partner, tick, rctx)
        elif run.return_draw:
            run.return_draw = False
            _pair_draw(medic, run.partner, tick, rctx)


def _pair_draw(medic: Any, ammo: Any, tick: int, rctx: Any) -> PairDrawOutcome:
    """In reach (both Active and placed, the Ammo within ``PAIR_SQUARES`` of
    the Medic and seeing it), one draw: the Ammo tags the Medic under
    ``DRAW_AMMO_TO_MEDIC``; a Mutual resupply under ``+ DRAW_MUTUAL``, both
    halves this tick (the Medic's reset is lifted around its own Resupply so
    it lands, then put back); else nothing. Out of reach, no draw. Returns the
    outcome (MECH-26c: ``round_probe`` reads it)."""
    mc = rctx.movement_ctx
    if mc is None or not (medic.is_active_at(tick) and ammo.is_active_at(tick)):
        return "skipped"
    if not _sees_partner(ammo, medic, mc):
        return "skipped"
    draw = random.random()
    if draw < DRAW_AMMO_TO_MEDIC:
        _give(ammo, medic, tick, rctx)
        return "ammo_to_medic"
    if draw < DRAW_AMMO_TO_MEDIC + DRAW_MUTUAL:
        before = medic.last_downed_time
        _give(ammo, medic, tick, rctx)
        reset = medic.last_downed_time
        medic.last_downed_time = before
        _give(medic, ammo, tick, rctx)
        medic.last_downed_time = reset
        return "mutual"
    return "nothing"


def _give(tagger: Any, receiver: Any, tick: int, rctx: Any) -> None:
    """*tagger* shoots its Resupply at *receiver* (``attempt_resupply``)."""
    from .combat import attempt_resupply  # combat imports this module

    attempt_resupply(tagger, receiver, tick, ctx=rctx)
    tagger.last_shot_time = tick


def end_base_run(player: Any, tick: int, reason: BaseRunEndReason) -> None:
    """End *player*'s run for *reason* (one of ``BASE_RUN_END_REASONS``) and
    write its end row. The single ending path; ``round_probe`` wraps it."""
    if reason not in BASE_RUN_END_REASONS:
        raise ValueError(f"unknown base run end reason {reason!r}")
    run = player.base_run
    player.base_run = None
    if run is not None:
        run.ended = reason  # a follower reads its leader's
    if run is not None and run.events is not None:
        run.events.base_run(
            player, tick, "end", run.trigger, run.bases, reason=reason, taken=run.taken
        )


def end_on_down(player: Any, tick: int) -> None:
    """At a Down (from ``record_down``, after ``board_flip.end_on_down``): an
    Elimination ends the run (``eliminated``), any other Down too (``down``),
    except a Down in the start tick of a run started while Downed."""
    run = player.base_run
    if run is None:
        return
    if player.final_lives <= 0:
        end_base_run(player, tick, "eliminated")
    elif not (tick <= run.started and not player.is_active_at(tick)):
        end_base_run(player, tick, "down")
