"""LG-01z-b — Power Rankings league screen view.

Read-only, GET-only screen rendering a ranked table of every Team enrolled
in the League's ``displayed_season``, scored by a composite **power score**
(see ``matches/power_rankings_logic.py``). Follows the LG-01z shared view
contract (§2): GET-guard → ``get_owned_or_404`` → session write →
``displayed_season`` pick → sidebar links → screen aggregation → render.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404
from matches.league_views import (
    _build_league_sidebar_links,
    _resolve_season_scope,
    _season_param,
)
from matches.league_screen_services import build_power_rankings
from matches.models import League
from matches.power_rankings_logic import SORT_KEYS_DISPLAY, coerce_dir, coerce_sort


def power_rankings(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-b — Power Rankings page for a League's displayed Season.

    Ranks enrolled Teams by a composite power score = sum of three
    min-max-normalized (per-League, to [0, 1]) components: (1) team mean
    ``overall_rating`` over ``active_players``; (2) win% from completed
    Matches via ``compute_standings`` = wins / (wins + losses + ties)
    (0 when none); (3) avg score diff per Round (mean of
    ``red_points - blue_points`` from the team's perspective). Highest sum
    ranks #1; ties break on team name ascending. Only Teams enrolled in
    ``displayed_season`` are ranked.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_owned_or_404(League, request, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="power_rankings"
    )

    sort = coerce_sort(request.GET.get("sort"))
    direction = coerce_dir(request.GET.get("dir"))

    # LG-06d — season selector. Picker options + the forgiving ``?season=``
    # coercion (defaults to displayed_season — fully backward-compatible).
    seasons, selected_season, season_options, season_filter = _resolve_season_scope(
        request, league, displayed_season
    )

    # COERCE-BEFORE-QUERYSTRING: the sort-header href carries every param
    # except sort/dir, with the coerced ``season`` re-set so the chosen scope
    # survives a re-sort (LG-06d).
    qs_no_sort_dir = request.GET.copy()
    qs_no_sort_dir.pop("sort", None)
    qs_no_sort_dir.pop("dir", None)
    qs_no_sort_dir["season"] = _season_param(selected_season)
    querystring_without_sort_dir = qs_no_sort_dir.urlencode()

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "power_rankings",
        "sort": sort,
        "dir": direction,
        "sort_keys_display": SORT_KEYS_DISPLAY,
        "season_options": season_options,
        "selected_season": selected_season,
        "querystring_without_sort_dir": querystring_without_sort_dir,
    }

    if season_filter is None:
        # Empty-state per §2 — no Season scope, render the notice instead of
        # the body. The sidebar still renders.
        context["rows"] = []
        return render(request, "leagues/power_rankings.html", context)

    rows = build_power_rankings(
        displayed_season, season_filter, sort=sort, direction=direction
    )

    context["rows"] = rows
    return render(request, "leagues/power_rankings.html", context)
