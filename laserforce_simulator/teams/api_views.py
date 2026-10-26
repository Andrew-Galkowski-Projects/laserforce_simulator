from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import QuerySet
from django.http import HttpResponse
from django.utils.decorators import method_decorator
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from accounts.permissions import get_owned_or_404, manager_or_none, owned_queryset
from laserforce_simulator.api.exceptions import (
    EnvelopeValidationError,
    ErrorEntry,
    django_validation_error_to_envelope,
    validate_form_or_raise,
)
from laserforce_simulator.api.forms import merged_form_data, plain_form_data
from laserforce_simulator.api.viewsets import ListOnlyFilterMixin
from matches.api_forms import PlayerH2HParamsForm, TeamH2HParamsForm
from matches.api_refs import player_ref, team_ref
from matches.services import build_player_h2h, build_team_h2h
from matches.team_match_history import (
    build_team_match_history,
    team_match_history_payload,
)
from teams.api_forms import BenchmarkParamsForm
from teams.filters import PlayerFilterSet, TeamFilterSet
from teams.forms import (
    GenerateLeagueForm,
    PlayerForm,
    RosterImportForm,
    TeamForm,
    TeamSlotForm,
)
from teams.models import Player, Team
from teams.roster_importer import RosterImportError, RowError
from teams.serializers import PlayerSerializer, TeamListSerializer, TeamSerializer
from teams.services import (
    annotate_overall_rating,
    build_career_stats,
    build_role_benchmarks,
    build_roster_template_csv,
    fill_preferred_slot,
    player_form_initial,
    run_player_generation,
    run_roster_import,
)
from teams.views import _SORT_KEYS


def roster_import_errors_to_envelope(errors: list[RowError]) -> list[ErrorEntry]:
    """LG-00b ``RowError``s as envelope entries, in parser order.

    ``field`` is ``row:<n>`` or ``row:<n>:<column>``; ``row:0`` is a
    file-level (header) error.
    """
    return [
        {
            "code": "roster_invalid",
            "message": e.message,
            "field": f"row:{e.row_num}:{e.field}" if e.field else f"row:{e.row_num}",
        }
        for e in errors
    ]


def _full_clean_or_raise(player: Player) -> None:
    """``player.full_clean()``, re-raised as a 400 envelope on failure."""
    try:
        player.full_clean()
    except DjangoValidationError as exc:
        raise EnvelopeValidationError(django_validation_error_to_envelope(exc))


