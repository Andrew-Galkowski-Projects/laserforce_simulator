"""The public Job status vocabulary, mapped from Celery's native task states.

Shared by the batch / save polling endpoints (``matches.api_views``), the
tournament play-all poll and the Season play-run status
(``matches.league_play_services``). Also holds the batch / save poll-body
builders (moved out of ``matches.views`` in ANG-08). Kept free of view imports
so the service layer never has to import a views module.
"""

from celery.result import AsyncResult


def celery_state_to_job_status(state: str) -> str:
    """Map a Celery native state to the public SIM-10 vocabulary."""
    if state == "SUCCESS":
        return "complete"
    if state in ("FAILURE", "REVOKED"):
        return "error"
    # PENDING / STARTED / PROGRESS / RETRY / anything else → keep polling.
    return "running"


def int_or_none(raw: str | None) -> int | None:
    """Coerce a request GET param to int; return None on missing/invalid."""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def build_batch_status_response(
    async_result: AsyncResult,
    *,
    team_red_id: int | None,
    team_blue_id: int | None,
    arena_map_id: int | None,
) -> dict:
    """Build the polling JSON dict for a batch job from an AsyncResult."""
    state = async_result.state
    status = celery_state_to_job_status(state)

    completed = 0
    total = 0
    partial: dict | None = None
    error: str | None = None

    if state == "PROGRESS":
        info = async_result.info or {}
        if isinstance(info, dict):
            completed = int(info.get("completed", 0) or 0)
            total = int(info.get("total", 0) or 0)
            partial = info.get("aggregate")
    elif state == "SUCCESS":
        result = async_result.result or {}
        if isinstance(result, dict):
            n_val = int(result.get("n", 0) or 0)
            completed = n_val
            total = n_val
            partial = result
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)

    return {
        "status": status,
        "completed": completed,
        "total": total,
        "partial": partial,
        "error": error,
        "team_red_id": team_red_id,
        "team_blue_id": team_blue_id,
        "arena_map_id": arena_map_id,
    }


def build_save_status_response(async_result: AsyncResult) -> dict:
    """Build the polling JSON dict for a save job from an AsyncResult."""
    state = async_result.state
    status = celery_state_to_job_status(state)

    round_ids: list[int] = []
    error: str | None = None

    if state == "SUCCESS":
        result = async_result.result or {}
        if isinstance(result, dict):
            round_ids = list(result.get("round_ids", []) or [])
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)

    return {"status": status, "error": error, "round_ids": round_ids}
