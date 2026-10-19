"""LG-01z-l — Game Log league screen.

Read-only, GET-only view rendering one row per played ``GameRound`` in the
League's displayed Season (chronological), with an optional ``?team_id=``
Team filter. See ``.claude/worktrees/lg-01z-seam-contract.md`` §2 / §4-l.
"""

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404
from matches.league_screen_services import (
    GAME_LOG_SORT_KEYS,
    build_game_log,
    enrolled_teams as _enrolled_teams,
    parse_team_id as _parse_team_id,
)
from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_sort_key,
    _resolve_season_scope,
    _season_param,
)
from matches.models import League
from teams.views import _coerce_dir

# LG-06c — sortable Game Log columns. Whitelist derived from the rendered
# columns; default is ``date_played`` asc (reproduces the current
# chronological-by-id order, with ``round_id`` as the always-appended
# secondary tiebreak).
_GAME_LOG_SORT_KEYS: frozenset[str] = frozenset(GAME_LOG_SORT_KEYS)
_GAME_LOG_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("matchday", "Matchday"),
    ("date_played", "Date"),
    ("team_red", "Red"),
    ("team_blue", "Blue"),
    ("score", "Score"),
    ("winner", "Winner"),
)


def game_log(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-l — Game Log page for a League's displayed Season.

    One row per played ``GameRound`` (chronological, ``id`` ascending):
    matchday, date, red team, blue team, score, winner. An optional
    ``?team_id=`` query param filters to Rounds involving that Team
    (invalid / non-enrolled ids are silently ignored). Each row deep-links
    to the Round detail page. Renders an empty-state notice when the
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
        league, displayed_season, sidebar_active="game_log"
    )

    # LG-06c — coerce the sort/dir params before anything else so the
    # context (and querystring-carry below) always reflects canonical values.
    sort = _coerce_sort_key(request.GET.get("sort"), _GAME_LOG_SORT_KEYS, "date_played")
    direction = _coerce_dir(request.GET.get("dir"))

    # LG-06d — season selector. Picker options + the forgiving ``?season=``
    # coercion (defaults to displayed_season — fully backward-compatible).
    seasons, selected_season, season_options, season_filter = _resolve_season_scope(
        request, league, displayed_season
    )

    if season_filter is None:
        return render(
            request,
            "leagues/game_log.html",
            {
                "league": league,
                "displayed_season": None,
                "sidebar_links": sidebar_links,
                "sidebar_active": "game_log",
                "rows": [],
                "team_options": [],
                "selected_team_id": None,
                "sort": sort,
                "dir": direction,
                "sort_keys": _GAME_LOG_SORT_KEYS_DISPLAY,
                "querystring_without_sort_dir": "",
                "season_options": season_options,
                "selected_season": selected_season,
            },
        )

    # Enrolled teams for the filter dropdown (and the valid-id allowlist). The
    # picker lists the displayed Season's enrolment even under a Career /
    # past-Season scope.
    team_options = (
        _enrolled_teams(displayed_season) if displayed_season is not None else []
    )
    enrolled_ids = {t.id for t in team_options}

    # Optional ?team_id= filter — silently ignore a bad / non-enrolled id.
    candidate = _parse_team_id(request.GET.get("team_id"))
    selected_team_id: int | None = candidate if candidate in enrolled_ids else None

    rows = build_game_log(
        season_filter, team_id=selected_team_id, sort=sort, direction=direction
    )

    # COERCE-BEFORE-QUERYSTRING: build the header href carry from the coerced
    # ``team_id`` (re-set) with ``sort`` / ``dir`` popped, so invalid params
    # never survive into the header / filter links.
    qs_no_sort_dir = request.GET.copy()
    qs_no_sort_dir.pop("sort", None)
    qs_no_sort_dir.pop("dir", None)
    qs_no_sort_dir["season"] = _season_param(selected_season)
    if selected_team_id is not None:
        qs_no_sort_dir["team_id"] = str(selected_team_id)
    else:
        qs_no_sort_dir.pop("team_id", None)
    querystring_without_sort_dir = qs_no_sort_dir.urlencode()

    return render(
        request,
        "leagues/game_log.html",
        {
            "league": league,
            "displayed_season": displayed_season,
            "sidebar_links": sidebar_links,
            "sidebar_active": "game_log",
            "rows": rows,
            "team_options": team_options,
            "selected_team_id": selected_team_id,
            "sort": sort,
            "dir": direction,
            "sort_keys": _GAME_LOG_SORT_KEYS_DISPLAY,
            "querystring_without_sort_dir": querystring_without_sort_dir,
            "season_options": season_options,
            "selected_season": selected_season,
        },
    )
