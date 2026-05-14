"""Shared tick-engine helpers for ResourceBasedSimulator and BatchSimulator.

Both simulators maintain four pending-event queues.  At each tick every queue
is split into items that are due now and items that are still waiting.  This
drain/split pattern is identical in both simulators; only the resolution logic
differs (DB writes vs in-memory mutation + optional event_log append).

This module extracts the drain/split step into typed helper functions so the
simulators can share the structural logic without duplicating it.

Queue entry types (typed dataclasses from pending_events.py):

    pending_missiles  — PendingMissile(complete_time, attacker, defender)
    pending_nukes     — PendingNuke(complete_time, player)
    pending_reactions — PendingReaction(fire_at, attacker, defender)
    pending_followups — PendingFollowup(fire_at, attacker, defender, chain_depth)
"""

from __future__ import annotations

from .pending_events import (
    PendingMissile,
    PendingNuke,
    PendingFollowup,
    PendingReaction,
)


def drain_missiles(
    pending: list[PendingMissile],
    second: float,
) -> tuple[list[PendingMissile], list[PendingMissile]]:
    """Split pending_missiles into (ready_now, still_pending).

    Each entry is a ``PendingMissile`` with a ``complete_time`` attribute.
    Returns (ready_now, still_pending).
    """
    ready: list[PendingMissile] = []
    still: list[PendingMissile] = []
    for item in pending:
        if item.complete_time <= second:
            ready.append(item)
        else:
            still.append(item)
    return ready, still


def drain_nukes(
    pending: list[PendingNuke],
    second: float,
) -> tuple[list[PendingNuke], list[PendingNuke]]:
    """Split pending_nukes into (ready_now, still_pending).

    Each entry is a ``PendingNuke`` with a ``complete_time`` attribute.
    Returns (ready_now, still_pending).
    """
    ready: list[PendingNuke] = []
    still: list[PendingNuke] = []
    for item in pending:
        if item.complete_time <= second:
            ready.append(item)
        else:
            still.append(item)
    return ready, still


def drain_reactions(
    pending: list[PendingReaction],
    second: float,
) -> tuple[list[PendingReaction], list[PendingReaction]]:
    """Split pending_reactions into (ready_now, still_pending).

    Each entry is a ``PendingReaction`` with a ``fire_at`` attribute.
    Returns (ready_now, still_pending).
    """
    ready: list[PendingReaction] = []
    still: list[PendingReaction] = []
    for item in pending:
        if item.fire_at <= second:
            ready.append(item)
        else:
            still.append(item)
    return ready, still


def drain_followups(
    pending: list[PendingFollowup],
    second: float,
) -> tuple[list[PendingFollowup], list[PendingFollowup]]:
    """Split pending_followups into (ready_now, still_pending).

    Each entry is a ``PendingFollowup`` with a ``fire_at`` attribute.
    Returns (ready_now, still_pending).
    """
    ready: list[PendingFollowup] = []
    still: list[PendingFollowup] = []
    for item in pending:
        if item.fire_at <= second:
            ready.append(item)
        else:
            still.append(item)
    return ready, still
