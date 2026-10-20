"""ANG-01f — ``TournamentViewSet`` (``/api/tournaments/``), its conflict
``APIException``s and the one translator from the ``tournament_services``
domain errors.

Follows ADR-0039: one envelope, Form-bound writes, output-only serializers,
views raise (never hand-build a 4xx ``Response``). Every row resolves through
``owned_queryset`` so a cross-Account Tournament is a 404 and an anonymous
request is DRF's 403. Write order inside every action: ``get_object()`` (404),
then the guards (409), then the Form (400), then the service.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import QuerySet
from django.utils.decorators import method_decorator
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings

from accounts.permissions import owned_queryset
from laserforce_simulator.api.viewsets import ListOnlyFilterMixin
from matches.api_refs import player_ref, team_ref
from matches.filters import TournamentFilterSet
from matches.models import Tournament, TournamentPlayerEntry
from matches.serializers import TournamentSerializer
from matches.tournament_api_views.draw import DrawActionsMixin
from matches.tournament_api_views.play import PlayActionsMixin
from matches.tournament_api_views.setup import SetupActionsMixin
from matches.tournament_services.detail import build_tournament_detail, pool_entries
from matches.tournament_services.errors import (
    BrokerUnavailableError,
    DrawNotReadyError,
    NoPlayableNodeError,
    PlayInProgressError,
    TournamentEmbeddedError,
    TournamentNotActiveError,
    TournamentNotSetupError,
)

# ---------------------------------------------------------------------------
# State conflicts (named codes; the envelope handler shapes them).
# ---------------------------------------------------------------------------


class TournamentEmbedded(APIException):
    """409 — a write on an in-League Tournament (played through its Season)."""

    status_code = 409
    default_code = "tournament_embedded"
    default_detail = str(TournamentEmbeddedError())


class TournamentNotSetup(APIException):
    """409 — a setup-only write on a locked or completed Tournament."""

    status_code = 409
    default_code = "tournament_not_setup"
    default_detail = str(TournamentNotSetupError())


class TournamentNotActive(APIException):
    """409 — a play action on a Tournament that is not active."""

    status_code = 409
    default_code = "tournament_not_active"
    default_detail = str(TournamentNotActiveError())


class NoPlayableNode(APIException):
    """409 — play-next found no ready Match."""

    status_code = 409
    default_code = "no_playable_node"
    default_detail = str(NoPlayableNodeError())


class PlayInProgress(APIException):
    """409 — the Tournament already has an active play run (ADR-0040).

    The detail is keyed ``job:<id>`` so the envelope ``field`` names the live
    Job the client should poll instead.
    """

    status_code = 409
    default_code = "play_in_progress"
    default_detail = "The Tournament already has a play run in progress."

    def __init__(self, job_id: str) -> None:
        super().__init__(detail={f"job:{job_id}": self.default_detail})


class BrokerUnavailable(APIException):
    """503 — the Celery broker refused the play-all enqueue (nothing started)."""

    status_code = 503
    default_code = "broker_unavailable"
    default_detail = str(BrokerUnavailableError())


# One mapping from each ``tournament_services.errors`` exception to its API
# exception (each factory takes the caught error).
_SERVICE_ERRORS: dict[type[Exception], Callable[[Any], APIException]] = {
    TournamentEmbeddedError: lambda exc: TournamentEmbedded(str(exc)),
    TournamentNotSetupError: lambda exc: TournamentNotSetup(str(exc)),
    TournamentNotActiveError: lambda exc: TournamentNotActive(str(exc)),
    NoPlayableNodeError: lambda exc: NoPlayableNode(str(exc)),
    PlayInProgressError: lambda exc: PlayInProgress(exc.job_id),
    BrokerUnavailableError: lambda exc: BrokerUnavailable(str(exc)),
    DrawNotReadyError: lambda exc: ValidationError(str(exc)),
    # The lock's model ValidationError (format rules) — 400 ``invalid``, field null.
    DjangoValidationError: lambda exc: ValidationError(exc.messages),
}


@contextmanager
def translate_tournament_errors() -> Iterator[None]:
    """Re-raise a Tournament service error as its API exception.

    409 ``tournament_embedded`` / ``tournament_not_setup`` /
    ``tournament_not_active`` / ``no_playable_node``; 409 ``play_in_progress``
    with ``field = job:<id>``; 503 ``broker_unavailable``; ``DrawNotReadyError``
    and the lock's ``ValidationError`` ⇒ 400 ``invalid`` with field null.
    Anything else propagates unchanged.
    """
    try:
        yield
    except tuple(_SERVICE_ERRORS) as exc:
        factory = next(
            _SERVICE_ERRORS[cls] for cls in type(exc).__mro__ if cls in _SERVICE_ERRORS
        )
        raise factory(exc) from exc


def _pool_entry(entry: TournamentPlayerEntry) -> dict:
    """One pool entry: ``{player: player_ref, tier, drawn_team: team_ref | None}``."""
    return {
        "player": player_ref(entry.player),
        "tier": entry.tier,
        "drawn_team": (
            team_ref(entry.drawn_team) if entry.drawn_team_id is not None else None
        ),
    }


class _NoFormatOverrideSettings:
    """``api_settings`` with ``URL_FORMAT_OVERRIDE`` switched off."""

    URL_FORMAT_OVERRIDE = None

    def __getattr__(self, name: str) -> Any:
        return getattr(api_settings, name)


class _FormatFilterNegotiation(DefaultContentNegotiation):
    """Content negotiation that leaves ``?format=`` alone.

    DRF reads ``?format=`` as a renderer override (an unknown value is a 404),
    but here ``format`` is the Tournament bracket-format filter (spec #190).
    """

    settings = _NoFormatOverrideSettings()


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 403 JSON; the settings-level `IsAuthenticated` gates it. No `update` /
# `partial_update` / `destroy`, so PUT / PATCH / DELETE on the detail are 405.
@method_decorator(login_not_required, name="dispatch")
class TournamentViewSet(
    SetupActionsMixin,
    DrawActionsMixin,
    PlayActionsMixin,
    ListOnlyFilterMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    # Class-level queryset for router introspection; get_queryset() scopes it.
    queryset = Tournament.objects.all()
    content_negotiation_class = _FormatFilterNegotiation
    serializer_class = TournamentSerializer
    filterset_class = TournamentFilterSet
    ordering_fields = ("name", "created_at", "id")
    ordering = ("-id",)
    ordering_tiebreak = ("-id",)

    def get_queryset(self) -> QuerySet[Tournament]:
        return owned_queryset(
            Tournament.objects.select_related("champion", "season_phase__season"),
            self.request.user,
        )

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        """The ``build_tournament_detail`` payload."""
        return Response(build_tournament_detail(self.get_object()))

    @action(detail=True, methods=["get"], url_path="pool", url_name="pool")
    def pool(self, request: Request, pk: str | None = None) -> Response:
        """The Player pool, paginated: ``{player, tier, drawn_team}`` entries."""
        page = self.paginate_queryset(pool_entries(self.get_object()))
        return self.get_paginated_response([_pool_entry(entry) for entry in page])
