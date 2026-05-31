"""LG-01z-f — Free Agents (league-context) screen.

Read-only, GET-only, sortable + paginated list of every **free agent** in
a League's displayed Season. A free agent is a Player on the reserved
"Free Agents" pool team (``teams.models.get_free_agents_team()``) — i.e. a
Player on no competitive roster. Players on any real Team (enrolled in the
Season or not) have a team and are NOT free agents. Each player links to
their career / player page (``player_career_stats`` —
``/players/<id>/stats/``). There is no sign action (deferred with the cap
model).

Follows the shared LG-01z view contract (§2 of the seam contract):
GET-guard → ``get_object_or_404(League)`` → session write →
displayed-Season pick → sidebar links with ``sidebar_active="free_agents"``
→ screen aggregation → render ``leagues/free_agents.html``. Empty-state
notice when the League has no Season.

Sorting reuses the LG-00c helpers (``_coerce_sort`` / ``_coerce_dir`` /
``_SORT_KEYS`` / ``_SORT_KEYS_DISPLAY``) imported from ``teams.views`` and
the pagination helpers (``_coerce_per_page`` / ``_coerce_page``) from
``matches.league_views`` — no pure module, no reimplementation.
"""

from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import F, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
)
from matches.models import League
from teams.models import Player
from teams.views import _coerce_dir, _coerce_sort, _SORT_KEYS, _SORT_KEYS_DISPLAY


def _free_agent_queryset(league: League) -> "QuerySet[Player]":
    """Players who are free agents in ``league`` — on **no** competitive Team.

    A free agent is a Player on this League's dedicated free-agent pool
    Team (``league.free_agent_pool``). Each League owns its own pool, so a
    Player only appears for the League whose pool they belong to. Players
    on any competitive Team are NOT free agents (they have a team).
    Filtering on ``league.free_agent_pool_id`` (``None`` when the League has
    no pool — e.g. legacy Leagues) yields an empty queryset, since every
    Player has a non-null team. The queryset is annotated with
    ``overall_rating_db`` (mean of the 19 stats) so the LG-00c
    ``overall_rating`` sort key has an ORM target.
    """
    qs = Player.objects.select_related("team").filter(team_id=league.free_agent_pool_id)
    return qs.annotate(
        overall_rating_db=(
            F("player_awareness")
            + F("game_awareness")
            + F("resource_awareness")
            + F("decision_making")
            + F("positioning")
            + F("stamina")
            + F("speed")
            + F("flexibility")
            + F("adaptability")
            + F("communication")
            + F("teamwork")
            + F("Offensive_synergy")
            + F("defensive_synergy")
            + F("midfield_synergy")
            + F("resupply_synergy")
            + F("resupply_efficiency")
            + F("accuracy")
            + F("survival")
            + F("special_usage")
        )
        / 19.0
    )


def free_agents(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-f — Free Agents page for a League's displayed Season.

    Sortable (``?sort=&dir=``, LG-00c forgiving fallback) + paginated
    (``?per_page=&page=``) list of free agents. Renders an empty-state
    notice when the League has no Season.
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
        league, displayed_season, sidebar_active="free_agents"
    )

    sort = _coerce_sort(request.GET.get("sort"))
    direction = _coerce_dir(request.GET.get("dir"))
    per_page = _coerce_per_page(request.GET.get("per_page"))

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "free_agents",
        "sort": sort,
        "dir": direction,
        "per_page": per_page,
        "sort_keys": _SORT_KEYS_DISPLAY,
    }

    # Empty-state per §2 — no Season; render the notice instead of the body.
    # The sidebar still renders.
    if displayed_season is None:
        context["page_obj"] = None
        context["paginator"] = None
        context["querystring_without_page"] = ""
        context["querystring_without_sort_dir_page"] = ""
        return render(request, "leagues/free_agents.html", context)

    qs = _free_agent_queryset(league)

    if sort == "preferred_roles":
        rows = list(qs)
        rows.sort(
            key=lambda p: (",".join(p.preferred_roles or []), p.name),
            reverse=(direction == "desc"),
        )
        paginator = Paginator(rows, per_page)
    else:
        prefix = "" if direction == "asc" else "-"
        qs = qs.order_by(prefix + _SORT_KEYS[sort], "name")
        paginator = Paginator(qs, per_page)

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

    return render(request, "leagues/free_agents.html", context)
