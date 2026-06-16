"""FIN-01 — League Finances (league-wide) screen.

GET-only read-only view rendering the league-wide finance table — revenue /
profit / cash / payroll per enrolled Team, sourced from the
``TeamSeasonFinance`` rows of the displayed Season.

Follows the shared LG-01z view contract: 405 GET-guard first line,
``get_object_or_404(League)``, session write, displayed-Season pick, sidebar
links with ``sidebar_active="finances"``, render ``leagues/league_finances.html``,
empty-state notice when there is no Season. Finance-disabled League ⇒ a
"Finances are disabled for this League" notice in place of the body.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_views import _build_league_sidebar_links
from matches.models import League, TeamSeasonFinance


def league_finances(request: HttpRequest, league_id: int) -> HttpResponse:
    """FIN-01 — League Finances (league-wide) page."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )

    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="finances"
    )

    base_context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "finances",
        "finance_enabled": league.finance_enabled,
    }

    # No Season ⇒ empty-state (the sidebar still renders).
    if displayed_season is None:
        context = {**base_context, "rows": []}
        return render(request, "leagues/league_finances.html", context)

    # One row per enrolled Team, pairing the Team with its displayed-Season
    # finance snapshot (None when finance is OFF or no row was written yet).
    finance_by_team = {
        f.team_id: f for f in TeamSeasonFinance.objects.filter(season=displayed_season)
    }
    enrolled_teams = list(displayed_season.teams.order_by("name"))
    rows = []
    for team in enrolled_teams:
        rows.append({"team": team, "finance": finance_by_team.get(team.id)})

    context = {**base_context, "rows": rows}
    return render(request, "leagues/league_finances.html", context)
