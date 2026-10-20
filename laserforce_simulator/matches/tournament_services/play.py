"""ANG-01f-6 -- playing a sandbox Tournament: play next, play all, Job status.

Shared by the HTML play views and ``/api/tournaments/`` (ADR-0039). A
Tournament has at most one **active play run** (ADR-0040, Tournaments):
"live" means ``Tournament.active_play_job_id`` is non-null. Guard order inside
every play action: ``require_active`` (embedded first), then the run guard
(``release_stale_run`` before refusing), then the work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from celery.result import AsyncResult
from kombu.exceptions import OperationalError

from matches.job_status import celery_state_to_job_status
from matches.models import BracketNode, Tournament
from matches.tasks import play_tournament_task
from matches.tournament_engine import play_next_node
from matches.tournament_services.errors import (
    BrokerUnavailableError,
    NoPlayableNodeError,
    PlayInProgressError,
    require_active,
)
from teams.models import Team

#: Celery states of a finished Job; only these release a stale run.
_TERMINAL_JOB_STATES = frozenset({"SUCCESS", "FAILURE", "REVOKED"})

#: The HTML play-all 503 message (kept verbatim; the API uses it too).
BROKER_UNAVAILABLE_MESSAGE = (
    "Couldn't start the Play All job — the background task queue is "
    "unavailable. Start a Celery worker + broker, or run the server with "
    "LF_CELERY_EAGER=1 for local play."
)


@dataclass(frozen=True)
class PlayedNode:
    """One play-next result: the advanced node, its played Match ids (in game
    order) and the Tournament's state and champion afterwards."""

    node: BracketNode
    match_ids: list[int]
    state: str
    champion: Team | None


def _live_job_id(tournament: Tournament) -> str | None:
    return (
        Tournament.objects.filter(pk=tournament.pk)
        .values_list("active_play_job_id", flat=True)
        .first()
    )


def _require_no_live_run(tournament: Tournament) -> None:
    """Raise ``PlayInProgressError`` while a run is live (after releasing a
    stale one)."""
    if _live_job_id(tournament) is None:
        return
    release_stale_run(tournament)
    job_id = _live_job_id(tournament)
    if job_id is not None:
        raise PlayInProgressError(job_id)


def release_stale_run(tournament: Tournament) -> bool:
    """Release a run whose Job is terminal; ``True`` when the column was cleared.

    Only ``SUCCESS`` / ``FAILURE`` / ``REVOKED`` count: ``PENDING`` is never
    treated as finished (an expired result reads ``PENDING`` forever, and so
    does a queued one). The update is filtered on that exact id so a newer
    run's id is never cleared.
    """
    job_id = _live_job_id(tournament)
    if job_id is None or AsyncResult(job_id).state not in _TERMINAL_JOB_STATES:
        return False
    released = Tournament.objects.filter(
        pk=tournament.pk, active_play_job_id=job_id
    ).update(active_play_job_id=None)
    if released:
        tournament.active_play_job_id = None
    return bool(released)


def enqueue_play_all(tournament: Tournament) -> str:
    """Start ``play_tournament_task`` as the active play run; return its Job id.

    The Job id is made up and written to ``active_play_job_id`` BEFORE
    ``apply_async(task_id=...)`` so a fast or eager task can never clear the
    column ahead of the write. A broker ``OperationalError`` releases the
    claim and raises ``BrokerUnavailableError``.
    """
    require_active(tournament)
    _require_no_live_run(tournament)
    job_id = str(uuid.uuid4())
    claimed = Tournament.objects.filter(
        pk=tournament.pk, active_play_job_id__isnull=True
    ).update(active_play_job_id=job_id)
    if not claimed:
        # Lost a race to another enqueue between the read and the write.
        raise PlayInProgressError(_live_job_id(tournament) or "")
    tournament.active_play_job_id = job_id
    try:
        # retry=False: an unreachable broker fails after one bounded attempt.
        play_tournament_task.apply_async((tournament.pk,), task_id=job_id, retry=False)
    except OperationalError as exc:
        Tournament.objects.filter(pk=tournament.pk, active_play_job_id=job_id).update(
            active_play_job_id=None
        )
        tournament.active_play_job_id = None
        raise BrokerUnavailableError(BROKER_UNAVAILABLE_MESSAGE) from exc
    return job_id


def build_play_status(tournament: Tournament, job_id: str) -> dict:
    """The locked 5-key polling JSON for a play-all Job: ``status``,
    ``completed``, ``total``, ``error``, ``tournament_id``."""
    async_result = AsyncResult(job_id)
    state = async_result.state
    completed = 0
    total = 0
    error: str | None = None
    if state == "PROGRESS":
        info = async_result.info or {}
        if isinstance(info, dict):
            completed = int(info.get("completed", 0) or 0)
            total = int(info.get("total", 0) or 0)
    elif state == "SUCCESS":
        result = async_result.result or {}
        if isinstance(result, dict):
            completed = int(result.get("completed", 0) or 0)
            total = int(result.get("total", 0) or 0)
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)
    return {
        "status": celery_state_to_job_status(state),
        "completed": completed,
        "total": total,
        "error": error,
        "tournament_id": tournament.pk,
    }


def play_next(tournament: Tournament) -> PlayedNode:
    """Play the next ready Match synchronously.

    Raises ``TournamentEmbeddedError`` / ``TournamentNotActiveError`` /
    ``PlayInProgressError``, and ``NoPlayableNodeError`` when nothing is ready.
    """
    require_active(tournament)
    _require_no_live_run(tournament)
    node = play_next_node(tournament)
    if node is None:
        raise NoPlayableNodeError()
    # Re-read: the engine's instance may carry a stale ``series_matches`` cache.
    node = BracketNode.objects.select_related("team_a", "team_b", "winner").get(
        pk=node.pk
    )
    tournament.refresh_from_db()
    match_ids = list(
        node.series_matches.filter(match__isnull=False)
        .order_by("game_number")
        .values_list("match_id", flat=True)
    )
    return PlayedNode(
        node=node,
        match_ids=match_ids,
        state=tournament.state,
        champion=tournament.champion,
    )
