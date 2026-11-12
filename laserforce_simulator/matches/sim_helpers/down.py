"""The Down chokepoint — the single life-loss bookkeeping site.

``record_down(player, tick, ctx)`` is what every site that decrements a
player's ``final_lives`` calls *after* the decrement. It owns six
state mutations (last_downed_time, _path_cache, is_holding,
_committed_goal-iff-action-driven, down_chain_count, a Commander's
arming ``special_active_until``) plus two RV-02 emit rules
(medic_reset, nuke_cancelled).

Lifted from ``BatchSimulator._record_down`` by the shot-resolver
consolidation; the EventLog candidate replaced the per-call
``ctx.event_log`` list-and-dict pattern with the ``ctx.events.*`` verb
calls (single source of truth for the GameEvent-dict shape, owned by
``sim_helpers.event_log.EventLog``). Pure function — no ``self``, no
Django imports.

Callers (3): ``sim_helpers.shot.resolve_shot`` (the primary site —
every shot-driven life loss), ``BatchSimulator._complete_missile``,
``BatchSimulator._complete_nuke``. Does NOT mutate ``final_lives``
or ``shields`` — those differ per call site and are the caller's job.
"""

from __future__ import annotations

from typing import Any, Optional

from .combat import cancel_missile_locks_involving
from .downed_option import choose_downed_option
from .follow_rule import round_map, start_trail
from . import resupply_visit
from .round_context import RoundContext
from .time_constants import RESPAWN_TICKS


def record_down(
    player, tick: int, ctx: Optional[RoundContext], *, tagger: Any = None
) -> None:
    """Single life-loss chokepoint.

    ``tagger`` (MOVE-08) is who caused the Down: the shooter of a Tag or a
    Missile, the Commander of a Nuke. It decides whether Make a trail is
    allowed (``downed_option.choose_downed_option``).

    Behaviours, in evaluation order:
      1. RV-02 medic-reset chain — set or increment
         ``down_chain_count``. ``player.is_active_at(tick)`` True
         (fresh Down / fully recovered) ⇒ chain = 1; False (re-Down
         within the respawn cooldown) ⇒ chain += 1. Medic at chain == 2
         emits one ``medic_reset`` event via ``ctx.events.medic_reset``.
      2. Stamp ``player.last_downed_time = tick``.
      3. Clear ``player._path_cache = None`` (MOVE-02 — knocked off
         committed route).
      4. Clear ``player.is_holding = False`` and ``player.hold_until``
         (MOVE-03, MECH-17 — Down ends Overwatch).
      5. Clear ``player._committed_goal = None`` iff the committed
         goal was action-driven (MOVE-04 — positioning goals survive
         a Down).
      6. Zero a Commander's ``special_active_until`` while its fuse runs
         (CAL #558, 2026-11-07 — every Down, a missile's included, ends
         the nuke; the drain then skips it).
      7. RV-02 nuke_cancelled — if the player is a Commander, scan
         ``ctx.pending_nukes``; for each nuke whose ``player is`` this
         Commander and ``cancel_logged`` is False, emit one
         ``nuke_cancelled`` event via ``ctx.events.nuke_cancelled``, set
         ``cancel_logged = True`` and count it on
         ``player.counters.own_specials_cancelled`` (LFS-01a). The nuke
         is LEFT in the queue (MECH-05 — drain path is structurally
         unchanged).

    The function does NOT mutate ``final_lives`` or ``shields``.

    ``ctx`` is typed ``Optional[RoundContext]`` for compatibility with
    legacy/test callsites that exercise the resolver without a
    per-round context (mirroring the pre-refactor ``getattr(self,
    "_event_log", None)`` defensiveness). When ``ctx is None`` the
    state mutations still happen; only the RV-02 verb emits and the
    nuke scan are skipped.
    """
    # 1. medic-reset chain counter (must precede the last_downed_time
    # stamp, which would otherwise flip is_active_at False).
    if player.is_active_at(tick):
        player.down_chain_count = 1
    else:
        player.down_chain_count += 1
    if player.role == "medic" and player.down_chain_count == 2 and ctx is not None:
        ctx.events.medic_reset(player, tick)

    # MECH-10: start (or, before Active, extend) the Follow rule Trail.
    # Must precede the last_downed_time stamp, like step 1.
    start_trail(player, tick, round_map(ctx))

    # MOVE-08: a Down clears the pre-Down hide, then rolls the Downed option
    # (which may set it again: Stay hidden). A Reset-window Down rolls afresh.
    player.is_hiding = False
    choose_downed_option(player, tagger, tick, ctx)

    # MECH-10: resupply death % counts
    player.downs_taken += 1
    resupplied_at = player.last_resupplied_at
    if resupplied_at is not None and tick - resupplied_at < RESPAWN_TICKS:
        player.downs_in_resupply_cooldown += 1

    # CAL-03 rule 6: an Elimination ends a Resupply visit. A Down does not: the
    # visit goes on through the Respawn cooldown, as in the R7 prototype (#588).
    # Called through the module so ``round_probe`` sees the reason (CAL-04a).
    if player.final_lives <= 0 and getattr(player, "visit", None) is not None:
        resupply_visit.end_visit(player, tick, "eliminated")

    # 2-4. simple state mutations
    player.last_downed_time = tick
    player._path_cache = None
    player.is_holding = False
    player.hold_until = None

    # 5. _committed_goal clear is conditional on the action-driven flag
    if player._committed_goal is not None and player._committed_goal[1]:
        player._committed_goal = None

    # 6. A Down ends a Commander's arming nuke (the arena's tag-cancel; the drain
    # reads ``special_active_until >= complete_time``). The shot path and an enemy
    # detonation cleared it themselves; a missile Down did not, so that nuke still
    # detonated, 1 to 3 a round (CAL #558, 2026-11-07). A Scout's Rapid Fire is
    # not a fuse and survives.
    if player.role == "commander" and player.special_active_until > tick:
        player.special_active_until = 0

    # MECH-21: a Down ends the player's own Lock and every Lock on them, at
    # the Down tick ("out" once eliminated, else "down"). The caller has
    # already taken the lives.
    if ctx is not None:
        cancel_missile_locks_involving(ctx, player, tick)

    # 7. RV-02 nuke cancellation — only Commanders scan the queue
    if player.role == "commander" and ctx is not None and ctx.pending_nukes:
        for n in ctx.pending_nukes:
            if n.player is player and not n.cancel_logged:
                n.cancel_logged = True
                player.counters.own_specials_cancelled += 1
                ctx.events.nuke_cancelled(player, tick)
