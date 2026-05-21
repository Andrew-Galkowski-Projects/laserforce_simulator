"""
Resupply request queue resolution for MECH-01.

Resolves all request_resupply actions for a single tick.
"""

from __future__ import annotations

import random
from typing import Any, Callable, Optional

from .mechanics import shot_cooldown


# ----------------------------------------------------------------------------
# RES-02b — Universal event metadata snapshot helpers.
# Local copies of the helpers in matches/simulation.py to avoid a circular
# import (simulation imports sim_helpers indirectly). Any change to the
# helper key set must be made in all three locations.
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


# Priority order for the queue sort (lower = higher priority)
_ROLE_QUEUE_PRIORITY: dict[str, int] = {
    "heavy": 0,
    "commander": 1,
    "scout": 2,
    "ammo": 3,
    "medic": 4,
}


def _priority_param(player: Any) -> str:
    """Determine which resource the player most needs.

    Returns ``"lives"`` or ``"shots"``.

    - Ammo player → always ``"lives"``
    - Medic player → always ``"shots"``
    - Others → ``"lives"`` if lives ratio <= shots ratio, else ``"shots"``
    """
    if player.role == "ammo":
        return "lives"
    if player.role == "medic":
        return "shots"
    lives_ratio = player.final_lives / player.max_lives if player.max_lives > 0 else 1.0
    shots_ratio = player.final_shots / player.max_shots if player.max_shots > 0 else 1.0
    return "lives" if lives_ratio <= shots_ratio else "shots"


def _queue_priority(player: Any) -> int:
    """Return the queue sort key for a player (lower = higher priority)."""
    return _ROLE_QUEUE_PRIORITY.get(player.role, 99)


def _combo_chance(ammo: Any, medic: Any) -> float:
    """Compute the double-resupply (combo) chance for a given Ammo+Medic pair.

    Formula: min(0.95, 0.20 + (ammo.resupply_synergy/100 * medic.resupply_synergy/100)
                                + (ammo.resupply_efficiency/100 * medic.resupply_efficiency/100))
    """
    rs_m = getattr(medic, "resupply_synergy", 50)
    rs_a = getattr(ammo, "resupply_synergy", 50)
    re_m = getattr(medic, "resupply_efficiency", 50)
    re_a = getattr(ammo, "resupply_efficiency", 50)
    return min(
        0.95,
        0.20 + (rs_a / 100.0 * rs_m / 100.0) + (re_a / 100.0 * re_m / 100.0),
    )


def _is_in_los(requestor: Any, support: Any, movement_ctx: Any) -> bool:
    """Check if support can see requestor (LOS or same-zone fallback)."""
    if movement_ctx is None:
        return requestor.current_zone == support.current_zone
    if requestor.cell_row is None or support.cell_row is None:
        return requestor.current_zone == support.current_zone
    return movement_ctx.can_see(
        (requestor.cell_row, requestor.cell_col),
        (support.cell_row, support.cell_col),
    )


def _is_available(
    support: Any, second: float, movement_ctx: Any, requestor: Any
) -> bool:
    """Return True if a support player is available to resupply this tick."""
    if support.final_lives <= 0:
        return False
    # Not in reset window: last_downed_time < 0 (or None) means never downed;
    # otherwise must be more than 7 seconds ago.
    last_down = getattr(support, "last_downed_time", None)
    if last_down is not None and last_down >= 0:
        if second - last_down <= 7:
            return False
    if support.final_shots <= 0:
        return False
    if not _is_in_los(requestor, support, movement_ctx):
        return False
    cooldown = shot_cooldown(support, second)
    last_shot = getattr(support, "last_shot_time", -99.0)
    if cooldown > 0.0 and (second - last_shot) < cooldown:
        return False
    return True


def _stress_fail(support: Any, prior_request_count: int) -> bool:
    """Roll whether a support player fails under stress.

    Returns True if the support player fails (treat as unavailable).
    """
    dm = getattr(support, "decision_making", 50)
    tw = getattr(support, "teamwork", 50)
    failure_pct = min(100.0, (dm + tw) / 10.0 * prior_request_count)
    return random.random() * 100 < failure_pct


