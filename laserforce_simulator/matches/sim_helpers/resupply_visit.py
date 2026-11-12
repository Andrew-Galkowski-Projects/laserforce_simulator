"""CAL-03 rules 6, 7 and 10 (#588): Resupply visits, call-outs and the backline.

A Commander, Heavy or Scout low on lives or shots starts a **Resupply visit**: it
walks back to its supports, waits briefly for both, takes its Resupplies (doubles
where it can) and goes back out (``visit_step``, read by ``combat.plan_action`` and
``pathfinding.choose_goal_cell``). A player at ``CALLOUT_LIVES`` or fewer that no
own support sees hides and calls a support over (``call_out``, ``freshest_callout``).
A Commander or Scout keeps its last known backline position (``update_backline``).

Map rounds only, and no rule here draws RNG. Pure Python; ``resupply_queue`` is
imported when called (it imports ``combat``, which imports this module).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable, Literal, Optional

from . import support_survival
from .support_survival import SUPPORT_ROLES, Callout, Visit

Cell = tuple[int, int]
#: What a visit makes of this tick's Action roll (a Tag roll always stands):
#: ``hide``, ``move`` (``only_move``) or ``request`` (``request_resupply``).
VisitAction = Literal["hide", "move", "request"]
#: CAL-04a (#599): why a visit ended, passed to ``end_visit`` by every caller.
#: ``satisfied`` / ``timeout`` / ``max`` come from ``_over``, ``no_support`` from
#: ``visit_step``, ``eliminated`` from ``down.record_down`` and ``round_end`` from
#: ``end_open_visits``.
VisitEnd = Literal[
    "satisfied", "timeout", "max", "no_support", "eliminated", "round_end"
]

#: Rule 10: the backline is the Heavy, Ammo and Medic; the offense keeps its cell.
_BACKLINE_ROLES: tuple[str, ...] = ("heavy", "ammo", "medic")
_OFFENSE_ROLES: tuple[str, ...] = ("commander", "scout")


def _here(player: Any) -> Optional[Cell]:
    if player.cell_row is None:
        return None
    return (player.cell_row, player.cell_col)


def _own_supports(player: Any, all_alive: list) -> list:
    """*player*'s living Medic then Ammo (not *player* itself)."""
    supports = []
    for role in SUPPORT_ROLES:
        support = support_survival.own_teammate(player, role, all_alive)
        if support is not None:
            supports.append(support)
    return supports


def sees(player: Any, support: Any, movement_ctx: Any) -> bool:
    """True when *player* and *support* are in line of sight on the map."""
    from .resupply_queue import _is_in_los  # noqa: PLC0415 (resupply_queue -> here)

    return bool(_is_in_los(player, support, movement_ctx))


def is_critical(player: Any) -> bool:
    """CAL-04b: True when Lives or Shots are at ``VISIT_SEEK_SHARE`` of the cap or
    less (a Critical visit). No RNG."""
    seek = support_survival.VISIT_SEEK_SHARE
    return (
        player.final_lives <= seek * player.max_lives
        or player.final_shots <= seek * player.max_shots
    )


def manhattan(a: Cell, b: Cell) -> int:
    """The Manhattan distance between two cells."""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def chebyshev(a: Cell, b: Cell) -> int:
    """The Chebyshev (king-move) distance between two cells."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def nearest_cell(cells: Iterable[Cell], here: Cell) -> Optional[Cell]:
    """The cell nearest *here* (Manhattan, then the cell), or None."""
    return min(cells, key=lambda c: (manhattan(c, here), c), default=None)


def end_visit(player: Any, tick: float, reason: VisitEnd) -> None:
    """End *player*'s visit for *reason* and start both cooldowns, whatever the
    reason: no new visit before ``tick + VISIT_COOLDOWN_TICKS``, and no Critical
    visit (at ``VISIT_SEEK_SHARE``) before ``tick + CRITICAL_COOLDOWN_TICKS``
    (CAL-04b). The reason changes nothing here; ``round_probe`` reads it."""
    player.visit = None
    player.visit_next_tick = int(tick) + support_survival.VISIT_COOLDOWN_TICKS
    player.critical_visit_next_tick = (
        int(tick) + support_survival.CRITICAL_COOLDOWN_TICKS
    )


def end_open_visits(players: Iterable[Any], tick: float) -> None:
    """The round ended: end every visit still open (``round_end``)."""
    for player in players:
        if player.visit is not None:
            end_visit(player, tick, "round_end")


def _support_cell(player: Any, support: Any) -> Optional[Cell]:
    """Decision 28: the support's remembered cell, else its true cell."""
    entry = player.player_memory.get(support.tag_id_key)
    if entry is not None and entry.get("cell") is not None:
        return (int(entry["cell"][0]), int(entry["cell"][1]))
    return _here(support)


