"""LG-01z-p — Team Stats league screen view.

Read-only, GET-only screen rendering a sortable per-team statistics table
for the League's ``displayed_season``. Follows the LG-01z shared view
contract (§2): GET-guard → ``get_owned_or_404`` → session write →
``displayed_season`` pick → sidebar links → screen aggregation → render.

Heavy aggregation lives in the pure module ``matches/team_stats_logic.py``;
this view materialises plain per-round + per-event dicts from the ORM and
hands them over. See ``.claude/worktrees/lg-01z-seam-contract.md`` §4-p and
the event-type → column mapping documented in ``team_stats_logic`` /
``matches/CLAUDE.md``.
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
from matches.league_screen_services import build_team_stats
from matches.models import League
from matches.team_stats_logic import SORT_KEYS_DISPLAY, coerce_dir, coerce_sort


def team_stats(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-p — Team Stats page for a League's displayed Season.

    Aggregates per-team stats over the displayed Season's completed Rounds
    and their GameEvents: avg points-for / against / margin, avg survivors
    (count of ``final_lives > 0`` per Round), total tags landed, total times
    tagged, base captures, missiles fired / hit, nukes fired / landed, and
    cancelled-nuke count. Sortable via ``?sort=&dir=``. Only Teams enrolled
    in ``displayed_season`` are listed. Renders an empty-state notice when
    the League has no Season.
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
        league, displayed_season, sidebar_active="team_stats"
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
        "sidebar_active": "team_stats",
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
        return render(request, "leagues/team_stats.html", context)

    rows = build_team_stats(
        displayed_season, season_filter, sort=sort, direction=direction
    )

    context["rows"] = rows
    return render(request, "leagues/team_stats.html", context)
