"""LG-02a / LG-02b — play-next, the async Play All job and its polling."""

from celery.result import AsyncResult
from django.contrib import messages
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import redirect
from kombu.exceptions import OperationalError

from accounts.permissions import get_owned_or_404

from matches.job_status import celery_state_to_job_status as _celery_state_to_job_status
from matches.models import Tournament
from matches.tasks import play_tournament_task
from matches.tournament_engine import play_next_node


def tournament_play_next(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Find next playable node, sim ONE Match, resolve winner (incl. tie-break),
    Advance, stamp champion if final. Delegates to ``play_next_node``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    if tournament.state != "active":
        messages.error(request, "The tournament is not active.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    node = play_next_node(tournament)
    if node is None:
        messages.error(request, "No playable match is ready.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    return redirect("tournament_detail", tournament_id=tournament.id)


def tournament_play_all(request: HttpRequest, tournament_id: int) -> JsonResponse:
    """Enqueue the async Play Tournament job. POST-only. 202 + job JSON."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    if tournament.state != "active":
        return JsonResponse({"error": "Tournament is not active."}, status=409)

    try:
        # retry=False so an unreachable broker raises OperationalError after a
        # single bounded attempt instead of retry-hanging the request.
        result = play_tournament_task.apply_async((tournament_id,), retry=False)
    except OperationalError:
        # The Celery broker (Redis) is unreachable. Return a clean JSON error
        # (503) instead of a 500 HTML page so the UI can show a clear message
        # rather than a JSON-parse failure on the error page.
        return JsonResponse(
            {
                "error": (
                    "Couldn't start the Play All job — the background task "
                    "queue is unavailable. Start a Celery worker + broker, or "
                    "run the server with LF_CELERY_EAGER=1 for local play."
                )
            },
            status=503,
        )
    return JsonResponse(
        {"job_id": result.id, "tournament_id": tournament.id}, status=202
    )


def _build_tournament_play_status_response(
    async_result: AsyncResult, *, tournament_id: int
) -> dict:
    """Locked 5-key polling JSON for a Play Tournament job."""
    state = async_result.state
    status = _celery_state_to_job_status(state)

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
        "status": status,
        "completed": completed,
        "total": total,
        "error": error,
        "tournament_id": tournament_id,
    }


def tournament_play_status(
    request: HttpRequest, tournament_id: int, job_id: str
) -> JsonResponse:
    """Poll a Play Tournament job. GET-only. Returns the locked 5-key JSON."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    get_owned_or_404(Tournament, request, pk=tournament_id)
    async_result = AsyncResult(job_id)
    return JsonResponse(
        _build_tournament_play_status_response(
            async_result, tournament_id=tournament_id
        )
    )
