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
from matches.league_views import _build_league_sidebar_links
from matches.models import League, PlayerRoundState


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

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "league_leaders",
    }

    if displayed_season is None:
        # Empty-state per §2 — no Season; render the notice instead of the
        # body. The sidebar still renders.
        context["leaderboards"] = {
            "avg_tags": [],
            "avg_score": [],
            "fewest_tagged": [],
            "tag_ratio": [],
        }
        return render(request, "leagues/league_leaders.html", context)

    # Materialise one dict per PlayerRoundState row across the Season's
    # completed Rounds (mirrors the LG-01c season-dashboard player-round
    # dict shape). order_by("id") makes the "last row wins" defensive
    # fallback in the pure module deterministic.
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

    context["leaderboards"] = compute_leaderboards(player_rounds, limit=10)
    return render(request, "leagues/league_leaders.html", context)
