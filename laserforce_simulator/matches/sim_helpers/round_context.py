"""Per-round mutable state bundle for the simulator tick loop.

``RoundContext`` collects the six per-round mutable references that the
shot resolver, the Down chokepoint, and the queue-drain branches need.
It replaces the RV-02 static->instance stash
(``BatchSimulator._event_log`` / ``BatchSimulator._pending_nukes``) and
the per-call kwarg sprawl that would otherwise infect every sim_helpers
function.

Pure Python, zero imports (mirrors ``time_constants.py`` discipline) so
sim_helpers stays Django-free and the dataclass can be constructed by
any caller without paying an import cost. Pinned by the seam contract
at ``.claude/worktrees/shot-resolver-seam-contract.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class RoundContext:
    """The six per-round mutable references the tick loop threads through.

    Built once in ``BatchSimulator._simulate_round`` immediately after the
    four pending-event queues are initialised; passed to
    ``sim_helpers.down.record_down`` and
    ``sim_helpers.shot.resolve_shot`` (the wide-Shot resolver).

    Fields:
        event_log: per-round event-dict buffer; ``None`` on the batch
            path where no persistence is requested. ``resolve_shot``
            appends ``tag`` / ``miss`` / ``elimination`` rows here when
            non-None; ``record_down`` appends ``medic_reset`` /
            ``nuke_cancelled`` rows here when non-None.
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

    event_log: Optional[list]
    pending_nukes: list
    pending_followups: list
    pending_reactions: list
    all_alive: list
    movement_ctx: Any  # MapContext | None — typed as Any to avoid the import
