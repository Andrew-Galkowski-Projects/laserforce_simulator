from celery.result import AsyncResult
from django.contrib.auth.decorators import login_not_required
from django.db.models import QuerySet
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from django_filters.utils import translate_validation
from rest_framework import serializers, status, views
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer
from rest_framework.viewsets import ReadOnlyModelViewSet

from accounts.permissions import get_owned_or_404, owned_game_round_q, owned_match_q
from laserforce_simulator.api.exceptions import (
    EnvelopeValidationError,
    validate_form_or_raise,
)
from laserforce_simulator.api.forms import plain_form_data
from laserforce_simulator.api.viewsets import ListOnlyFilterMixin
from matches import round_comparison
from matches.api_forms import CompareRoundsParamsForm
from matches.api_refs import round_ref
from matches.filters import GameEventFilterSet, GameRoundFilterSet, MatchFilterSet
from matches.forms import MatchSetupForm, SaveBatchGamesForm, SingleRoundSetupForm
from matches.models import GameRound, Match
from matches.serializers import (
    GameEventSerializer,
    GameRoundListSerializer,
    GameRoundSerializer,
    MatchSerializer,
)
from matches.services import (
    MatchSetupError,
    build_heatmap,
    build_missile_log,
    build_round_comparison,
    build_round_report_data,
    create_sandbox_match,
    create_sandbox_round,
    resolve_batch_teams,
    round_report_filename,
    shared_team_ids,
)
from matches.sim_helpers.pdf_report import build_round_report
from matches.simulation import BatchSimulator
from matches.tasks import save_games_task, simulate_batch_task
from matches.views import (
    build_batch_status_response,
    build_save_status_response,
    int_or_none,
)


class SimulateBatchRequestSerializer(serializers.Serializer):
    team_red = serializers.IntegerField(min_value=1)
    team_blue = serializers.IntegerField(min_value=1)
    n = serializers.IntegerField(min_value=1, max_value=500)
    arena_map = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    master_seed = serializers.IntegerField(required=False, allow_null=True)


