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

from accounts.permissions import manager_or_none, owned_queryset
from laserforce_simulator.api.exceptions import (
    EnvelopeValidationError,
    ErrorEntry,
    django_validation_error_to_envelope,
    validate_form_or_raise,
)
from laserforce_simulator.api.forms import merged_form_data, plain_form_data
from laserforce_simulator.api.viewsets import ListOnlyFilterMixin
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
    build_roster_template_csv,
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


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 403 JSON rather than an HTML 302 to the login page; the settings-level
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
        return owned_queryset(
            Team.objects.prefetch_related("players"), self.request.user
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
