"""ANG-01e -- the League screens REST API (``/api/leagues/<id>/...``).

``LeagueScreensMixin`` holds the screen ``@action``s; it is mixed into
``LeagueViewSet`` (``matches.league_api_views``), so every action resolves its
League with ``self.get_object()``: another Account's League is a 404 and an
anonymous request is DRF's 403. All computation lives in
``matches.league_screen_services`` (shared with the HTML views). Follows
ADR-0039: one envelope, strict param Forms (bad value = 400), views raise and
never build a 4xx ``Response``. No action reads or writes
``request.session["last_league_id"]`` or builds sidebar links.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.permissions import get_owned_or_404
from laserforce_simulator.api.exceptions import validate_form_or_raise
from laserforce_simulator.api.forms import plain_form_data
from matches.api_forms import (
    LeagueScreenParamsForm,
    WatchListParamsForm,
    parse_ordering,
)
from matches.api_refs import player_ref, season_ref
from matches.league_screen_services import (
    PlayerNotInLeague,
    SeasonScope,
    add_watch,
    build_watch_list_rows,
    clear_watch_list,
    remove_watch,
    resolve_season_scope,
    watched_player_ids,
)
from matches.league_services import pick_displayed_season
from matches.models import League, Season
from matches.season_player_stats import PlayerStatRow
from teams.models import Player

# --- league player page (T3) imports ---
from matches import development
from matches.league_screen_services import PlayerDetail, build_player_detail

# ---------------------------------------------------------------------------
# Errors (named codes; the envelope handler shapes them)
# ---------------------------------------------------------------------------


class PlayerNotInLeagueError(APIException):
    """400 -- the Player is on no displayed-Season Team and not a free agent."""

    status_code = 400
    default_code = "player_not_in_league"
    default_detail = "The Player is not in this League."

    def __init__(self) -> None:
        super().__init__(detail={"player_id": self.default_detail})


# Service exception -> APIException factory. Later tickets add their entries
# (e.g. ``FinanceDisabled`` / ``TeamNotEditable``).
_SCREEN_SERVICE_ERRORS: dict[type[Exception], Callable[[Exception], APIException]] = {
    PlayerNotInLeague: lambda exc: PlayerNotInLeagueError(),
}


def season_scope_meta(scope: SeasonScope) -> dict:
    """``meta.season_scope``: ``{"selected": season_ref | "career" | None,
    "options": [season_ref]}``."""
    selected = scope.selected
    return {
        "selected": season_ref(selected) if isinstance(selected, Season) else selected,
        "options": [season_ref(s) for s in scope.options],
    }


def player_stat_rows(
    rows: list[PlayerStatRow], *, watched_ids: set[int] | None
) -> list[dict]:
    """The Player-Stats row shape: ``{player, watched, team, role, games, stats}``.

    ``watched_ids=None`` marks every row watched (the Watch List screen).
    One query fetches the page's Players for ``player_ref``.
    """
    players = Player.objects.select_related("team").in_bulk(
        [row.player_id for row in rows]
    )
    out: list[dict] = []
    for row in rows:
        player = players.get(row.player_id)
        out.append(
            {
                "player": (
                    player_ref(player)
                    if player is not None
                    else {"id": row.player_id, "name": row.player_name, "team": None}
                ),
                "watched": watched_ids is None or row.player_id in watched_ids,
                "team": (
                    {"id": row.team_id, "name": row.team_name} if row.team_id else None
                ),
                "role": row.role,
                "games": row.games,
                "stats": dict(row.stats),
            }
        )
    return out


# --- finances (T5) ---
# Imports kept local to this block so sibling tickets' header edits don't clash.
from django.shortcuts import get_object_or_404  # noqa: E402
from rest_framework.exceptions import NotFound  # noqa: E402

from laserforce_simulator.api.forms import merged_form_data  # noqa: E402
from matches.api_refs import team_ref  # noqa: E402
from matches.forms import TeamFinancesForm  # noqa: E402
from matches.league_screen_services import (  # noqa: E402
    FinanceDisabled,
    TeamNotEditable,
    build_league_finances,
    build_team_finances,
    enrolled_team_ids,
    update_team_finances,
)
from teams.models import Team  # noqa: E402


class FinanceDisabledError(APIException):
    """409 -- the League's finances are switched off."""

    status_code = 409
    default_code = "finance_disabled"
    default_detail = "Finances are disabled for this League."


