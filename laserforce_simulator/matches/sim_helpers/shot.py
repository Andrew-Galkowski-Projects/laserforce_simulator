"""The wide-Shot resolver — the single Shot → Hit → Tag → Down → Elimination
ladder consumed by all four call-site ``kind`` s (initial / follow_up /
reaction / overwatch).

Replaces the five inline copies of shot-resolution logic that previously
lived in ``BatchSimulator._resolve_tag_attempts`` (3 branches: initial,
immediate-reaction, immediate-follow-up) and ``_simulate_round`` (2
branches: queued ``due_rx``, queued ``due_fu``). Each call site now
dispatches one line to ``resolve_shot``; the immediate-vs-deferred
distinction is handled by the resolver via shot-cooldown gating.

Pinned by the seam contract at
``.claude/worktrees/shot-resolver-seam-contract.md`` §1 (the 10-phase
spec). Two behaviour changes are deliberate and fold into the
already-pending post-MOVE-01 Score Calibration re-baseline:

  - **Uniform hide-50%-miss roll** across all four ``kind`` s
    (pre-refactor only ``SHOT_KIND_INITIAL`` rolled it).
  - **Uniform Ammo non-decrement of ``final_shots``** across all four
    ``kind`` s + the ``miss_hid`` branch (pre-refactor the initial-tag
    hit/miss branches decremented even for Ammo).

Per-attempt one-pass interleaving means seeded games **differ** from
pre-refactor; the internal SIM-07/SIM-08 contract (same seed +
Orientation + rosters + map ⇒ identical game, serial == parallel,
faithful Replay) holds in form.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .combat import _elevation_hit_modifier
from .down import record_down
from .mechanics import shot_cooldown
from .pending_events import PendingFollowup, PendingReaction
from .round_context import RoundContext
from .time_constants import TICK_SECONDS

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

SHOT_KIND_INITIAL: str = "initial"
SHOT_KIND_FOLLOW_UP: str = "follow_up"
SHOT_KIND_REACTION: str = "reaction"
SHOT_KIND_OVERWATCH: str = "overwatch"

_VALID_KINDS = frozenset(
    {
        SHOT_KIND_INITIAL,
        SHOT_KIND_FOLLOW_UP,
        SHOT_KIND_REACTION,
        SHOT_KIND_OVERWATCH,
    }
)

#: Maximum follow-up chain depth. A ``SHOT_KIND_FOLLOW_UP`` at
#: ``chain_depth == _MAX_CHAIN_DEPTH`` will NOT schedule another follow-up
#: regardless of the player_awareness roll.
_MAX_CHAIN_DEPTH: int = 2


@dataclass(frozen=True)
class ShotOutcome:
    """Per-shot summary returned by ``resolve_shot``.

    ``invalid`` / ``miss_hid`` / plain ``miss`` all surface as
    ``hit=False, downed=False, eliminated=False``. Tests pin on the
    fields directly; production callers ignore the return value.
    """

    hit: bool
    downed: bool
    eliminated: bool


# ---------------------------------------------------------------------------
# Kind → elimination_action translation. The EventLog candidate moved the
# metadata builders (_actor_meta / _target_meta / _build_meta) and the kind
# flag translation (_kind_extras) into sim_helpers/event_log.py — they are
# the single owner of the GameEvent-dict shape now. Only this shot-resolver-
# specific kind → elimination_action mapping stays here: it's a domain
# concept of the Shot module, not the EventLog (the EventLog takes a
# pre-translated ``action=...`` string).
# ---------------------------------------------------------------------------


def _elimination_action(kind: str) -> str:
    """Translate SHOT_KIND_* to the elimination_action string the
    EventLog ``elimination`` verb expects.

    Per the shot-resolver seam contract: OVERWATCH maps to ``"tag"``
    (not ``"overwatch"``); only the initiating overwatch shot is
    flagged on tag/miss events, not on its elimination row.
    """
    if kind == SHOT_KIND_FOLLOW_UP:
        return "follow_up_tag"
    if kind == SHOT_KIND_REACTION:
        return "reaction"
    return "tag"  # INITIAL and OVERWATCH both map here


# ---------------------------------------------------------------------------
# Cooldown -> tick conversion
# ---------------------------------------------------------------------------


def _cooldown_ticks(player, tick: int) -> int:
    """TIME-01: round a seconds cooldown to whole ticks.

    Mirrors the ``simulation._cooldown_ticks`` helper byte-for-byte.
    """
    return int(round(shot_cooldown(player, tick) / TICK_SECONDS))


# ---------------------------------------------------------------------------
# Scheduling helpers (phases 9 & 10)
# ---------------------------------------------------------------------------


def _maybe_schedule_reaction(attacker, defender, tick: int, ctx: RoundContext) -> None:
    """Phase 9. Defender rolls player_awareness; on success, react.

    Eligibility (matches the pre-refactor immediate-tag reaction block):
    defender alive, attacker alive, defender active, defender has shots
    (or is Ammo). Roll is ``defender.player_awareness >=
    random.randint(0, 100)``.

    If the cooldown rounds to 0 ticks, dispatch immediately via a
    recursive ``resolve_shot`` call with ``kind=SHOT_KIND_REACTION``.
    Otherwise, defer to ``ctx.pending_reactions``.
    """
    if defender.final_lives <= 0 or attacker.final_lives <= 0:
        return
    if not defender.is_active_at(tick):
        return
    if defender.final_shots <= 0 and defender.role != "ammo":
        return
    if defender.player_awareness < random.randint(0, 100):
        return
    cd_ticks = _cooldown_ticks(defender, tick)
    if cd_ticks == 0:
        resolve_shot(defender, attacker, tick, kind=SHOT_KIND_REACTION, ctx=ctx)
    else:
        ctx.pending_reactions.append(
            PendingReaction(tick + cd_ticks, defender, attacker)
        )


def _maybe_schedule_followup(
    attacker, defender, tick: int, ctx: RoundContext, next_chain: int
) -> None:
    """Phase 10. Defender rolls player_awareness; on FAILURE, chain.

    Eligibility (matches the pre-refactor immediate-tag follow-up
    block): defender alive, attacker has shots (or is Ammo). Roll is
    ``defender.player_awareness < random.randint(0, 100)`` —
    contrastive with the reaction roll (low defender awareness ⇒ more
    likely to be follow-up-able).

    If the cooldown rounds to 0 ticks (rapid-fire scout), dispatch
    immediately via a recursive ``resolve_shot`` call with
    ``kind=SHOT_KIND_FOLLOW_UP, chain_depth=next_chain``. Otherwise,
    defer to ``ctx.pending_followups``.
    """
    if defender.final_lives <= 0:
        return
    if attacker.final_shots <= 0 and attacker.role != "ammo":
        return
    if defender.player_awareness >= random.randint(0, 100):
        return  # defender saw it coming — no follow-up
    cd_ticks = _cooldown_ticks(attacker, tick)
    if cd_ticks == 0:
        resolve_shot(
            attacker,
            defender,
            tick,
            kind=SHOT_KIND_FOLLOW_UP,
            ctx=ctx,
            chain_depth=next_chain,
        )
    else:
        ctx.pending_followups.append(
            PendingFollowup(tick + cd_ticks, attacker, defender, next_chain)
        )


# ---------------------------------------------------------------------------
# Public resolver — the 10-phase wide Shot
# ---------------------------------------------------------------------------


def resolve_shot(
    attacker,
    defender,
    tick: int,
    *,
    kind: str,
    ctx: RoundContext,
    chain_depth: int = 0,
) -> ShotOutcome:
    """Resolve one Shot end-to-end. See the module docstring and the
    seam contract for the 10-phase spec.

    Returns ``ShotOutcome(hit, downed, eliminated)``. Invalid /
    miss_hid / miss all return ``(False, False, False)``.
    """
    assert kind in _VALID_KINDS, f"unknown shot kind {kind!r}"

    # ── Phase 1. Validity gate ─────────────────────────────────────────
    if attacker.final_shots <= 0 and attacker.role != "ammo":
        return ShotOutcome(False, False, False)
    if defender.final_lives <= 0:
        return ShotOutcome(False, False, False)

    # ── Phase 2. Hide-50%-miss roll (uniform across all kinds) ────────
    if defender.is_hiding and random.random() > 0.5:
        if attacker.role != "ammo":
            attacker.final_shots = max(0, attacker.final_shots - 1)
        attacker.shots_missed += 1
        attacker.last_shot_time = tick
        ctx.events.miss(
            attacker,
            defender,
            tick,
            kind=kind,
            chain_depth=chain_depth,
            reason="hiding",
        )
        return ShotOutcome(False, False, False)

    # ── Phase 3. Hit roll ─────────────────────────────────────────────
    elev_mod = _elevation_hit_modifier(
        attacker.cell_row,
        attacker.cell_col,
        defender.cell_row,
        defender.cell_col,
        ctx.movement_ctx,
    )
    hit_chance = max(
        10,
        min(
            95,
            int(
                (70 + attacker.accuracy - defender.survival)
                * elev_mod
                * attacker.stamina_hit_modifier
            ),
        ),
    )
    hit = random.randint(1, 100) < hit_chance

    # ── Phase 4. Kind-specific counter ───────────────────────────────
    if kind == SHOT_KIND_FOLLOW_UP:
        attacker.follow_up_shots += 1
    elif kind == SHOT_KIND_REACTION:
        attacker.reaction_shots += 1

    # ── Phase 5. Decrement final_shots (uniform Ammo non-decrement) ──
    if attacker.role != "ammo":
        attacker.final_shots = max(0, attacker.final_shots - 1)

    # ── Phase 6. Stamp last_shot_time ────────────────────────────────
    attacker.last_shot_time = tick

    downed = False
    eliminated = False

    if hit:
        # ── Phase 7. Hit cascade ─────────────────────────────────────
        attacker.tags_made += 1
        if defender.role == "medic":
            attacker.medic_hits += 1
        if attacker.role != "heavy":
            attacker.final_special = min(
                attacker.max_special, attacker.final_special + 1
            )
        attacker.points_scored += 100
        attacker.last_tagged_id = defender.tag_id
        defender.times_tagged += 1
        defender.points_scored -= 20
        if not defender.is_active_at(tick) and defender.is_taggable_at(tick):
            defender.times_tagged_in_reset_window += 1
        defender.shields = max(0, defender.shields - attacker.shot_power)
        downed = defender.shields == 0
        if downed:
            if defender.role == "commander" and defender.special_active_until > tick:
                defender.special_active_until = 0
            defender.final_lives = max(0, defender.final_lives - 1)
            record_down(defender, tick, ctx)
            defender.shields = defender.max_shields
            eliminated = defender.final_lives <= 0
            if eliminated:
                defender.was_eliminated_at = tick
                ctx.events.elimination(
                    attacker, defender, tick, action=_elimination_action(kind)
                )
        ctx.events.tag(attacker, defender, tick, kind=kind, chain_depth=chain_depth)

        # MECH-06 side effects: medic-under-fire alert + memory update +
        # broadcast. Lazy-imported from simulation.py to keep shot.py
        # Django-free at module load and to avoid a circular import once
        # step 5 wires simulation.py to import resolve_shot. The
        # EventLog deepening candidate (#2) will consolidate these into
        # sim_helpers proper.
        if defender.role == "medic" and ctx.all_alive:
            from matches.simulation import _check_medic_under_fire

            _check_medic_under_fire(defender, ctx.all_alive, tick)
        if ctx.movement_ctx is not None and ctx.all_alive:
            from matches.simulation import (
                _broadcast_communication,
                _update_player_memory,
            )

            _update_player_memory(attacker, [defender], tick)
            _broadcast_communication(attacker, ctx.all_alive, ctx.movement_ctx, tick)
    else:
        # ── Phase 8. Miss ────────────────────────────────────────────
        attacker.shots_missed += 1
        ctx.events.miss(attacker, defender, tick, kind=kind, chain_depth=chain_depth)

    # ── Phase 9. Schedule reaction (skipped when kind == REACTION;
    #            also skipped on FOLLOW_UP — the pre-refactor follow-up
    #            blocks did not provoke victim reactions). INITIAL and
    #            OVERWATCH both invite a reaction. ──
    if kind in (SHOT_KIND_INITIAL, SHOT_KIND_OVERWATCH):
        _maybe_schedule_reaction(attacker, defender, tick, ctx)

    # ── Phase 10. Schedule follow-up (only on a non-downing hit; never
    #             after a REACTION; chain capped at _MAX_CHAIN_DEPTH). ──
    if (
        hit
        and not downed
        and kind != SHOT_KIND_REACTION
        and chain_depth < _MAX_CHAIN_DEPTH
    ):
        _maybe_schedule_followup(attacker, defender, tick, ctx, chain_depth + 1)

    return ShotOutcome(hit=hit, downed=downed, eliminated=eliminated)
