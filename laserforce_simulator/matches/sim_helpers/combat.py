"""Shared combat mechanics used by both ResourceBasedSimulator and BatchSimulator.

Functions operate on duck-typed player state objects (PlayerRoundState or
PlayerState) and emit events through an optional callable rather than writing
to a specific storage backend.  No Django imports — this module must stay
importable without the ORM.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .map_context import MapContext

from .mechanics import (
    shot_cooldown,
    choose_tag_target,
    choose_resupply_target,
    choose_zone_change,
)
from .pathfinding import choose_goal_cell
from .pending_events import PendingMissileLock
from .weights import (
    _get_medic_weights,
    _get_ammo_weights,
    _get_scout_weights,
    _get_heavy_weights,
    _get_commander_weights,
    apply_decision_making_spread,
    check_stamina_penalty,
)

# Neutral base_types in priority order for capture/reset checks
_NEUTRAL_BASE_TYPES = ("neutral_1", "neutral_2", "neutral_3", "neutral_4")

_AMMO_CHART = {"commander": 5, "heavy": 5, "scout": 10, "medic": 5}
_MEDIC_CHART = {"commander": 4, "heavy": 3, "scout": 5, "ammo": 3}


# ----------------------------------------------------------------------------
# RES-02b — Universal event metadata snapshot helpers.
# Local copies of the helpers in matches/simulation.py to avoid a circular
# import (simulation imports combat). The pair is intentionally duplicated;
# any change to the helper key set must be made in both places.
# ----------------------------------------------------------------------------
def _actor_meta(actor) -> dict:
    """Universal actor snapshot block (post-event values)."""
    return {
        "actor_role": actor.role,
        "actor_shots": actor.final_shots,
        "actor_lives": actor.final_lives,
        "actor_points": actor.points_scored,
        "sp": actor.final_special,
    }


def _target_meta(target) -> dict:
    """Universal target snapshot block (post-event values)."""
    return {
        "target_role": target.role,
        "target_shots": target.final_shots,
        "target_lives": target.final_lives,
        "target_points": target.points_scored,
    }


def _build_meta(actor, target=None, **extras) -> dict:
    """Build event metadata with actor block, optional target block, and extras."""
    md = _actor_meta(actor)
    if target is not None:
        md.update(_target_meta(target))
    md.update(extras)
    return md


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
    weights = [70, 30, 0, 0, 0, 0, 0, 0, 0]

    # MECH-06 wired: teamwork bias in pathfinding._goal_from_role; communication
    # broadcast in simulation.py tick loop; memory updated from LOS + global broadcasts.

    if time_domain == "ticks":
        from .time_constants import TICKS_PER_ROUND

        check_stamina_penalty(player, second, round_duration=TICKS_PER_ROUND)
    else:
        check_stamina_penalty(player, second)

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

    penalty_count = getattr(player, "stamina_penalty_count", 0)
    if penalty_count > 0:
        cz_idx = _ACTION_IDX["only_move"]
        if weights[cz_idx] > 0:
            weights[cz_idx] = max(
                0, int(weights[cz_idx] * max(0.1, 1.0 - 0.10 * penalty_count))
            )

    cooldown = shot_cooldown(player, second)
    if cooldown > 0.0 and (second - player.last_shot_time) < cooldown:
        weights[_ACTION_IDX["tag_player"]] = 0
        if sum(weights) == 0:
            weights[_ACTION_IDX["hide"]] = 1

    weights = apply_decision_making_spread(
        weights, getattr(player, "decision_making", 50)
    )

    choice = random.choices(_CHOICES, weights)[0]
    player.last_chosen_action = choice

    if player.is_hiding and choice not in ("hide", "only_move", "resupply_ally"):
        player.is_hiding = False
        # MOVE-04 / ADR-0010: exiting Stationary (hide→non-hide) is a force
        # trigger — drop the committed goal so the next ``choose_goal_cell``
        # recomputes immediately rather than reusing a stale hide-time goal.
        # Safe for RBS: RBS never sets ``_committed_goal``, so the assignment
        # is a no-op on None.
        player._committed_goal = None
        if save_player is not None:
            save_player(player)

    # MOVE-03: ``is_holding`` carry-over (mirrors is_hiding; transient — no DB
    # column / no migration, like _path_cache / movement_trail). A ``hold`` roll
    # keeps the player in Overwatch (Stationary) until it rolls a non-Hold
    # Action; a Down/respawn force-clears it via _record_down. Consumes no RNG.
    if choice == "hold":
        player.is_holding = True
    elif getattr(player, "is_holding", False):
        player.is_holding = False
        # MOVE-04 / ADR-0010: exiting Stationary (hold→non-hold) is a force
        # trigger — drop the committed goal for the same reason as the hide
        # transition above. Safe for RBS (never sets _committed_goal).
        player._committed_goal = None

    plans = []
    if choice == "tag_player":
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
        teammate = choose_resupply_target(player, all_alive, second)
        if teammate:
            rtype = "resupply_ammo" if player.role == "ammo" else "resupply_lives"
            plans.append({"type": rtype, "actor": player, "target": teammate})
    elif choice == "missile_player":
        if player.final_missiles > 0:
            candidates = [
                p
                for p in all_alive
                if p.team_color != player.team_color
                and p.final_lives > 0
                and p.is_taggable_at(second)
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
        if movement_ctx is not None and player.cell_row is not None:
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
        if (
            player.can_use_special
            and player.final_lives > 0
            and player.is_active_at(second)
        ):
            plans.append({"type": "use_special", "actor": player})
    elif choice == "hide":
        plans.append({"type": "hide", "actor": player})
    elif choice == "request_resupply":
        plans.append({"type": "request_resupply", "actor": player})
    elif choice == "hold":
        # MOVE-03: enter/maintain Overwatch. Stationary; the actual Overwatch
        # shots are resolved by the BatchSimulator tick loop (BatchSim-only by
        # design — RBS treats this as a no-op).
        plans.append({"type": "hold", "actor": player})

    return plans


def attempt_resupply(
    tagger,
    teammate,
    second: float,
    *,
    emit_event=None,
) -> None:
    """Apply a resupply action from tagger to teammate.

    Mutates tagger.resupplies_given and the teammate's resource counters.
    Clears any active commander nuke (special_active_until) on the teammate
    when resupplied — nuke-cancel stat tracking (ally_nuke_cancels, etc.) is
    the caller's responsibility when using ResourceBasedSimulator.

    emit_event: optional callable(event_dict) for event recording.
    """
    if tagger.role == "ammo" and teammate.is_resupplyable_at(second):
        amount = _AMMO_CHART.get(teammate.role, 5)
        teammate.final_shots = min(teammate.max_shots, teammate.final_shots + amount)
        teammate.last_downed_time = second
        teammate.shields = teammate.max_shields
        if teammate.role == "scout" and teammate.special_active_until > second:
            teammate.special_active_until = second
        if teammate.role == "commander" and teammate.special_active_until > second:
            teammate.special_active_until = 0
        tagger.last_tagged_id = teammate.tag_id_key
        tagger.resupplies_given += 1
        if emit_event is not None:
            emit_event(
                {
                    "event_type": "resupply_ammo",
                    "actor_id": tagger.player_id,
                    "target_id": teammate.player_id,
                    "timestamp": second,
                    "points_awarded": 0,
                    "description": f"{tagger.name} resupplies {teammate.name}",
                    "metadata": _build_meta(tagger, teammate, amount=amount),
                }
            )
    elif (
        tagger.role == "medic"
        and tagger.final_shots > 0
        and teammate.is_resupplyable_at(second)
    ):
        amount = _MEDIC_CHART.get(teammate.role, 3)
        teammate.final_lives = min(teammate.max_lives, teammate.final_lives + amount)
        teammate.last_downed_time = second
        teammate.shields = teammate.max_shields
        if teammate.role == "scout" and teammate.special_active_until > second:
            teammate.special_active_until = second
        if teammate.role == "commander" and teammate.special_active_until > second:
            teammate.special_active_until = 0
        tagger.last_tagged_id = teammate.tag_id_key
        tagger.resupplies_given += 1
        if emit_event is not None:
            emit_event(
                {
                    "event_type": "resupply_lives",
                    "actor_id": tagger.player_id,
                    "target_id": teammate.player_id,
                    "timestamp": second,
                    "points_awarded": 0,
                    "description": f"{tagger.name} heals {teammate.name}",
                    "metadata": _build_meta(tagger, teammate, amount=amount),
                }
            )


def capture_base(
    player,
    base_id: int,
    second: float,
    movement_ctx: "MapContext | None" = None,
    *,
    emit_event=None,
) -> bool:
    """Attempt to capture a base for player.

    Returns True if the capture succeeded, False otherwise.
    emit_event: optional callable(event_dict) for event recording.
    """
    if movement_ctx is not None and player.cell_row is not None:
        base_sight_data = movement_ctx.base_sight_data
        cell_key = f"{player.cell_row},{player.cell_col}"
        if base_id == 15:
            in_range = any(
                cell_key in base_sight_data.get(bt, frozenset())
                for bt in _NEUTRAL_BASE_TYPES
            )
        else:
            opp_type = "blue" if player.team_color == "red" else "red"
            in_range = cell_key in base_sight_data.get(opp_type, frozenset())
        if not in_range:
            return False

    if player.final_shots >= 3 or player.role == "ammo":
        if player.role != "ammo":
            player.final_shots -= 3
        player.last_tagged_id = base_id
        if base_id == 15:
            player.neutral_base_destroyed = True
        else:
            player.opposing_base_destroyed = True
        player.points_scored += 1001
        if player.role != "heavy":
            player.final_special = min(player.max_special, player.final_special + 5)
        if emit_event is not None:
            emit_event(
                {
                    "event_type": "base_capture",
                    "actor_id": player.player_id,
                    "target_id": None,
                    "timestamp": second,
                    "points_awarded": 1001,
                    "description": f"{player.name} captures base {'neutral' if base_id == 15 else 'opposing'}",
                    "metadata": _build_meta(
                        player,
                        base_id=base_id,
                        shots_remaining=player.final_shots,
                        points_scored=player.points_scored,
                    ),
                }
            )
        return True
    return False


def award_bases(
    player,
    second: float,
    *,
    emit_event=None,
) -> None:
    """Award any uncaptured bases to a surviving player at round end."""
    if player.final_lives <= 0:
        return
    if not player.neutral_base_destroyed:
        player.points_scored += 1001
        player.neutral_base_destroyed = True
        if emit_event is not None:
            emit_event(
                {
                    "event_type": "base_capture",
                    "actor_id": player.player_id,
                    "target_id": None,
                    "timestamp": second,
                    "points_awarded": 1001,
                    "description": f"{player.name} awarded neutral base",
                    "metadata": _build_meta(player, base_id=15),
                }
            )
    if not player.opposing_base_destroyed:
        player.points_scored += 1001
        player.opposing_base_destroyed = True
        if emit_event is not None:
            emit_event(
                {
                    "event_type": "base_capture",
                    "actor_id": player.player_id,
                    "target_id": None,
                    "timestamp": second,
                    "points_awarded": 1001,
                    "description": f"{player.name} awarded opposing base",
                    "metadata": _build_meta(
                        player,
                        base_id=14 if player.team_color == "red" else 13,
                    ),
                }
            )


def start_missile_lock(
    attacker,
    defender,
    second: float,
    movement_ctx: "MapContext | None" = None,
    *,
    emit_event=None,
) -> "PendingMissileLock | None":
    """Initiate a 3-tick missile lock on defender.

    Validates state and requires initial LOS.  Consumes one missile immediately.
    Returns a ``PendingMissileLock`` on success, or ``None`` if the attempt is
    invalid (bad state or no initial LOS).

    The lock is resolved by calling ``tick_missile_lock`` once per simulation
    tick.  The old 45% instant-dodge is replaced by per-tick LOS checks and a
    survival-based dodge roll at the moment of impact.
    """
    if not (
        attacker.is_active_at(second)
        and defender.is_taggable_at(second)
        and attacker.final_missiles > 0
        and not defender.is_hiding
    ):
        return None

    # Require LOS at lock initiation
    if (
        movement_ctx is not None
        and attacker.cell_row is not None
        and defender.cell_row is not None
    ):
        sight_data = movement_ctx.sight_data
        actor_key = f"{attacker.cell_row},{attacker.cell_col}"
        def_key = f"{defender.cell_row},{defender.cell_col}"
        has_los = sight_data is not None and def_key in sight_data.get(
            actor_key, frozenset()
        )
    else:
        has_los = attacker.current_zone == defender.current_zone

    if not has_los:
        return None

    # Consume the missile immediately; if the lock breaks the shot is still used
    attacker.final_missiles -= 1

    return PendingMissileLock(
        attacker=attacker,
        defender=defender,
        ticks_remaining=3,
        los_broken=False,
    )


def tick_missile_lock(
    lock: "PendingMissileLock",
    second: float,
    movement_ctx: "MapContext | None",
) -> str:
    """Advance a missile lock by one tick and return its status.

    Returns one of:
      ``"pending"`` — lock still in progress; keep in the queue.
      ``"hit"``     — all 3 ticks completed with LOS; caller should apply
                      survival-based dodge then call ``_complete_missile``.
      ``"miss"``    — lock failed (LOS broken without special_usage save, or a
                      participant died); missile already consumed, no damage.
    """
    if lock.attacker.final_lives <= 0 or lock.defender.final_lives <= 0:
        return "miss"

    # LOS check for this tick
    if (
        movement_ctx is not None
        and lock.attacker.cell_row is not None
        and lock.defender.cell_row is not None
    ):
        sight_data = movement_ctx.sight_data
        actor_key = f"{lock.attacker.cell_row},{lock.attacker.cell_col}"
        def_key = f"{lock.defender.cell_row},{lock.defender.cell_col}"
        has_los = sight_data is not None and def_key in sight_data.get(
            actor_key, frozenset()
        )
    else:
        has_los = lock.attacker.current_zone == lock.defender.current_zone

    if not has_los:
        # special_usage gives a chance to hold lock through broken LOS
        special_usage = getattr(lock.attacker, "special_usage", 50)
        if random.random() * 100 >= special_usage:
            lock.los_broken = True

    lock.ticks_remaining -= 1

    if lock.los_broken:
        return "miss"
    if lock.ticks_remaining > 0:
        return "pending"
    return "hit"
