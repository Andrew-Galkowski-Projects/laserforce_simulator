from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet

from matches.models import GameRound, Match
from matches.serializers import (
    GameEventSerializer,
    GameRoundListSerializer,
    GameRoundSerializer,
    MatchSerializer,
)


class MatchViewSet(ReadOnlyModelViewSet):
    queryset = Match.objects.select_related("team_red", "team_blue", "winner").order_by(
        "-date_played"
    )
    serializer_class = MatchSerializer


class GameRoundViewSet(ReadOnlyModelViewSet):
    # Class-level queryset required by the DRF router for model introspection.
    # get_queryset() overrides this at request time.
    queryset = GameRound.objects.all()

    def get_queryset(self):
        qs = GameRound.objects.select_related(
            "match", "team_red", "team_blue", "winner"
        ).order_by("-date_played")
        if self.action == "retrieve":
            return qs.prefetch_related("player_states")
        return qs

    def get_serializer_class(self):
        if self.action == "retrieve":
            return GameRoundSerializer
        return GameRoundListSerializer

    @action(detail=True, url_path="events")
    def events(self, request: Request, pk: int | None = None) -> Response:
        game_round = self.get_object()
        qs = game_round.events.select_related("actor", "target").order_by("timestamp")
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = GameEventSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = GameEventSerializer(qs, many=True)
        return Response(serializer.data)
