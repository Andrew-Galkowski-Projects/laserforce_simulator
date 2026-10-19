"""LG-01z-n — Player Ratings (league-context) screen.

Read-only, GET-only, sortable + paginated table of every Player on a Team
enrolled in a League's displayed Season, showing each player's RATING
attributes — the 19 stat fields plus the derived ``overall_rating`` (NOT
performance stats; those live on the separate Player Stats screen,
LG-01z-o). Each player links to their career / player page
(``player_career_stats`` — ``/players/<id>/stats/``).

Follows the shared LG-01z view contract (§2 of the seam contract):
GET-guard → ``get_owned_or_404(League)`` → session write →
displayed-Season pick → sidebar links with ``sidebar_active="player_ratings"``
→ screen aggregation → render ``leagues/player_ratings.html``. Empty-state
notice when the League has no Season.

Sorting reuses the LG-00c helpers (``_coerce_sort`` / ``_coerce_dir`` /
``_SORT_KEYS`` / ``_SORT_KEYS_DISPLAY``) imported from ``teams.views`` and
the pagination helpers (``_coerce_per_page`` / ``_coerce_page``) from
``matches.league_views`` — no pure module, no reimplementation. The
``overall_rating_db`` annotation gives the ``overall_rating`` sort key an
ORM target (mirrors the LG-00c ``player_list`` precedent).

The screenshot matched is ``league_player_stats.png`` (the zengm "Player
Ratings" page — a sortable per-player ratings table with a per-page
selector + pagination); ``player_detail.png`` shows the same 19 attributes
for a single player and confirms the rating-attribute set.
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404
from matches.league_screen_services import build_player_ratings_queryset
from matches.league_views import (
    RATING_SORT_KEYS_DISPLAY,
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
    _coerce_team_id,
    _LG01F_PER_PAGE_OPTIONS,
)
from matches.models import League
from teams.views import _coerce_dir, _coerce_sort


def player_ratings(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-n — Player Ratings page for a League's displayed Season.

    Sortable (``?sort=&dir=``, LG-00c forgiving fallback) + paginated
    (``?per_page=&page=``) table of the 19 stat ratings + ``overall_rating``
    for every player on an enrolled Team. Renders an empty-state notice
    when the League has no Season.
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
        league, displayed_season, sidebar_active="player_ratings"
    )

    sort = _coerce_sort(request.GET.get("sort"))
    direction = _coerce_dir(request.GET.get("dir"))
    per_page = _coerce_per_page(request.GET.get("per_page"))

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "player_ratings",
        "sort": sort,
        "dir": direction,
        "per_page": per_page,
        "per_page_options": _LG01F_PER_PAGE_OPTIONS,
        "sort_keys": RATING_SORT_KEYS_DISPLAY,
    }

    # Empty-state per §2 — no Season; render the notice instead of the body.
    # The sidebar still renders.
    if displayed_season is None:
        context["enrolled_teams"] = []
        context["selected_team_id"] = None
        context["page_obj"] = None
        context["paginator"] = None
        context["querystring_without_page"] = ""
        context["querystring_without_sort_dir_page"] = ""
        return render(request, "leagues/player_ratings.html", context)

    # LG-06b — team filter. Enrolled teams (the picker options) + the
    # forgiving ``?team_id=`` coercion against the enrolment set.
    enrolled_teams = list(displayed_season.teams.order_by("name"))
    enrolled_ids = {t.id for t in enrolled_teams}
    selected_team_id = _coerce_team_id(request.GET.get("team_id"), enrolled_ids)
    context["enrolled_teams"] = enrolled_teams
    context["selected_team_id"] = selected_team_id

    # ANG-01e -- the shared builder (team filter, then the LG-00c sort:
    # ``preferred_roles`` in Python, ``potential`` nulls last both ways).
    rows = build_player_ratings_queryset(
        displayed_season,
        team_id=selected_team_id,
        sort=sort,
        direction=direction,
    )
    paginator = Paginator(rows, per_page)

    page_obj = paginator.get_page(_coerce_page(request.GET.get("page")))

    # Build querystring helpers from the COERCED values so invalid params
    # do not survive in pagination / re-sort links (LG-00c precedent).
    qs_no_page = request.GET.copy()
    qs_no_page.pop("page", None)
    qs_no_page["sort"] = sort
    qs_no_page["dir"] = direction
    qs_no_page["per_page"] = str(per_page)
    if selected_team_id is not None:
        qs_no_page["team_id"] = str(selected_team_id)
    querystring_without_page = qs_no_page.urlencode()

    qs_no_sort_dir_page = request.GET.copy()
    qs_no_sort_dir_page.pop("page", None)
    qs_no_sort_dir_page.pop("sort", None)
    qs_no_sort_dir_page.pop("dir", None)
    qs_no_sort_dir_page["per_page"] = str(per_page)
    if selected_team_id is not None:
        qs_no_sort_dir_page["team_id"] = str(selected_team_id)
    querystring_without_sort_dir_page = qs_no_sort_dir_page.urlencode()

    context["page_obj"] = page_obj
    context["paginator"] = paginator
    context["querystring_without_page"] = querystring_without_page
    context["querystring_without_sort_dir_page"] = querystring_without_sort_dir_page

    return render(request, "leagues/player_ratings.html", context)
