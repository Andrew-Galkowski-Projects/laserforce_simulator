"""LG-06f / ANG-01e -- Watch List as the Player-Stats column set + per-League watch flag.

The Watch List screen shows the **Player-Stats column set** filtered to watched
players (zero-fill for watched players with no Rounds in scope). ANG-01e moved
the watch list from the browser session to the Account: one ``WatchedPlayer``
row per (Account, League, Player), read and written only through
``matches.league_screen_services`` (shared with ``/api/leagues/<id>/watch-list/``).

Three callables live here:

* :func:`watch_list` -- the GET-only screen view, running
  ``build_watch_list_rows`` over the requesting Account's watched ids.
* :func:`watch_list_toggle` -- the POST-only, CSRF-protected flag-toggle
  endpoint that flips a player's membership in this League's list.
* :func:`watch_list_clear` -- the POST-only "Remove All" (it replaced the old
  ``GET ?action=clear``, so a link preview or prefetch cannot wipe the list).
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import redirect, render

from accounts.permissions import get_owned_or_404
from matches.league_screen_services import (
    PlayerNotInLeague,
    add_watch,
    build_watch_list_rows,
    clear_watch_list,
    remove_watch,
    watched_player_ids,
)
from matches.league_screens.player_stats import (
    _PLAYER_STATS_COLUMNS,
    _RATE_OPTIONS,
)
from matches.league_services import pick_displayed_season
from matches.league_views import (
    _LG01F_PER_PAGE_OPTIONS,
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
    _coerce_rate,
    _lenient_season_scope,
    _season_param,
)
from matches.models import League
from matches.season_player_stats import STAT_KEYS, coerce_dir, coerce_sort
from teams.models import Player


def watch_list_toggle(request: HttpRequest, league_id: int) -> JsonResponse:
    """LG-06f -- POST-only flag toggle.

    Flips a player's membership in the Account's watch list for this League.
    CSRF-protected (NOT exempt). Returns the NEW watched state. A Player
    outside the League (on no displayed-Season Team, not a free agent) is a
    400 ``{"error": "player not in league"}``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    league = get_owned_or_404(League, request, pk=league_id)

    raw_player_id = request.POST.get("player_id")
    try:
        player_id = int(raw_player_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "invalid player_id"}, status=400)

    player = Player.objects.filter(pk=player_id).first()
    if player is None:
        return JsonResponse({"error": "unknown player_id"}, status=400)

    if player_id in watched_player_ids(request.user, league):
        remove_watch(request.user, league, player)
        watched = False
    else:
        try:
            add_watch(request.user, league, player)
        except PlayerNotInLeague:
            return JsonResponse({"error": "player not in league"}, status=400)
        watched = True

    return JsonResponse({"watched": watched, "player_id": player_id})


def watch_list_clear(request: HttpRequest, league_id: int) -> HttpResponse:
    """ANG-01e -- POST-only "Remove All": empty this League's list, redirect."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    league = get_owned_or_404(League, request, pk=league_id)
    clear_watch_list(request.user, league)
    return redirect(f"/leagues/{league.id}/players/watch-list/")


def watch_list(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-06f -- Watch List as the Player-Stats column set filtered to watched
    players (zero-fill for watched players with no Rounds in scope)."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_owned_or_404(League, request, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = pick_displayed_season(league)

    sidebar_links = _build_league_sidebar_links(league, displayed_season, "watch_list")

    sort = coerce_sort(request.GET.get("sort"))
    direction = coerce_dir(request.GET.get("dir"))
    per_page = _coerce_per_page(request.GET.get("per_page"))
    rate = _coerce_rate(request.GET.get("rate"))

    scope = _lenient_season_scope(request, league, displayed_season)
    selected_season = scope.selected_id
    season_options = [
        {
            "id": s.id,
            "name": s.name,
            "year": s.start_date.year if s.start_date is not None else None,
        }
        for s in scope.options
    ]

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "watch_list",
        "sort": sort,
        "dir": direction,
        "per_page": per_page,
        "per_page_options": _LG01F_PER_PAGE_OPTIONS,
        "columns": _PLAYER_STATS_COLUMNS,
        "stat_keys": STAT_KEYS,
        "season_options": season_options,
        "selected_season": selected_season,
        "rate": rate,
        "rate_options": _RATE_OPTIONS,
    }

    # Empty-state per the contract -- no Season scope => render the
    # "No Season" notice (mirrors player_stats' empty branch). NO team filter.
    if scope.round_filter is None:
        context["page_obj"] = None
        context["paginator"] = None
        context["querystring_without_page"] = ""
        context["querystring_without_sort_dir_page"] = ""
        return render(request, "leagues/watch_list.html", context)

    # NO team filter on this screen -- the Watch List is a personal cross-team set.
    rows = build_watch_list_rows(
        scope,
        watched_player_ids(request.user, league),
        rate=rate,
        sort=sort,
        direction=direction,
    )

    paginator = Paginator(rows, per_page)
    page_obj = paginator.get_page(_coerce_page(request.GET.get("page")))

    qs_no_page = request.GET.copy()
    qs_no_page.pop("page", None)
    qs_no_page["sort"] = sort
    qs_no_page["dir"] = direction
    qs_no_page["per_page"] = str(per_page)
    qs_no_page["rate"] = rate
    qs_no_page["season"] = _season_param(selected_season)
    querystring_without_page = qs_no_page.urlencode()

    qs_no_sort_dir_page = request.GET.copy()
    qs_no_sort_dir_page.pop("page", None)
    qs_no_sort_dir_page.pop("sort", None)
    qs_no_sort_dir_page.pop("dir", None)
    qs_no_sort_dir_page["per_page"] = str(per_page)
    qs_no_sort_dir_page["rate"] = rate
    qs_no_sort_dir_page["season"] = _season_param(selected_season)
    querystring_without_sort_dir_page = qs_no_sort_dir_page.urlencode()

    context["page_obj"] = page_obj
    context["paginator"] = paginator
    context["querystring_without_page"] = querystring_without_page
    context["querystring_without_sort_dir_page"] = querystring_without_sort_dir_page

    return render(request, "leagues/watch_list.html", context)
