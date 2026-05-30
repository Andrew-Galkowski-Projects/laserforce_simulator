"""LG-01z-l — Game Log league screen.

Read-only, GET-only view rendering one row per played ``GameRound`` in the
League's displayed Season (chronological), with an optional ``?team_id=``
Team filter. See ``.claude/worktrees/lg-01z-seam-contract.md`` §2 / §4-l.
"""

from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_views import _build_league_sidebar_links
from matches.models import GameRound, League


def game_log(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-l — Game Log page for a League's displayed Season.

    One row per played ``GameRound`` (chronological, ``id`` ascending):
    matchday, date, red team, blue team, score, winner. An optional
    ``?team_id=`` query param filters to Rounds involving that Team
    (invalid / non-enrolled ids are silently ignored). Each row deep-links
    to the Round detail page. Renders an empty-state notice when the
    League has no Season.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="game_log"
    )

    if displayed_season is None:
        return render(
            request,
            "leagues/game_log.html",
            {
                "league": league,
                "displayed_season": None,
                "sidebar_links": sidebar_links,
                "sidebar_active": "game_log",
                "rows": [],
                "team_options": [],
                "selected_team_id": None,
            },
        )

    # Enrolled teams for the filter dropdown (and the valid-id allowlist).
    team_options = list(displayed_season.teams.order_by("name"))
    enrolled_ids = {t.id for t in team_options}

    # Optional ?team_id= filter — silently ignore a bad / non-enrolled id.
    selected_team_id: int | None = None
    raw_team_id = request.GET.get("team_id")
    if raw_team_id is not None:
        try:
            candidate: int | None = int(raw_team_id)
        except (TypeError, ValueError):
            candidate = None
        if candidate is not None and candidate in enrolled_ids:
            selected_team_id = candidate

    rounds_qs = (
        GameRound.objects.filter(match__season=displayed_season)
        .select_related("match", "team_red", "team_blue", "winner")
        .order_by("id")
    )
    if selected_team_id is not None:
        rounds_qs = rounds_qs.filter(
            Q(team_red_id=selected_team_id) | Q(team_blue_id=selected_team_id)
        )

    rows: list[dict] = []
    for game_round in rounds_qs:
        rows.append(
            {
                "round_id": game_round.id,
                "matchday": game_round.round_number,
                "date_played": game_round.date_played,
                "team_red": game_round.team_red,
                "team_blue": game_round.team_blue,
                "red_points": game_round.red_points,
                "blue_points": game_round.blue_points,
                "winner": game_round.winner,
            }
        )

    return render(
        request,
        "leagues/game_log.html",
        {
            "league": league,
            "displayed_season": displayed_season,
            "sidebar_links": sidebar_links,
            "sidebar_active": "game_log",
            "rows": rows,
            "team_options": team_options,
            "selected_team_id": selected_team_id,
        },
    )