class TeamNotEditableError(APIException):
    """409 -- only the League's ``current_team`` may have its finances edited."""

    status_code = 409
    default_code = "team_not_editable"
    default_detail = "Only the League's current team's finances can be edited."


_SCREEN_SERVICE_ERRORS.update(
    {
        FinanceDisabled: lambda exc: FinanceDisabledError(),
        TeamNotEditable: lambda exc: TeamNotEditableError(),
    }
)


def team_finances_payload(league: League, season: Season | None, team: Team) -> dict:
    """The ``teams/<team_id>/finances/`` ``data``; finances off => the three
    header keys only."""
    editable = league.finance_enabled and team.id == league.current_team_id
    header = {
        "finance_enabled": league.finance_enabled,
        "team": team_ref(team),
        "editable": editable,
    }
    if not league.finance_enabled or season is None:
        return header
    data = build_team_finances(season, team)
    return {
        **header,
        "budgets": {
            "scouting": team.budget_scouting,
            "coaching": team.budget_coaching,
            "facilities": team.budget_facilities,
            "health": team.budget_health,
        },
        "budget_costs": data.budget_costs,
        "ticket_price": team.ticket_price,
        "cash": team.cash,
        "injury_policy": team.injury_policy,
        "live_payroll": data.live_payroll,
        "live_luxury_tax": data.live_luxury_tax,
        "live_min_payroll_penalty": data.live_min_payroll_penalty,
        "record": {"wins": data.wins, "losses": data.losses, "games": data.games},
        "current_hype": data.current_hype,
        "roster_salaries": [
            {"player": player_ref(r["player"]), "salary": r["salary"]}
            for r in data.roster_salaries
        ],
        "availability": [
            {"player": player_ref(r["player"]), "games_remaining": r["games_remaining"]}
            for r in data.availability
        ],
        "history": [_finance_line(row) for row in data.history],
    }


_FINANCE_LINE_FIELDS = (
    "ticket",
    "national_tv",
    "local_tv",
    "sponsor",
    "merch",
    "payroll",
    "scouting_cost",
    "coaching_cost",
    "facilities_cost",
    "health_cost",
    "luxury_tax",
    "min_payroll_penalty",
    "revenue",
    "expenses",
    "profit",
    "hype",
)


def _finance_line(row: Any) -> dict:
    """One ``TeamSeasonFinance`` snapshot: its money lines + ``season``."""
    return {
        "season": season_ref(row.season),
        **{field: getattr(row, field) for field in _FINANCE_LINE_FIELDS},
    }


def finance_team_or_404(league: League, team_id: str | None) -> tuple[Season, Team]:
    """The displayed Season and the enrolled Team ``team_id``; 404 when the
    League has no Season or the Team is not enrolled in it."""
    season = pick_displayed_season(league)
    if season is None:
        raise NotFound("No Season in this League.")
    team = get_object_or_404(Team, pk=team_id, id__in=enrolled_team_ids(season))
    return season, team


# --- league player page (T3) ---


def _season_row_ref(row: dict) -> dict:
    """``{"id", "name"}`` (``season_ref``'s shape) from a builder row dict."""
    return {"id": row["season_id"], "name": row["season_name"]}


def _stat_row_payload(row: dict) -> dict:
    """``{team, games, stats}`` from a ``PlayerDetail`` RS / Career row."""
    return {
        "team": (
            {"id": row["team_id"], "name": row["team_name"]} if row["team_id"] else None
        ),
        "games": row["games"],
        "stats": dict(row["stats"]),
    }


