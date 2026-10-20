"""ANG-01f-3 -- the ``create`` action of ``TournamentViewSet``
(``POST /api/tournaments/``)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

from laserforce_simulator.api.exceptions import validate_form_or_raise
from laserforce_simulator.api.forms import plain_form_data
from matches.tournament_services.create import (
    TournamentCreateForm,
    create_tournament,
)
from matches.tournament_services.detail import build_tournament_detail


class CreateActionsMixin:
    """``create``: bind ``TournamentCreateForm`` (400 on bad input), create the
    Tournament stamped with the requester, 201 with the detail payload."""

    def create(self, request: Request) -> Response:
        form = TournamentCreateForm(
            plain_form_data(TournamentCreateForm, request.data), user=request.user
        )
        validate_form_or_raise(form)
        tournament = create_tournament(form, user=request.user)
        return Response(
            build_tournament_detail(tournament), status=status.HTTP_201_CREATED
        )
