"""ANG-01d-1 — League lifecycle + read REST API (``/api/leagues/``, ``/api/seasons/``).

Follows ADR-0039: one envelope, Form-bound writes, output-only serializers,
views RAISE (never hand-build a 4xx ``Response``). Every League / Season /
Team row resolves through ``owned_queryset`` / ``get_owned_or_404``, so a
cross-Account row is a 404 and an anonymous request is DRF's 401. All
computation is shared with the HTML views via ``matches.league_services``; the
API never reads or writes ``request.session["last_league_id"]``.

The play loop (start season, play week / months / playoffs, job status + cancel,
member nights) is ANG-01d-2 and deliberately absent here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar
from dataclasses import asdict, fields

from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.urls import reverse
from django.utils.decorators import method_decorator
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.serializers import Serializer

from accounts.permissions import get_owned_or_404, owned_queryset
from laserforce_simulator.api.exceptions import (
    EnvelopeValidationError,
    django_validation_error_to_envelope,
    validate_form_or_raise,
)
from laserforce_simulator.api.forms import plain_form_data
from laserforce_simulator.api.viewsets import ListOnlyFilterMixin
from matches.api_forms import StandingsParamsForm
from matches.api_refs import conference_ref, league_ref, season_ref, team_ref
from matches.filters import LeagueFilterSet, SeasonFilterSet
from matches.forms import (
    ConferencePartitionForm,
    CreateLeagueForm,
    LeagueTemplateForm,
    MemberNightSetupForm,
    ReassignTeamForm,
)
from matches.league_services import (
    ManagerFiredError,
    NoCompletedSeasonError,
    SeasonInProgressError,
    build_conferences_state,
    build_dashboard_context,
    build_history_row,
    build_season_awards,
    build_season_schedule,
    build_season_standings,
    build_team_schedule,
    compute_season_award_sets,
    create_league_and_season,
    eligible_new_teams,
    ensure_owner_evaluation,
    history_seasons,
    is_career_league,
    latest_completed_season,
    pick_displayed_season,
    manager_awaiting_reassignment,
    reassign_and_roll,
    replace_conferences,
    season_mode_for,
    start_next_season,
    teardown_league,
    template_to_form_data,
)
from matches import league_play_services as play_services
from matches.league_play_services import (
    BrokerUnavailableError,
    NoLiveCursorError,
    NoMemberNightGamesError,
    PlayInProgressError,
    SeasonNotActiveError,
    SeasonNotDraftError,
    WrongPhaseError,
)
from matches.league_screen_api_views import LeagueScreensMixin
from matches.league_templates import LEAGUE_TEMPLATES, LEAGUE_TEMPLATES_BY_KEY
from matches.models import League, Season
from matches.season_awards import ROLE_KEYS, AwardWinner
from matches.season_dashboard import LeaderRow
from matches.serializers import LeagueSerializer, SeasonListSerializer, SeasonSerializer
from matches.standings import StandingsRow
from teams.models import Team

_DELETE_NOT_CAREER = "Delete League is only available for career (league-mode) Leagues."
_FIRING_NOT_CAREER = "Manager firing applies only in single-player career mode."

# The retrieve / write-response prefetch for a Season detail payload.
_SEASON_DETAIL_PREFETCH = ("phases", "conferences__teams", "teams", "map_pool")


# ---------------------------------------------------------------------------
# 409 state conflicts (named codes; the envelope handler shapes them).
# ---------------------------------------------------------------------------


class SeasonInProgress(APIException):
    """409 — the League already has a non-completed Season.

    The detail is keyed ``season:<id>`` so the envelope ``field`` points at the
    in-progress Season (the ``roster_invalid`` ``row:<n>`` precedent).
    """

    status_code = 409
    default_code = "season_in_progress"
    default_detail = "The League already has a Season in progress."

    def __init__(self, season: Season) -> None:
        super().__init__(detail={f"season:{season.pk}": self.default_detail})


class ManagerFired(APIException):
    """409 — the Manager was fired and must reassign before rolling."""

    status_code = 409
    default_code = "manager_fired"
    default_detail = (
        "The Manager was fired. Choose a new team before starting the next Season."
    )


class SeasonNotDraft(APIException):
    """409 — Conferences are editable only while the Season is draft."""

    status_code = 409
    default_code = "season_not_draft"
    default_detail = "Conferences can only be edited while the Season is in draft."


class ManagerNotFired(APIException):
    """409 (UXP-03) — only a fired, not-yet-reassigned Manager picks a new team."""

    status_code = 409
    default_code = "manager_not_fired"
    default_detail = "Only a fired Manager can choose a new team."


class NoCompletedSeason(APIException):
    """409 — the League has no completed Season to roll / pick from."""

    status_code = 409
    default_code = "no_completed_season"
    default_detail = "No completed Season in this League."


class NotCareerLeague(APIException):
    """409 — the action exists only for career (``mode="league"``) Leagues."""

    status_code = 409
    default_code = "not_career_league"
    default_detail = "This action is only available for career (league-mode) Leagues."


class PlayInProgress(APIException):
    """409 — the Season already has an Active play run (ADR-0040).

    The detail is keyed ``job:<id>`` so the envelope ``field`` names the live
    Job the client should poll instead.
    """

    status_code = 409
    default_code = "play_in_progress"
    default_detail = "The Season already has a play run in progress."

    def __init__(self, job_id: str) -> None:
        super().__init__(detail={f"job:{job_id}": self.default_detail})


class SeasonNotActive(APIException):
    """409 — a play action on a Season that is not active."""

    status_code = 409
    default_code = "season_not_active"
    default_detail = "The Season is not active."


class WrongPhase(APIException):
    """409 — the play action does not apply to the Season's current phase."""

    status_code = 409
    default_code = "wrong_phase"
    default_detail = "That play action does not apply to the current phase."


