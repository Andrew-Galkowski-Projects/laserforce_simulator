"""Per-round mutable state bundle for the simulator tick loop.

``RoundContext`` collects the six per-round mutable references that the
shot resolver, the Down chokepoint, and the queue-drain branches need.
It replaces the RV-02 static->instance stash
(``BatchSimulator._event_log`` / ``BatchSimulator._pending_nukes``) and
the per-call kwarg sprawl that would otherwise infect every sim_helpers
function.

Pure Python, no Django imports. Pinned by the seam contracts at
``.claude/worktrees/shot-resolver-seam-contract.md`` (original) and
``.claude/worktrees/event-log-seam-contract.md`` (which renamed the
``event_log: Optional[list]`` field to ``events: EventLog`` and
introduced the null-object EventLog pattern).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .event_log import EventLog


@dataclass
class RoundContext:
    """The six per-round mutable references the tick loop threads through.

    Built once in ``BatchSimulator._simulate_round`` immediately after the
    four pending-event queues are initialised; passed to
    ``sim_helpers.down.record_down`` and
    ``sim_helpers.shot.resolve_shot`` (the wide-Shot resolver).

    Fields:
        events: ``EventLog`` — single source of truth for the
            ``GameEvent``-dict shape. Always non-None (null-object
            pattern: ``EventLog(persist=False)`` is the no-op variant
            used on the batch path). ``resolve_shot`` and
            ``record_down`` call its per-event-type verbs
            (``events.tag(...)``, ``events.medic_reset(...)``, etc.)
            instead of constructing dict literals.
            Default is ``EventLog(persist=False)`` so test factories
            can construct ``RoundContext`` without explicitly passing
            ``events`` when they don't care about emits; tests that
            want emits build ``EventLog(persist=True)`` explicitly.
        pending_nukes: ``list[PendingNuke]`` — the live nuke queue.
            Read by ``record_down`` for the RV-02 nuke-cancellation
            emit; written by the use-special action dispatch.
        pending_followups: ``list[PendingFollowup]`` — written by
            ``resolve_shot`` when a hit chains a deferred follow-up.
        pending_reactions: ``list[PendingReaction]`` — written by
            ``resolve_shot`` when a tag/miss provokes a deferred
            reaction.
        all_alive: live ``PlayerState`` list this tick. Consumed by the
            medic-under-fire alert and the memory-broadcast side
            effects on a hit.
        movement_ctx: ``MapContext | None``. ``resolve_shot`` reads
            elevation, line-of-sight, and base-in-range gates via this;
            ``None`` on the 3-zone fallback path.
    """

    events: EventLog = field(default_factory=lambda: EventLog(persist=False))
    pending_nukes: list = field(default_factory=list)
    pending_followups: list = field(default_factory=list)
    pending_reactions: list = field(default_factory=list)
    all_alive: list = field(default_factory=list)
    movement_ctx: Any = None  # MapContext | None — typed as Any to avoid the import
