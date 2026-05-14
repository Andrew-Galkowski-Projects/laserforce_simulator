"""Typed dataclasses for the four pending-event queues used by both simulators.

Both ``ResourceBasedSimulator`` and ``BatchSimulator`` maintain four lists of
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
class PendingMissile:
    """A missile lock that has been initiated and will complete at *complete_time*.

    Matches ``(complete_time, attacker, defender)`` tuples previously used by
    both simulators.
    """

    complete_time: float
    attacker: Any
    defender: Any


@dataclass
class PendingNuke:
    """A commander nuke that will detonate at *complete_time*.

    Matches ``(complete_time, player_state)`` tuples previously used by both
    simulators.
    """

    complete_time: float
    player: Any


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
