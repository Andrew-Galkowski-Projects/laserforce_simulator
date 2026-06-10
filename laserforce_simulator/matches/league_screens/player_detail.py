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

The RS aggregation reuses, by import, the existing modules — no new pure
module:

- :func:`matches.league_screens.player_stats._build_round_dicts` — one plain
  dict per ``PlayerRoundState`` from a ``game_round__…``-joined lookup dict.
- :func:`matches.season_player_stats.aggregate_player_stats` — sums the count
  keys, averages mvp / accuracy, returns ``list[PlayerStatRow]``.

See ``.claude/worktrees/lg-06h-seam-contract.md`` for the locked seam.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_screens.player_stats import (
    _build_round_dicts,
    _PLAYER_STATS_COLUMNS,
)
from matches.league_views import (
    _build_league_sidebar_links,
    _compute_season_award_set,
    _player_award_labels,
)
from matches.models import League, PlayerRoundState
from matches.season_player_stats import PlayerStatRow, aggregate_player_stats
from teams.models import Player

# The RS table's stat-column spec — the STAT portion of the Player Stats
# column spec, dropping ``name`` (index 0) and ``team`` (index 1): Team is a
# dedicated prefix column derived per-Season, so it must not be double-rendered.
# Starts at ``("games", "GP", False)`` — 15 entries.
_RS_STAT_COLUMNS: tuple[tuple[str, str, bool], ...] = _PLAYER_STATS_COLUMNS[2:]


def _row_from_stat_row(
    stat_row: PlayerStatRow, year: str, season_id: "int | None"
) -> dict:
    """Build a per-Season / Career row dict from an aggregated PlayerStatRow."""
    return {
        "year": year,
        "season_id": season_id,
        "team_name": stat_row.team_name,
        "team_id": stat_row.team_id,
        "games": stat_row.games,
        "stats": stat_row.stats,
    }


def player_detail(request: HttpRequest, league_id: int, player_id: int) -> HttpResponse:
    """LG-06h — read-only League player page pinned to one League."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)
    player = get_object_or_404(Player, pk=player_id)

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

    # Per-Season rows — one aggregation pass per this-League Season the player
    # has Rounds in (newest-first).
    rs_rows: list[dict] = []
    for season in league.seasons.order_by("-id"):
        prs_filter = {
            "game_round__match__season": season,
            "player_id": player.id,
        }
        round_dicts = _build_round_dicts(prs_filter)
        if not round_dicts:
            continue
        agg = aggregate_player_stats(round_dicts)
        if not agg:
            continue
        rs_rows.append(_row_from_stat_row(agg[0], season.name, season.id))

    # Career-in-league row — one league-wide aggregation pass.
    career_filter = {
        "game_round__match__season__league": league,
        "player_id": player.id,
    }
    career_dicts = _build_round_dicts(career_filter)
    career_row: "dict | None" = None
    if career_dicts:
        career_agg = aggregate_player_stats(career_dicts)
        if career_agg:
            career_row = _row_from_stat_row(career_agg[0], "Career", None)

    # LG-03 — per-Season awards this Player won in THIS League (newest-first;
    # one entry per Season with >= 1 award). Reuses the shared award path,
    # but prunes Seasons the Player never appeared in via a cheap existence
    # check first — the full award computation is O(PlayerRoundState), so a
    # Player who only played a handful of the League's Seasons skips the rest.
    player_awards: list[dict] = []
    for season in league.seasons.order_by("-id"):
        if not PlayerRoundState.objects.filter(
            game_round__match__season=season, player_id=player.id
        ).exists():
            continue
        award_set = _compute_season_award_set(season)
        labels = _player_award_labels(award_set, player.id)
        if labels:
            player_awards.append(
                {
                    "season_id": season.id,
                    "season_name": season.name,
                    "award_labels": labels,
                }
            )

    context = {
        "league": league,
        "player": player,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": None,
        "rs_rows": rs_rows,
        "career_row": career_row,
        "stat_columns": _RS_STAT_COLUMNS,
        "player_awards": player_awards,
    }
    return render(request, "leagues/player_detail.html", context)