def league_player_payload(
    player: Player, detail: PlayerDetail, *, watched: bool
) -> dict:
    """The ``players/<player_id>/`` ``data`` (spec #180)."""
    return {
        "player": player_ref(player),
        "watched": watched,
        "overall_rating": player.overall_rating,
        "potential": player.potential,
        "bio": {
            "age": player.age,
            "height": player.height,
            "home_site": player.home_site,
            "started_playing_age": player.started_playing_age,
            "total_games": player.total_games,
            "preferred_roles": list(player.preferred_roles or []),
        },
        "stats": {name: getattr(player, name) for name in development.STAT_FIELDS},
        "seasons": [
            {
                "season": {"id": row["season_id"], "name": row["year"]},
                **_stat_row_payload(row),
            }
            for row in detail.rs_rows
        ],
        "career": (
            _stat_row_payload(detail.career_row)
            if detail.career_row is not None
            else None
        ),
        "awards": [
            {"season": _season_row_ref(a), "award_labels": list(a["award_labels"])}
            for a in detail.player_awards
        ],
        "ratings_history": [
            {
                "season": _season_row_ref(r),
                "age": r["age"],
                "overall_rating": r["overall_rating"],
                "potential": r["potential"],
                "stats": dict(r["stats"]),
            }
            for r in detail.ratings_history
        ],
    }


@contextmanager
def translate_screen_errors() -> Iterator[None]:
    """Re-raise a ``league_screen_services`` error as its API error."""
    try:
        yield
    except tuple(_SCREEN_SERVICE_ERRORS) as exc:
        factory = next(
            _SCREEN_SERVICE_ERRORS[cls]
            for cls in type(exc).__mro__
            if cls in _SCREEN_SERVICE_ERRORS
        )
        raise factory(exc) from exc


