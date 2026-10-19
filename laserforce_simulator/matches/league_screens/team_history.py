"""LG-01z-e — Team History (league-context, 3-tab) screen.

Read-only view rendering a Team's league history across three Bootstrap
tabs inside a League:

* **Overall** — the team's all-time round-level W-L-T across every Season
  it played (per-Round outcome from each ``GameRound``'s red/blue points,
  mirroring the LG-01g per-Round W/L/T rule — NOT the rolled-up Match
  winner), plus playoff appearances (placeholder ``0`` until LG-02) and
  championships (count of Seasons where ``Season.champion_team == team``).
* **Seasons** — one row per Season the team enrolled in (Season whose
  ``starting_team_ids_json`` contains the team id, falling back to the
  live M2M for draft Seasons): the start-date year, that Season's
  round-level record, and final standing/rank via ``compute_standings``.
* **Players** — every Player who appeared for the team (distinct players
  across the team's GameRounds via ``PlayerRoundState``), with career-long
  aggregate stats, games played on the team, last season played, and a
  colour flag (green = still on the team, blue = now elsewhere). Each
  links to the player's career page.

Team via ``?team_id=`` (validated against the displayed Season's
enrolment), defaulting to ``league.current_team`` then
``_resolve_current_team_for_sidebar``. Follows the shared LG-01z view
contract (§2): GET-only, ``get_owned_or_404(League)``, session write,
displayed-Season pick, sidebar links with ``sidebar_active="history_team"``,
render ``leagues/team_history.html``. Empty-state when the League has no
Season or no Team is resolvable.

Heavy aggregation lives in the pure sibling
``matches.team_history_logic``; this view only materialises plain dicts
from the ORM and feeds them across that seam.
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404
from matches.league_screen_services import (
    TEAM_HISTORY_PLAYERS_SORT_KEYS,
    TEAM_HISTORY_SEASONS_SORT_KEYS as _SEASONS_SORT_KEYS,
    build_team_history,
    build_team_history_players,
    enrolled_teams as _enrolled_teams,
    parse_team_id as _parse_team_id,
    resolve_league_team,
    team_history_seasons,
)
from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
    _coerce_sort_key,
    _LG01F_PER_PAGE_OPTIONS,
)
from matches.models import League
from teams.views import _coerce_dir

# LG-06c — sortable Seasons table (keys + builders live in
# ``league_screen_services``). Default ``year`` desc reproduces the current
# newest-first order. The Record column's header sorts on the ``wins`` key
# (locked); ``losses`` / ``ties`` stay in the allowed set for forgiving
# fallback but are not surfaced as their own headers.
_SEASONS_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("year", "Year"),
    ("record", "Record (W-L-T)"),
    ("rank", "Final rank"),
)

# LG-06c — sortable Players table. Default ``name`` asc reproduces the pure
# module's ``(name, player_id)`` order; ``player_id`` is the always-appended
# secondary tiebreak. Mixed top-level attrs + nested ``stats`` keys.
_TH_PLAYERS_SORT_KEYS: frozenset[str] = frozenset(TEAM_HISTORY_PLAYERS_SORT_KEYS)
_TH_PLAYERS_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("name", "Name"),
    ("games_played", "Games"),
    ("points_scored", "Points"),
    ("tags_made", "Tags"),
    ("times_tagged", "Times tagged"),
    ("missiles_landed", "Missiles"),
    ("resupplies_given", "Resupplies"),
    ("specials_used", "Specials"),
    ("last_season_year", "Last season"),
)


def team_history(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-e — Team History (league-context, 3-tab) page."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_owned_or_404(League, request, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )

    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="history_team"
    )

    per_page = _coerce_per_page(request.GET.get("per_page"))

    # LG-06c — coerce both namespaced sort/dir pairs up front.
    seasons_sort = _coerce_sort_key(
        request.GET.get("seasons_sort"), _SEASONS_SORT_KEYS, "year"
    )
    seasons_dir = _coerce_dir(request.GET.get("seasons_dir"), default="desc")
    players_sort = _coerce_sort_key(
        request.GET.get("players_sort"), _TH_PLAYERS_SORT_KEYS, "name"
    )
    players_dir = _coerce_dir(request.GET.get("players_dir"))

    base_context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "history_team",
        "per_page": per_page,
        "per_page_options": _LG01F_PER_PAGE_OPTIONS,
        "seasons_sort": seasons_sort,
        "seasons_dir": seasons_dir,
        "seasons_sort_keys": _SEASONS_SORT_KEYS_DISPLAY,
        "players_sort": players_sort,
        "players_dir": players_dir,
        "players_sort_keys": _TH_PLAYERS_SORT_KEYS_DISPLAY,
    }

    # No Season ⇒ empty-state (the sidebar still renders).
    if displayed_season is None:
        context = {
            **base_context,
            "team": None,
            "enrolled_teams": [],
            "overall_record": None,
            "season_rows": [],
            "page_obj": None,
            "paginator": None,
            "players_querystring_without_page": "",
            "seasons_querystring_without_sort": "",
            "players_querystring_without_sort_page": "",
        }
        return render(request, "leagues/team_history.html", context)

    enrolled_teams = _enrolled_teams(displayed_season)
    team = resolve_league_team(
        league, displayed_season, _parse_team_id(request.GET.get("team_id"))
    )

    if team is None:
        context = {
            **base_context,
            "team": None,
            "enrolled_teams": enrolled_teams,
            "overall_record": None,
            "season_rows": [],
            "page_obj": None,
            "paginator": None,
            "players_querystring_without_page": "",
            "seasons_querystring_without_sort": "",
            "players_querystring_without_sort_page": "",
        }
        return render(request, "leagues/team_history.html", context)

    # One cross-League Season scan, shared by both builders.
    seasons_by_id = team_history_seasons(team)

    # --- Overall + Seasons tabs (sorted in the builder).
    history = build_team_history(
        team, sort=seasons_sort, direction=seasons_dir, seasons_by_id=seasons_by_id
    )
    season_rows = history.seasons

    # --- Players tab: SORT BEFORE PAGINATION (the builder sorts the whole
    # rollup list), then construct the Paginator.
    player_rollups = build_team_history_players(
        team, sort=players_sort, direction=players_dir, seasons_by_id=seasons_by_id
    )
    paginator = Paginator(player_rollups, per_page)
    page_obj = paginator.get_page(_coerce_page(request.GET.get("page")))

    # COERCE-BEFORE-QUERYSTRING. Pagination Previous/Next links carry the
    # resolved team_id + per_page + BOTH namespaced sort pairs and omit page
    # so the sort survives page navigation.
    players_qs = request.GET.copy()
    players_qs.pop("page", None)
    players_qs["team_id"] = str(team.id)
    players_qs["per_page"] = str(per_page)
    players_qs["players_sort"] = players_sort
    players_qs["players_dir"] = players_dir
    players_qs["seasons_sort"] = seasons_sort
    players_qs["seasons_dir"] = seasons_dir
    players_querystring_without_page = players_qs.urlencode()

    # Players column-header hrefs reset to page 1: pop players_sort/dir + page,
    # keep team_id + per_page + the seasons_* pair.
    players_qs_no_sort = request.GET.copy()
    players_qs_no_sort.pop("players_sort", None)
    players_qs_no_sort.pop("players_dir", None)
    players_qs_no_sort.pop("page", None)
    players_qs_no_sort["team_id"] = str(team.id)
    players_qs_no_sort["per_page"] = str(per_page)
    players_qs_no_sort["seasons_sort"] = seasons_sort
    players_qs_no_sort["seasons_dir"] = seasons_dir
    players_querystring_without_sort_page = players_qs_no_sort.urlencode()

    # Seasons column-header hrefs: pop seasons_sort/dir, keep team_id + the
    # players_* pair (so re-sorting Seasons doesn't reset the Players table).
    seasons_qs = request.GET.copy()
    seasons_qs.pop("seasons_sort", None)
    seasons_qs.pop("seasons_dir", None)
    seasons_qs["team_id"] = str(team.id)
    seasons_qs["players_sort"] = players_sort
    seasons_qs["players_dir"] = players_dir
    seasons_querystring_without_sort = seasons_qs.urlencode()

    context = {
        **base_context,
        "team": team,
        "enrolled_teams": enrolled_teams,
        "overall_record": history.overall,
        "season_rows": season_rows,
        "page_obj": page_obj,
        "paginator": paginator,
        "players_querystring_without_page": players_querystring_without_page,
        "players_querystring_without_sort_page": (
            players_querystring_without_sort_page
        ),
        "seasons_querystring_without_sort": seasons_querystring_without_sort,
    }
    return render(request, "leagues/team_history.html", context)
