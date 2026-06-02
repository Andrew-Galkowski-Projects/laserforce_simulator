"""LG-06f — Watch List as the Player-Stats column set + per-League watch flag.

The Watch List screen is reshaped from the legacy 3-column bookmark table into
the **Player-Stats column set** filtered to watched players (zero-fill for
watched players with no Rounds in scope). Watch lists are now **per-League** in
the browser session — stored as ``request.session["watch_lists"]: dict[str,
list[int]]`` keyed by ``str(league_id)``.

Two callables live here:

* :func:`watch_list` — the GET-only screen view (with the retained
  ``?action=clear`` GET branch) running the Player-Stats pipeline filtered to
  the session's watched ids.
* :func:`watch_list_toggle` — the POST-only, CSRF-protected flag-toggle
  endpoint that flips a player's membership in this League's session list.
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render

from matches.league_screens.player_stats import (
    _PLAYER_STATS_COLUMNS,
    _RATE_OPTIONS,
    _build_round_dicts,
)
from matches.league_views import (
    _LG01F_PER_PAGE_OPTIONS,
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
    _coerce_rate,
    _resolve_season_scope,
    _season_param,
    _watched_player_ids,
)
from matches.models import League
from matches.season_player_stats import (
    STAT_KEYS,
    aggregate_player_stats,
    apply_rate,
    coerce_dir,
    coerce_sort,
    sort_player_stats,
    zero_fill_watched,
)
from teams.models import Player


def watch_list_toggle(request: HttpRequest, league_id: int) -> JsonResponse:
    """LG-06f — POST-only flag toggle.

    Flips a player's membership in this League's session watch list.
    CSRF-protected (NOT exempt). Returns the NEW watched state.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    league = get_object_or_404(League, pk=league_id)

    raw_player_id = request.POST.get("player_id")
    try:
        player_id = int(raw_player_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "invalid player_id"}, status=400)

    if not Player.objects.filter(pk=player_id).exists():
        return JsonResponse({"error": "unknown player_id"}, status=400)

    lists = request.session.get("watch_lists", {})
    if not isinstance(lists, dict):
        lists = {}
    key = str(league.id)
    raw_current = lists.get(key, [])
    current: list[int] = []
    for entry in raw_current:
        if isinstance(entry, bool):
            continue
        if isinstance(entry, int):
            current.append(entry)
            continue
        if isinstance(entry, str):
            try:
                current.append(int(entry))
            except (TypeError, ValueError):
                continue

    if player_id in current:
        current = [pid for pid in current if pid != player_id]
        watched = False
    else:
        current.append(player_id)
        watched = True

    lists[key] = current
    request.session["watch_lists"] = lists
    request.session.modified = True

    return JsonResponse({"watched": watched, "player_id": player_id})


def watch_list(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-06f — Watch List as the Player-Stats column set filtered to watched
    players (zero-fill for watched players with no Rounds in scope)."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)

    # Retained ``?action=clear`` GET branch — clears this League's watch list.
    if request.GET.get("action") == "clear":
        lists = request.session.get("watch_lists", {})
        if not isinstance(lists, dict):
            lists = {}
        lists.pop(str(league.id), None)
        request.session["watch_lists"] = lists
        request.session.modified = True
        return redirect(f"/leagues/{league.id}/players/watch-list/")

    request.session["last_league_id"] = league.id

    watched_ids = _watched_player_ids(request, league.id)

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )

    sidebar_links = _build_league_sidebar_links(league, displayed_season, "watch_list")

    sort = coerce_sort(request.GET.get("sort"))
    direction = coerce_dir(request.GET.get("dir"))
    per_page = _coerce_per_page(request.GET.get("per_page"))
    rate = _coerce_rate(request.GET.get("rate"))

    seasons, selected_season, season_options, season_filter = _resolve_season_scope(
        request, league, displayed_season
    )

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

    # Empty-state per the contract — no Season scope ⇒ render the
    # "No Season" notice (mirrors player_stats' empty branch). NO team filter.
    if season_filter is None:
        context["page_obj"] = None
        context["paginator"] = None
        context["querystring_without_page"] = ""
        context["querystring_without_sort_dir_page"] = ""
        return render(request, "leagues/watch_list.html", context)

    prs_filter = {f"game_round__{key}": value for key, value in season_filter.items()}
    round_dicts = _build_round_dicts(prs_filter)
    rows = aggregate_player_stats(round_dicts)

    # Identity map for zero-fill — built from the watched ids. ``role`` mirrors
    # _build_round_dicts' "role" string; the Player model has no single role
    # field (only ``preferred_roles`` JSON list), so it is the empty string.
    identity_by_id = {
        p.id: {
            "player_name": p.name,
            "team_id": p.team_id if p.team_id is not None else 0,
            "team_name": p.team.name if p.team is not None else "",
            "role": "",
        }
        for p in Player.objects.filter(pk__in=watched_ids).select_related("team")
    }

    # NO team filter on this screen — the Watch List is a personal cross-team set.
    rows = zero_fill_watched(rows, watched_ids, identity_by_id)
    rows = apply_rate(rows, rate)
    rows = sort_player_stats(rows, sort, direction)

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
