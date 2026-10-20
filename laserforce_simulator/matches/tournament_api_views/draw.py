"""ANG-01f — the Player-pool and Draw write actions of ``TournamentViewSet``
(ticket ANG-01f-5). The ``pool/`` GET lives in ``base``.

Order inside every action: ``get_object()`` (404 for a cross-Account row), then
``require_pool_setup`` inside ``translate_tournament_errors()`` (409
``tournament_embedded`` / ``tournament_not_setup``; a preset Tournament is 400
``invalid`` with field null), then the Form (400), then the service.

Pool-entry writes return pool-entry payloads (``{player, tier, drawn_team}``,
the ``pool/`` GET row shape): add / generate / import are 201 with the created
entries, the draw-entry edit is 200 with the edited entry, remove is 204. The
Draw itself returns 200 with the ``build_tournament_detail`` payload.
"""

from __future__ import annotations

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
from matches.models import Tournament, TournamentPlayerEntry
from matches.tournament_services.detail import build_tournament_detail
from matches.tournament_services.draw import (
    DrawEntryForm,
    PoolAddPlayersForm,
    PoolGenerateForm,
    add_pool_players,
    edit_draw_entry,
    generate_pool_players,
    import_pool_players,
    remove_pool_player,
    require_pool_setup,
    run_draw,
)
from teams.api_views import roster_import_errors_to_envelope
from teams.forms import RosterImportForm
from teams.roster_importer import RosterImportError


def _entries_payload(entries: list[TournamentPlayerEntry]) -> list[dict]:
    # Imported lazily: ``base`` imports this module to compose the viewset.
    from matches.tournament_api_views.base import _pool_entry

    return [_pool_entry(entry) for entry in entries]


class DrawActionsMixin:
    """Pool add / generate / import / remove, run the Draw, edit a draw entry."""

    def _pool_tournament(self) -> Tournament:
        """``get_object()`` (404), then the pool-setup guards (409 / 400)."""
        from matches.tournament_api_views.base import translate_tournament_errors

        tournament = self.get_object()  # type: ignore[attr-defined]
        with translate_tournament_errors():
            require_pool_setup(tournament)
        return tournament

    @action(
        detail=True,
        methods=["post"],
        url_path="pool/players",
        url_name="pool-players",
    )
    def pool_add_players(self, request: Request, pk: str | None = None) -> Response:
        """Add owned existing Players to the pool: 201 with the new entries."""
        tournament = self._pool_tournament()
        form = PoolAddPlayersForm(
            plain_form_data(PoolAddPlayersForm, request.data),
            tournament=tournament,
            user=manager_or_none(request),
        )
        validate_form_or_raise(form)
        entries = add_pool_players(tournament, form)
        return Response(_entries_payload(entries), status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["delete"],
        url_path=r"pool/players/(?P<player_id>\d+)",
        url_name="pool-player",
    )
    def pool_remove_player(
        self, request: Request, pk: str | None = None, player_id: str = ""
    ) -> Response:
        """Remove one Player from the pool: 204; not in the pool is a 404."""
        tournament = self._pool_tournament()
        remove_pool_player(tournament, int(player_id))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="draw", url_name="draw")
    def draw(self, request: Request, pk: str | None = None) -> Response:
        """Run (or re-roll) the Draw: 200 with the ``build_tournament_detail``
        payload. A bad pool size is 400 ``invalid`` with the rule."""
        from matches.tournament_api_views.base import translate_tournament_errors

        tournament = self._pool_tournament()
        with translate_tournament_errors():
            run_draw(tournament, user=manager_or_none(request))
        return Response(build_tournament_detail(tournament))

    @action(
        detail=True,
        methods=["patch"],
        url_path=r"draw/entries/(?P<player_id>\d+)",
        url_name="draw-entry",
    )
    def draw_entry(
        self, request: Request, pk: str | None = None, player_id: str = ""
    ) -> Response:
        """Hand-edit one entry's ``tier`` / ``drawn_team``: 200 with the entry."""
        tournament = self._pool_tournament()
        form = DrawEntryForm(
            plain_form_data(DrawEntryForm, request.data), tournament=tournament
        )
        validate_form_or_raise(form)
        entry = edit_draw_entry(tournament, int(player_id), form)
        return Response(_entries_payload([entry])[0])

    @action(
        detail=True,
        methods=["post"],
        url_path="pool/generate",
        url_name="pool-generate",
    )
    def pool_generate(self, request: Request, pk: str | None = None) -> Response:
        """Generate ``count`` new pool Players: 201 with the new entries."""
        tournament = self._pool_tournament()
        form = PoolGenerateForm(plain_form_data(PoolGenerateForm, request.data))
        validate_form_or_raise(form)
        entries = generate_pool_players(tournament, form)
        return Response(_entries_payload(entries), status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["post"],
        url_path="pool/import",
        url_name="pool-import",
        parser_classes=[MultiPartParser, FormParser],
    )
    def pool_import(self, request: Request, pk: str | None = None) -> Response:
        """Import pool Players from a roster CSV: 201 with the new entries.

        CSV errors are 400 ``roster_invalid`` with ``row:<n>[:<column>]`` fields.
        """
        tournament = self._pool_tournament()
        form = RosterImportForm(data=request.data, files=request.FILES)
        validate_form_or_raise(form)
        try:
            entries = import_pool_players(
                tournament, form, user=manager_or_none(request)
            )
        except RosterImportError as exc:
            raise EnvelopeValidationError(roster_import_errors_to_envelope(exc.errors))
        return Response(_entries_payload(entries), status=status.HTTP_201_CREATED)
