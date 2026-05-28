"""The Down chokepoint — the single life-loss bookkeeping site.

``record_down(player, tick, ctx)`` is what every site that decrements a
player's ``final_lives`` calls *after* the decrement. It owns five
state mutations (last_downed_time, _path_cache, is_holding,
_committed_goal-iff-action-driven, down_chain_count) plus two RV-02
emit rules (medic_reset, nuke_cancelled).

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

from typing import Optional

from .round_context import RoundContext


def record_down(player, tick: int, ctx: Optional[RoundContext]) -> None:
    """Single life-loss chokepoint.

    Behaviours, in evaluation order:
      1. RV-02 medic-reset chain — set or increment
         ``down_chain_count``. ``player.is_active_at(tick)`` True
         (fresh Down / fully recovered) ⇒ chain = 1; False (re-Down
         within the respawn cooldown) ⇒ chain += 1. Medic at chain == 2
         emits one ``medic_reset`` event via ``ctx.events.medic_reset``.
      2. Stamp ``player.last_downed_time = tick``.
      3. Clear ``player._path_cache = None`` (MOVE-02 — knocked off
         committed route).
      4. Clear ``player.is_holding = False`` (MOVE-03 — Down ends
         Overwatch).
      5. Clear ``player._committed_goal = None`` iff the committed
         goal was action-driven (MOVE-04 — positioning goals survive
         a Down).
      6. RV-02 nuke_cancelled — if the player is a Commander, scan
         ``ctx.pending_nukes``; for each nuke whose ``player is`` this
         Commander and ``cancel_logged`` is False, emit one
         ``nuke_cancelled`` event via ``ctx.events.nuke_cancelled`` and
         set ``cancel_logged = True``. The nuke is LEFT in the queue
         (MECH-05 — drain path is structurally unchanged).

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

    # 2-4. simple state mutations
    player.last_downed_time = tick
    player._path_cache = None
    player.is_holding = False

    # 5. _committed_goal clear is conditional on the action-driven flag
    if player._committed_goal is not None and player._committed_goal[1]:
        player._committed_goal = None

    # 6. RV-02 nuke cancellation — only Commanders scan the queue
    if player.role == "commander" and ctx is not None and ctx.pending_nukes:
        for n in ctx.pending_nukes:
            if n.player is player and not n.cancel_logged:
                n.cancel_logged = True
                ctx.events.nuke_cancelled(player, tick)
