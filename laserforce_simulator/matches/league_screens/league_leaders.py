"""LG-01z-m — League Leaders league screen view.

Read-only, GET-only screen rendering four top-10 leaderboards over every
player who appeared in the League's ``displayed_season``'s completed Rounds.
Follows the LG-01z shared view contract (§2): GET-guard →
``get_object_or_404`` → session write → ``displayed_season`` pick → sidebar
links → screen aggregation → render. Heavy aggregation lives in the pure
module ``matches/league_leaders_logic.py``.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_leaders_logic import compute_leaderboards
from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_sort_key,
    _resolve_season_scope,
    _season_param,
)
from matches.models import League, PlayerRoundState
from teams.views import _coerce_dir

# LG-06c — the four League Leaders boards. Each gets its own namespaced
# ``?<board>_sort=&<board>_dir=`` param pair so sorting one board never
# resets a sibling.
_LEADERS_BOARDS: tuple[str, ...] = (
    "avg_tags",
    "avg_score",
    "fewest_tagged",
    "tag_ratio",
)

# Shared sort-key whitelist across all four boards — derived from the
# ``LeaderRow`` attribute names.
_LEADERS_SORT_KEYS: frozenset[str] = frozenset(
    {"rank", "player_name", "team_name", "role", "value", "games_played"}
)
# Shared display tuple. Each board renders the subset of columns it shows; the
# current 2-column (player, value) layout makes ``player_name`` and ``value``
# the rendered sortable headers (``rank`` is shown inline as ``{{ row.rank }}.``).
_LEADERS_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("rank", "#"),
    ("player_name", "Player"),
    ("team_name", "Team"),
    ("role", "Role"),
    ("value", "Value"),
    ("games_played", "GP"),
)


def _sort_leaderboard(rows: list, sort: str, direction: str) -> list:
    """In-memory sort of one board's ``LeaderRow`` list per the LG-06c contract.

    ``player_id`` is the always-appended stable secondary tiebreak. ``rank`` is
    NOT recomputed — it stays the canonical metric-rank from the pure module.
    """
    return sorted(
        rows,
        key=lambda row: (getattr(row, sort), row.player_id),
        reverse=(direction == "desc"),
    )


def league_leaders(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-m — League Leaders page for a League's displayed Season.

    Four top-10 leaderboards over all players in ``displayed_season``'s
    completed Rounds: average tags made (desc), average score (desc), fewest
    times tagged (asc — least-tagged leads), and tag ratio (sum/sum, desc).
    Each leader links to their career page. Renders an empty-state notice
    when the League has no Season.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="league_leaders"
    )

    # LG-06d — season selector. Picker options + the forgiving ``?season=``
    # coercion (defaults to displayed_season — fully backward-compatible).
    seasons, selected_season, season_options, season_filter = _resolve_season_scope(
        request, league, displayed_season
    )

    # LG-06c — per-board namespaced sort/dir coercion. Default ``rank`` asc on
    # every board reproduces the pure module's natural metric order.
    board_sort: dict[str, str] = {}
    board_dir: dict[str, str] = {}
    for board in _LEADERS_BOARDS:
        board_sort[board] = _coerce_sort_key(
            request.GET.get(f"{board}_sort"), _LEADERS_SORT_KEYS, "rank"
        )
        board_dir[board] = _coerce_dir(request.GET.get(f"{board}_dir"))

    # COERCE-BEFORE-QUERYSTRING: each board's header href carries ALL eight
    # params (so flipping board A's sort preserves B/C/D) by popping only that
    # board's own pair, then re-setting every board's coerced pair.
    board_querystring_without_sort: dict[str, str] = {}
    for board in _LEADERS_BOARDS:
        qs = request.GET.copy()
        # LG-06d — carry the chosen Season scope across a board re-sort.
        qs["season"] = _season_param(selected_season)
        for other in _LEADERS_BOARDS:
            if other == board:
                qs.pop(f"{other}_sort", None)
                qs.pop(f"{other}_dir", None)
            else:
                qs[f"{other}_sort"] = board_sort[other]
                qs[f"{other}_dir"] = board_dir[other]
        board_querystring_without_sort[board] = qs.urlencode()

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "league_leaders",
        "leaders_sort_keys": _LEADERS_SORT_KEYS_DISPLAY,
        "season_options": season_options,
        "selected_season": selected_season,
    }
    for board in _LEADERS_BOARDS:
        context[f"{board}_sort"] = board_sort[board]
        context[f"{board}_dir"] = board_dir[board]
        context[f"{board}_querystring_without_sort"] = board_querystring_without_sort[
            board
        ]

    if season_filter is None:
        # Empty-state per §2 — no Season scope; render the notice instead of
        # the body. The sidebar still renders.
        context["leaderboards"] = {
            "avg_tags": [],
            "avg_score": [],
            "fewest_tagged": [],
            "tag_ratio": [],
        }
        return render(request, "leagues/league_leaders.html", context)

    # Re-point the LG-06d scope onto the PlayerRoundState ``game_round__…`` join.
    prs_filter = {f"game_round__{k}": v for k, v in season_filter.items()}

    # Materialise one dict per PlayerRoundState row across the scoped Rounds
    # (mirrors the LG-01c season-dashboard player-round dict shape).
    # order_by("id") makes the "last row wins" defensive fallback in the pure
    # module deterministic.
    prs_qs = (
        PlayerRoundState.objects.filter(**prs_filter)
        .select_related(
            "player",
            "game_round",
            "game_round__match",
            "game_round__team_red",
            "game_round__team_blue",
        )
        .order_by("id")
    )

    player_rounds: list[dict] = []
    for prs in prs_qs:
        game_round = prs.game_round
        if prs.team_color == "red":
            team = game_round.team_red
        elif prs.team_color == "blue":
            team = game_round.team_blue
        else:
            team = None
        player_rounds.append(
            {
                "player_id": prs.player_id,
                "player_name": prs.player.name,
                "role": prs.role,
                "team_id": team.id if team is not None else 0,
                "team_name": team.name if team is not None else "",
                "tags_made": prs.tags_made,
                "times_tagged": prs.times_tagged,
                "points_scored": prs.points_scored,
            }
        )

    leaderboards = compute_leaderboards(player_rounds, limit=10)

    # LG-06c — sort each board's rows in-memory with its own namespaced
    # sort/dir. The pure module's ``rank`` field is preserved (not recomputed).
    context["leaderboards"] = {
        board: _sort_leaderboard(
            leaderboards[board], board_sort[board], board_dir[board]
        )
        for board in _LEADERS_BOARDS
    }
    return render(request, "leagues/league_leaders.html", context)