class NoMemberNightGames(APIException):
    """409 — the member-night phase has no unplayed game."""

    status_code = 409
    default_code = "no_member_night_games"
    default_detail = "No member-night game to play."


class NoLiveCursor(APIException):
    """409 — there is no game for the manager's team to play live."""

    status_code = 409
    default_code = "no_live_cursor"
    default_detail = "No live game to play for your team."


class BrokerUnavailable(APIException):
    """503 — the Celery broker refused a play-run enqueue (nothing started)."""

    status_code = 503
    default_code = "broker_unavailable"
    default_detail = "The background task queue is unavailable."


# ---------------------------------------------------------------------------
# Row flatteners — builder output → plain JSON-able dicts.
# ---------------------------------------------------------------------------


def _ref_or_none(team: Team | None) -> dict | None:
    return team_ref(team) if team is not None else None


def _standings_row(row: StandingsRow | dict, team: Team | None) -> dict:
    """The 17 standings keys in ``StandingsRow`` field order + ``team``.

    Works for a ``StandingsRow`` and the 17-key zeroed draft-preview dict.
    """
    source = asdict(row) if isinstance(row, StandingsRow) else row
    out = {f.name: source[f.name] for f in fields(StandingsRow)}
    out["team"] = _ref_or_none(team)
    return out


def _leader(row: LeaderRow) -> dict:
    return asdict(row)


def _winner(winner: AwardWinner | None) -> dict | None:
    return asdict(winner) if winner is not None else None


def _snippet_row(row_dict: dict, team: Team | None) -> dict:
    return {**row_dict, "team": _ref_or_none(team)}