class JobNotComplete(APIException):
    """ANG-01b — 409: the batch Job a save targets has not reached SUCCESS."""

    status_code = 409
    default_code = "job_not_complete"
    default_detail = "The batch job is not complete."


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 401 JSON rather than an HTML 302 to the login page; the settings-level
# `IsAuthenticated` is what actually gates it.
#
# ANG-01a — ordering comes from `AliasOrderingFilter` / `ordering` (with an
# `-id` tiebreak for stable pages), not from `get_queryset`.
#
# ANG-01b — gains a `create` (no `CreateModelMixin`), so PUT / PATCH / DELETE
# stay 405 with the `Allow` header.
@method_decorator(login_not_required, name="dispatch")
class MatchViewSet(ListOnlyFilterMixin, ReadOnlyModelViewSet):
    # Class-level queryset required by the DRF router for model introspection.
    # get_queryset() overrides this at request time.
    queryset = Match.objects.select_related("team_red", "team_blue", "winner")
    serializer_class = MatchSerializer
    filterset_class = MatchFilterSet
    ordering_fields = ("date_played", "id")
    ordering = ("-date_played",)
    ordering_tiebreak = ("-id",)

    def get_queryset(self) -> QuerySet[Match]:
        return Match.objects.filter(owned_match_q(self.request.user)).select_related(
            "team_red", "team_blue", "winner"
        )

    def create(self, request: Request) -> Response:
        """Simulate a 2-round sandbox Match synchronously; 201 with the Match."""
        form = MatchSetupForm(
            data=plain_form_data(MatchSetupForm, request.data), user=request.user
        )
        validate_form_or_raise(form)
        try:
            match = create_sandbox_match(
                form.cleaned_data["team_red"],
                form.cleaned_data["team_blue"],
                form.cleaned_data["match_type"],
                arena_map=form.cleaned_data.get("arena_map"),
                user=request.user,
            )
        except MatchSetupError as exc:
            raise ValidationError(str(exc))
        match = self.get_queryset().get(pk=match.pk)
        return Response(self.get_serializer(match).data, status=status.HTTP_201_CREATED)


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 401 JSON rather than an HTML 302 to the login page; the settings-level
# `IsAuthenticated` is what actually gates it.
@method_decorator(login_not_required, name="dispatch")
class GameRoundViewSet(ListOnlyFilterMixin, ReadOnlyModelViewSet):
    # Class-level queryset required by the DRF router for model introspection.
    # get_queryset() overrides this at request time.
    queryset = GameRound.objects.all()
    filterset_class = GameRoundFilterSet
    ordering_fields = ("date_played", "round_number", "id")
    ordering = ("-date_played",)
    ordering_tiebreak = ("-id",)

    def get_queryset(self) -> QuerySet[GameRound]:
        qs = GameRound.objects.filter(
            owned_game_round_q(self.request.user)
        ).select_related("match", "team_red", "team_blue", "winner")
        if self.action in ("retrieve", "create"):
            return qs.prefetch_related("player_states")
        return qs

    def get_serializer_class(self) -> type[Serializer]:
        if self.action in ("retrieve", "create"):
            return GameRoundSerializer
        return GameRoundListSerializer

    def create(self, request: Request) -> Response:
        """Simulate a standalone sandbox Round synchronously; 201 with the Round."""
        form = SingleRoundSetupForm(
            data=plain_form_data(SingleRoundSetupForm, request.data),
            user=request.user,
        )
        validate_form_or_raise(form)
        try:
            game_round = create_sandbox_round(
                form.cleaned_data["team_red"],
                form.cleaned_data["team_blue"],
                arena_map=form.cleaned_data.get("arena_map"),
                user=request.user,
            )
        except MatchSetupError as exc:
            raise ValidationError(str(exc))
        game_round = self.get_queryset().get(pk=game_round.pk)
        return Response(
            self.get_serializer(game_round).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"], url_path="events", url_name="events")
    def events(self, request: Request, pk: str | None = None) -> Response:
        """The round's events in fixed ``(timestamp, id)`` order, filterable."""
        game_round = self.get_object()
        # ANG-01b — the event log needs `full` fidelity (GEN-01 lazy upgrade).
        BatchSimulator().ensure_fidelity(game_round, "full")
        qs = game_round.events.select_related("actor", "target").order_by(
            "timestamp", "id"
        )
        fs = GameEventFilterSet(request.query_params, queryset=qs, request=request)
        if not fs.is_valid():
            raise translate_validation(fs.errors)
        qs = fs.qs
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = GameEventSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = GameEventSerializer(qs, many=True)
        return Response(serializer.data)

    @action(
        detail=True, methods=["get"], url_path="missile-log", url_name="missile-log"
    )
    def missile_log(self, request: Request, pk: str | None = None) -> Response:
        """RES-03 missile log (upgrades the round to ``combat`` fidelity)."""
        game_round = self.get_object()
        return Response({"round_id": game_round.id, **build_missile_log(game_round)})

    @action(detail=True, methods=["get"], url_path="heatmap", url_name="heatmap")
    def heatmap(self, request: Request, pk: str | None = None) -> Response:
        """RES-04 movement heatmap (upgrades the round to ``full`` fidelity)."""
        game_round = self.get_object()
        payload = build_heatmap(game_round)
        arena_map = payload["arena_map"]
        return Response(
            {
                "round_id": game_round.id,
                "has_map": payload["has_map"],
                "arena_map": (
                    {"id": arena_map.id, "name": arena_map.name}
                    if arena_map is not None
                    else None
                ),
                "zone_size": payload["zone_size"],
                "processed_image_url": payload["processed_image_url"],
                "cell_occupancy_json": payload["cell_occupancy_json"],
                "player_roster": payload["player_roster"],
            }
        )

    @action(detail=True, methods=["get"], url_path="report", url_name="report")
    def report(self, request: Request, pk: str | None = None) -> HttpResponse:
        """RV-03 round report — a raw PDF ``HttpResponse``, never enveloped.

        RV-05: upgrades the round to ``full`` fidelity first (as ``events``
        does) so the PDF's chart section plots the same events.
        """
        game_round = self.get_object()
        BatchSimulator().ensure_fidelity(game_round, "full")
        pdf_bytes = build_round_report(
            build_round_report_data(game_round), watermark=game_round.is_simulated
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{round_report_filename(game_round)}"'
        )
        return response

    @action(detail=False, methods=["get"], url_path="compare", url_name="compare")
    def compare(self, request: Request) -> Response:
        """RV-01 side-by-side comparison of two owned rounds sharing a team."""
        form = CompareRoundsParamsForm(
            data=plain_form_data(CompareRoundsParamsForm, request.query_params)
        )
        validate_form_or_raise(form)
        round_a: GameRound = get_owned_or_404(
            GameRound, request, id=form.cleaned_data["round_a"]
        )
        round_b: GameRound = get_owned_or_404(
            GameRound, request, id=form.cleaned_data["round_b"]
        )
        if not shared_team_ids(round_a, round_b):
            raise EnvelopeValidationError(
                [
                    {
                        "code": "invalid",
                        "message": (
                            "These rounds share no team, so there is nothing "
                            "to compare."
                        ),
                        "field": None,
                    }
                ]
            )
        comparison = build_round_comparison(round_a, round_b, request.user)
        return Response(
            {
                "round_a": round_ref(round_a),
                "round_b": round_ref(round_b),
                "stat_keys": list(round_comparison.COMPARE_STAT_KEYS),
                "team_ids": comparison["team_ids"],
                "points_series": comparison["points_series"],
                "deltas": comparison["deltas"],
            }
        )


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 401 JSON rather than an HTML 302 to the login page; the settings-level
# `IsAuthenticated` is what actually gates it.
@method_decorator(login_not_required, name="dispatch")
class SimulateBatchAPIView(views.APIView):
    """POST /api/simulate-batch/ → enqueue a batch-sim Celery task.

    Returns ``{job_id, team_red_id, team_red_name, team_blue_id,
    team_blue_name, arena_map_id, n}`` (identical shape to the UI POST).
    ANG-01b — the team lookup is ownership-scoped (a foreign team reads as
    "Team {id} does not exist.").
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

        try:
            team_red, team_blue = resolve_batch_teams(
                team_red_id, team_blue_id, request.user
            )
        except MatchSetupError as exc:
            raise ValidationError(str(exc))

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


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 401 JSON rather than an HTML 302 to the login page; the settings-level
# `IsAuthenticated` is what actually gates it.
@method_decorator(login_not_required, name="dispatch")
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


@method_decorator(login_not_required, name="dispatch")
class SaveBatchGamesAPIView(views.APIView):
    """POST /api/simulate-batch/<job_id>/save/ → enqueue a save-games Job.

    The batch Job id is a capability token (no owner check on it); ownership
    is enforced on the input teams. The session is not used: the teams and
    the category ride in the body. Returns 200 ``{"job_id": <save job id>}``.
    """

    def post(self, request: Request, job_id: str) -> Response:
        form = SaveBatchGamesForm(
            data=plain_form_data(SaveBatchGamesForm, request.data)
        )
        validate_form_or_raise(form)
        cleaned = form.cleaned_data

        try:
            resolve_batch_teams(cleaned["team_red"], cleaned["team_blue"], request.user)
        except MatchSetupError as exc:
            raise ValidationError(str(exc))

        async_result = AsyncResult(job_id)
        if async_result.state != "SUCCESS":
            raise JobNotComplete()

        result = async_result.result
        key = "avg_seeds" if cleaned["game_type"] == "avg" else "outlier_seeds"
        n = cleaned["n"]
        seeds = (result.get(key) if isinstance(result, dict) else None) or []
        seeds = seeds[:n]
        if not seeds:
            raise ValidationError("No saved seeds for this category.")

        save_result = save_games_task.delay(
            team_red_id=cleaned["team_red"],
            team_blue_id=cleaned["team_blue"],
            seeds=seeds,
            n=n,
            arena_map_id=cleaned.get("arena_map"),
            manager_id=request.user.pk,
        )
        return Response({"job_id": save_result.id})


@method_decorator(login_not_required, name="dispatch")
class SaveBatchStatusAPIView(views.APIView):
    """GET /api/simulate-batch/save/<save_job_id>/ → save-job polling JSON.

    Byte-identical to the HTML ``save_batch_status`` body; never 4xxs (an
    unknown id reads as ``running``).
    """

    def get(self, request: Request, save_job_id: str) -> Response:
        return Response(build_save_status_response(AsyncResult(save_job_id)))