def resolve_resupply_requests(
    requestors: list,
    all_alive: list,
    second: float,
    movement_ctx: Any,
    *,
    emit_event: Optional[Callable[..., None]] = None,
) -> None:
    """Resolve all request_resupply actions for a single tick.

    Parameters
    ----------
    requestors:
        Players who chose ``request_resupply`` this tick, in arrival order.
    all_alive:
        All alive players this tick.
    second:
        Current simulation time.
    movement_ctx:
        MapContext or None (for LOS checks).
    emit_event:
        Optional callable ``(event_type: str, **kwargs)`` for event recording.
        kwargs may include: requestor, target, actor, second, metadata.
    """
    # Local import to avoid circular dependency:
    # resupply_queue → combat → weights (no back-reference at module level).
    from .combat import attempt_resupply

    if not requestors:
        return

    # Stable sort preserves arrival order within same priority tier.
    sorted_queue = sorted(requestors, key=_queue_priority)

    prior_request_count = 0

    for requestor in sorted_queue:
        priority = _priority_param(requestor)

        # Gather available support players for each role (same team as requestor).
        available_medics = [
            p
            for p in all_alive
            if p.role == "medic"
            and p is not requestor
            and p.team_color == requestor.team_color
            and _is_available(p, second, movement_ctx, requestor)
        ]
        available_ammos = [
            p
            for p in all_alive
            if p.role == "ammo"
            and p is not requestor
            and p.team_color == requestor.team_color
            and _is_available(p, second, movement_ctx, requestor)
        ]

        # Stress failure check — each support player rolls independently.
        if prior_request_count > 0:
            available_medics = [
                m for m in available_medics if not _stress_fail(m, prior_request_count)
            ]
            available_ammos = [
                a for a in available_ammos if not _stress_fail(a, prior_request_count)
            ]

        # Attempt combo if both Medic and Ammo are available.
        if available_medics and available_ammos:
            medic = available_medics[0]
            ammo = available_ammos[0]
            chance = _combo_chance(ammo, medic)
            if random.random() < chance:
                # Combo resupply: medic gives lives, ammo gives shots.
                # Save last_downed_time so the second resupply is not blocked by the
                # reset that attempt_resupply applies to last_downed_time.
                saved_downed_time = getattr(requestor, "last_downed_time", None)
                _do_resupply(medic, requestor, second, emit_event, attempt_resupply)
                requestor.last_downed_time = saved_downed_time
                _do_resupply(ammo, requestor, second, emit_event, attempt_resupply)
                requestor.combo_resupply_count += 1
                medic.last_shot_time = second
                ammo.last_shot_time = second
                if emit_event is not None:
                    # RES-02b: combo_resupply now uniformly carries
                    # target_id=requestor.player_id (was None) and the
                    # universal actor/target snapshot blocks. The "actor" is
                    # one of the two supporters; we pick medic by convention
                    # (the other supporter is named via the ammo_tag extra).
                    emit_event(
                        "combo_resupply",
                        actor=medic,
                        target=requestor,
                        requestor=requestor,
                        second=second,
                        metadata=_build_meta(
                            medic,
                            requestor,
                            medic_tag=medic.tag_id_key,
                            ammo_tag=ammo.tag_id_key,
                        ),
                    )
                prior_request_count += 1
                continue

        # Single fallback: only one support available, or combo roll failed.
        if available_medics and available_ammos:
            # Combo failed — pick based on priority_param with 75%/25% split.
            if random.random() < 0.75:
                chosen_role = priority  # "lives" → medic, "shots" → ammo
            else:
                chosen_role = "shots" if priority == "lives" else "lives"
            support = (
                available_medics[0] if chosen_role == "lives" else available_ammos[0]
            )
        elif available_medics:
            support = available_medics[0]
        elif available_ammos:
            support = available_ammos[0]
        else:
            # No support available — skip.
            prior_request_count += 1
            continue

        _do_resupply(support, requestor, second, emit_event, attempt_resupply)
        support.last_shot_time = second
        prior_request_count += 1


def _do_resupply(
    support: Any,
    requestor: Any,
    second: float,
    emit_event: Optional[Callable[..., None]],
    attempt_resupply_fn: Callable[..., None],
) -> None:
    """Call attempt_resupply, bridging from its dict-style emit_event to kwargs-style.

    Protocol contract: ``attempt_resupply`` calls ``emit_event(event_dict: dict)``
    where event_dict has keys ``event_type``, ``actor_id``, ``target_id``, etc.
    This adapter converts that to the kwargs-style ``emit_event(etype, **kwargs)``
    used by the outer simulators.  If attempt_resupply's emit_event dict structure
    changes, update the adapter below to match.
    """
    if emit_event is None:
        attempt_resupply_fn(support, requestor, second, emit_event=None)
        return

    def _adapter(event_dict: dict) -> None:
        etype = event_dict.get("event_type", "")
        emit_event(
            etype,
            requestor=requestor,
            target=requestor,
            actor=support,
            second=second,
            metadata=event_dict.get("metadata", {}),
        )

    attempt_resupply_fn(support, requestor, second, emit_event=_adapter)
