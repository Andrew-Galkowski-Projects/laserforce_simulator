"""LG-01z-o — Player Stats (league-context performance) screen.

Read-only, GET-only, sortable + paginated table of per-player PERFORMANCE
aggregated across a League's displayed Season's completed Rounds — the
HX-01 ``STAT_KEYS`` set (NOT the rating attributes; those live on the
separate Player Ratings screen, LG-01z-n). Each player links to their
career / player page (``player_career_stats`` — ``/players/<id>/stats/``).

Follows the shared LG-01z view contract (§2 of the seam contract):
GET-guard → ``get_owned_or_404(League)`` → session write →
displayed-Season pick → sidebar links with ``sidebar_active="player_stats"``
→ screen aggregation → render ``leagues/player_stats.html``. Empty-state
notice when the League has no Season.

The view reads ``get_mvp`` / ``get_accuracy`` per ``PlayerRoundState`` row,
builds a flat list of plain dicts, and hands them to the pure aggregation
module ``matches.season_player_stats`` — the only thing crossing the
view ↔ pure-module seam. The pure module sums the count keys, averages
``mvp`` / ``accuracy``, sorts, and the view paginates over the result.

Pagination reuses ``_coerce_per_page`` / ``_coerce_page`` from
``matches.league_views``; sorting uses the local forgiving validators
``coerce_sort`` / ``coerce_dir`` from the pure module (the LG-00c
``teams.views._SORT_KEYS`` whitelist covers RATING keys, not these
PERFORMANCE keys, so a screen-local validator over the STAT_KEYS is used).

The screenshot matched is ``league_player_stats.png`` (the zengm sortable
per-player stats table with a per-page selector + pagination).
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404
from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
    _coerce_rate,
    _coerce_team_id,
    _lenient_season_scope,
    _season_param,
    _LG01F_PER_PAGE_OPTIONS,
)
from matches.league_screen_services import build_player_stats, enrolled_teams
from matches.models import League
from matches.season_player_stats import (
    STAT_KEYS,
    coerce_dir,
    coerce_sort,
)

# LG-06d — the three rate-toggle options, ``(value, label)`` pairs (the
# template renders the selector). Totals / Per Game / Per 10 min — the
# laser-tag analogue of ZenGM's Per-36 (denominator = total uptime seconds).
_RATE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("total", "Totals"),
    ("per_game", "Per Game"),
    ("per_10", "Per 10 min"),
)

# Column display spec (single source of truth for both the <th> headers
# and the per-row <td> cells). The first three columns are the player
# identity / team / games; the remaining 12 are the STAT_KEYS in order.
# Each entry: (sort_key, label, is_float). ``is_float`` flags the two
# averaged keys (mvp / accuracy) so the template can render them with a
# decimal place.
_PLAYER_STATS_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("name", "Name", False),
    ("team", "Team", False),
    ("games", "GP", False),
    ("points_scored", "Points", False),
    ("mvp", "MVP", True),
    ("tags_made", "Tags", False),
    ("times_tagged", "Tagged", False),
    ("tag_ratio", "Tag Ratio", True),
    ("accuracy", "Acc%", True),
    ("survival", "Survival", True),
    ("final_lives", "Lives", False),
    ("resupplies_given", "Resup", False),
    ("missiles_landed", "Missiles", False),
    ("specials_used", "Specials", False),
    ("follow_up_shots", "Follow-up", False),
    ("reaction_shots", "Reaction", False),
    ("combo_resupply_count", "Combo Resup", False),
)


def player_stats(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-o — Player Stats (performance) page for a League's Season.

    Sortable (``?sort=&dir=``, forgiving fallback over the STAT_KEYS) +
    paginated (``?per_page=&page=``) table of per-player Season
    performance totals (counts summed, mvp / accuracy averaged) over the
    displayed Season's Rounds. Renders an empty-state notice when the
    League has no Season.
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
        league, displayed_season, sidebar_active="player_stats"
    )

    sort = coerce_sort(request.GET.get("sort"))
    direction = coerce_dir(request.GET.get("dir"))
    per_page = _coerce_per_page(request.GET.get("per_page"))
    rate = _coerce_rate(request.GET.get("rate"))

    # LG-06d — season selector. Picker options + the forgiving ``?season=``
    # coercion (defaults to displayed_season — fully backward-compatible).
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
        "sidebar_active": "player_stats",
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

    # Empty-state per §2 — no Season scope (the League has no Season); render
    # the notice instead of the body. The sidebar still renders.
    if scope.round_filter is None:
        context["enrolled_teams"] = []
        context["selected_team_id"] = None
        context["page_obj"] = None
        context["paginator"] = None
        context["querystring_without_page"] = ""
        context["querystring_without_sort_dir_page"] = ""
        return render(request, "leagues/player_stats.html", context)

    # LG-06b — team filter. Enrolled teams (the picker options) + the
    # forgiving ``?team_id=`` coercion against the enrolment set. The picker
    # lists the displayed Season's enrolment even under a Career / past-Season
    # scope (the displayed Season is the League's current roster).
    team_options = enrolled_teams(displayed_season)
    enrolled_ids = {t.id for t in team_options}
    selected_team_id = _coerce_team_id(request.GET.get("team_id"), enrolled_ids)
    context["enrolled_teams"] = team_options
    context["selected_team_id"] = selected_team_id

    # ANG-01e -- the shared builder (aggregate -> apply_rate -> team filter ->
    # sort); the view only paginates.
    rows = build_player_stats(
        scope,
        rate=rate,
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
    qs_no_page["rate"] = rate
    qs_no_page["season"] = _season_param(selected_season)
    if selected_team_id is not None:
        qs_no_page["team_id"] = str(selected_team_id)
    querystring_without_page = qs_no_page.urlencode()

    qs_no_sort_dir_page = request.GET.copy()
    qs_no_sort_dir_page.pop("page", None)
    qs_no_sort_dir_page.pop("sort", None)
    qs_no_sort_dir_page.pop("dir", None)
    qs_no_sort_dir_page["per_page"] = str(per_page)
    qs_no_sort_dir_page["rate"] = rate
    qs_no_sort_dir_page["season"] = _season_param(selected_season)
    if selected_team_id is not None:
        qs_no_sort_dir_page["team_id"] = str(selected_team_id)
    querystring_without_sort_dir_page = qs_no_sort_dir_page.urlencode()

    context["page_obj"] = page_obj
    context["paginator"] = paginator
    context["querystring_without_page"] = querystring_without_page
    context["querystring_without_sort_dir_page"] = querystring_without_sort_dir_page

    return render(request, "leagues/player_stats.html", context)
