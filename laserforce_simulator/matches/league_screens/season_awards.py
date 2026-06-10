"""LG-03 — per-Season awards screen.

Read-only, GET-only awards page at ``/seasons/<season_id>/awards/`` rendering
the 6 category awards + the 2 headline awards (Season MVP / Finals MVP) for one
Season. Awards are computed lazily on first render of a ``completed`` Season via
the ``Season.get_or_compute_awards`` cache chokepoint; a non-completed Season
renders the "not yet awarded" empty state.

Follows the season-URL-family GET-guard precedent + the LG-01f session-write
contract. The sidebar renders with NO active entry (no sidebar key matches the
awards page).

See ``.claude/worktrees/lg-03-seam-contract.md`` §4 for the locked seam.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_views import _build_league_sidebar_links
from matches.models import Season
from matches.season_awards import AWARD_CATEGORIES


def season_awards(request: HttpRequest, season_id: int) -> HttpResponse:
    """LG-03 — read-only per-Season awards page."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    season = get_object_or_404(Season, pk=season_id)

    # LG-01f session-write contract (int) — after the 404 guard, before render.
    request.session["last_league_id"] = season.league_id

    # The awards page IS this Season; the sidebar's Standings / Schedule links
    # target the displayed Season (same chain as the dashboards). No sidebar
    # entry matches the awards page, so every entry renders inactive.
    displayed_season = (
        season.league.active_season
        or season.league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        season.league, displayed_season, sidebar_active=None
    )

    awards = season.get_or_compute_awards()
    is_awarded = season.state == "completed" and bool(awards)

    context = {
        "season": season,
        "league": season.league,
        "sidebar_links": sidebar_links,
        "sidebar_active": None,
        "awards": awards,
        "award_categories": AWARD_CATEGORIES,
        "is_awarded": is_awarded,
    }
    return render(request, "seasons/awards.html", context)
