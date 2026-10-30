"""ANG-01e -- the League screens REST API (``/api/leagues/<id>/...``).

``LeagueScreensMixin`` holds the screen ``@action``s; it is mixed into
``LeagueViewSet`` (``matches.league_api_views``), so every action resolves its
League with ``self.get_object()``: another Account's League is a 404 and an
anonymous request is DRF's 401. All computation lives in
``matches.league_screen_services`` (shared with the HTML views). Follows
ADR-0039: one envelope, strict param Forms (bad value = 400), views raise and
never build a 4xx ``Response``. No action reads or writes
``request.session["last_league_id"]`` or builds sidebar links.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.permissions import get_owned_or_404
from laserforce_simulator.api.exceptions import validate_form_or_raise
from laserforce_simulator.api.forms import merged_form_data, plain_form_data
from matches import finance
from matches.api_forms import (
    FreeAgentsParamsForm,
    GameLogParamsForm,
    LeadersParamsForm,
    LeagueScreenParamsForm,
    PlayerRatingsParamsForm,
    PlayerStatsParamsForm,
    PlayoffsParamsForm,
    PowerRankingsParamsForm,
    StatisticalFeatsParamsForm,
    TeamHistoryPlayersParamsForm,
    TeamStatsParamsForm,
    WatchListParamsForm,
    parse_ordering,
)
from matches.api_refs import conference_ref, player_ref, round_ref, season_ref, team_ref
from matches.forms import TeamFinancesForm
from matches.league_screen_services import (
    FinanceDisabled,
    PlayerDetail,
    PlayerNotInLeague,
    SeasonScope,
    TeamNotEditable,
    add_watch,
    build_free_agents_queryset,
    build_game_log,
    build_leaderboards,
    build_league_finances,
    build_player_detail,
    build_player_ratings_queryset,
    build_player_stats,
    build_playoffs,
    build_power_rankings,
    build_statistical_feats,
    build_team_finances,
    build_team_history,
    build_team_history_players,
    build_team_roster,
    build_team_stats,
    build_watch_list_rows,
    clear_watch_list,
    enrolled_team,
    enrolled_teams,
    remove_watch,
    rating_stats,
    resolve_season_scope,
    update_team_finances,
    watched_player_ids,
)
from matches.league_services import pick_displayed_season
from matches.models import GameRound, League, Season
from matches.season_player_stats import PlayerStatRow
from matches.tournament_services.detail import node_payload
from teams.models import Player, Team


def player_bio(player: Player) -> dict:
    """A Player's bio and rating values: ``{overall_rating, potential, age,
    height, home_site, started_playing_age, total_games}`` -- ANG-06-16 B1."""
    return {
        "overall_rating": player.overall_rating,
        "potential": player.potential,
        "age": player.age,
        "height": player.height,
        "home_site": player.home_site,
        "started_playing_age": player.started_playing_age,
        "total_games": player.total_games,
    }


def player_rating_rows(players: list[Player], *, watched_ids: set[int]) -> list[dict]:
    """The Player Ratings / Free Agents row shape: ``{player, watched,
    preferred_roles, overall_rating, potential, age, height, home_site,
    started_playing_age, total_games, stats: {19}}``."""
    return [
        {
            "player": player_ref(player),
            "watched": player.id in watched_ids,
            "preferred_roles": list(player.preferred_roles or []),
            **player_bio(player),
            "stats": rating_stats(player),
        }
        for player in players
    ]


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


# Service exception -> APIException factory.
_SCREEN_SERVICE_ERRORS: dict[type[Exception], Callable[[Exception], APIException]] = {
    PlayerNotInLeague: lambda exc: PlayerNotInLeagueError(),
    FinanceDisabled: lambda exc: FinanceDisabledError(),
    TeamNotEditable: lambda exc: TeamNotEditableError(),
}


def enrolled_team_or_404(league: League, team_id: str | None) -> tuple[Season, Team]:
    """The displayed Season and its enrolled Team ``team_id`` (the path
    kwarg); 404 when the League has no Season or the Team is not enrolled."""
    season = pick_displayed_season(league)
    if season is None:
        raise NotFound("No Season in this League.")
    team = enrolled_team(season, int(team_id or 0))
    if team is None:
        raise NotFound("Team is not enrolled in the displayed Season.")
    return season, team


def season_option(season: Season) -> dict:
    """One Season-picker option: ``season_ref`` plus ``year`` (the start
    date's year, ``None`` without a start date) -- ANG-06-15."""
    return {
        **season_ref(season),
        "year": season.start_date.year if season.start_date is not None else None,
    }


def season_scope_meta(scope: SeasonScope) -> dict:
    """``meta.season_scope``: ``{"selected": season_ref | "career" | None,
    "options": [{id, name, year}]}``."""
    selected = scope.selected
    return {
        "selected": season_ref(selected) if isinstance(selected, Season) else selected,
        "options": [season_option(s) for s in scope.options],
    }


def team_options_meta(season: Season | None) -> list[dict]:
    """``meta.team_options``: the Teams enrolled in ``season`` (the displayed
    one), by name, as ``team_ref``s; ``[]`` for no Season -- ANG-06-15."""
    return [team_ref(team) for team in enrolled_teams(season)]


def team_picker_response(data: Any, season: Season) -> Response:
    """A non-paginated ``Response`` with ``meta.team_options`` for the
    displayed ``season`` -- the team screens' Team picker (ANG-06-16 B2)."""
    response = Response(data)
    response.envelope_meta = {"team_options": team_options_meta(season)}
    return response


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


# ---------------------------------------------------------------------------
# Finances payloads
# ---------------------------------------------------------------------------


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
        # ANG-06-17 B2: the Budget level bounds (the form's min / max / neutral).
        "max_level": finance.MAX_LEVEL,
        "default_level": finance.DEFAULT_LEVEL,
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


# ---------------------------------------------------------------------------
# League player page payload
# ---------------------------------------------------------------------------


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
        "stats": rating_stats(player),
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


# The mixin is only ever mixed into ``LeagueViewSet`` (a ``GenericViewSet``),
# so it relies on ``request`` / ``get_object`` / ``paginator`` /
# ``paginate_queryset`` from that base. Type-check against it; stay a plain
# ``object`` at runtime so the viewset's MRO is unchanged.
if TYPE_CHECKING:
    from rest_framework.generics import GenericAPIView as _ScreensBase
else:
    _ScreensBase = object


class LeagueScreensMixin(_ScreensBase):
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
    def scoped_response(
        data: Any, scope: SeasonScope, *, extra_meta: dict | None = None
    ) -> Response:
        """A non-paginated ``Response`` with ``meta.season_scope`` set;
        ``extra_meta`` (e.g. ``team_options``) is merged beside it."""
        response = Response(data)
        response.envelope_meta = {
            "season_scope": season_scope_meta(scope),
            **(extra_meta or {}),
        }
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

    # --- player list screens ---------------------------------------------

    @action(
        detail=True, methods=["get"], url_path="player-stats", url_name="player-stats"
    )
    def player_stats(self, request: Request, pk: str | None = None) -> Response:
        """The Player Stats screen: per-player performance over a Season scope."""
        league = self.get_object()
        params = self.screen_params(PlayerStatsParamsForm, league)
        displayed_season = pick_displayed_season(league)
        scope = resolve_season_scope(league, displayed_season, params["season"])
        sort, direction = parse_ordering(params["ordering"])
        rows = build_player_stats(
            scope,
            rate=params["rate"],
            team_id=params["team_id"],
            sort=sort,
            direction=direction,
        )
        watched_ids = watched_player_ids(request.user, league)
        return self.paginated_rows(
            rows,
            lambda page: player_stat_rows(page, watched_ids=watched_ids),
            extra_meta={
                "season_scope": season_scope_meta(scope),
                "team_options": team_options_meta(displayed_season),
            },
        )

    @action(
        detail=True,
        methods=["get"],
        url_path="player-ratings",
        url_name="player-ratings",
    )
    def player_ratings(self, request: Request, pk: str | None = None) -> Response:
        """The Player Ratings screen: live ratings of the enrolled Teams' Players."""
        league = self.get_object()
        params = self.screen_params(PlayerRatingsParamsForm, league)
        sort, direction = parse_ordering(params["ordering"])
        displayed_season = pick_displayed_season(league)
        rows = build_player_ratings_queryset(
            displayed_season,
            team_id=params["team_id"],
            sort=sort,
            direction=direction,
        )
        watched_ids = watched_player_ids(request.user, league)
        return self.paginated_rows(
            rows,
            lambda page: player_rating_rows(page, watched_ids=watched_ids),
            extra_meta={"team_options": team_options_meta(displayed_season)},
        )

    @action(detail=True, methods=["get"], url_path="leaders", url_name="leaders")
    def leaders(self, request: Request, pk: str | None = None) -> Response:
        """The League Leaders screen: four top-10 boards in default rank order."""
        league = self.get_object()
        params = self.screen_params(LeadersParamsForm, league)
        scope = resolve_season_scope(
            league, pick_displayed_season(league), params["season"]
        )
        boards = build_leaderboards(scope)
        players = Player.objects.select_related("team").in_bulk(
            {row.player_id for rows in boards.values() for row in rows}
        )
        watched_ids = watched_player_ids(request.user, league)
        data = {
            board: [
                {
                    **asdict(row),
                    "player": (
                        player_ref(players[row.player_id])
                        if row.player_id in players
                        else {
                            "id": row.player_id,
                            "name": row.player_name,
                            "team": None,
                        }
                    ),
                    "watched": row.player_id in watched_ids,
                }
                for row in rows
            ]
            for board, rows in boards.items()
        }
        return self.scoped_response(data, scope)

    @action(
        detail=True, methods=["get"], url_path="free-agents", url_name="free-agents"
    )
    def free_agents(self, request: Request, pk: str | None = None) -> Response:
        """The Free Agents screen: the League's free-agent pool with ratings."""
        league = self.get_object()
        params = self.screen_params(FreeAgentsParamsForm, league)
        sort, direction = parse_ordering(params["ordering"])
        rows = build_free_agents_queryset(
            league, pick_displayed_season(league), sort=sort, direction=direction
        )
        watched_ids = watched_player_ids(request.user, league)
        return self.paginated_rows(
            rows, lambda page: player_rating_rows(page, watched_ids=watched_ids)
        )

    # --- league player page ---------------------------------------------

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
            {
                **league_player_payload(
                    player,
                    detail,
                    watched=player.id in watched_player_ids(request.user, league),
                ),
                # ANG-06-16 B3: the template's team.manager_id == user.id; an
                # unmanaged Team (manager_id None) never matches a signed-in pk.
                "can_link_self_insert": player.team.manager_id == request.user.pk,
            }
        )

    # --- finances -------------------------------------------------------

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
        season, team = enrolled_team_or_404(league, team_id)
        return team_picker_response(team_finances_payload(league, season, team), season)

    @team_finances.mapping.patch
    def update_finances(
        self, request: Request, pk: str | None = None, team_id: str | None = None
    ) -> Response:
        """Change the current Team's budgets / ticket price / injury policy;
        only the sent fields change. Returns the GET payload."""
        league = self.get_object()
        season, team = enrolled_team_or_404(league, team_id)
        form = TeamFinancesForm(
            merged_form_data(TeamFinancesForm, team, request.data), instance=team
        )
        with translate_screen_errors():
            team = update_team_finances(league, team, form)
        validate_form_or_raise(form)
        team.refresh_from_db()
        return team_picker_response(team_finances_payload(league, season, team), season)

    @action(detail=True, methods=["get"], url_path="finances", url_name="finances")
    def league_finances(self, request: Request, pk: str | None = None) -> Response:
        """Every enrolled Team's cash + displayed-Season revenue / profit /
        payroll (``null`` without a snapshot); finances off => empty ``rows``."""
        league = self.get_object()
        if not league.finance_enabled:
            return Response({"finance_enabled": False, "rows": []})
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

    # --- statistical feats + playoffs -----------------------------------

    @action(
        detail=True,
        methods=["get"],
        url_path="statistical-feats",
        url_name="statistical-feats",
    )
    def statistical_feats(self, request: Request, pk: str | None = None) -> Response:
        """Statistical Feats: paged per-(Player, Round) feat rows + team feats."""
        league = self.get_object()
        params = self.screen_params(StatisticalFeatsParamsForm, league)
        displayed_season = pick_displayed_season(league)
        scope = resolve_season_scope(league, displayed_season, params["season"])
        sort, direction = parse_ordering(params["ordering"])
        feat_rows, team_feats = build_statistical_feats(
            scope, team_id=params["team_id"], sort=sort, direction=direction
        )
        page = self.paginate_queryset(feat_rows)
        data = {
            "feats": feat_rows_payload(
                page, watched_ids=watched_player_ids(request.user, league)
            ),
            "team_feats": team_feats_payload(team_feats),
        }
        return self.paginator.get_paginated_response(
            data,
            extra_meta={
                "season_scope": season_scope_meta(scope),
                "team_options": team_options_meta(displayed_season),
            },
        )

    @action(detail=True, methods=["get"], url_path="playoffs", url_name="playoffs")
    def playoffs(self, request: Request, pk: str | None = None) -> Response:
        """Playoffs: the Season's brackets + Worlds qualifiers (no Career)."""
        league = self.get_object()
        params = self.screen_params(PlayoffsParamsForm, league)
        scope = resolve_season_scope(
            league,
            pick_displayed_season(league),
            params["season"],
            allow_career=False,
        )
        season = scope.selected if isinstance(scope.selected, Season) else None
        screen = build_playoffs(season)
        return self.scoped_response(
            {
                "season": season_ref(season) if season is not None else None,
                "brackets": [bracket_payload(b) for b in screen.brackets],
                "worlds_qualifiers": [
                    {**asdict(q), "provenance_label": q.provenance_label}
                    for q in screen.worlds_qualifiers
                ],
            },
            scope,
        )

    # --- team screens ---------------------------------------------------

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
        season, team = enrolled_team_or_404(league, team_id)
        roster = build_team_roster(team)
        watched = watched_player_ids(request.user, league)

        def row(player: Player) -> dict:
            return {
                "player": player_ref(player),
                "watched": player.id in watched,
                **player_bio(player),
            }

        return team_picker_response(
            {
                "team": team_ref(roster.team),
                "starters": [
                    {"role": role, **row(player)} for role, player in roster.starters
                ],
                "bench": [row(player) for player in roster.bench],
            },
            season,
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
        season, team = enrolled_team_or_404(league, team_id)
        history = build_team_history(team)
        overall = history.overall
        return team_picker_response(
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
            },
            season,
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
        _, team = enrolled_team_or_404(league, team_id)
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
        params, displayed_season, scope, sort, direction = self.screen_scope(
            league, GameLogParamsForm
        )
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
        return self.scoped_response(
            data,
            scope,
            extra_meta={"team_options": team_options_meta(displayed_season)},
        )


def _team_ref_or_none(team: Team | None) -> dict | None:
    """``team_ref(team)``, or ``None`` for no Team."""
    return team_ref(team) if team is not None else None


def bracket_payload(bracket: dict) -> dict:
    """One ``build_playoffs`` bracket as refs: ``{key, name, stage,
    stage_label, conference, champion, pending, rounds: [{bracket_round,
    nodes}]}`` (the ``phase`` / ``tournament`` instances are dropped)."""
    conference = bracket["conference"]
    return {
        "key": bracket["key"],
        "name": bracket["name"],
        "stage": bracket["stage"],
        "stage_label": bracket["stage_label"],
        "conference": conference_ref(conference) if conference is not None else None,
        "champion": _team_ref_or_none(bracket["champion"]),
        "pending": bracket["pending"],
        "rounds": [
            {
                "bracket_round": bracket_round["bracket_round"],
                "nodes": [node_payload(node) for node in bracket_round["nodes"]],
            }
            for bracket_round in bracket["rounds"]
        ],
    }


def feat_rows_payload(rows: list, *, watched_ids: set[int]) -> list[dict]:
    """``FeatRow`` flattened (``asdict``) plus ``player`` (ref) and ``watched``."""
    players = Player.objects.select_related("team").in_bulk(
        [row.player_id for row in rows]
    )
    out: list[dict] = []
    for row in rows:
        player = players.get(row.player_id)
        out.append(
            {
                **asdict(row),
                "stats": dict(row.stats),
                "player": (
                    player_ref(player)
                    if player is not None
                    else {"id": row.player_id, "name": row.player_name, "team": None}
                ),
                "watched": row.player_id in watched_ids,
            }
        )
    return out


def team_feats_payload(team_feats: list) -> list[dict]:
    """``{kind, label, team_name, team: team_ref | None, round_id}`` per team feat.

    ``team_name`` is the record's own winner name (what the HTML shows), so it
    is set even when ``team`` is ``None``. ``TeamFeatRecord`` carries only that
    name (Team names are not unique), so the ref resolves from the record's
    Round's Match winner.
    """
    rounds = GameRound.objects.select_related("match__winner").in_bulk(
        [r.round_id for r in team_feats if r.round_id is not None]
    )
    out: list[dict] = []
    for record in team_feats:
        game_round = rounds.get(record.round_id)
        winner = (
            game_round.match.winner
            if game_round is not None and game_round.match is not None
            else None
        )
        out.append(
            {
                "kind": record.kind,
                "label": record.label,
                "team_name": record.team_name,
                "team": team_ref(winner) if winner is not None else None,
                "round_id": record.round_id,
            }
        )
    return out
