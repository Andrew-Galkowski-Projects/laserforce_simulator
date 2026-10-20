"""ANG-01f — the setup actions of ``TournamentViewSet``: seeds, lock and
participant import (ticket ANG-01f-4). Create lives in ``create``.

Each write runs ``get_object()`` (404 cross-Account), then the guards through
``translate_tournament_errors()`` (409), then the Form (400), then the
``tournament_services.setup`` service, and answers with the detail payload.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.permissions import manager_or_none
from laserforce_simulator.api.exceptions import (
    EnvelopeValidationError,
    validate_form_or_raise,
)
from laserforce_simulator.api.forms import plain_form_data
from matches.tournament_api_views.errors import translate_tournament_errors
from matches.tournament_services.detail import build_tournament_detail
from matches.tournament_services.errors import require_setup
from matches.tournament_services.setup import SeedsForm, apply_seeds
from matches.tournament_services.setup import (
    import_participants as run_participant_import,
)
from matches.tournament_services.setup import lock_tournament
from teams.api_views import roster_import_errors_to_envelope
from teams.forms import RosterImportForm
from teams.roster_importer import RosterImportError

if TYPE_CHECKING:
    from matches.models import Tournament


class SetupActionsMixin:
    """Seeds / lock / import-participants actions."""

    if TYPE_CHECKING:
        # Provided by ``TournamentViewSet`` (``GenericViewSet``).
        def get_object(self) -> Tournament: ...

    @action(detail=True, methods=["put"], url_path="seeds", url_name="seeds")
    def seeds(self, request: Request, pk: str | None = None) -> Response:
        """Replace the whole Seeding with a ``{team_id: seed}`` map (1..N)."""
        tournament = self.get_object()
        with translate_tournament_errors():
            require_setup(tournament)
        form = SeedsForm(
            plain_form_data(SeedsForm, request.data), tournament=tournament
        )
        validate_form_or_raise(form)
        with translate_tournament_errors():
            apply_seeds(tournament, form)
        return Response(build_tournament_detail(tournament))

    @action(detail=True, methods=["post"], url_path="lock", url_name="lock")
    def lock(self, request: Request, pk: str | None = None) -> Response:
        """Build the Bracket and move ``setup`` to ``active``.

        A format-rule failure is a 400 ``invalid`` (field null) with the reason.
        """
        tournament = self.get_object()
        with translate_tournament_errors():
            require_setup(tournament)
            tournament = lock_tournament(tournament)
        return Response(build_tournament_detail(tournament))

    @action(
        detail=True,
        methods=["post"],
        url_path="import-participants",
        url_name="import-participants",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_participants(self, request: Request, pk: str | None = None) -> Response:
        """Import participant Teams from a roster CSV (201 with the detail).

        CSV errors are 400 ``roster_invalid`` with ``row:<n>[:<column>]`` fields;
        a Player-pool Tournament is 400 ``invalid`` with field null.
        """
        tournament = self.get_object()
        with translate_tournament_errors():
            require_setup(tournament)
        form = RosterImportForm(data=request.data, files=request.FILES)
        validate_form_or_raise(form)
        try:
            with translate_tournament_errors():
                run_participant_import(tournament, form, user=manager_or_none(request))
        except RosterImportError as exc:
            raise EnvelopeValidationError(roster_import_errors_to_envelope(exc.errors))
        return Response(
            build_tournament_detail(tournament), status=status.HTTP_201_CREATED
        )
