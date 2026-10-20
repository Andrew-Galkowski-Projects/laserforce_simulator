"""LG-02a — the Tournament detail page."""

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404

from matches.models import Tournament
from matches.tournament_services.detail import build_detail_context


def tournament_detail(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Render the bracket tree + (in setup) the Seeding-edit form + play
    controls.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    return render(
        request,
        "matches/tournament_detail.html",
        build_detail_context(tournament, user=request.user),
    )