# The six role-slot FKs of a Team.
_SLOT_FIELDS = (
    "slot_commander",
    "slot_heavy",
    "slot_scout_1",
    "slot_scout_2",
    "slot_medic",
    "slot_ammo",
)


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 401 JSON rather than an HTML 302 to the login page; the settings-level
# `IsAuthenticated` is what actually gates it. The class-level `queryset` stays
# for the router's basename/model introspection.
#
# ANG-01a — writes bind the same Django Forms the HTML views bind (ADR-0039);
# serializers are output-only. Every write resolves its Team through
# `get_object()` on the owned queryset, so another Account's Team is a 404.
# No `update` / `destroy` → PUT / DELETE on a team detail are 405.
@method_decorator(login_not_required, name="dispatch")
class TeamViewSet(
    ListOnlyFilterMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Team.objects.prefetch_related("players")
    filterset_class = TeamFilterSet
    ordering_fields = ("name", "created_date", "wins", "losses", "id")
    ordering = ("name",)
    ordering_tiebreak = ("id",)

    def get_queryset(self) -> QuerySet[Team]:
        # The six slot FKs are joined so `roster_errors` (on every Team
        # response) adds no per-Team queries.
        return owned_queryset(
            Team.objects.prefetch_related("players").select_related(*_SLOT_FIELDS),
            self.request.user,
        )

    def get_serializer_class(self) -> type[Serializer]:
        if self.action == "list":
            return TeamListSerializer
        return TeamSerializer

    def _team_response(self, team: Team, status_code: int = 200) -> Response:
        # Re-fetch so the nested `players` prefetch reflects the write.
        team = self.get_queryset().get(pk=team.pk)
        return Response(self.get_serializer(team).data, status=status_code)

    def create(self, request: Request) -> Response:
        form = TeamForm(data=plain_form_data(TeamForm, request.data))
        validate_form_or_raise(form)
        # UX-01 — stamp the **Manager** at creation from request.user.
        team = form.save(commit=False)
        team.manager = manager_or_none(request)
        team.save()
        return self._team_response(team, status.HTTP_201_CREATED)

    def partial_update(self, request: Request, pk: str | None = None) -> Response:
        team = self.get_object()
        form = TeamForm(
            data=merged_form_data(TeamForm, team, request.data), instance=team
        )
        validate_form_or_raise(form)
        form.save()
        return self._team_response(team)

    @action(detail=True, methods=["put"], url_path="slots", url_name="slots")
    def slots(self, request: Request, pk: str | None = None) -> Response:
        """Full replace of the six role slots; an omitted slot is cleared."""
        team = self.get_object()
        sent = plain_form_data(TeamSlotForm, request.data)
        data = {slot: sent.get(slot) for slot in TeamSlotForm.base_fields}
        # `instance` must be a kwarg — TeamSlotForm reads kwargs["instance"]
        # to restrict every slot to this team's own players.
        form = TeamSlotForm(data=data, instance=team)
        validate_form_or_raise(form)
        form.save()
        return self._team_response(team)

    @action(detail=True, methods=["post"], url_path="players", url_name="players")
    def add_player(self, request: Request, pk: str | None = None) -> Response:
        """Add a player; omitted fields take the HTML ``player_add`` initials."""
        team = self.get_object()
        data = {**player_form_initial(), **plain_form_data(PlayerForm, request.data)}
        form = PlayerForm(data=data)
        validate_form_or_raise(form)
        player = form.save(commit=False)
        player.team = team
        _full_clean_or_raise(player)
        player.save()
        fill_preferred_slot(team, player)
        return Response(
            PlayerSerializer(player, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="generate", url_name="generate")
    def generate(self, request: Request) -> Response:
        form = GenerateLeagueForm(
            data=plain_form_data(GenerateLeagueForm, request.data)
        )
        validate_form_or_raise(form)
        result = run_player_generation(
            form.cleaned_data, manager=manager_or_none(request)
        )
        context = self.get_serializer_context()
        return Response(
            {
                "created_teams": TeamListSerializer(
                    result.created_teams, many=True, context=context
                ).data,
                "free_agent_count": result.free_agent_count,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="import",
        url_name="import",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_roster(self, request: Request) -> Response:
        form = RosterImportForm(data=request.data, files=request.FILES)
        validate_form_or_raise(form)
        try:
            result = run_roster_import(
                form.cleaned_data["csv_file"], manager=manager_or_none(request)
            )
        except RosterImportError as exc:
            raise EnvelopeValidationError(roster_import_errors_to_envelope(exc.errors))
        context = self.get_serializer_context()
        return Response(
            {
                "created_teams": TeamListSerializer(
                    result.created_teams, many=True, context=context
                ).data,
                "appended_teams": TeamListSerializer(
                    result.appended_teams, many=True, context=context
                ).data,
                "player_count": result.player_count,
                "row_count": result.row_count,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=False,
        methods=["get"],
        url_path="import/template",
        url_name="import-template",
    )
    def import_template(self, request: Request) -> HttpResponse:
        """Raw CSV — a Django ``HttpResponse``, never enveloped (ADR-0039)."""
        response = HttpResponse(build_roster_template_csv(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="roster_template.csv"'
        return response

    @action(
        detail=False,
        methods=["get"],
        url_path="head-to-head",
        url_name="head-to-head",
    )
    def head_to_head(self, request: Request) -> Response:
        """HX-03 team head-to-head record (strict params; 404 cross-Account)."""
        form = TeamH2HParamsForm(
            data=plain_form_data(TeamH2HParamsForm, request.query_params)
        )
        validate_form_or_raise(form)
        cleaned = form.cleaned_data
        team_a: Team = get_owned_or_404(Team, request, id=cleaned["team_a"])
        team_b: Team = get_owned_or_404(Team, request, id=cleaned["team_b"])
        payload = build_team_h2h(
            team_a,
            team_b,
            provenance=cleaned["provenance"],
            date_from=cleaned["from"],
            date_to=cleaned["to"],
        )
        return Response(
            {
                "team_a": team_ref(team_a),
                "team_b": team_ref(team_b),
                "provenance": cleaned["provenance"],
                "date_from": cleaned["from"],
                "date_to": cleaned["to"],
                **payload,
            }
        )

    @action(detail=True, methods=["get"], url_path="history", url_name="history")
    def history(self, request: Request, pk: str | None = None) -> Response:
        """ANG-06-9 Team match history, scoped to the caller's own games.

        ``scope`` (repeated): absent selects every scope; present, its values
        are the selection. Unknown keys (``""`` among them) are ignored, so a
        bare ``?scope=`` selects none.
        """
        team = self.get_object()
        scopes = None
        if "scope" in request.query_params:
            scopes = set(request.query_params.getlist("scope"))
        return Response(
            team_match_history_payload(
                build_team_match_history(team, request.user, scopes)
            )
        )


@method_decorator(login_not_required, name="dispatch")
class PlayerViewSet(
    ListOnlyFilterMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Player.objects.select_related("team")
    serializer_class = PlayerSerializer
    filterset_class = PlayerFilterSet
    # LG-00c's 23 public sort keys, aliased to their ORM targets
    # (offensive_synergy -> Offensive_synergy, team -> team__name,
    # overall_rating -> the overall_rating_db annotation).
    ordering_fields = tuple(_SORT_KEYS)
    ordering_aliases = _SORT_KEYS
    ordering = ("team__name", "name")
    ordering_tiebreak = ("name", "id")

    def get_queryset(self) -> QuerySet[Player]:
        return annotate_overall_rating(
            owned_queryset(
                Player.objects.select_related("team"), self.request.user, path="team"
            )
        )

    def partial_update(self, request: Request, pk: str | None = None) -> Response:
        player = self.get_object()
        form = PlayerForm(
            data=merged_form_data(PlayerForm, player, request.data), instance=player
        )
        validate_form_or_raise(form)
        player = form.save(commit=False)
        _full_clean_or_raise(player)
        player.save()
        return Response(self.get_serializer(player).data)

    @action(
        detail=False,
        methods=["get"],
        url_path="head-to-head",
        url_name="head-to-head",
    )
    def head_to_head(self, request: Request) -> Response:
        """HX-04 player head-to-head record (strict params; 404 cross-Account)."""
        form = PlayerH2HParamsForm(
            data=plain_form_data(PlayerH2HParamsForm, request.query_params)
        )
        validate_form_or_raise(form)
        cleaned = form.cleaned_data
        player_a = get_owned_or_404(
            Player.objects.select_related("team"), request, id=cleaned["player_a"]
        )
        player_b = get_owned_or_404(
            Player.objects.select_related("team"), request, id=cleaned["player_b"]
        )
        payload = build_player_h2h(
            player_a,
            player_b,
            role=cleaned["role"],
            provenance=cleaned["provenance"],
            date_from=cleaned["from"],
            date_to=cleaned["to"],
        )
        return Response(
            {
                "player_a": player_ref(player_a),
                "player_b": player_ref(player_b),
                "role": cleaned["role"],
                "provenance": cleaned["provenance"],
                "date_from": cleaned["from"],
                "date_to": cleaned["to"],
                **payload,
            }
        )

    @action(detail=True, methods=["get"], url_path="career", url_name="career")
    def career(self, request: Request, pk: str | None = None) -> Response:
        """HX-01 career stats + HX-02 per-role benchmark overlay."""
        player = self.get_object()
        form = BenchmarkParamsForm(
            data=plain_form_data(BenchmarkParamsForm, request.query_params)
        )
        validate_form_or_raise(form)
        payload = build_career_stats(
            player,
            min_rounds=form.cleaned_data["threshold"],
            display=form.cleaned_data["display"],
            user=request.user,
        )
        return Response(
            {
                "player": player_ref(player),
                "total_rounds": payload["total_rounds"],
                "has_rounds": payload["has_rounds"],
                "career": payload["career"],
                "per_role": payload["per_role"],
                "trend": payload["trend"],
                "min_rounds": payload["min_rounds"],
                "display": payload["display"],
                "stat_keys": payload["stat_keys"],
                "per_role_with_benchmarks": payload["per_role_with_benchmarks"],
            }
        )

    @action(detail=False, methods=["get"], url_path="benchmarks", url_name="benchmarks")
    def benchmarks(self, request: Request) -> Response:
        """HX-02 role benchmarks, UXP-14 scoped to the caller's own + Unmanaged
        Rounds (``benchmarks_by_role`` is HTML-only)."""
        form = BenchmarkParamsForm(
            data=plain_form_data(BenchmarkParamsForm, request.query_params)
        )
        validate_form_or_raise(form)
        payload = build_role_benchmarks(
            min_rounds=form.cleaned_data["threshold"],
            display=form.cleaned_data["display"],
            user=request.user,
        )
        return Response(
            {
                "min_rounds": payload["min_rounds"],
                "display": payload["display"],
                "roles": payload["roles"],
                "stat_keys": payload["stat_keys"],
                "benchmarks": payload["benchmarks"],
                "any_data": payload["any_data"],
            }
        )