class LeagueScreensMixin:
    """The League screen actions; mixed into ``LeagueViewSet``.

    Later ANG-01e tickets add their ``@action``s to this class. The pattern:
    ``league = self.get_object()``, ``params = self.screen_params(Form,
    league)``, ``scope = resolve_season_scope(league,
    pick_displayed_season(league), params["season"])``, call the screen's
    ``league_screen_services`` builder, then ``self.paginated_rows(...)`` (or a
    plain ``Response`` with ``season_scope`` set via ``scoped_response``).
    """

    def screen_params(
        self, form_class: type[LeagueScreenParamsForm], league: League
    ) -> dict[str, Any]:
        """Bind ``form_class`` to the query string; 400 on a bad value."""
        form = form_class(
            data=plain_form_data(form_class, self.request.query_params), league=league
        )
        validate_form_or_raise(form)
        return form.cleaned_data

    def paginated_rows(
        self,
        rows: list,
        serialize: Callable[[list], list[dict]],
        *,
        extra_meta: dict | None = None,
    ) -> Response:
        """Page a built list with the shared paginator; ``extra_meta`` (e.g.
        ``season_scope``) is merged beside the page keys in ``meta``."""
        page = self.paginate_queryset(rows)
        return self.paginator.get_paginated_response(
            serialize(page), extra_meta=extra_meta
        )

    @staticmethod
    def scoped_response(data: Any, scope: SeasonScope) -> Response:
        """A non-paginated ``Response`` with ``meta.season_scope`` set."""
        response = Response(data)
        response.envelope_meta = {"season_scope": season_scope_meta(scope)}
        return response

    # --- watch list ------------------------------------------------------

    @action(
        detail=True,
        methods=["put"],
        url_path=r"watch-list/(?P<player_id>[0-9]+)",
        url_name="watch-player",
    )
    def watch_player(
        self, request: Request, pk: str | None = None, player_id: str | None = None
    ) -> Response:
        """Add a Player to the Account's watch list for this League; idempotent."""
        league = self.get_object()
        player = get_owned_or_404(
            Player.objects.select_related("team"), request, pk=player_id
        )
        with translate_screen_errors():
            add_watch(request.user, league, player)
        return Response({"player": player_ref(player), "watched": True})

    @watch_player.mapping.delete
    def unwatch_player(
        self, request: Request, pk: str | None = None, player_id: str | None = None
    ) -> Response:
        """Take a Player off the Account's watch list for this League; idempotent."""
        league = self.get_object()
        player = get_owned_or_404(
            Player.objects.select_related("team"), request, pk=player_id
        )
        remove_watch(request.user, league, player)
        return Response({"player": player_ref(player), "watched": False})

    @action(detail=True, methods=["get"], url_path="watch-list", url_name="watch-list")
    def watch_list(self, request: Request, pk: str | None = None) -> Response:
        """The Watch List screen: Player-Stats rows for the watched Players."""
        league = self.get_object()
        params = self.screen_params(WatchListParamsForm, league)
        scope = resolve_season_scope(
            league, pick_displayed_season(league), params["season"]
        )
        sort, direction = parse_ordering(params["ordering"])
        rows = build_watch_list_rows(
            scope,
            watched_player_ids(request.user, league),
            rate=params["rate"],
            sort=sort,
            direction=direction,
        )
        return self.paginated_rows(
            rows,
            lambda page: player_stat_rows(page, watched_ids=None),
            extra_meta={"season_scope": season_scope_meta(scope)},
        )

    @watch_list.mapping.delete
    def unwatch_all(self, request: Request, pk: str | None = None) -> Response:
        """Clear the Account's watch list for this League."""
        league = self.get_object()
        return Response({"removed": clear_watch_list(request.user, league)})

    # --- league player page (T3) ------------------------------------------

    @action(
        detail=True,
        methods=["get"],
        url_path=r"players/(?P<player_id>[0-9]+)",
        url_name="league-player",
    )
    def league_player(
        self, request: Request, pk: str | None = None, player_id: str | None = None
    ) -> Response:
        """The League player page in one response. Lenient: a Player with no
        games in this League is a 200 with empty sections; only another
        Account's / a missing League or Player is a 404."""
        league = self.get_object()
        player = get_owned_or_404(
            Player.objects.select_related("team"), request, pk=player_id
        )
        detail = build_player_detail(league, player)
        return Response(
            league_player_payload(
                player,
                detail,
                watched=player.id in watched_player_ids(request.user, league),
            )
        )

    # --- finances (T5) ---------------------------------------------------

    @action(
        detail=True,
        methods=["get"],
        url_path=r"teams/(?P<team_id>[0-9]+)/finances",
        url_name="team-finances",
    )
    def team_finances(
        self, request: Request, pk: str | None = None, team_id: str | None = None
    ) -> Response:
        """One enrolled Team's finances in the displayed Season."""
        league = self.get_object()
        season, team = finance_team_or_404(league, team_id)
        return Response(team_finances_payload(league, season, team))

    @team_finances.mapping.patch
    def update_finances(
        self, request: Request, pk: str | None = None, team_id: str | None = None
    ) -> Response:
        """Change the current Team's budgets / ticket price / injury policy;
        only the sent fields change. Returns the GET payload."""
        league = self.get_object()
        season, team = finance_team_or_404(league, team_id)
        form = TeamFinancesForm(
            merged_form_data(TeamFinancesForm, team, request.data), instance=team
        )
        with translate_screen_errors():
            team = update_team_finances(league, team, form)
        validate_form_or_raise(form)
        team.refresh_from_db()
        return Response(team_finances_payload(league, season, team))

    @action(detail=True, methods=["get"], url_path="finances", url_name="finances")
    def league_finances(self, request: Request, pk: str | None = None) -> Response:
        """Every enrolled Team's cash + displayed-Season revenue / profit /
        payroll (``null`` without a snapshot); finances off => no ``rows``."""
        league = self.get_object()
        if not league.finance_enabled:
            return Response({"finance_enabled": False})
        season = pick_displayed_season(league)
        rows = build_league_finances(season) if season is not None else []
        return Response(
            {
                "finance_enabled": True,
                "rows": [
                    {
                        "team": team_ref(row["team"]),
                        "cash": row["team"].cash,
                        "revenue": row["finance"].revenue if row["finance"] else None,
                        "profit": row["finance"].profit if row["finance"] else None,
                        "payroll": row["finance"].payroll if row["finance"] else None,
                    }
                    for row in rows
                ],
            }
        )