def visit_goal(player: Any, supports: list, movement_ctx: Any) -> Optional[Cell]:
    """The nearest cell that sees every support's cell (remembered, else true);
    else the nearest that sees the first support's; else that cell. No RNG."""
    cells = [c for c in (_support_cell(player, s) for s in supports) if c is not None]
    here = _here(player)
    if not cells or here is None:
        return None
    seen = [set(movement_ctx.cells_visible_from(c)) for c in cells]
    both = set.intersection(*seen)
    return nearest_cell(both, here) or nearest_cell(seen[0], here) or cells[0]


def _forget(player: Any, supports: Iterable[Any]) -> None:
    """Unstick (decision 54): forget these supports' remembered cells."""
    for support in supports:
        player.player_memory.pop(support.tag_id_key, None)


def _start(player: Any, tick: float) -> Optional[Visit]:
    """A new visit when lives or shots fall to ``visit_share`` of the cap outside
    the cooldown (``visit_next_tick``); a Critical visit, at ``VISIT_SEEK_SHARE``
    or less, needs only ``tick >= critical_visit_next_tick`` (CAL-04b). Else None."""
    critical = is_critical(player)
    next_tick = player.critical_visit_next_tick if critical else player.visit_next_tick
    if tick < next_tick:
        return None
    share = support_survival.visit_share(player)
    low = critical or (
        player.final_lives <= share * player.max_lives
        or player.final_shots <= share * player.max_shots
    )
    return Visit(phase="go", started=int(tick)) if low else None


def satisfied(player: Any, visit: Visit) -> bool:
    """CAL-04b: True when Lives and Shots are each at least ``SATISFIED_SHARE`` of
    the cap, or *visit* has had ``SATISFIED_DOUBLES`` doubles. No RNG."""
    share = support_survival.SATISFIED_SHARE
    shares = player.final_lives >= share * player.max_lives and (
        player.final_shots >= share * player.max_shots
    )
    return shares or visit.doubles >= support_survival.SATISFIED_DOUBLES


def _over(player: Any, visit: Visit, tick: float) -> Optional[VisitEnd]:
    """The end rules, in order: ``satisfied`` (CAL-04b), ``timeout`` (``VISIT_TIMEOUT_TICKS`` since
    arrival), ``max`` (``VISIT_MAX_TICKS`` since the start); else None."""
    if satisfied(player, visit):
        return "satisfied"
    if (
        visit.arrived is not None
        and tick - visit.arrived >= support_survival.VISIT_TIMEOUT_TICKS
    ):
        return "timeout"
    if tick - visit.started >= support_survival.VISIT_MAX_TICKS:
        return "max"
    return None


def _at_supports(
    player: Any,
    visit: Visit,
    supports: list,
    in_sight: list,
    movement_ctx: Any,
    tick: float,
) -> tuple[Visit, VisitAction]:
    """The ``at`` phase: some support sees the player. In every support's sight its
    goal is its own cell; else the cell that sees both (unstick on it: forget the
    unseen support and the ``backline_cell``). It requests while every support is
    Active and sees it, or once ``VISIT_WAIT_TICKS`` have passed since arrival and
    a seeing support is Active; otherwise it holds, hidden."""
    arrived = int(tick) if visit.arrived is None else visit.arrived
    here = _here(player)
    if all(in_sight):
        goal = here
    else:
        goal = visit_goal(player, supports, movement_ctx)
        if goal == here:
            _forget(player, (s for v, s in zip(in_sight, supports) if not v))
            player.backline_cell = None
            goal = visit_goal(player, supports, movement_ctx)
    visit = replace(visit, phase="at", arrived=arrived, goal=goal)
    ready = all(in_sight) and all(s.is_active_at(tick) for s in supports)
    late = tick - arrived >= support_survival.VISIT_WAIT_TICKS and any(
        v and s.is_active_at(tick) for v, s in zip(in_sight, supports)
    )
    if ready or late:
        return visit, "request"
    return visit, "hide"


def _walk(
    player: Any, visit: Visit, supports: list, movement_ctx: Any
) -> tuple[Visit, VisitAction]:
    """The ``go`` phase: to the ``backline_cell`` first (decision 54), then to a
    cell that sees both supports. On that cell with no support in sight it forgets
    their cells and its ``backline_cell`` and draws the goal again (unstick)."""
    here = _here(player)
    backline = player.backline_cell
    if backline is not None and not visit.backline_done:
        if backline != here:
            return replace(visit, phase="go", goal=backline), "move"
        visit = replace(visit, backline_done=True)
    goal = visit_goal(player, supports, movement_ctx)
    if goal == here:
        _forget(player, supports)
        player.backline_cell = None
        goal = visit_goal(player, supports, movement_ctx)
    return replace(visit, phase="go", goal=goal), "move"


