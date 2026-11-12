"""Shared combat mechanics used by both ResourceBasedSimulator and BatchSimulator.

Functions operate on duck-typed player state objects (PlayerRoundState or
PlayerState) and emit events through an optional callable rather than writing
to a specific storage backend.  No Django imports — this module must stay
importable without the ORM.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .map_context import MapContext

from . import resupply_visit, support_survival
from .downed_option import choose_resupply_trail, plan_is_live
from .follow_rule import may_tag, round_map, start_trail
from .map_context import _NEUTRAL_BASE_TYPES, comms_reach
from .modes import (
    BASE_CAPTURE_SHOTS,
    apply_mode_weights,
    hide_allowed,
    known_threats,
)
from .round_context import RoundContext
from .support_survival import SUPPORT_ROLES
from .time_constants import HOLD_TICKS
from .mechanics import (
    cooldown_ticks,
    shot_cooldown,
    choose_tag_target,
    choose_resupply_target,
    choose_zone_change,
)
from .pending_events import LockCancelReason, PendingMissileLock
from .weights import (
    BASELINE_ACTION_WEIGHTS,
    _get_medic_weights,
    _get_ammo_weights,
    _get_scout_weights,
    _get_heavy_weights,
    _get_commander_weights,
    apply_decision_making_spread,
    check_stamina_penalty,
    finalize_action_weights,
)

_AMMO_CHART = {"commander": 5, "heavy": 5, "scout": 10, "medic": 5}
_MEDIC_CHART = {"commander": 4, "heavy": 3, "scout": 5, "ammo": 3}
#: RV-06b: the ``base_type`` an active capture of base 13 / 14 records.
_CAPTURED_BASE_TYPES = {13: "red", 14: "blue"}


# ----------------------------------------------------------------------------
# RES-02b — Universal event metadata snapshot helpers.
# The local _actor_meta / _target_meta / _build_meta helpers were removed
# by the EventLog candidate — the GameEvent-dict shape now lives entirely
# inside ``sim_helpers.event_log.EventLog``. Helpers below take ``ctx:
# RoundContext`` instead of the legacy ``emit_event`` callable seam and
# call ``ctx.events.*`` verbs directly.
# ----------------------------------------------------------------------------


_ACTION_IDX = {
    "tag_player": 0,
    "only_move": 1,
    "hide": 2,
    "capture_base": 3,
    "use_special": 4,
    "resupply_ally": 5,
    "missile_player": 6,
    "request_resupply": 7,
    "hold": 8,
}
_CHOICES = [
    "tag_player",
    "only_move",
    "hide",
    "capture_base",
    "use_special",
    "resupply_ally",
    "missile_player",
    "request_resupply",
    "hold",
]


# ---------------------------------------------------------------------------
# Visibility helpers (moved from simulation.py — no Django deps)
# ---------------------------------------------------------------------------


def _can_tag_through_windowed_wall(
    r1: int, c1: int, r2: int, c2: int, zone_grid: list, wall_meta: dict
) -> bool:
    """Check whether a tag can reach (r2,c2) from (r1,c1) through windowed walls.

    Windowed walls block normal LOS but have a directional aperture:
      facing N/S → aperture along N-S axis (same column required: c1 == c2).
      facing E/W → aperture along E-W axis (same row required: r1 == r2).
    High walls (0) on the path always block regardless of windowed walls.
    Returns False if any cell on the path is an unpassable high wall, or if a
    windowed wall's aperture axis does not align with the attack direction.
    """
    x0, y0 = c1, r1
    x1, y1 = c2, r2
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    cx, cy = x0, y0

    while True:
        e2 = 2 * err
        if e2 > -dy:
            err -= dy
            cx += sx
        if e2 < dx:
            err += dx
            cy += sy
        if cx == x1 and cy == y1:
            return True
        cell_val = zone_grid[cy][cx]
        if cell_val == 0:
            return False
        if cell_val == 5:
            meta = wall_meta.get(f"{cy},{cx}", {})
            facing = meta.get("facing", "")
            if facing in ("N", "S"):
                if c1 != c2:
                    return False
            elif facing in ("E", "W"):
                if r1 != r2:
                    return False
            else:
                return False


def _get_los_targets(
    actor, candidates: list, movement_ctx: "MapContext | None"
) -> list:
    """Return subset of candidates visible to actor.

    With a map: looks up actor's cell in SightLineConfig (normal LOS), then
    additionally checks windowed-wall aperture targeting for candidates not in
    normal sight. Falls back to same-zone filtering when no map is active.
    """
    if movement_ctx is None or actor.cell_row is None:
        return [p for p in candidates if p.current_zone == actor.current_zone]
    sight_data = movement_ctx.sight_data
    if sight_data is None:
        return [p for p in candidates if p.current_zone == actor.current_zone]
    actor_key = f"{actor.cell_row},{actor.cell_col}"
    visible_cells = sight_data.get(actor_key, frozenset())

    result = []
    windowed_candidates = []
    for p in candidates:
        if p.cell_row is not None:
            if f"{p.cell_row},{p.cell_col}" in visible_cells:
                result.append(p)
            else:
                windowed_candidates.append(p)

    zone_grid = movement_ctx.get_zone_data()
    wall_meta: dict = movement_ctx.get_wall_meta()
    if zone_grid is not None and wall_meta and windowed_candidates:
        for p in windowed_candidates:
            if _can_tag_through_windowed_wall(
                actor.cell_row,
                actor.cell_col,
                p.cell_row,
                p.cell_col,
                zone_grid,
                wall_meta,
            ):
                result.append(p)

    return result


def _get_base_interaction(player, movement_ctx: "MapContext | None") -> int | None:
    """Return base_id of the first uncaptured base the player is in range of, or None.

    base_id 15=neutral, 14=red player captures blue base, 13=blue player captures red base.
    Returns None when no map is active, player has no cell position, or no capturable base
    is in range.
    """
    if movement_ctx is None:
        return None
    base_sight_data = movement_ctx.base_sight_data
    if not base_sight_data or player.cell_row is None:
        return None

    cell_key = f"{player.cell_row},{player.cell_col}"

    if not player.neutral_base_destroyed:
        for neutral_type in _NEUTRAL_BASE_TYPES:
            if cell_key in base_sight_data.get(neutral_type, frozenset()):
                return 15

    if not player.opposing_base_destroyed:
        opposing_type = "blue" if player.team_color == "red" else "red"
        if cell_key in base_sight_data.get(opposing_type, frozenset()):
            return 14 if player.team_color == "red" else 13

    return None


def elevation_hit_modifier(attacker_elev: float, target_elev: float) -> float:
    """Public pure formula for the uphill hit-chance penalty (MAP-09).

    Formula: max(0.5, 1 - 0.1 * elevation_diff)
    where elevation_diff = max(0, target_elev - attacker_elev).
    """
    elevation_diff = max(0.0, target_elev - attacker_elev)
    return max(0.5, 1.0 - 0.1 * elevation_diff)


def _elevation_hit_modifier(
    attacker_row: int | None,
    attacker_col: int | None,
    defender_row: int | None,
    defender_col: int | None,
    movement_ctx: "MapContext | None",
) -> float:
    """MAP-09: Return the uphill hit-chance multiplier for a shot from attacker to defender.

    Returns 1.0 (no modifier) when no map is active, elevation_grid is absent,
    or either cell position is None.
    """
    if movement_ctx is None:
        return 1.0
    if movement_ctx.elevation_grid is None:
        return 1.0
    if attacker_row is None or attacker_col is None:
        return 1.0
    if defender_row is None or defender_col is None:
        return 1.0
    attacker_elev = movement_ctx.elevation_at(attacker_row, attacker_col)
    defender_elev = movement_ctx.elevation_at(defender_row, defender_col)
    return elevation_hit_modifier(attacker_elev, defender_elev)


# ---------------------------------------------------------------------------
# Shared combat mechanics
# ---------------------------------------------------------------------------


def plan_action(
    player,
    all_alive: list,
    second: float,
    movement_ctx: "MapContext | None" = None,
    *,
    save_player=None,
    time_domain: str = "seconds",
) -> list:
    """Return a list of planned action dicts for player at this tick.

    Does not apply any state changes except:
    - player.last_chosen_action is updated
    - player.is_hiding may be cleared (save_player called if provided)

    save_player: optional callable(player) invoked when is_hiding is cleared.
    ResourceBasedSimulator passes ``lambda p: p.save()`` so the ORM state is
    persisted before the next refresh_from_db call in the game loop.

    time_domain (TIME-01): ``"seconds"`` (default) keeps the proportional
    stamina schedule and the memory-staleness / score-remaining heuristics in
    the seconds domain — used by ResourceBasedSimulator (which stays
    second-internal and byte-identical) and by existing unit tests that pass
    seconds. ``"ticks"`` selects the tick-native thresholds from
    ``time_constants`` — used by the fully tick-native BatchSimulator.
    """
    # MOVE-03: 9-slot action array — index 8 is the new ``hold`` Action
    # (Overwatch). Per-role weight redistribution is applied in weights.py.
    # Copy the module constant — plan_action mutates the list in place.
    weights = list(BASELINE_ACTION_WEIGHTS)

    # MECH-06 wired: communication broadcast in simulation.py tick loop; memory updated
    # from LOS + global broadcasts. Teamwork reaches goals through modes.mode_goal.

    if time_domain == "ticks":
        from .time_constants import TICKS_PER_ROUND

        check_stamina_penalty(player, second, round_duration=TICKS_PER_ROUND)
    else:
        check_stamina_penalty(player, second)

    is_active = player.is_active_at(second)
    # CAL-03 rules 9 and 10: the support link and the backline position, before a
    # Hold can return (no RNG).
    if movement_ctx is not None and player.role in SUPPORT_ROLES:
        _support_link(player, all_alive, movement_ctx, second)
    resupply_visit.update_backline(player, all_alive, movement_ctx)
    # MECH-17: a Hold is a commitment. While one stands no Action is drawn, so the
    # tick spends no RNG; anything else ends it, and this tick then rolls as usual.
    hold_until = getattr(player, "hold_until", None)
    if hold_until is not None:
        if (
            second < hold_until
            and is_active
            and player.final_shots > 0
            and not getattr(player, "reacting_to_nuke", False)
        ):
            player.last_chosen_action = "hold"
            return [{"type": "hold", "actor": player}]
        # The roll below clears ``is_holding`` and the commitment, or starts a new Hold.
        player.hold_until = None

    # MECH-17: what the weights read on a map: whether a capture can happen here, and
    # how many enemies the player knows can see it.
    if movement_ctx is not None:
        player.base_in_reach = (
            is_active
            and _get_base_interaction(player, movement_ctx) is not None
            and (player.final_shots >= BASE_CAPTURE_SHOTS or player.role == "ammo")
        )
        player.known_threats = known_threats(
            player, all_alive, movement_ctx, second, time_domain
        )

    if player.role == "medic":
        weights = _get_medic_weights(
            player, _ACTION_IDX, weights, all_alive, second, time_domain
        )
    elif player.role == "ammo":
        weights = _get_ammo_weights(
            player, _ACTION_IDX, weights, all_alive, second, time_domain
        )
    elif player.role == "scout":
        weights = _get_scout_weights(
            player, _ACTION_IDX, weights, all_alive, second, time_domain
        )
    elif player.role == "heavy":
        weights = _get_heavy_weights(
            player, _ACTION_IDX, weights, all_alive, second, time_domain
        )
    elif player.role == "commander":
        weights = _get_commander_weights(
            player, _ACTION_IDX, weights, all_alive, second, time_domain
        )

    # MECH-07: a Mode nudges its Actions on map rounds only. Before the stamina
    # penalty and cooldown zeroing, so those still get the last word.
    if movement_ctx is not None:
        apply_mode_weights(player, weights)

    penalty_count = getattr(player, "stamina_penalty_count", 0)
    if penalty_count > 0:
        cz_idx = _ACTION_IDX["only_move"]
        if weights[cz_idx] > 0:
            weights[cz_idx] = max(
                0, int(weights[cz_idx] * max(0.1, 1.0 - 0.10 * penalty_count))
            )

    cooldown = shot_cooldown(player, second)
    if time_domain == "ticks":
        # CAL-02a bug B (2026-11-08): ``second`` and ``last_shot_time`` are ticks
        # here, so the seconds cooldown is converted to whole ticks first (Heavy
        # 2, every other role 1, a rapid-fire Scout 0), the way the deferred
        # queues already space their shots.
        cooldown = cooldown_ticks(player, second)
    if cooldown > 0 and (second - player.last_shot_time) < cooldown:
        weights[_ACTION_IDX["tag_player"]] = 0
        if sum(weights) == 0:
            weights[_ACTION_IDX["hide"]] = 1

    # SM5-02: the role functions drain these while Downed, but the losing-side
    # score broadcast (+10 tag_player) runs after that drain, so zero them again.
    if not is_active:
        weights[_ACTION_IDX["tag_player"]] = 0
        weights[_ACTION_IDX["resupply_ally"]] = 0
        # MECH-17: a player who is not Active cannot start a Hold.
        if movement_ctx is not None:
            weights[_ACTION_IDX["hold"]] = 0
        if sum(weights) == 0:
            weights[_ACTION_IDX["hide"]] = 1

    # MECH-17: a Hold that could not stand would end next tick, and hold the player
    # still until then.
    if movement_ctx is not None and (
        player.final_shots <= 0 or getattr(player, "reacting_to_nuke", False)
    ):
        weights[_ACTION_IDX["hold"]] = 0

    # MECH-17: a support who is needed, or has somewhere to be, gets no hide this roll
    # and leaves a hide it is in. It reads the player before the roll changes anything.
    must_leave_hide = (
        movement_ctx is not None
        and is_active
        and player.role in SUPPORT_ROLES
        and not hide_allowed(player, all_alive, movement_ctx, second, time_domain)
    )
    if must_leave_hide:
        weights[_ACTION_IDX["hide"]] = 0

    weights = apply_decision_making_spread(
        weights, getattr(player, "decision_making", 50)
    )

    # CAL-03 rule 6: on a map a non-support asks for a Resupply only on a visit.
    visit = resupply_visit.visit_step(player, all_alive, movement_ctx, second)
    if movement_ctx is not None and player.role not in SUPPORT_ROLES and not visit:
        weights[_ACTION_IDX["request_resupply"]] = 0

    # SIM-12 / UXP-23: finalize_action_weights clamps every slot to >= 0 (a
    # negative bucket would skew its neighbours in random.choices' cumulative
    # bisect) and then applies the UXP-23 fallback: the role weight functions
    # can leave nothing positive (e.g. a low-lives Medic with resupply_synergy
    # 0, or a negative use_special bucket on the 3-zone fallback), which
    # random.choices rejects, so fall back to hiding as the cooldown guard above
    # does. A vector with a positive total is left untouched, so non-affected
    # ticks replay byte-identically.
    weights = finalize_action_weights(weights)

    choice = _visit_choice(visit, random.choices(_CHOICES, weights)[0], player, second)
    # CAL-03 rule 7: at 1-3 lives with no own support in sight, hide and call out.
    called_out = resupply_visit.call_out(player, all_alive, movement_ctx, second)
    if called_out and choice != "tag_player":
        choice = "hide"
    player.last_chosen_action = choice

    # MECH-17: on a map an only_move roll takes an Active non-support out of its hide.
    moves_out_of_hide = (
        movement_ctx is not None
        and is_active
        and player.role not in SUPPORT_ROLES
        and choice == "only_move"
    )
    # MOVE-08: Stay hidden keeps its hide for the whole Down.
    if (
        player.is_hiding
        and (
            choice not in ("hide", "only_move", "resupply_ally")
            or must_leave_hide
            or moves_out_of_hide
        )
        and not plan_is_live(player, second)
    ):
        player.is_hiding = False
        # MOVE-04 / ADR-0010: exiting Stationary (hide→non-hide) is a force
        # trigger — drop the committed goal so the next ``choose_goal_cell``
        # recomputes immediately rather than reusing a stale hide-time goal.
        # Safe for RBS: RBS never sets ``_committed_goal``, so the assignment
        # is a no-op on None.
        player._committed_goal = None
        if save_player is not None:
            save_player(player)

    # MOVE-03: ``is_holding`` (mirrors is_hiding; transient — no DB column / no
    # migration, like _path_cache / movement_trail). A ``hold`` roll keeps the player
    # in Overwatch (Stationary): MECH-17, on a map until ``hold_until`` (the top of
    # this function); map-less, until it rolls a non-Hold Action. A Down/respawn
    # force-clears it via _record_down. Consumes no RNG.
    if choice == "hold":
        player.is_holding = True
        if movement_ctx is not None:
            player.hold_until = int(second) + HOLD_TICKS
    elif getattr(player, "is_holding", False):
        player.is_holding = False
        # MOVE-04 / ADR-0010: exiting Stationary (hold→non-hold) is a force
        # trigger — drop the committed goal for the same reason as the hide
        # transition above. Safe for RBS (never sets _committed_goal).
        player._committed_goal = None

    plans = []
    # SM5-02: a Downed pack is dark. A player that is not Active plans no Shot
    # and no Resupply, and checks it before the target pick so no RNG is drawn.
    if choice == "tag_player":
        if not is_active:
            return plans
        target = choose_tag_target(
            player, all_alive, second, movement_ctx, los_filter=_get_los_targets
        )
        if target and player.final_shots > 0:
            plans.append({"type": "tag", "actor": player, "target": target})
            if player.role == "scout" and player.special_active_until > second:
                second_target = choose_tag_target(
                    player,
                    all_alive,
                    second,
                    movement_ctx,
                    los_filter=_get_los_targets,
                )
                if second_target:
                    plans.append(
                        {"type": "tag", "actor": player, "target": second_target}
                    )
    elif choice == "resupply_ally":
        if not is_active:
            return plans
        # MECH-17: line of sight on a map round; the zone rule on a map-less one.
        teammate = choose_resupply_target(
            player,
            all_alive,
            second,
            movement_ctx,
            los_filter=_get_los_targets if movement_ctx is not None else None,
        )
        if teammate:
            rtype = "resupply_ammo" if player.role == "ammo" else "resupply_lives"
            plans.append({"type": rtype, "actor": player, "target": teammate})
        else:
            plans.extend(_idle_trade(player, all_alive, second, movement_ctx, cooldown))
    elif choice == "missile_player":
        if player.final_missiles > 0:
            candidates = [
                p
                for p in all_alive
                if p.team_color != player.team_color
                and p.final_lives > 0
                and p.is_taggable_at(second)
                and may_tag(player, p, second)  # MECH-10 Follow rule
            ]
            targets = _get_los_targets(player, candidates, movement_ctx)
            if targets:
                plans.append(
                    {
                        "type": "missile",
                        "actor": player,
                        "target": random.choice(targets),
                    }
                )
    elif choice == "only_move":
        if movement_ctx is not None and player.cell_row is not None:
            # MOVE-01 (Option B): on the map path the goal-directed Advance is
            # an always-on per-tick step performed by each simulator's dispatch
            # (decoupled from the weighted action roll). The only effect of the
            # ``only_move`` roll itself is to make this tick's Advance a single
            # 2× step; the dispatch reads this marker plan to apply that.
            plans.append({"type": "only_move", "actor": player})
        else:
            # MOVE-01 decision 7: the 3-zone fallback (movement_ctx is None)
            # keeps the existing weighted _change_zone behaviour triggered by
            # the only_move roll (MAP-06 pattern). Always-on Advance + 2× apply
            # to the map path only.
            zone = choose_zone_change(player, all_alive)
            plans.append({"type": "change_zone", "actor": player, "zone": zone})
    elif choice == "capture_base":
        # MECH-15: a player in the respawn cooldown (Downed, not yet active)
        # cannot interact with a Base — never plan a capture while inactive.
        if not is_active:
            pass
        elif movement_ctx is not None and player.cell_row is not None:
            base_id = _get_base_interaction(player, movement_ctx)
            if base_id is not None:
                plans.append(
                    {
                        "type": "capture_base",
                        "actor": player,
                        "base_id": base_id,
                        "movement_ctx": movement_ctx,
                    }
                )
        else:
            base_id = (
                15
                if player.current_zone == 1
                else (14 if player.team_color == "red" else 13)
            )
            plans.append({"type": "capture_base", "actor": player, "base_id": base_id})
    elif choice == "use_special":
        if player.can_use_special and player.final_lives > 0 and is_active:
            plans.append({"type": "use_special", "actor": player})
    elif choice == "hide":
        trade = (
            _idle_trade(player, all_alive, second, movement_ctx, cooldown)
            if is_active and not plan_is_live(player, second)
            else []
        )
        if trade:
            # CAL-03 rule 2: the hiding support comes out to shoot.
            player.is_hiding = False
            plans.extend(trade)
        else:
            plans.append({"type": "hide", "actor": player})
    elif choice == "request_resupply":
        plans.append({"type": "request_resupply", "actor": player})
    elif choice == "hold":
        # MOVE-03: enter/maintain Overwatch. Stationary; the actual Overwatch
        # shots are resolved by the BatchSimulator tick loop (BatchSim-only by
        # design — RBS treats this as a no-op).
        plans.append({"type": "hold", "actor": player})

    return plans


#: CAL-03 rule 6: the roll each ``VisitAction`` makes of a non-Tag roll (a
#: ``request`` that cannot be resupplied hides). One key per ``VisitAction``.
_VISIT_ROLLS: dict[resupply_visit.VisitAction, str] = {
    "hide": "hide",
    "move": "only_move",
    "request": "hide",
}


def _visit_choice(
    visit: "resupply_visit.VisitAction | None", choice: str, player: Any, second: float
) -> str:
    """CAL-03 rule 6: what a Resupply visit makes of the rolled *choice*. A Tag roll
    stands, except that a player the visit has at its supports requests while it
    can be resupplied (and hides otherwise)."""
    if visit == "request" and player.is_resupplyable_at(second):
        return "request_resupply"
    if visit is None or choice == "tag_player":
        return choice
    return _VISIT_ROLLS[visit]


def _support_link(
    player: Any, all_alive: list, movement_ctx: "MapContext", second: float
) -> None:
    """CAL-03 rule 9, once per plan of a Medic or Ammo. No RNG.

    Within comms range (half the map diagonal, as ``_broadcast_communication``) the
    support writes its partner's true cell into its memory. The Ammo stamps
    ``link_left_at`` when ``medic_with_ammo`` turns False and clears it once the
    Medic is with it again. CAL-04c: it also stamps ``leash_far_since`` the first
    plan its cell is more than ``LEASH_SQUARES`` (Chebyshev) from its memory's
    cell for the Medic, and clears it within reach or with no remembered cell."""
    other = support_survival.own_teammate(
        player, support_survival.partner_role(player.role), all_alive
    )
    if other is None or player.cell_row is None or other.cell_row is None:
        return
    here, there = (player.cell_row, player.cell_col), (other.cell_row, other.cell_col)
    if math.dist(here, there) <= comms_reach(movement_ctx.get_zone_data()):
        if not other.is_taggable_at(second):
            status = "downed"
        elif not other.is_active_at(second):
            status = "reset_window"
        else:
            status = "active"
        player.player_memory[other.tag_id_key] = {
            "cell": there,
            "timestamp": second,
            "role": other.role,
            "status": status,
        }
    if player.role == "ammo":
        if support_survival.medic_with_ammo(player, other, movement_ctx):
            player.link_left_at = None
        elif player.link_left_at is None:
            player.link_left_at = int(second)
        entry = player.player_memory.get(other.tag_id_key)
        cell = entry.get("cell") if entry else None
        far = cell is not None and (
            resupply_visit.chebyshev(cell, here) > support_survival.LEASH_SQUARES
        )
        if not far:
            player.leash_far_since = None
        elif player.leash_far_since is None:
            player.leash_far_since = int(second)


def _idle_trade(
    player: Any,
    all_alive: list,
    second: float,
    movement_ctx: "MapContext | None",
    cooldown: float,
) -> list:
    """CAL-03 rule 2: an Active Medic or Ammo whose roll planned nothing Tags an
    enemy in sight (``choose_tag_target``) with probability ``IDLE_TRADE_RATE``, its
    gun off cooldown. The rate draw is made only once a target is in sight; ``[]``
    otherwise (and on a map-less round)."""
    if player.role not in SUPPORT_ROLES or movement_ctx is None:
        return []
    if player.final_shots <= 0 or not player.is_active_at(second):
        return []
    if cooldown > 0 and (second - player.last_shot_time) < cooldown:
        return []
    target = choose_tag_target(
        player, all_alive, second, movement_ctx, los_filter=_get_los_targets
    )
    if target is None or random.random() >= support_survival.IDLE_TRADE_RATE:
        return []
    return [{"type": "tag", "actor": player, "target": target}]


def _reset_resupplied(teammate, second: float, ctx: RoundContext) -> None:
    """A Resupply resets the receiver's pack: the Respawn cooldown restarts,
    shields refill, and a Scout's Rapid Fire / a Commander's pending Nuke ends.

    MECH-10: it also starts a Follow rule Trail, as a Down does (before the
    ``last_downed_time`` stamp), and stamps ``last_resupplied_at``.
    MOVE-08: then the receiver may Make a trail (``choose_resupply_trail``).
    MECH-21: the reset ends the receiver's Lock and every Lock on them
    (``reason=down``: no longer Active or Taggable, so no launch could land).
    """
    cancel_missile_locks_involving(ctx, teammate, int(second))
    start_trail(teammate, second, round_map(ctx))
    teammate.last_downed_time = second
    teammate.last_resupplied_at = second
    choose_resupply_trail(teammate, second, ctx)
    teammate.shields = teammate.max_shields
    if teammate.role == "scout" and teammate.special_active_until > second:
        teammate.special_active_until = second
    if teammate.role == "commander" and teammate.special_active_until > second:
        teammate.special_active_until = 0


def attempt_resupply(
    tagger,
    teammate,
    second: float,
    *,
    ctx: RoundContext,
) -> None:
    """Apply a resupply action from tagger to teammate.

    Mutates tagger.counters.resupplies_given and the teammate's resource counters.
    Clears any active commander nuke (special_active_until) on the teammate
    when resupplied.

    EventLog candidate: takes ``ctx: RoundContext`` and calls
    ``ctx.events.resupply_ammo(...)`` / ``ctx.events.resupply_lives(...)``
    directly. Replaces the pre-refactor ``emit_event=None`` callable seam.

    SM5-02: a tagger that is not Active gives nothing (a Downed pack is dark).
    CAL-03 rule 1: while ``double_window_open`` the other support's Resupply
    lands although the first one's reset has started; every landed Resupply
    updates the receiver's ``last_single``. Rule 6: a single to a player on a
    visit is followed by the partner's (``_partner_follows``). CAL-04b: a window
    Resupply (the second of a pair) to a player on a visit adds one to
    ``visit.doubles``, so each double counts once whichever path delivered it.
    """
    if not tagger.is_active_at(second):
        return
    in_window = support_survival.double_window_open(teammate, tagger, second)
    receivable = in_window or teammate.is_resupplyable_at(second)
    if tagger.role == "ammo" and receivable:
        amount = _AMMO_CHART.get(teammate.role, 5)
        teammate.final_shots = min(teammate.max_shots, teammate.final_shots + amount)
        _landed(tagger, teammate, second, in_window, ctx)
        ctx.events.resupply_ammo(tagger, teammate, second, amount=amount)
    elif tagger.role == "medic" and tagger.final_shots > 0 and receivable:
        amount = _MEDIC_CHART.get(teammate.role, 3)
        teammate.final_lives = min(teammate.max_lives, teammate.final_lives + amount)
        _landed(tagger, teammate, second, in_window, ctx)
        ctx.events.resupply_lives(tagger, teammate, second, amount=amount)
    else:
        return
    visit = teammate.visit
    if in_window and visit is not None:
        teammate.visit = replace(visit, doubles=visit.doubles + 1)
    if not in_window:
        _partner_follows(tagger, teammate, second, ctx)


def _partner_follows(
    tagger: Any, teammate: Any, second: float, ctx: RoundContext
) -> None:
    """CAL-03 decision 43: a single Resupply to a player on a visit is followed in
    the same tick by the other support's, when that support is available (Active,
    with Shots, off its shot cooldown) and sees the player; the double window lets
    it land. No RNG."""
    from .resupply_queue import _is_available  # noqa: PLC0415 (resupply_queue -> here)

    if teammate.visit is None:
        return
    partner = support_survival.own_teammate(
        teammate, support_survival.partner_role(tagger.role), ctx.all_alive
    )
    if partner is None or not _is_available(
        partner, second, ctx.movement_ctx, teammate
    ):
        return
    attempt_resupply(partner, teammate, second, ctx=ctx)
    partner.last_shot_time = second


def _landed(
    tagger: Any, teammate: Any, second: float, in_window: bool, ctx: RoundContext
) -> None:
    """The bookkeeping of a landed Resupply, before its event: the pack reset,
    the receiver's ``last_single`` (CAL-03) and the supporter's counters."""
    _reset_resupplied(teammate, second, ctx)
    teammate.last_single = support_survival.resupply_landed(
        teammate, tagger, second, in_window
    )
    tagger.last_tagged_id = teammate.tag_id_key
    tagger.counters.resupplies_given += 1


def capture_base(
    player,
    base_id: int,
    second: float,
    movement_ctx: "MapContext | None" = None,
    *,
    ctx: RoundContext,
) -> bool:
    """Attempt to capture a base for player.

    Returns True if the capture succeeded, False otherwise.

    EventLog candidate: emits via ``ctx.events.base_capture`` with the
    "active capture" wording and the per-capture metadata extras
    (shots_remaining, points_scored, base_type).

    RV-06b: ``base_type`` names the base hit: ``"red"`` for 13, ``"blue"`` for
    14, and for 15 the first ``neutral_*`` whose sight set holds the player's
    cell. A neutral capture with no map or no cell omits it. Persistence only.
    """
    base_type: str | None = _CAPTURED_BASE_TYPES.get(base_id)
    if movement_ctx is not None and player.cell_row is not None:
        base_sight_data = movement_ctx.base_sight_data
        cell_key = f"{player.cell_row},{player.cell_col}"
        if base_id == 15:
            base_type = next(
                (
                    bt
                    for bt in _NEUTRAL_BASE_TYPES
                    if cell_key in base_sight_data.get(bt, frozenset())
                ),
                None,
            )
            in_range = base_type is not None
        else:
            opp_type = "blue" if player.team_color == "red" else "red"
            in_range = cell_key in base_sight_data.get(opp_type, frozenset())
        if not in_range:
            return False

    if player.final_shots >= BASE_CAPTURE_SHOTS or player.role == "ammo":
        # MECH-21: an active capture is a trigger pull, so it ends the
        # player's Lock (a round-end ``award_bases`` does not).
        cancel_missile_lock(ctx, player, int(second), "shot")
        if player.role != "ammo":
            player.final_shots -= BASE_CAPTURE_SHOTS
        player.last_tagged_id = base_id
        if base_id == 15:
            player.neutral_base_destroyed = True
        else:
            player.opposing_base_destroyed = True
        player.counters.points_scored += 1001
        if player.role != "heavy":
            player.final_special = min(player.max_special, player.final_special + 5)
        extras: dict[str, object] = {
            "shots_remaining": player.final_shots,
            "points_scored": player.counters.points_scored,
        }
        if base_type is not None:
            extras["base_type"] = base_type
        ctx.events.base_capture(
            player, int(second), base_id=base_id, metadata_extras=extras
        )
        return True
    return False


def award_bases(
    player,
    second: float,
    *,
    ctx: RoundContext,
) -> None:
    """Award any uncaptured bases to a surviving player at round end.

    EventLog candidate: emits via ``ctx.events.base_capture`` with
    awarded-style descriptions ("X awarded neutral base" / "X awarded
    opposing base") and minimal metadata (base_id only).
    """
    if player.final_lives <= 0:
        return
    if not player.neutral_base_destroyed:
        player.counters.points_scored += 1001
        player.neutral_base_destroyed = True
        ctx.events.base_capture(
            player,
            second,
            base_id=15,
            description=f"{player.name} awarded neutral base",
        )
    if not player.opposing_base_destroyed:
        player.counters.points_scored += 1001
        player.opposing_base_destroyed = True
        opp_base_id = 14 if player.team_color == "red" else 13
        ctx.events.base_capture(
            player,
            second,
            base_id=opp_base_id,
            description=f"{player.name} awarded opposing base",
        )


def start_missile_lock(
    attacker,
    defender,
    second: float,
    movement_ctx: "MapContext | None" = None,
    *,
    ctx: RoundContext,
) -> "PendingMissileLock | None":
    """Start a Lock on defender (MECH-21, ADR-0050); nothing is spent.

    Gates: attacker Active, defender Taggable, a missile left, defender not
    hiding, no MECH-10 Chase ban (``may_tag``), and sight at the start. A player holds at most one Lock, keyed
    by ``player_id`` in ``ctx.missile_locks``:

    - a lock already on this defender holds: ``None``, no row;
    - a lock on another defender is cancelled (``"retarget"``) only once the
      new gates pass, then the new lock starts on this tick; if they fail,
      the old lock holds.

    On a start, writes ``locking`` and stores the lock. The lock is advanced
    by ``tick_missile_lock`` at the top of each tick and launches at
    ``second + LOCK_TICKS``, where the missile is spent.
    """
    held = ctx.missile_locks.get(attacker.player_id)
    if held is not None and held.defender is defender:
        return None
    if not (
        attacker.is_active_at(second)
        and defender.is_taggable_at(second)
        and attacker.final_missiles > 0
        and not defender.is_hiding
        and may_tag(attacker, defender, second)  # MECH-10 Follow rule
        and _lock_has_sight(attacker, defender, movement_ctx)
    ):
        return None
    if held is not None:
        cancel_missile_lock(ctx, attacker, int(second), "retarget")

    ctx.events.locking(attacker, defender, int(second))
    lock = PendingMissileLock(attacker=attacker, defender=defender)
    ctx.missile_locks[attacker.player_id] = lock
    return lock


def _lock_has_sight(attacker, defender, movement_ctx: "MapContext | None") -> bool:
    """Sight between locker and target: the map's sight set, or the same zone
    on a map-less round."""
    if (
        movement_ctx is not None
        and attacker.cell_row is not None
        and defender.cell_row is not None
    ):
        sight_data = movement_ctx.sight_data
        actor_key = f"{attacker.cell_row},{attacker.cell_col}"
        def_key = f"{defender.cell_row},{defender.cell_col}"
        return sight_data is not None and def_key in sight_data.get(
            actor_key, frozenset()
        )
    return attacker.current_zone == defender.current_zone


def cancel_missile_lock(
    ctx: RoundContext, locker, tick: int, reason: LockCancelReason
) -> "PendingMissileLock | None":
    """End the locker's Lock without a missile: remove it from
    ``ctx.missile_locks`` and write ``lock_cancelled`` with ``reason``.

    Returns the lock, or ``None`` (and writes nothing) when the locker held
    none. Draws no RNG.
    """
    lock = ctx.missile_locks.pop(locker.player_id, None)
    if lock is not None:
        ctx.events.lock_cancelled(locker, lock.defender, tick, reason=reason)
    return lock


def cancel_missile_locks_involving(ctx: RoundContext, player, tick: int) -> int:
    """Cancel the player's own Lock and every Lock on them; return how many.

    The reason is ``"out"`` when ``player.final_lives <= 0`` and ``"down"``
    otherwise. Called by ``down.record_down``, so every Down (Tag, Missile,
    Nuke) ends the locks at the Down tick. Locks keep their insertion order.
    """
    reason: LockCancelReason = "out" if player.final_lives <= 0 else "down"
    lockers = [
        lock.attacker
        for lock in ctx.missile_locks.values()
        if lock.attacker is player or lock.defender is player
    ]
    for locker in lockers:
        cancel_missile_lock(ctx, locker, tick, reason)
    return len(lockers)


def tick_missile_lock(
    lock: "PendingMissileLock",
    second: float,
    movement_ctx: "MapContext | None",
) -> Literal["pending", "launch", "sight"]:
    """Advance a Lock by one tick, at the top of the tick, before planning.

    With no sight, a failed ``special_usage`` save returns ``"sight"`` (the
    caller cancels the lock on this tick). Otherwise the lock counts down, and
    at 0 it returns ``"launch"``. Lives are not read: a Down cancels the lock
    through ``record_down``.
    """
    if not _lock_has_sight(lock.attacker, lock.defender, movement_ctx):
        special_usage = getattr(lock.attacker, "special_usage", 50)
        if random.random() * 100 >= special_usage:
            return "sight"

    lock.ticks_remaining -= 1
    if lock.ticks_remaining > 0:
        return "pending"
    return "launch"
