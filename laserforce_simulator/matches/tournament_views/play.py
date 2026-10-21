"""LG-02a / LG-02b / ANG-01f-6 -- play-next, the async Play All job and its
polling, on the shared ``tournament_services.play`` service.

Both play actions share the API's guards (embedded, not active, one active
play run per Tournament -- ADR-0040). Play-next flashes and redirects; play-all
keeps its 202 ``{job_id, tournament_id}`` and answers a guard with a 409 JSON
``{"error"}`` (503 when the broker is down).
"""

from django.contrib import messages
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import redirect

from accounts.permissions import get_owned_or_404

from matches.models import Tournament, count_series_wins
from matches.tournament_services.errors import (
    BrokerUnavailableError,
    NoPlayableNodeError,
    PlayInProgressError,
    TournamentEmbeddedError,
    TournamentNotActiveError,
)
from matches.tournament_services.play import (
    PlayedNode,
    build_play_status,
    enqueue_play_all,
    play_next,
)

# UXP-13 -- the bracket section a played node is named after; the winners
# bracket, a round-robin and a Swiss round are just "Round N".
_SECTION_PREFIX = {"losers": "Losers "}

_PLAY_GUARD_ERRORS = (
    TournamentEmbeddedError,
    TournamentNotActiveError,
    PlayInProgressError,
)


def tournament_play_next(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Play the next ready Match (``play_next``); a guard or an empty bracket
    is flashed. Always redirects to the detail page."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament: Tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    try:
        played = play_next(tournament)
    except (*_PLAY_GUARD_ERRORS, NoPlayableNodeError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _played_message(played))
    return redirect("tournament_detail", tournament_id=tournament.id)


def _played_message(played: PlayedNode) -> str:
    """UXP-13 -- "Played Round 1, Match 2: A 2–0 B." plus the champion when
    this node ended the Tournament."""
    node = played.node
    if node.bracket_type == "grand_final":
        label = "Grand Final"
    else:
        prefix = _SECTION_PREFIX.get(node.bracket_type, "")
        label = f"{prefix}Round {node.bracket_round}, Match {node.position + 1}"
    wins_a, wins_b = count_series_wins(
        node.series_matches.all(), node.team_a_id, node.team_b_id
    )
    name_a = node.team_a.name if node.team_a else "—"
    name_b = node.team_b.name if node.team_b else "—"
    text = f"Played {label}: {name_a} {wins_a}–{wins_b} {name_b}."
    if played.champion is not None:
        text += f" {played.champion.name} win the Tournament!"
    return text


def tournament_play_all(request: HttpRequest, tournament_id: int) -> JsonResponse:
    """Enqueue the async Play Tournament job. POST-only. 202 + job JSON."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament: Tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    try:
        job_id = enqueue_play_all(tournament)
    except _PLAY_GUARD_ERRORS as exc:
        return JsonResponse({"error": str(exc)}, status=409)
    except BrokerUnavailableError as exc:
        # A clean JSON 503 (not a 500 HTML page) so the UI shows the message.
        return JsonResponse({"error": str(exc)}, status=503)
    return JsonResponse({"job_id": job_id, "tournament_id": tournament.id}, status=202)


def tournament_play_status(
    request: HttpRequest, tournament_id: int, job_id: str
) -> JsonResponse:
    """Poll a Play Tournament job. GET-only. Returns the locked 5-key JSON."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    tournament: Tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    return JsonResponse(build_play_status(tournament, job_id))
