"""Typed dataclasses for the pending-event queues used by both simulators.

Both ``ResourceBasedSimulator`` and ``BatchSimulator`` maintain lists of
scheduled future actions.  Historically these were raw tuples whose fields were
accessed by position, which made adding new fields error-prone.

These dataclasses give each field a name so consumers can access
``item.complete_time`` instead of ``item[0]``, and so that new fields (such as
a nuke-ID for cancellation tracking) can be added in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .time_constants import LOCK_TICKS


@dataclass
class PendingNuke:
    """A commander nuke that will detonate at *complete_time*.

    Matches ``(complete_time, player_state)`` tuples previously used by both
    simulators.
    """

    complete_time: float
    player: Any
    # RV-02: set True once a nuke_cancelled GameEvent has been logged for this
    # nuke (at the firing Commander's down/disarm tick) so the drain-time
    # fallback does not emit a duplicate. The nuke is LEFT in pending_nukes so
    # _apply_nuke_reaction_flags / drain_nukes reads are unchanged (no seeded
    # drift). Default False keeps PendingNuke(complete_time=..., player=...)
    # construction unaffected.
    cancel_logged: bool = False


@dataclass
class PendingFollowup:
    """A deferred follow-up shot scheduled to fire at *fire_at*.

    Matches ``(fire_at, attacker, defender, chain_depth)`` tuples previously
    used by both simulators.  *chain_depth* starts at 1 and is capped at 2.
    """

    fire_at: float
    attacker: Any
    defender: Any
    chain_depth: int


@dataclass
class PendingReaction:
    """A deferred reaction shot scheduled to fire at *fire_at*.

    Matches ``(fire_at, attacker, defender)`` tuples previously used by both
    simulators.
    """

    fire_at: float
    attacker: Any
    defender: Any


#: MECH-21: why a Lock ended without a missile, the ``lock_cancelled`` row's
#: ``metadata.reason``.
LOCK_CANCEL_REASONS: tuple[str, ...] = (
    "sight",
    "dodge",
    "shot",
    "retarget",
    "down",
    "out",
)


@dataclass
class PendingMissileLock:
    """A Lock in progress (MECH-21, ADR-0050): nothing is spent yet.

    ``tick_missile_lock`` counts it down once a tick; at 0 it launches, and a
    failed ``special_usage`` save with no sight ends it on that tick. The
    round's locks live in ``RoundContext.missile_locks``, one per locker.
    """

    attacker: Any
    defender: Any
    ticks_remaining: int = LOCK_TICKS
