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


@dataclass
class PendingMissileLock:
    """A missile lock in progress requiring 3 consecutive ticks of LOS.

    Missile is consumed at lock initiation.  Each tick ``tick_missile_lock``
    checks LOS; if broken without a special_usage save the lock fails and the
    missile misses.  When all 3 ticks succeed a survival-based dodge roll is
    applied before the missile lands.
    """

    attacker: Any
    defender: Any
    ticks_remaining: int = 3
    los_broken: bool = False
