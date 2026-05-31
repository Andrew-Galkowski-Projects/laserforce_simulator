"""LG-01z-j — Watch List (league-context) screen.

Session-scoped watch list of players (no model, no migration). The watched
player ids live in ``request.session["watch_list"]`` as a list of ints,
keyed globally per browser session (NOT per-League — noted in the template
UI text).

Follows the shared LG-01z view contract (§2 of the seam contract) EXCEPT
for the documented GET-toggle exception (§4 entry "j"): a plain GET renders
the list, while ``?action=add|remove&player_id=<id>`` mutates the session
and redirects back to the bare watch-list URL so a refresh does not
re-toggle. ``?action=clear`` empties the whole list (the "Remove All"
control; no ``player_id`` needed). POST would require CSRF plumbing; a GET
toggle is acceptable for a session-local convenience list.

Renders the watched players (each links to their player/career page) with a
Remove control, plus an add control listing the remaining players with an
Add link. Empty-state notice covers both "no Season" (substring
"No Season") and "watch list empty" via distinct messages sharing the same
DOM id ``watch-list-empty-notice``.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from matches.league_views import _build_league_sidebar_links
from matches.models import League
from teams.models import Player


def _coerce_player_id(raw: str | None) -> int | None:
    """Coerce a ``?player_id=`` query value to a positive int, else None."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def watch_list(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-j — Watch List (league-context) page with GET add/remove toggle."""
    # The only valid traffic is GET (plain render OR the add/remove toggle).
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)

    # --- GET toggle: ?action=add|remove&player_id=<id> -------------------
    # Mutate the session then redirect back to the bare watch-list URL so a
    # refresh does not re-toggle. The League 404 above still fires first.
    action = request.GET.get("action")
    if action in ("add", "remove", "clear"):
        watched: list = list(request.session.get("watch_list", []))
        if action == "clear":
            # Drop the whole list — no player_id needed; any supplied is ignored.
            request.session["watch_list"] = []
            request.session.modified = True
        else:
            player_id = _coerce_player_id(request.GET.get("player_id"))
            # Only mutate for a real Player id; invalid / unknown ids are ignored.
            if player_id is not None and Player.objects.filter(pk=player_id).exists():
                if action == "add":
                    if player_id not in watched:
                        watched.append(player_id)
                else:  # action == "remove"
                    watched = [pid for pid in watched if pid != player_id]
                request.session["watch_list"] = watched
                request.session.modified = True
        # Redirect back to the bare watch-list URL so a refresh does not
        # re-toggle. The central owner wires the ``players_watch_list`` route
        # (§3); until then the locked path is built directly so the view is
        # self-contained and the redirect target is stable.
        return redirect(f"/leagues/{league.id}/players/watch-list/")

    # --- Plain GET: render the watch list --------------------------------
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )

    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="watch_list"
    )

    # No Season ⇒ empty-state (the sidebar still renders).
    if displayed_season is None:
        context = {
            "league": league,
            "displayed_season": None,
            "sidebar_links": sidebar_links,
            "sidebar_active": "watch_list",
            "watched_players": [],
            "addable_players": [],
        }
        return render(request, "leagues/watch_list.html", context)

    # Resolve the watched ids (ints) into real Player rows; drop stale ids
    # (deleted players) silently. Preserve session list order for display.
    watched_ids: list[int] = []
    for pid in request.session.get("watch_list", []):
        coerced = _coerce_player_id(pid) if isinstance(pid, str) else pid
        if isinstance(coerced, int) and coerced not in watched_ids:
            watched_ids.append(coerced)

    players_by_id = {
        p.id: p
        for p in Player.objects.filter(pk__in=watched_ids).select_related("team")
    }
    watched_players = [
        players_by_id[pid] for pid in watched_ids if pid in players_by_id
    ]

    # Add control: every Player NOT already watched, ordered for a stable list.
    watched_set = {p.id for p in watched_players}
    addable_players = [
        p
        for p in Player.objects.select_related("team").order_by("team__name", "name")
        if p.id not in watched_set
    ]

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "watch_list",
        "watched_players": watched_players,
        "addable_players": addable_players,
    }
    return render(request, "leagues/watch_list.html", context)
