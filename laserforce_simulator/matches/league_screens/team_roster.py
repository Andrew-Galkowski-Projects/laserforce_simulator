"""LG-01z-c — Team Roster (league-context) screen.

Read-only view rendering a selected Team's starting six
(``Team.active_roster`` / ``slot_*`` FKs) and bench
(``Team.bench_players``) inside the displayed Season of a League. Each
player links to their career page. A team-picker dropdown lists the
enrolled Teams; the selected Team is read from ``?team_id=`` (validated
against the displayed Season's enrolment) and defaults to
``league.current_team`` (falling back to
``_resolve_current_team_for_sidebar``).

Follows the shared LG-01z view contract (§2 of the seam contract):
GET-only, ``get_object_or_404(League)``, session write, displayed-Season
pick, sidebar links with ``sidebar_active="roster"``, render
``leagues/team_roster.html``. Empty-state when the League has no Season
or no Team is resolvable.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_views import (
    _build_league_sidebar_links,
    _resolve_current_team_for_sidebar,
)
from matches.models import League
from teams.models import Team


def team_roster(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-c — Team Roster (league-context) page."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)
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
    enrolled_teams = list(displayed_season.teams.order_by("name"))
    enrolled_ids = {t.id for t in enrolled_teams}

    # Team selection: ?team_id= must be enrolled; invalid → default.
    team: Team | None = None
    raw_team_id = request.GET.get("team_id")
    if raw_team_id is not None:
        try:
            requested_id = int(raw_team_id)
        except (TypeError, ValueError):
            requested_id = None
        if requested_id is not None and requested_id in enrolled_ids:
            team = next((t for t in enrolled_teams if t.id == requested_id), None)

    # Default: league.current_team if enrolled, else the sidebar resolver.
    if team is None:
        current_team_id = league.current_team_id
        if current_team_id is not None and current_team_id in enrolled_ids:
            team = next((t for t in enrolled_teams if t.id == current_team_id), None)
    if team is None:
        resolved = _resolve_current_team_for_sidebar(league, displayed_season)
        if resolved is not None and resolved.id in enrolled_ids:
            team = next((t for t in enrolled_teams if t.id == resolved.id), None)

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
    starting_roster = team.active_roster
    bench_players = team.bench_players

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
