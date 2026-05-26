from celery.result import AsyncResult
from rest_framework import serializers, status, views
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
from matches.tasks import simulate_batch_task
from matches.views import build_batch_status_response, int_or_none
from teams.models import Team


class SimulateBatchRequestSerializer(serializers.Serializer):
    team_red = serializers.IntegerField(min_value=1)
    team_blue = serializers.IntegerField(min_value=1)
    n = serializers.IntegerField(min_value=1, max_value=500)
    arena_map = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    master_seed = serializers.IntegerField(required=False, allow_null=True)


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


class SimulateBatchAPIView(views.APIView):
    """POST /api/simulate-batch/ → enqueue a batch-sim Celery task.

    Returns ``{job_id, team_red_id, team_red_name, team_blue_id,
    team_blue_name, arena_map_id, n}`` (identical shape to the UI POST).
    """

    def post(self, request: Request) -> Response:
        serializer = SimulateBatchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        team_red_id = data["team_red"]
        team_blue_id = data["team_blue"]
        n = data["n"]
        arena_map_id = data.get("arena_map")
        master_seed = data.get("master_seed")

        if team_red_id == team_blue_id:
            return Response(
                {"detail": "A team cannot play against itself!"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        teams: dict = {}
        for slot, tid in (("red", team_red_id), ("blue", team_blue_id)):
            try:
                teams[slot] = Team.objects.get(id=tid)
            except Team.DoesNotExist:
                return Response(
                    {"detail": f"Team {tid} does not exist."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        team_red = teams["red"]
        team_blue = teams["blue"]

        for team in (team_red, team_blue):
            roster_errors = team.roster_errors
            if roster_errors:
                return Response(
                    {
                        "detail": (
                            f"{team.name} has an invalid roster: "
                            f"{'; '.join(roster_errors)}"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        async_result = simulate_batch_task.delay(
            team_red_id=team_red_id,
            team_blue_id=team_blue_id,
            n=n,
            arena_map_id=arena_map_id,
            master_seed=master_seed,
        )

        return Response(
            {
                "job_id": async_result.id,
                "team_red_id": team_red_id,
                "team_red_name": team_red.name,
                "team_blue_id": team_blue_id,
                "team_blue_name": team_blue.name,
                "arena_map_id": arena_map_id,
                "n": n,
            }
        )


class SimulateBatchStatusAPIView(views.APIView):
    """GET /api/simulate-batch/<job_id>/ → polling JSON.

    Same shape as ``/matches/simulate-batch/status/<job_id>/``.
    """

    def get(self, request: Request, job_id: str) -> Response:
        async_result = AsyncResult(job_id)
        team_red_id = int_or_none(request.query_params.get("team_red_id"))
        team_blue_id = int_or_none(request.query_params.get("team_blue_id"))
        arena_map_id = int_or_none(request.query_params.get("arena_map_id"))
        return Response(
            build_batch_status_response(
                async_result,
                team_red_id=team_red_id,
                team_blue_id=team_blue_id,
                arena_map_id=arena_map_id,
            )
        )
