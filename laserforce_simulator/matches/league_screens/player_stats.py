"""LG-01z-o — Player Stats (league-context performance) screen.

Read-only, GET-only, sortable + paginated table of per-player PERFORMANCE
aggregated across a League's displayed Season's completed Rounds — the
HX-01 ``STAT_KEYS`` set (NOT the rating attributes; those live on the
separate Player Ratings screen, LG-01z-n). Each player links to their
career / player page (``player_career_stats`` — ``/players/<id>/stats/``).

Follows the shared LG-01z view contract (§2 of the seam contract):
GET-guard → ``get_object_or_404(League)`` → session write →
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
from django.shortcuts import get_object_or_404, render

from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
    _LG01F_PER_PAGE_OPTIONS,
)
from matches.models import League, PlayerRoundState
from matches.season_player_stats import (
    STAT_KEYS,
    aggregate_player_stats,
    coerce_dir,
    coerce_sort,
    sort_player_stats,
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


def _build_round_dicts(displayed_season) -> list[dict]:
    """Build one plain dict per ``PlayerRoundState`` in the Season's Rounds.

    Scope: every ``PlayerRoundState`` on a completed Round
    (``game_round__match__season == displayed_season``) for players on
    enrolled Teams. ``mvp`` / ``accuracy`` are read from the per-Round
    ``get_mvp`` / ``get_accuracy`` properties here so the pure module
    never touches the MVP formula or the ORM.

    Team identity per row resolves from the Round's ``team_red`` /
    ``team_blue`` keyed on the player's ``team_color`` (mirrors the
    LG-01c dashboard-leaders precedent).
    """
    prs_qs = (
        PlayerRoundState.objects.filter(game_round__match__season=displayed_season)
        .select_related(
            "player",
            "game_round",
            "game_round__match",
            "game_round__team_red",
            "game_round__team_blue",
        )
        .order_by("id")
    )

    rounds: list[dict] = []
    for prs in prs_qs:
        game_round = prs.game_round
        if prs.team_color == "red":
            team = game_round.team_red
        elif prs.team_color == "blue":
            team = game_round.team_blue
        else:
            team = None
        rounds.append(
            {
                "player_id": prs.player_id,
                "player_name": prs.player.name,
                "team_id": team.id if team is not None else 0,
                "team_name": team.name if team is not None else "",
                "role": prs.role,
                # 12 STAT_KEYS — mvp / accuracy pre-computed per round.
                "points_scored": prs.points_scored,
                "mvp": float(prs.get_mvp),
                "tags_made": prs.tags_made,
                "times_tagged": prs.times_tagged,
                "accuracy": float(prs.get_accuracy),
                # Derived survival seconds: elimination tick capped at the
                # round length (1800), ÷2 to seconds at the display boundary
                # (TIME-01). The pure module averages this across rounds.
                "survival_seconds": min(prs.was_eliminated_at, 1800) / 2,
                "final_lives": prs.final_lives,
                "resupplies_given": prs.resupplies_given,
                "missiles_landed": prs.missiles_landed,
                "specials_used": prs.specials_used,
                "follow_up_shots": prs.follow_up_shots,
                "reaction_shots": prs.reaction_shots,
                "combo_resupply_count": prs.combo_resupply_count,
            }
        )
    return rounds


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

    league = get_object_or_404(League, pk=league_id)
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
    }

    # Empty-state per §2 — no Season; render the notice instead of the body.
    # The sidebar still renders.
    if displayed_season is None:
        context["page_obj"] = None
        context["paginator"] = None
        context["querystring_without_page"] = ""
        context["querystring_without_sort_dir_page"] = ""
        return render(request, "leagues/player_stats.html", context)

    round_dicts = _build_round_dicts(displayed_season)
    rows = aggregate_player_stats(round_dicts)
    rows = sort_player_stats(rows, sort, direction)

    paginator = Paginator(rows, per_page)
    page_obj = paginator.get_page(_coerce_page(request.GET.get("page")))

    # Build querystring helpers from the COERCED values so invalid params
    # do not survive in pagination / re-sort links (LG-00c precedent).
    qs_no_page = request.GET.copy()
    qs_no_page.pop("page", None)
    qs_no_page["sort"] = sort
    qs_no_page["dir"] = direction
    qs_no_page["per_page"] = str(per_page)
    querystring_without_page = qs_no_page.urlencode()

    qs_no_sort_dir_page = request.GET.copy()
    qs_no_sort_dir_page.pop("page", None)
    qs_no_sort_dir_page.pop("sort", None)
    qs_no_sort_dir_page.pop("dir", None)
    qs_no_sort_dir_page["per_page"] = str(per_page)
    querystring_without_sort_dir_page = qs_no_sort_dir_page.urlencode()

    context["page_obj"] = page_obj
    context["paginator"] = paginator
    context["querystring_without_page"] = querystring_without_page
    context["querystring_without_sort_dir_page"] = querystring_without_sort_dir_page

    return render(request, "leagues/player_stats.html", context)
