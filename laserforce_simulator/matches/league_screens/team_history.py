"""LG-01z-e — Team History (league-context, 3-tab) screen.

Read-only view rendering a Team's league history across three Bootstrap
tabs inside a League:

* **Overall** — the team's all-time round-level W-L-T across every Season
  it played (per-Round outcome from each ``GameRound``'s red/blue points,
  mirroring the LG-01g per-Round W/L/T rule — NOT the rolled-up Match
  winner), plus playoff appearances (placeholder ``0`` until LG-02) and
  championships (count of Seasons where ``Season.champion_team == team``).
* **Seasons** — one row per Season the team enrolled in (Season whose
  ``starting_team_ids_json`` contains the team id, falling back to the
  live M2M for draft Seasons): the start-date year, that Season's
  round-level record, and final standing/rank via ``compute_standings``.
* **Players** — every Player who appeared for the team (distinct players
  across the team's GameRounds via ``PlayerRoundState``), with career-long
  aggregate stats, games played on the team, last season played, and a
  colour flag (green = still on the team, blue = now elsewhere). Each
  links to the player's career page.

Team via ``?team_id=`` (validated against the displayed Season's
enrolment), defaulting to ``league.current_team`` then
``_resolve_current_team_for_sidebar``. Follows the shared LG-01z view
contract (§2): GET-only, ``get_object_or_404(League)``, session write,
displayed-Season pick, sidebar links with ``sidebar_active="history_team"``,
render ``leagues/team_history.html``. Empty-state when the League has no
Season or no Team is resolvable.

Heavy aggregation lives in the pure sibling
``matches.team_history_logic``; this view only materialises plain dicts
from the ORM and feeds them across that seam.
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
    _LG01F_PER_PAGE_OPTIONS,
    _resolve_current_team_for_sidebar,
)
from matches.models import GameRound, League, PlayerRoundState, Season
from matches.standings import compute_standings
from matches.team_history_logic import (
    compute_overall_record,
    compute_player_rollups,
    compute_season_rows,
    round_outcome,
)
from teams.models import Team


def _enrolled_team_ids(season: Season) -> list[int]:
    """Team ids enrolled in ``season`` — snapshot first, live M2M fallback.

    Active / completed Seasons read the frozen ``starting_team_ids_json``
    snapshot; draft Seasons (snapshot still ``None``) read the live M2M.
    """
    if season.starting_team_ids_json is not None:
        return list(season.starting_team_ids_json)
    return sorted(t.id for t in season.teams.all())


def _completed_season_ids_for_team(team: Team) -> dict[int, Season]:
    """Map of ``season_id -> Season`` for every Season the team enrolled in.

    Walks every Season across every League the team is enrolled in. Used
    to scope the Overall tab's round corpus and the Seasons tab's rows.
    """
    out: dict[int, Season] = {}
    for season in Season.objects.select_related("league").all():
        if team.id in _enrolled_team_ids(season):
            out[season.id] = season
    return out


def _build_overall_context(team: Team) -> dict:
    """Aggregate the Overall tab — round-level W-L-T + championships."""
    # Every persisted GameRound whose Match belongs to a Season and that
    # the team physically played either Side of. red_points / blue_points
    # store the actual per-Round scores; team_red / team_blue store the
    # actual physical sides — so per-Round W/L/T mirrors LG-01g exactly.
    rounds = (
        GameRound.objects.filter(match__season__isnull=False)
        .filter(match__is_completed=True)
        .filter(Q(team_red=team) | Q(team_blue=team))
        .only("team_red_id", "team_blue_id", "red_points", "blue_points")
    )
    outcomes: list[str] = []
    for gr in rounds:
        if gr.team_red_id == team.id:
            outcomes.append(round_outcome(gr.red_points, gr.blue_points))
        elif gr.team_blue_id == team.id:
            outcomes.append(round_outcome(gr.blue_points, gr.red_points))

    championships = Season.objects.filter(champion_team=team).count()

    record = compute_overall_record(outcomes, championships=championships)
    return {"overall_record": record}


def _build_seasons_context(team: Team, seasons_by_id: dict[int, Season]) -> dict:
    """Aggregate the Seasons tab — one row per enrolled Season."""
    # Newest Season first.
    seasons = sorted(seasons_by_id.values(), key=lambda s: s.id, reverse=True)

    season_dicts: list[dict] = []
    for season in seasons:
        team_ids = _enrolled_team_ids(season)
        enrolled = list(Team.objects.filter(id__in=team_ids).values_list("id", "name"))

        # Per-Season completed Matches → standings dicts for rank, and the
        # team's own per-Round record for the W-L-T columns.
        completed = list(
            season.matches.filter(is_completed=True).prefetch_related("game_rounds")
        )
        match_dicts: list[dict] = []
        wins = losses = ties = 0
        for match in completed:
            match_dicts.append(
                {
                    "match_id": match.id,
                    "team_red_id": match.team_red_id,
                    "team_blue_id": match.team_blue_id,
                    "winner_team_id": match.winner_id,
                    "red_rounds_won": match.red_rounds_won,
                    "blue_rounds_won": match.blue_rounds_won,
                    "red_total_points": match.red_total_points,
                    "blue_total_points": match.blue_total_points,
                }
            )
            for gr in match.game_rounds.all():
                if gr.team_red_id == team.id:
                    outcome = round_outcome(gr.red_points, gr.blue_points)
                elif gr.team_blue_id == team.id:
                    outcome = round_outcome(gr.blue_points, gr.red_points)
                else:
                    continue
                if outcome == "W":
                    wins += 1
                elif outcome == "L":
                    losses += 1
                else:
                    ties += 1

        standings = compute_standings(match_dicts, enrolled)
        rank = next((r.rank for r in standings if r.team_id == team.id), None)

        year = season.start_date.year if season.start_date is not None else None
        season_dicts.append(
            {
                "season_id": season.id,
                "year": year,
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "rank": rank,
            }
        )

    return {"season_rows": compute_season_rows(season_dicts)}


def _build_players_context(team: Team, seasons_by_id: dict[int, Season]) -> dict:
    """Aggregate the Players tab — every player who appeared for the team."""
    # Every PlayerRoundState row where the player physically played for the
    # team in one of the team's GameRounds (either Side).
    states = (
        PlayerRoundState.objects.filter(
            Q(game_round__team_red=team, team_color="red")
            | Q(game_round__team_blue=team, team_color="blue")
        )
        .select_related("player", "game_round", "game_round__match")
        .order_by("id")
    )

    # Resolve each round's Season start-year for the "last season played"
    # column without a per-row query.
    season_year_by_id = {
        sid: (s.start_date.year if s.start_date is not None else None)
        for sid, s in seasons_by_id.items()
    }

    player_round_dicts: list[dict] = []
    for prs in states:
        match = prs.game_round.match
        season_id = match.season_id if match is not None else None
        season_year = season_year_by_id.get(season_id) if season_id else None
        player_round_dicts.append(
            {
                "player_id": prs.player_id,
                "player_name": prs.player.name,
                "on_team": prs.player.team_id == team.id,
                "season_year": season_year,
                "points_scored": prs.points_scored,
                "tags_made": prs.tags_made,
                "times_tagged": prs.times_tagged,
                "missiles_landed": prs.missiles_landed,
                "resupplies_given": prs.resupplies_given,
                "specials_used": prs.specials_used,
            }
        )

    return {"player_rollups": compute_player_rollups(player_round_dicts)}


def _resolve_team(
    request: HttpRequest, league: League, displayed_season: Season
) -> Team | None:
    """Pick the Team to display: ?team_id= (enrolled) → current_team → resolver."""
    enrolled_teams = list(displayed_season.teams.order_by("name"))
    enrolled_ids = {t.id for t in enrolled_teams}

    raw_team_id = request.GET.get("team_id")
    if raw_team_id is not None:
        try:
            requested_id = int(raw_team_id)
        except (TypeError, ValueError):
            requested_id = None
        if requested_id is not None and requested_id in enrolled_ids:
            return next((t for t in enrolled_teams if t.id == requested_id), None)

    current_team_id = league.current_team_id
    if current_team_id is not None and current_team_id in enrolled_ids:
        return next((t for t in enrolled_teams if t.id == current_team_id), None)

    resolved = _resolve_current_team_for_sidebar(league, displayed_season)
    if resolved is not None and resolved.id in enrolled_ids:
        return next((t for t in enrolled_teams if t.id == resolved.id), None)
    return None


def team_history(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-e — Team History (league-context, 3-tab) page."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )

    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="history_team"
    )

    per_page = _coerce_per_page(request.GET.get("per_page"))

    base_context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "history_team",
        "per_page": per_page,
        "per_page_options": _LG01F_PER_PAGE_OPTIONS,
    }

    # No Season ⇒ empty-state (the sidebar still renders).
    if displayed_season is None:
        context = {
            **base_context,
            "team": None,
            "enrolled_teams": [],
            "overall_record": None,
            "season_rows": [],
            "page_obj": None,
            "paginator": None,
            "players_querystring_without_page": "",
        }
        return render(request, "leagues/team_history.html", context)

    enrolled_teams = list(displayed_season.teams.order_by("name"))
    team = _resolve_team(request, league, displayed_season)

    if team is None:
        context = {
            **base_context,
            "team": None,
            "enrolled_teams": enrolled_teams,
            "overall_record": None,
            "season_rows": [],
            "page_obj": None,
            "paginator": None,
            "players_querystring_without_page": "",
        }
        return render(request, "leagues/team_history.html", context)

    seasons_by_id = _completed_season_ids_for_team(team)

    players_context = _build_players_context(team, seasons_by_id)
    paginator = Paginator(players_context["player_rollups"], per_page)
    page_obj = paginator.get_page(_coerce_page(request.GET.get("page")))

    # Players pagination links carry the resolved team_id and omit page so
    # switching page stays on this team.
    players_qs = request.GET.copy()
    players_qs.pop("page", None)
    players_qs["team_id"] = str(team.id)
    players_querystring_without_page = players_qs.urlencode()

    context = {
        **base_context,
        "team": team,
        "enrolled_teams": enrolled_teams,
        **_build_overall_context(team),
        **_build_seasons_context(team, seasons_by_id),
        "page_obj": page_obj,
        "paginator": paginator,
        "players_querystring_without_page": players_querystring_without_page,
    }
    return render(request, "leagues/team_history.html", context)
