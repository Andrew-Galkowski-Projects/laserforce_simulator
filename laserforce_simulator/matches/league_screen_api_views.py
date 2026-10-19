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
from matches.api_refs import player_ref, season_ref, team_ref
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

# --- team screens (T4) imports ---
from dataclasses import asdict

from django.http import Http404

from matches.api_forms import (
    GameLogParamsForm,
    PowerRankingsParamsForm,
    TeamHistoryPlayersParamsForm,
    TeamStatsParamsForm,
)
from matches.api_refs import round_ref
from matches.league_screen_services import (
    build_game_log,
    build_power_rankings,
    build_team_history,
    build_team_history_players,
    build_team_roster,
    build_team_stats,
    enrolled_team,
)
from teams.models import Team

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

    # --- team screens (T4) -----------------------------------------------

    def screen_team(self, league: League, team_id: str | None) -> Team:
        """The path's Team, enrolled in the displayed Season; else 404."""
        team = enrolled_team(pick_displayed_season(league), int(team_id or 0))
        if team is None:
            raise Http404("Team is not enrolled in the displayed Season.")
        return team

    @action(
        detail=True,
        methods=["get"],
        url_path=r"teams/(?P<team_id>[0-9]+)/roster",
        url_name="team-roster",
    )
    def team_roster(
        self, request: Request, pk: str | None = None, team_id: str | None = None
    ) -> Response:
        """A Team's starters by role plus the bench."""
        league = self.get_object()
        roster = build_team_roster(self.screen_team(league, team_id))
        watched = watched_player_ids(request.user, league)

        def row(player: Player) -> dict:
            return {"player": player_ref(player), "watched": player.id in watched}

        return Response(
            {
                "team": team_ref(roster.team),
                "starters": [
                    {"role": role, **row(player)} for role, player in roster.starters
                ],
                "bench": [row(player) for player in roster.bench],
            }
        )

    @action(
        detail=True,
        methods=["get"],
        url_path=r"teams/(?P<team_id>[0-9]+)/history",
        url_name="team-history",
    )
    def team_history(
        self, request: Request, pk: str | None = None, team_id: str | None = None
    ) -> Response:
        """A Team's Overall record and Seasons table (year descending)."""
        league = self.get_object()
        history = build_team_history(self.screen_team(league, team_id))
        overall = history.overall
        return Response(
            {
                "team": team_ref(history.team),
                "overall": {
                    "wins": overall.wins,
                    "losses": overall.losses,
                    "ties": overall.ties,
                    "playoff_appearances": overall.playoff_appearances,
                    "championships": overall.championships,
                },
                "seasons": [
                    {
                        "season": season_ref(history.seasons_by_id[row.season_id]),
                        "year": row.year,
                        "wins": row.wins,
                        "losses": row.losses,
                        "ties": row.ties,
                        "rank": row.rank,
                    }
                    for row in history.seasons
                ],
            }
        )

    @action(
        detail=True,
        methods=["get"],
        url_path=r"teams/(?P<team_id>[0-9]+)/history/players",
        url_name="team-history-players",
    )
    def team_history_players(
        self, request: Request, pk: str | None = None, team_id: str | None = None
    ) -> Response:
        """Team History's Players tab: every Player who appeared for the Team."""
        league = self.get_object()
        team = self.screen_team(league, team_id)
        params = self.screen_params(TeamHistoryPlayersParamsForm, league)
        sort, direction = parse_ordering(params["ordering"])
        rows = build_team_history_players(team, sort=sort, direction=direction)
        watched = watched_player_ids(request.user, league)

        def serialize(page: list) -> list[dict]:
            players = Player.objects.select_related("team").in_bulk(
                [row.player_id for row in page]
            )
            return [
                {
                    "player": (
                        player_ref(players[row.player_id])
                        if row.player_id in players
                        else {"id": row.player_id, "name": row.name, "team": None}
                    ),
                    "watched": row.player_id in watched,
                    "on_team": row.on_team,
                    "games_played": row.games_played,
                    "last_season_year": row.last_season_year,
                    "stats": dict(row.stats),
                }
                for row in page
            ]

        return self.paginated_rows(rows, serialize)

    def screen_scope(
        self, league: League, form_class: type[LeagueScreenParamsForm]
    ) -> tuple[dict[str, Any], Season | None, SeasonScope, str, str]:
        """Validate a Season-scoped screen's params; return ``(params,
        displayed_season, scope, sort, direction)``."""
        params = self.screen_params(form_class, league)
        displayed_season = pick_displayed_season(league)
        scope = resolve_season_scope(league, displayed_season, params["season"])
        sort, direction = parse_ordering(params["ordering"])
        return params, displayed_season, scope, sort, direction

    @action(detail=True, methods=["get"], url_path="team-stats", url_name="team-stats")
    def team_stats(self, request: Request, pk: str | None = None) -> Response:
        """Per-Team stats for a Season or the League career, sortable."""
        league = self.get_object()
        _, displayed_season, scope, sort, direction = self.screen_scope(
            league, TeamStatsParamsForm
        )
        rows = build_team_stats(
            displayed_season, scope.round_filter, sort=sort, direction=direction
        )
        teams = Team.objects.in_bulk([row.team_id for row in rows])
        data = [
            {
                **asdict(row),
                "team": (
                    team_ref(teams[row.team_id])
                    if row.team_id in teams
                    else {"id": row.team_id, "name": row.team_name}
                ),
            }
            for row in rows
        ]
        return self.scoped_response(data, scope)

    @action(
        detail=True,
        methods=["get"],
        url_path="power-rankings",
        url_name="power-rankings",
    )
    def power_rankings(self, request: Request, pk: str | None = None) -> Response:
        """Power rankings for a Season or the League career, sortable."""
        league = self.get_object()
        _, displayed_season, scope, sort, direction = self.screen_scope(
            league, PowerRankingsParamsForm
        )
        rows = build_power_rankings(
            displayed_season, scope.round_filter, sort=sort, direction=direction
        )
        data = [
            {
                **asdict(row),
                "team": (
                    team_ref(team)
                    if team is not None
                    else {"id": row.team_id, "name": row.team_name}
                ),
            }
            for row, team in rows
        ]
        return self.scoped_response(data, scope)

    @action(detail=True, methods=["get"], url_path="game-log", url_name="game-log")
    def game_log(self, request: Request, pk: str | None = None) -> Response:
        """One row per played Round in scope, filterable by Team, sortable."""
        league = self.get_object()
        params, _, scope, sort, direction = self.screen_scope(league, GameLogParamsForm)
        rows = build_game_log(
            scope.round_filter,
            team_id=params["team_id"],
            sort=sort,
            direction=direction,
        )
        data = [
            {
                "round": round_ref(row["game_round"]),
                "red_points": row["red_points"],
                "blue_points": row["blue_points"],
                "winner": team_ref(row["winner"]) if row["winner"] else None,
            }
            for row in rows
        ]
        return self.scoped_response(data, scope)
