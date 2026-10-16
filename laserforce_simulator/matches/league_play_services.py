"""ANG-01d-2 -- League play-loop logic shared by the HTML views and ``/api/``.

A Season has at most one **Active play run** (ADR-0040): "live" means
``Season.active_play_job_id`` is non-null. ``enqueue_play_run`` is the only
enqueue path for Season play tasks; it writes the job id BEFORE the task is
enqueued, and each task clears the column only while it still holds its own id.

Must not import ``matches.league_views`` (the views import this module).
Errors are plain ``Exception`` subclasses; the HTML views and the API map them
to their own response shapes.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery.result import AsyncResult

from matches.models import Season
from matches.views import _celery_state_to_job_status

# ---------------------------------------------------------------------------
# Domain errors
# ---------------------------------------------------------------------------


class PlayInProgressError(Exception):
    """The Season already has an Active play run (``.job_id``)."""

    def __init__(self, job_id: str) -> None:
        super().__init__("The Season already has a play run in progress.")
        self.job_id = job_id


class SeasonNotActiveError(Exception):
    """A play action on a Season that is not ``active``."""


class SeasonNotDraftError(Exception):
    """Start Season on a Season that is not a draft."""


class WrongPhaseError(Exception):
    """The play action does not apply to the Season's current phase."""


class NoMemberNightGamesError(Exception):
    """The member-night phase has no unplayed game."""


class NoLiveCursorError(Exception):
    """There is no game for the manager's team to play live."""


# ---------------------------------------------------------------------------
# Active play run
# ---------------------------------------------------------------------------


def enqueue_play_run(season: Season, task: Any, **task_kwargs: Any) -> str:
    """Start ``task`` as the Season's Active play run; return its job id.

    Raises ``PlayInProgressError`` when a run is already live. The job id is
    written (and the cancel flag cleared) BEFORE ``apply_async`` so a fast or
    eager task can never clear the column ahead of the write.
    """
    current = (
        Season.objects.filter(pk=season.pk)
        .values_list("active_play_job_id", flat=True)
        .first()
    )
    if current:
        raise PlayInProgressError(current)
    job_id = str(uuid.uuid4())
    claimed = Season.objects.filter(
        pk=season.pk, active_play_job_id__isnull=True
    ).update(active_play_job_id=job_id, play_cancel_requested=False)
    if not claimed:
        # Lost a race to another enqueue between the read and the write.
        winner = (
            Season.objects.filter(pk=season.pk)
            .values_list("active_play_job_id", flat=True)
            .first()
        )
        raise PlayInProgressError(winner or "")
    season.active_play_job_id = job_id
    season.play_cancel_requested = False
    try:
        task.apply_async(kwargs={"season_id": season.pk, **task_kwargs}, task_id=job_id)
    except Exception:
        # The task never ran (or failed eagerly): release the run if we hold it.
        Season.objects.filter(pk=season.pk, active_play_job_id=job_id).update(
            active_play_job_id=None
        )
        raise
    return job_id


def request_play_cancel(season: Season) -> bool:
    """Ask the Active play run to stop; ``False`` (and no write) when idle."""
    updated = Season.objects.filter(
        pk=season.pk, active_play_job_id__isnull=False
    ).update(play_cancel_requested=True)
    if updated:
        season.play_cancel_requested = True
    return bool(updated)


def _job_status_fields(async_result: AsyncResult) -> dict:
    """``status`` / ``completed`` / ``total`` / ``cancelled`` / ``error`` for a
    Season play Job, mapped from Celery as the LG-01d poll does."""
    state = async_result.state
    completed = 0
    total = 0
    cancelled = False
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
            cancelled = bool(result.get("cancelled", False))
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)
    return {
        "status": _celery_state_to_job_status(state),
        "completed": completed,
        "total": total,
        "cancelled": cancelled,
        "error": error,
    }


def build_play_job_status(job_id: str) -> dict:
    """Structured progress for a Season play Job (no HTML fragments)."""
    return _job_status_fields(AsyncResult(job_id))


# ---------------------------------------------------------------------------
# Async play actions (two months / until end / playoffs)
# ---------------------------------------------------------------------------


def _require_active(season: Season) -> None:
    if season.state != "active":
        raise SeasonNotActiveError(
            f"Season must be active to play; got state={season.state!r}"
        )


def play_season_run(season: Season, *, max_matchdays: int | None) -> str:
    """Start a ``play_season_task`` run (two months = 8, until end = None)."""
    from matches.tasks import play_season_task

    _require_active(season)
    return enqueue_play_run(season, play_season_task, max_matchdays=max_matchdays)


def play_playoffs_run(season: Season) -> str:
    """Start a ``play_playoffs_task`` run over the built tournament phase."""
    from matches.tasks import play_playoffs_task

    _require_active(season)
    # CONF-04 -- build the Worlds bracket BEFORE the cursor read (ADR-0037).
    season.build_pending_worlds_bracket()
    phase = season.current_phase()
    if (
        phase is None
        or phase.phase_type != "tournament"
        or not season.tournaments_for_phase(phase)
    ):
        raise WrongPhaseError("No active playoff bracket to play.")
    return enqueue_play_run(season, play_playoffs_task)
