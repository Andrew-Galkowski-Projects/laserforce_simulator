"""LG-06h — League player page (per-Player, league-pinned detail).

Read-only, GET-only **League player page** at
``/leagues/<league_id>/players/<player_id>/`` — the in-League destination of
every player-name link on the 8 LG-06f league screens. Renders a header (with
the LG-06f watch flag + an external link to the global HX-01 career page), a
Regular-Season stats table (per-Season rows + a league-wide Career row, built
VIEW-SIDE by reusing existing modules), a "Potential" placeholder, and 5 inline
"coming soon" stub blocks for the model-less sections.

Lenient: any valid ``(League, Player)`` pair renders 200; the league-scoped RS
table renders an empty-state when the player has no Rounds in this League. It
NEVER 404s on "player not in league" — only a missing League OR a missing
Player 404s.

ANG-01e T3: the RS rows, Career row, awards and ratings history come from
:func:`matches.league_screen_services.build_player_detail`, shared with the
``/api/leagues/<id>/players/<player_id>/`` endpoint; this view only adds the
HTML-only pieces (session write, sidebar, column spec).

See ``.claude/worktrees/lg-06h-seam-contract.md`` for the locked seam.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404
from matches.league_screen_services import (
    _row_from_stat_row,  # noqa: F401 -- moved; re-exported under the old name
    build_player_detail,
)
from matches.league_screens.player_stats import _PLAYER_STATS_COLUMNS
from matches.league_views import _build_league_sidebar_links
from matches.models import League
from teams.models import Player

# The RS table's stat-column spec — the STAT portion of the Player Stats
# column spec, dropping ``name`` (index 0) and ``team`` (index 1): Team is a
# dedicated prefix column derived per-Season, so it must not be double-rendered.
# Starts at ``("games", "GP", False)`` — 15 entries.
_RS_STAT_COLUMNS: tuple[tuple[str, str, bool], ...] = _PLAYER_STATS_COLUMNS[2:]


def player_detail(request: HttpRequest, league_id: int, player_id: int) -> HttpResponse:
    """LG-06h — read-only League player page pinned to one League.

    Frozen context keys (the seam): ``league``, ``player``,
    ``displayed_season``, ``sidebar_links``, ``sidebar_active``, ``rs_rows``,
    ``career_row``, ``stat_columns``, ``player_awards``, and (LG-04)
    ``ratings_history`` — a league-scoped, oldest-first list of per-row dicts
    ``{season_id, season_name, age, overall_rating, potential, stats}``.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_owned_or_404(League, request, pk=league_id)
    player = get_owned_or_404(Player, request, pk=player_id)

    # LG-01f session-write contract (int) — after the 404 guards, before render.
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    # No sidebar entry matches this page — every entry renders inactive.
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active=None
    )

    # The page sections come from the builder shared with the API (ANG-01e T3).
    detail = build_player_detail(league, player)

    context = {
        "league": league,
        "player": player,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": None,
        "rs_rows": detail.rs_rows,
        "career_row": detail.career_row,
        "stat_columns": _RS_STAT_COLUMNS,
        "player_awards": detail.player_awards,
        "ratings_history": detail.ratings_history,
    }
    return render(request, "leagues/player_detail.html", context)
