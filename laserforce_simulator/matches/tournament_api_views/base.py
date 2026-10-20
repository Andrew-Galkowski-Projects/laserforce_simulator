"""ANG-01f — ``TournamentViewSet`` (``/api/tournaments/``).

The conflict ``APIException``s and the service-error translator live in the
leaf module ``tournament_api_views.errors``.

Follows ADR-0039: one envelope, Form-bound writes, output-only serializers,
views raise (never hand-build a 4xx ``Response``). Every row resolves through
``owned_queryset`` so a cross-Account Tournament is a 404 and an anonymous
request is DRF's 401. Write order inside every action: ``get_object()`` (404),
then the guards (409), then the Form (400), then the service.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_not_required
from django.db.models import (
    BooleanField,
    Exists,
    ExpressionWrapper,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
)
from django.utils.decorators import method_decorator
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.settings import api_settings

from accounts.permissions import owned_queryset
from laserforce_simulator.api.viewsets import ListOnlyFilterMixin
from matches.filters import TournamentFilterSet
from matches.models import SeasonPhase, Tournament
from matches.serializers import TournamentSerializer
from matches.tournament_api_views.create import CreateActionsMixin
from matches.tournament_api_views.draw import DrawActionsMixin
from matches.tournament_api_views.play import PlayActionsMixin
from matches.tournament_api_views.setup import SetupActionsMixin
from matches.tournament_services.detail import (
    build_tournament_detail,
    pool_entries,
    pool_entry_payload,
)


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
# DRF's 401 JSON; the settings-level `IsAuthenticated` gates it. No `update` /
# `partial_update` / `destroy`, so PUT / PATCH / DELETE on the detail are 405.
@method_decorator(login_not_required, name="dispatch")
class TournamentViewSet(
    CreateActionsMixin,
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
        # ``embedded`` is annotated and the Season-wide phases prefetched so a
        # list page runs a constant number of queries (``tournament_fields``
        # reads both when present).
        embedded = ExpressionWrapper(
            Q(season_phase__isnull=False)
            | Exists(SeasonPhase.objects.filter(tournament=OuterRef("pk"))),
            output_field=BooleanField(),
        )
        return owned_queryset(
            Tournament.objects.select_related("champion", "season_phase__season")
            .prefetch_related(
                Prefetch(
                    "season_phases",
                    queryset=SeasonPhase.objects.select_related("season"),
                )
            )
            .annotate(annotated_embedded=embedded),
            self.request.user,
        )

    def retrieve(self, request: Request, pk: str | None = None) -> Response:
        """The ``build_tournament_detail`` payload."""
        return Response(build_tournament_detail(self.get_object()))

    @action(detail=True, methods=["get"], url_path="pool", url_name="pool")
    def pool(self, request: Request, pk: str | None = None) -> Response:
        """The Player pool, paginated: ``{player, tier, drawn_team}`` entries."""
        page = self.paginate_queryset(pool_entries(self.get_object()))
        return self.get_paginated_response(
            [pool_entry_payload(entry) for entry in page]
        )