def _dashboard_payload(displayed_season: Season | None, season_mode: str) -> dict:
    """The read-only dashboard body (no play-control keys — those are 01d-2)."""
    body = build_dashboard_context(displayed_season, season_mode)
    return {
        "displayed_season": (
            season_ref(displayed_season) if displayed_season is not None else None
        ),
        "season_mode": season_mode,
        "standings_snippet": [
            _snippet_row(row, team) for row, team in body["standings_snippet"]
        ],
        "next_fixture": body["next_fixture"],
        "round_count_completed": body["round_count_completed"],
        "round_count_total": body["round_count_total"],
        "leaders_points": [_leader(r) for r in body["leaders_points"]],
        "leaders_tags": [_leader(r) for r in body["leaders_tags"]],
        "leaders_ratio": [_leader(r) for r in body["leaders_ratio"]],
        "map_config_label": body["map_config_label"],
        "playoff_tournament_id": body["playoff_tournament_id"],
    }


def _history_item(row: dict) -> dict:
    return {
        **row,
        "champion": _ref_or_none(row["champion"]),
        "runner_up": _ref_or_none(row["runner_up"]),
        "top_three": [_ref_or_none(t) for t in row["top_three"]],
        "season_mvp": _winner(row["season_mvp"]),
        "finals_mvp": _winner(row["finals_mvp"]),
    }


def _conferences_payload(season: Season) -> dict:
    state = build_conferences_state(season)
    return {
        "season": season_ref(season),
        "editable": state["editable"],
        "map_mode": state["map_mode"],
        "teams": [team_ref(t) for t in state["teams"]],
        "conferences": state["conferences"],
        "unassigned_team_ids": state["unassigned_team_ids"],
    }


def _job_ref(season: Season, job_id: str) -> dict:
    """A Job ref for an Active play run (the API-03 200 + Job convention)."""
    return {
        "job_id": job_id,
        "season_id": season.pk,
        "status_url": reverse("season-play-job", args=[season.pk, job_id]),
    }


# One mapping from each play-service domain error to its 409 APIException
# (each factory takes the caught error; ``Any`` so ``.job_id`` type-checks).
_SERVICE_ERRORS: dict[type[Exception], Callable[[Any], APIException]] = {
    PlayInProgressError: lambda exc: PlayInProgress(exc.job_id),
    SeasonNotActiveError: lambda exc: SeasonNotActive(str(exc)),
    SeasonNotDraftError: lambda exc: SeasonNotDraft(str(exc)),
    WrongPhaseError: lambda exc: WrongPhase(str(exc)),
    NoMemberNightGamesError: lambda exc: NoMemberNightGames(str(exc)),
    NoLiveCursorError: lambda exc: NoLiveCursor(str(exc)),
    BrokerUnavailableError: lambda exc: BrokerUnavailable(str(exc)),
}


@contextmanager
def _translate_service_errors() -> Iterator[None]:
    """Re-raise a play-service domain error as its 409 (``_SERVICE_ERRORS``)."""
    try:
        yield
    except tuple(_SERVICE_ERRORS) as exc:
        factory = next(
            _SERVICE_ERRORS[cls] for cls in type(exc).__mro__ if cls in _SERVICE_ERRORS
        )
        raise factory(exc) from exc


def _start_play_run(start: Callable[[], str]) -> str:
    """Run a play-run starter, mapping the service errors to 409s."""
    with _translate_service_errors():
        return start()


_T = TypeVar("_T")


def _run_sync_play(play: Callable[[], _T]) -> _T:
    """Run a Start Season / synchronous play service, mapping its errors.

    Returns the service's result. The service domain errors become 409s; a
    model ``ValidationError`` (e.g. starting a Season with fewer than 2 teams)
    and a simulator ``ValueError`` become 400s.
    """
    try:
        with _translate_service_errors():
            return play()
    except DjangoValidationError as exc:
        raise EnvelopeValidationError(django_validation_error_to_envelope(exc)) from exc
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _season_dashboard_body(season: Season) -> dict:
    """The ``GET /api/seasons/<id>/dashboard/`` body for ``season``."""
    return {
        "season": season_ref(season),
        "league": league_ref(season.league),
        **_dashboard_payload(season, season.state),
    }


def _season_detail(season: Season) -> Season:
    """Re-fetch a Season with the detail prefetch for a serializer payload."""
    return (
        Season.objects.select_related("league", "champion_team")
        .prefetch_related(*_SEASON_DETAIL_PREFETCH)
        .get(pk=season.pk)
    )


