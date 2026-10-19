"""LG-01z-c — Team Roster (league-context) screen.

Read-only view rendering a selected Team's starting six
(``Team.active_roster`` / ``slot_*`` FKs) and bench
(``Team.bench_players``) inside the displayed Season of a League. Each
player links to their career page. A team-picker dropdown lists the
enrolled Teams; the selected Team is read from ``?team_id=`` (validated
against the displayed Season's enrolment) and defaults to
``league.current_team`` (falling back to the first enrolled Team) via the
shared ``league_screen_services.resolve_league_team``.

Follows the shared LG-01z view contract (§2 of the seam contract):
GET-only, ``get_owned_or_404(League)``, session write, displayed-Season
pick, sidebar links with ``sidebar_active="roster"``, render
``leagues/team_roster.html``. Empty-state when the League has no Season
or no Team is resolvable.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404
from matches.league_screen_services import (
    build_team_roster,
    enrolled_teams as _enrolled_teams,
    parse_team_id as _parse_team_id,
    resolve_league_team,
)
from matches.league_views import _build_league_sidebar_links
from matches.models import League


def team_roster(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-c — Team Roster (league-context) page."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_owned_or_404(League, request, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )

    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="roster"
    )

    # No Season ⇒ empty-state (the sidebar still renders).
    if displayed_season is None:
        context = {
            "league": league,
            "displayed_season": None,
            "sidebar_links": sidebar_links,
            "sidebar_active": "roster",
            "team": None,
            "enrolled_teams": [],
            "starting_roster": [],
            "bench_players": [],
        }
        return render(request, "leagues/team_roster.html", context)

    # Teams enrolled in the displayed Season (the team-picker options).
    enrolled_teams = _enrolled_teams(displayed_season)

    # Team selection: ?team_id= must be enrolled; invalid → default
    # (league.current_team if enrolled, else the first enrolled Team).
    team = resolve_league_team(
        league, displayed_season, _parse_team_id(request.GET.get("team_id"))
    )

    if team is None:
        # No resolvable Team — render the no-team notice (sidebar still shows).
        context = {
            "league": league,
            "displayed_season": displayed_season,
            "sidebar_links": sidebar_links,
            "sidebar_active": "roster",
            "team": None,
            "enrolled_teams": enrolled_teams,
            "starting_roster": [],
            "bench_players": [],
        }
        return render(request, "leagues/team_roster.html", context)

    # Starting six: (role, player) tuples for filled slots.
    roster = build_team_roster(team)
    starting_roster = roster.starters
    bench_players = roster.bench

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "roster",
        "team": team,
        "enrolled_teams": enrolled_teams,
        "starting_roster": starting_roster,
        "bench_players": bench_players,
    }
    return render(request, "leagues/team_roster.html", context)