def visit_step(
    player: Any, all_alive: list, movement_ctx: Any, tick: float
) -> Optional[VisitAction]:
    """Rule 6, once per plan of a Commander, Heavy or Scout on a map: start, step
    or end its Resupply visit (``player.visit``) and return what the visit makes of
    this tick's roll, or None (no visit). The visit ends when the player is
    ``satisfied``, ``VISIT_TIMEOUT_TICKS`` after arrival, ``VISIT_MAX_TICKS`` after the start, or
    with no living support left, passing ``end_visit`` the reason. No RNG."""
    if movement_ctx is None or player.role in SUPPORT_ROLES or _here(player) is None:
        return None
    supports = _own_supports(player, all_alive)
    visit = player.visit
    if not supports:
        if visit is not None:
            end_visit(player, tick, "no_support")
        return None
    if visit is None:
        if not player.is_active_at(tick):
            return None
        visit = _start(player, tick)
        if visit is None:
            return None
    reason = _over(player, visit, tick)
    if reason is not None:
        end_visit(player, tick, reason)
        return None
    in_sight = [sees(player, s, movement_ctx) for s in supports]
    action: VisitAction
    if any(in_sight):
        visit, action = _at_supports(
            player, visit, supports, in_sight, movement_ctx, tick
        )
    elif visit.arrived is None and not all(s.is_active_at(tick) for s in supports):
        # ``wait``: a living support is not Active yet, so hide.
        visit, action = replace(visit, phase="wait", goal=None), "hide"
    else:
        visit, action = _walk(player, visit, supports, movement_ctx)
    player.visit = visit
    return action


def update_backline(player: Any, all_alive: list, movement_ctx: Any) -> None:
    """Rule 10, once per plan of a Commander or Scout on a map: its
    ``backline_cell`` becomes its Medic's cell when it sees the Medic, else the
    cell of the seen backline teammate nearest the centroid of those it sees. Seeing
    none, it keeps the old one. No RNG."""
    here = _here(player)
    if player.role not in _OFFENSE_ROLES or movement_ctx is None or here is None:
        return
    seen = [
        p
        for p in all_alive
        if p.team_color == player.team_color
        and p.role in _BACKLINE_ROLES
        and p.final_lives > 0
        and p.cell_row is not None
        and movement_ctx.can_see(here, (p.cell_row, p.cell_col))
    ]
    if not seen:
        return
    medic = next((p for p in seen if p.role == "medic"), None)
    if medic is not None:
        player.backline_cell = (medic.cell_row, medic.cell_col)
        return
    row = sum(p.cell_row for p in seen) / len(seen)
    col = sum(p.cell_col for p in seen) / len(seen)
    pick = min(
        seen,
        key=lambda p: ((p.cell_row - row) ** 2 + (p.cell_col - col) ** 2, _here(p)),
    )
    player.backline_cell = (pick.cell_row, pick.cell_col)


def call_out(player: Any, all_alive: list, movement_ctx: Any, tick: float) -> bool:
    """Rule 7: True when *player* is Active on a map at ``CALLOUT_LIVES`` or fewer,
    has a living own support (not itself) and none sees it; then its cell goes to
    the front of each of those supports' ``support_callouts``. No RNG."""
    here = _here(player)
    if movement_ctx is None or here is None or not player.is_active_at(tick):
        return False
    if not 0 < player.final_lives <= support_survival.CALLOUT_LIVES:
        return False
    supports = _own_supports(player, all_alive)
    if not supports or any(sees(player, s, movement_ctx) for s in supports):
        return False
    call = Callout(caller=player.tag_id_key, cell=here, tick=int(tick))
    for support in supports:
        support.support_callouts = [
            call,
            *(c for c in support.support_callouts if c.caller != call.caller),
        ]
    return True


def freshest_callout(support: Any, all_alive: list, tick: float) -> Optional[Cell]:
    """Rule 7: the cell of *support*'s freshest call-out from a teammate still at 1
    to ``CALLOUT_LIVES`` lives, made at most ``CALLOUT_TICKS`` ago; stale ones are
    dropped. None when there is none."""
    if not support.support_callouts:
        return None
    lives = {
        p.tag_id_key: p.final_lives
        for p in all_alive
        if p.team_color == support.team_color
    }
    live = [
        c
        for c in support.support_callouts
        if 0 < lives.get(c.caller, 0) <= support_survival.CALLOUT_LIVES
        and tick - c.tick <= support_survival.CALLOUT_TICKS
    ]
    support.support_callouts = live
    return live[0].cell if live else None