# ---------------------------------------------------------------------------
# /api/leagues/
# ---------------------------------------------------------------------------


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 401 JSON; the settings-level `IsAuthenticated` is what gates it. No
# `update` / `partial_update`, so PUT / PATCH on the detail stay 405.
@method_decorator(login_not_required, name="dispatch")
class LeagueViewSet(
    LeagueScreensMixin,
    ListOnlyFilterMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    # Class-level queryset for router introspection; get_queryset() scopes it.
    queryset = League.objects.select_related("current_team")
    serializer_class = LeagueSerializer
    filterset_class = LeagueFilterSet
    ordering_fields = ("name", "created_at", "id")
    ordering = ("-id",)
    ordering_tiebreak = ("-id",)

    def get_queryset(self) -> QuerySet[League]:
        return owned_queryset(
            League.objects.select_related("current_team"), self.request.user
        )

    def create(self, request: Request) -> Response:
        """Create a League + draft Season (template path or Advanced path); 201."""
        # Normalise first so a non-object body is a 400 ``parse_error``.
        template_data = plain_form_data(LeagueTemplateForm, request.data)
        if "template" in request.data:
            template_form = LeagueTemplateForm(data=template_data)
            validate_form_or_raise(template_form)
            template = LEAGUE_TEMPLATES_BY_KEY[template_form.cleaned_data["template"]]
            form = CreateLeagueForm(
                data=template_to_form_data(
                    template,
                    league_name=request.data.get("league_name", ""),
                    difficulty=request.data.get("difficulty", "medium"),
                )
            )
        else:
            form = CreateLeagueForm(
                data=plain_form_data(CreateLeagueForm, request.data)
            )
        validate_form_or_raise(form)
        season = create_league_and_season(form, manager=request.user)
        league = self.get_queryset().get(pk=season.league_id)
        return Response(
            {
                **self.get_serializer(league).data,
                "draft_season": season_ref(season),
                "conference_ids": [c.id for c in season.ordered_conferences()],
            },
            status=status.HTTP_201_CREATED,
        )

    def perform_destroy(self, instance: League) -> None:
        if not is_career_league(instance):
            raise NotCareerLeague(_DELETE_NOT_CAREER)
        teardown_league(instance)

    @action(detail=False, methods=["get"], url_path="templates", url_name="templates")
    def league_templates(self, request: Request) -> Response:
        """The 5 curated create-League presets (not paginated)."""
        return Response([asdict(t) for t in LEAGUE_TEMPLATES])

    @action(detail=True, methods=["get"], url_path="dashboard", url_name="dashboard")
    def dashboard(self, request: Request, pk: str | None = None) -> Response:
        """The League dashboard body for its displayed Season."""
        league = self.get_object()
        displayed_season = pick_displayed_season(league)
        mode = season_mode_for(displayed_season)
        return Response(
            {"league": league_ref(league), **_dashboard_payload(displayed_season, mode)}
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="play-controls",
        url_name="play-controls",
    )
    def play_controls(self, request: Request, pk: str | None = None) -> Response:
        """The structured play controls for the League's displayed Season."""
        league = self.get_object()
        return Response(
            play_services.build_play_controls(league, pick_displayed_season(league))
        )

    @action(detail=True, methods=["get"], url_path="history", url_name="history")
    def history(self, request: Request, pk: str | None = None) -> Response:
        """The League's Seasons (in-progress first, then completed newest-first)."""
        league = self.get_object()
        in_progress, completed, teams_by_id = history_seasons(league)
        seasons = ([in_progress] if in_progress is not None else []) + completed
        page = self.paginate_queryset(seasons)
        items = page if page is not None else seasons
        award_sets = compute_season_award_sets(items)
        data = [
            _history_item(
                build_history_row(
                    s,
                    teams_by_id,
                    is_in_progress=(s is in_progress),
                    award_set=award_sets[s.id],
                )
            )
            for s in items
        ]
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"teams/(?P<team_id>[0-9]+)/schedule",
        url_name="team-schedule",
    )
    def team_schedule(
        self, request: Request, pk: str | None = None, team_id: str | None = None
    ) -> Response:
        """One owned Team's per-Round schedule in the displayed Season."""
        league = self.get_object()
        team = get_owned_or_404(Team, request, pk=team_id)
        displayed_season = pick_displayed_season(league)
        if displayed_season is None:
            raise NotFound("No Season in this League.")
        return Response(
            {
                "league": league_ref(league),
                "season": season_ref(displayed_season),
                "team": team_ref(team),
                **build_team_schedule(displayed_season, team),
            }
        )

    @action(
        detail=True, methods=["post"], url_path="next-season", url_name="next-season"
    )
    @transaction.atomic
    def next_season(self, request: Request, pk: str | None = None) -> Response:
        """Start the next Season (verdict gate + rollover); 201 with it."""
        league = self.get_object()
        try:
            new_season = start_next_season(league)
        except SeasonInProgressError as exc:
            raise SeasonInProgress(exc.season) from exc
        except NoCompletedSeasonError as exc:
            raise NoCompletedSeason() from exc
        except ManagerFiredError as exc:
            raise ManagerFired() from exc
        return Response(
            SeasonSerializer(_season_detail(new_season)).data,
            status=status.HTTP_201_CREATED,
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="new-team-candidates",
        url_name="new-team-candidates",
    )
    def new_team_candidates(self, request: Request, pk: str | None = None) -> Response:
        """The worst-N teams a fired Manager may reassign to (current excluded)."""
        league = self.get_object()
        if not is_career_league(league):
            raise NotCareerLeague(_FIRING_NOT_CAREER)
        latest = latest_completed_season(league)
        if latest is None:
            raise NoCompletedSeason()
        if not manager_awaiting_reassignment(league, latest):
            raise ManagerNotFired()
        return Response(
            {
                "latest_completed": season_ref(latest),
                "candidates": [
                    _standings_row(row, team)
                    for row, team in eligible_new_teams(league, latest)
                ],
            }
        )

    @action(
        detail=True,
        methods=["post"],
        url_path="reassign-team",
        url_name="reassign-team",
    )
    @transaction.atomic
    def reassign_team(self, request: Request, pk: str | None = None) -> Response:
        """Reassign the Manager to an eligible team, then roll; 201."""
        league = self.get_object()
        if not is_career_league(league):
            raise NotCareerLeague(_FIRING_NOT_CAREER)
        active = league.active_season
        if active is not None:
            raise SeasonInProgress(active)
        latest = latest_completed_season(league)
        if latest is None:
            raise NoCompletedSeason()
        if not manager_awaiting_reassignment(league, latest):
            raise ManagerNotFired()
        eligible = eligible_new_teams(league, latest)
        form = ReassignTeamForm(
            data=plain_form_data(ReassignTeamForm, request.data),
            eligible_team_ids=[row.team_id for row, _team in eligible],
        )
        validate_form_or_raise(form)
        team = get_owned_or_404(Team, request, pk=form.cleaned_data["team_id"])
        new_season = reassign_and_roll(league, team, latest)
        return Response(
            SeasonSerializer(_season_detail(new_season)).data,
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# /api/seasons/
# ---------------------------------------------------------------------------


# Seasons are created by League create / next-season only, so POST on the list
# and PUT / PATCH / DELETE on the detail stay 405.
@method_decorator(login_not_required, name="dispatch")
class SeasonViewSet(ListOnlyFilterMixin, viewsets.ReadOnlyModelViewSet):
    queryset = Season.objects.select_related("league", "champion_team")
    filterset_class = SeasonFilterSet
    ordering_fields = ("start_date", "created_at", "id")
    ordering = ("-id",)
    ordering_tiebreak = ("-id",)

    def get_queryset(self) -> QuerySet[Season]:
        qs = owned_queryset(
            Season.objects.select_related("league", "champion_team"),
            self.request.user,
            path="league",
        )
        if self.action == "retrieve":
            return qs.prefetch_related(*_SEASON_DETAIL_PREFETCH)
        return qs

    def get_serializer_class(self) -> type[Serializer]:
        if self.action == "retrieve":
            return SeasonSerializer
        return SeasonListSerializer

    @action(detail=True, methods=["get"], url_path="dashboard", url_name="dashboard")
    def dashboard(self, request: Request, pk: str | None = None) -> Response:
        """The Season dashboard body (mode = the Season's state)."""
        return Response(_season_dashboard_body(self.get_object()))

    # --- ANG-01d-2 T2: Start Season + synchronous play ------------------

    def _play_then_dashboard(self, play: Callable[[Season], None]) -> Response:
        """Run a sync play service on this Season; 200 the refreshed dashboard."""
        season = self.get_object()
        _run_sync_play(lambda: play(season))
        season.refresh_from_db()
        return Response(_season_dashboard_body(season))

    @action(detail=True, methods=["post"], url_path="start", url_name="start")
    def start(self, request: Request, pk: str | None = None) -> Response:
        """Start a draft Season; 409 ``season_not_draft`` otherwise."""
        return self._play_then_dashboard(play_services.start_season)

    @action(detail=True, methods=["post"], url_path="play-week", url_name="play-week")
    def play_week(self, request: Request, pk: str | None = None) -> Response:
        """Play the next matchday (or one bracket stage) synchronously."""
        return self._play_then_dashboard(play_services.play_week)

    @action(
        detail=True,
        methods=["post"],
        url_path="play-single-round",
        url_name="play-single-round",
    )
    def play_single_round(self, request: Request, pk: str | None = None) -> Response:
        """Play one bracket round node synchronously."""
        return self._play_then_dashboard(play_services.play_single_round)

    @action(detail=True, methods=["get"], url_path="standings", url_name="standings")
    def standings(self, request: Request, pk: str | None = None) -> Response:
        """Per-Conference standings, sortable via ``?sort=&dir=``."""
        season = self.get_object()
        form = StandingsParamsForm(
            data=plain_form_data(StandingsParamsForm, request.query_params)
        )
        validate_form_or_raise(form)
        sort = form.cleaned_data["sort"]
        direction = form.cleaned_data["dir"]
        body = build_season_standings(season, sort=sort, direction=direction)
        return Response(
            {
                "season": season_ref(season),
                "is_draft_preview": body["is_draft_preview"],
                "sort": sort,
                "dir": direction,
                "groups": [
                    {
                        "conference": (
                            conference_ref(group["conference"])
                            if group["conference"] is not None
                            else None
                        ),
                        "name": group["name"],
                        "rows": [
                            _standings_row(row, team)
                            for row, team in group["rows_with_teams"]
                        ],
                    }
                    for group in body["standings_groups"]
                ],
            }
        )

    @action(detail=True, methods=["get"], url_path="schedule", url_name="schedule")
    def schedule(self, request: Request, pk: str | None = None) -> Response:
        """Matchdays of fixtures (played Rounds overlaid) + member-night games."""
        season = self.get_object()
        body = build_season_schedule(season)
        return Response(
            {
                "season": season_ref(season),
                "matchdays": [
                    {
                        "matchday": md["matchday"],
                        "date": md["date"],
                        "fixtures": [
                            {
                                **fixture,
                                "team_a": _ref_or_none(fixture["team_a"]),
                                "team_b": _ref_or_none(fixture["team_b"]),
                            }
                            for fixture in md["fixtures"]
                        ],
                    }
                    for md in body["matchdays"]
                ],
                "member_night_games": body["member_night_games"],
            }
        )

    @action(detail=True, methods=["get"], url_path="awards", url_name="awards")
    def awards(self, request: Request, pk: str | None = None) -> Response:
        """LG-03 season-end awards."""
        season = self.get_object()
        body = build_season_awards(season)
        award_set = body["awards"]
        return Response(
            {
                "season": season_ref(season),
                "has_rounds": body["has_rounds"],
                "most_points": _winner(award_set.most_points),
                "best_accuracy": _winner(award_set.best_accuracy),
                "kd_by_role": {
                    role: _winner(award_set.kd_by_role.get(role)) for role in ROLE_KEYS
                },
                "best_medic": _winner(award_set.best_medic),
                "most_efficient_nuke": _winner(award_set.most_efficient_nuke),
                "season_mvp": _winner(award_set.season_mvp),
                "finals_mvp": _winner(award_set.finals_mvp),
            }
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="owner-evaluation",
        url_name="owner-evaluation",
    )
    def owner_evaluation(self, request: Request, pk: str | None = None) -> Response:
        """CAR-02 owner evaluation (lazily writes the finance + eval rows)."""
        season = self.get_object()
        evaluation = ensure_owner_evaluation(season)
        if evaluation is None:
            raise NotFound("No owner evaluation for this Season.")
        league = season.league
        is_fired = evaluation.verdict == "fired"
        return Response(
            {
                "season": season_ref(season),
                "league": league_ref(league),
                "team_managed": (
                    team_ref(evaluation.team_managed)
                    if evaluation.team_managed_id is not None
                    else None
                ),
                "wins_delta": evaluation.wins_delta,
                "playoffs_delta": evaluation.playoffs_delta,
                "money_delta": evaluation.money_delta,
                "wins_total": evaluation.wins_total,
                "playoffs_total": evaluation.playoffs_total,
                "money_total": evaluation.money_total,
                "overall_mood": (
                    evaluation.wins_total
                    + evaluation.playoffs_total
                    + evaluation.money_total
                ),
                "verdict": evaluation.verdict,
                "hot_seat_level": evaluation.hot_seat_level,
                "fired_reason": evaluation.fired_reason,
                "is_fired": is_fired,
                "reassigned": (
                    is_fired and league.current_team_id != evaluation.team_managed_id
                ),
                "created_at": evaluation.created_at,
            }
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="conferences",
        url_name="conferences",
        parser_classes=[JSONParser],
    )
    def conferences(self, request: Request, pk: str | None = None) -> Response:
        """The Season's Conference partition."""
        return Response(_conferences_payload(self.get_object()))

    @conferences.mapping.put
    def put_conferences(self, request: Request, pk: str | None = None) -> Response:
        """Full replace of the partition (draft only); returns the saved state."""
        season = self.get_object()
        if season.state != "draft":
            raise SeasonNotDraft()
        form = ConferencePartitionForm(
            data=plain_form_data(ConferencePartitionForm, request.data), season=season
        )
        validate_form_or_raise(form)
        replace_conferences(season, form.cleaned_data["conferences"])
        return Response(_conferences_payload(season))

    # --- ANG-01d-2: the Active play run (ADR-0040) ----------------------

    @action(
        detail=True,
        methods=["post"],
        url_path="play-two-months",
        url_name="play-two-months",
    )
    def play_two_months(self, request: Request, pk: str | None = None) -> Response:
        """Start a background run of the next 8 matchdays; 200 Job ref."""
        season = self.get_object()
        job_id = _start_play_run(
            lambda: play_services.play_season_run(season, max_matchdays=8)
        )
        return Response(_job_ref(season, job_id))

    @action(
        detail=True,
        methods=["post"],
        url_path="play-until-end",
        url_name="play-until-end",
    )
    def play_until_end(self, request: Request, pk: str | None = None) -> Response:
        """Start a background run of the rest of the regular season."""
        season = self.get_object()
        job_id = _start_play_run(
            lambda: play_services.play_season_run(season, max_matchdays=None)
        )
        return Response(_job_ref(season, job_id))

    @action(
        detail=True,
        methods=["post"],
        url_path="play-playoffs",
        url_name="play-playoffs",
    )
    def play_playoffs(self, request: Request, pk: str | None = None) -> Response:
        """Start a background drain of the built tournament phase."""
        season = self.get_object()
        job_id = _start_play_run(lambda: play_services.play_playoffs_run(season))
        return Response(_job_ref(season, job_id))

    @action(detail=True, methods=["post"], url_path="play-live", url_name="play-live")
    def play_live(self, request: Request, pk: str | None = None) -> Response:
        """One Week (Live): commit the manager's game, run the rest in the
        background; 200 ``{kind, round_ids, job}``."""
        season = self.get_object()
        result = _run_sync_play(lambda: play_services.play_live(season))
        job = _job_ref(season, result.job_id) if result.job_id else None
        return Response(
            {"kind": result.kind, "round_ids": result.round_ids, "job": job}
        )

    # --- ANG-01d-2 T4: member nights ------------------------------------

    @action(
        detail=True, methods=["get"], url_path="member-night", url_name="member-night"
    )
    def member_night(self, request: Request, pk: str | None = None) -> Response:
        """The member-night Sites state (viable Sites + games drawn)."""
        season = self.get_object()
        return Response(
            {
                "season": season_ref(season),
                **play_services.build_member_night_state(season),
            }
        )

    @member_night.mapping.post
    def setup_member_night(self, request: Request, pk: str | None = None) -> Response:
        """Draw games for the chosen Sites (repeat appends); 200 dashboard.

        The state guards run before the Form so a wrong-phase / busy / inactive
        Season is a 409 even when ``sites`` is also invalid.
        """
        season = self.get_object()
        _run_sync_play(lambda: play_services.require_member_night(season))
        form = MemberNightSetupForm(
            data=plain_form_data(MemberNightSetupForm, request.data), season=season
        )
        validate_form_or_raise(form)
        _run_sync_play(
            lambda: play_services.setup_member_night(
                season, form.cleaned_data["sites"], manager=request.user
            )
        )
        season.refresh_from_db()
        return Response(_season_dashboard_body(season))

    @action(
        detail=True,
        methods=["post"],
        url_path="member-night/play",
        url_name="member-night-play",
    )
    def member_night_play(self, request: Request, pk: str | None = None) -> Response:
        """Play every unplayed member-night game as the Active play run."""
        season = self.get_object()
        job_id = _start_play_run(lambda: play_services.play_member_night_run(season))
        return Response(_job_ref(season, job_id))

    @action(
        detail=True,
        methods=["post"],
        url_path="member-night/play-single",
        url_name="member-night-play-single",
    )
    def member_night_play_single(
        self, request: Request, pk: str | None = None
    ) -> Response:
        """Play one member-night game synchronously; 200 dashboard."""
        return self._play_then_dashboard(play_services.play_member_night_single)

    @action(
        detail=True,
        methods=["post"],
        url_path="member-night/play-live",
        url_name="member-night-play-live",
    )
    def member_night_play_live(
        self, request: Request, pk: str | None = None
    ) -> Response:
        """Play one member-night game at full fidelity; 200 ``{kind, round_ids,
        job}`` (``job`` is always null -- no background run)."""
        season = self.get_object()
        result = _run_sync_play(lambda: play_services.play_member_night_live(season))
        return Response(
            {"kind": result.kind, "round_ids": result.round_ids, "job": None}
        )

    @action(
        detail=True, methods=["post"], url_path="play-cancel", url_name="play-cancel"
    )
    def play_cancel(self, request: Request, pk: str | None = None) -> Response:
        """Ask the Active play run to stop; a no-op when nothing is running."""
        season = self.get_object()
        return Response({"cancel_requested": play_services.request_play_cancel(season)})

    @action(
        detail=True,
        methods=["get"],
        url_path=r"play-jobs/(?P<job_id>[^/.]+)",
        url_name="play-job",
    )
    def play_job(
        self, request: Request, pk: str | None = None, job_id: str | None = None
    ) -> Response:
        """Structured Job status. The job id is a capability token (API-03)."""
        self.get_object()
        return Response(play_services.build_play_job_status(job_id or ""))
