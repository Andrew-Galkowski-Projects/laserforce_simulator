"""LG-01h — the League "Soon" placeholder view and its feature registry.

ANG-08 D2 deleted every Match / Round / Batch / Tournament template view from
this module. ``coming_soon`` and ``_FEATURE_REGISTRY`` stay only because the
League "Soon" URLs in ``league_urls.py`` still route here; ANG-08 D3 deletes
this module together with those URLs.
"""

from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404

# ====================================================================
# LG-01h — Shared placeholder view + feature registry
# ====================================================================

# Locked vocabulary per the LG-01h seam contract. 35 entries total:
# 10 League-scoped (3 LEAGUE + 3 TEAM + 4 reserved for LG-02), 6 PLAYERS,
# 6 STATS, 6 Help, 4 Tools. Note the contract says "League-scoped (10)"
# but enumerates only 3 (Playoffs / Finances / Power Rankings) — the
# Team-scoped 3 are listed separately, and Standings / Schedule /
# Schedule_team / History are already LIVE in LG-01f/g (not in this
# registry). Total entries below: 3 + 3 + 6 + 6 + 6 + 4 = 28… plus the
# 7 sidebar-active enum entries needed for forward-compat. Counted
# literally: 3 League + 3 Team + 6 Players + 6 Stats + 6 Help + 4 Tools
# = 28 — but the contract pins "Total: 35" — see PLAN.md grilling notes.
# Source of truth is the contract's enumeration in §"Shared placeholder
# view" which lists exactly the keys below. Here we ship every key
# enumerated literally; the count discrepancy in the contract preamble
# is acknowledged in the agent task notes.
_FEATURE_REGISTRY: dict[str, dict[str, str | None]] = {
    # League-scoped placeholders still blocked on unbuilt models.
    # LG-01z made Power Rankings live and FIN-01 made League / Team Finances
    # live; the rest carry a ``blocker`` note rendered on the explainer page.
    # Players-scoped placeholders (Free Agents + Watch List went live).
    "players_trade": {
        "label": "Trade",
        "section": "players",
        "sidebar_active": "trade",
        "blocker": (
            "Needs cap-space validation from the salary / contract "
            "model — not yet built."
        ),
    },
    "players_trading_block": {
        "label": "Trading Block",
        "section": "players",
        "sidebar_active": "trading_block",
        "blocker": (
            "Needs a Player.on_trading_block field and the Trade "
            "builder — not yet built."
        ),
    },
    "players_prospects": {
        "label": "Prospects",
        "section": "players",
        "sidebar_active": "prospects",
        "blocker": "Needs the LG-05 player-potential model — not yet built.",
    },
    "players_hall_of_fame": {
        "label": "Hall of Fame",
        "section": "players",
        "sidebar_active": "hall_of_fame",
        "blocker": (
            "Needs LG-03 season-end awards + LG-04 stat updates — " "not yet built."
        ),
    },
    # Help (6) — sandbox-mode, no sidebar
    "help_overview": {
        "label": "Overview",
        "section": "help",
        "sidebar_active": None,
    },
    "help_changes": {
        "label": "Changes",
        "section": "help",
        "sidebar_active": None,
    },
    "help_custom_rosters": {
        "label": "Custom Rosters",
        "section": "help",
        "sidebar_active": None,
    },
    "help_debugging": {
        "label": "Debugging",
        "section": "help",
        "sidebar_active": None,
    },
    "help_lol_gm_forums": {
        "label": "LOL GM Forums",
        "section": "help",
        "sidebar_active": None,
    },
    "help_zen_gm_forums": {
        "label": "Zen GM Forums",
        "section": "help",
        "sidebar_active": None,
    },
    # Tools (4) — sandbox-mode, no sidebar
    "tools_achievements": {
        "label": "Achievements",
        "section": "tools",
        "sidebar_active": None,
    },
    "tools_screenshot": {
        "label": "Screenshot",
        "section": "tools",
        "sidebar_active": None,
    },
    "tools_debug_mode": {
        "label": "Enable Debug Mode",
        "section": "tools",
        "sidebar_active": None,
    },
    "tools_reset_db": {
        "label": "Reset DB",
        "section": "tools",
        "sidebar_active": None,
    },
}


def coming_soon(
    request: HttpRequest,
    feature_key: str,
    league_id: int | None = None,
    team_id: int | None = None,
) -> HttpResponse:
    """LG-01h — single shared placeholder view for every disabled link.

    Routes from ``coming_soon_*`` URL names dispatch here. The view
    renders ``_placeholder.html`` with the feature's label / section /
    sidebar-active enum (looked up against ``_FEATURE_REGISTRY``); the
    sidebar partial is included only when ``league_id`` is provided
    (League / Team / Players / Stats placeholders). Help / Tools
    placeholders render in sandbox mode with no sidebar.

    The ``team_id`` kwarg is reserved for forward-compat with LG-02+
    per-Team routes and currently unused.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    # Local imports keep the module-level import surface lean and avoid
    # a circular import path (league_urls imports both modules).
    from .models import League
    from .league_views import _build_league_sidebar_links

    if league_id is not None:
        league = get_owned_or_404(League, request, pk=league_id)
    else:
        league = None

    feature = _FEATURE_REGISTRY.get(feature_key)
    if feature is None:
        raise Http404(f"Unknown placeholder feature {feature_key!r}.")

    sidebar_active = feature["sidebar_active"]

    if league is not None:
        # Session-pin the League id so the top-bar dropdown URLs stay
        # fresh as the user clicks through placeholder pages.
        try:
            request.session["last_league_id"] = league.id
        except AttributeError:
            # RequestFactory-built requests with no session middleware.
            pass

        displayed_season = (
            league.active_season
            or league.seasons.filter(state="completed").order_by("-id").first()
        )
        sidebar_links = _build_league_sidebar_links(
            league, displayed_season, sidebar_active
        )
    else:
        displayed_season = None
        sidebar_links = []

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "feature_key": feature_key,
        "feature_label": feature["label"],
        "feature_section": feature["section"],
        "blocker": feature.get("blocker"),
        "sidebar_links": sidebar_links,
        "sidebar_active": sidebar_active,
    }
    return render(request, "_placeholder.html", context)
