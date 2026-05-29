"""LG-01f / LG-01h — Project-wide context processors.

Exposes two processors:

* :func:`league_nav` — resolves the top-bar dropdown link targets per
  the LG-01f / LG-01h seam contracts (Standings / Playoffs / Finances /
  History / Power Rankings) using a three-step resolution chain
  (session pin → single League → fallback to list).
* :func:`app_mode` — LG-01h path-prefix mode detector returning
  ``{"app_mode": "league" | "sandbox"}`` so ``base.html`` can branch
  its top-bar navigation between league-context and sandbox modes.
"""

from __future__ import annotations

import logging

from django.db import DatabaseError
from django.http import HttpRequest
from django.urls import reverse

logger = logging.getLogger(__name__)


def league_nav(request: HttpRequest) -> dict[str, str]:
    """LG-01f / LG-01h — resolve the top-bar League dropdown link targets.

    Resolution chain (locked, applied to each of the four placeholder
    keys plus the LIVE History key):

        1. ``request.session["last_league_id"]``: if present AND the
           League still exists, return the per-League URL for that key.
        2. Otherwise, if exactly one League exists in the DB, return
           the per-League URL for that key.
        3. Otherwise (zero or 2+ Leagues, no session pin), return
           ``reverse("league_list")``.

    Returns keys (LG-01f + LG-01h):

    * ``top_bar_history_url`` (LG-01f, LIVE ``league_history`` route).
    * ``top_bar_standings_url`` (LG-01h, ``season_standings`` of the
      picked League's displayed Season — falls back to ``league_list``
      when no Season is available).
    * ``top_bar_playoffs_url`` (LG-01h, ``coming_soon_playoffs``).
    * ``top_bar_finances_url`` (LG-01h, ``coming_soon_finances``).
    * ``top_bar_power_rankings_url`` (LG-01h,
      ``coming_soon_power_rankings``).

    Defensive: a session id pointing at a deleted League falls through
    via the ``.exists()`` guard. If the request is rendered inside an
    atomic transaction marked for rollback (e.g. an ``@transaction.atomic``
    view that caught an exception and re-renders with form errors), the
    ORM raises ``TransactionManagementError`` on any query — catch and
    fall through to the list URL so the response still renders.
    """
    # Local import keeps the test-time fresh-import of unrelated apps
    # cheap; settings registration loads this module on every render so
    # we still want League visible at function scope, not module scope.
    from matches.models import League

    list_url = reverse("league_list")

    def _urls_for_league(lid: int) -> dict[str, str]:
        """Build the five top-bar URLs for a given League id."""
        history_url = reverse("league_history", kwargs={"league_id": lid})
        playoffs_url = reverse("coming_soon_playoffs", kwargs={"league_id": lid})
        finances_url = reverse("coming_soon_finances", kwargs={"league_id": lid})
        power_rankings_url = reverse(
            "coming_soon_power_rankings", kwargs={"league_id": lid}
        )
        # Standings is resolved via the picked League's active or most
        # recent completed Season; fall back to the list URL when no
        # Season is available.
        try:
            league_obj = League.objects.filter(pk=lid).first()
        except DatabaseError:
            logger.debug(
                "league_nav: League.objects.filter(pk=%s).first() raised "
                "inside broken transaction; using list URL for standings",
                lid,
                exc_info=True,
            )
            league_obj = None
        standings_url = list_url
        if league_obj is not None:
            displayed = league_obj.active_season
            if displayed is None:
                try:
                    displayed = (
                        league_obj.seasons.filter(state="completed")
                        .order_by("-id")
                        .first()
                    )
                except DatabaseError:
                    logger.debug(
                        "league_nav: fallback completed-season lookup raised "
                        "inside broken transaction; using list URL for standings",
                        exc_info=True,
                    )
                    displayed = None
            if displayed is not None:
                standings_url = reverse(
                    "season_standings", kwargs={"season_id": displayed.id}
                )
        return {
            "top_bar_history_url": history_url,
            "top_bar_standings_url": standings_url,
            "top_bar_playoffs_url": playoffs_url,
            "top_bar_finances_url": finances_url,
            "top_bar_power_rankings_url": power_rankings_url,
        }

    def _all_list() -> dict[str, str]:
        """Fallback: every key resolves to the league_list URL."""
        return {
            "top_bar_history_url": list_url,
            "top_bar_standings_url": list_url,
            "top_bar_playoffs_url": list_url,
            "top_bar_finances_url": list_url,
            "top_bar_power_rankings_url": list_url,
        }

    session = getattr(request, "session", None)
    last_league_id = session.get("last_league_id") if session is not None else None

    if last_league_id is not None:
        try:
            lid = int(last_league_id)
        except (TypeError, ValueError):
            lid = None
        if lid is not None:
            try:
                exists = League.objects.filter(pk=lid).exists()
            except DatabaseError:
                logger.debug(
                    "league_nav: session-pin .exists() raised inside broken "
                    "transaction; falling through to list URL",
                    exc_info=True,
                )
                exists = False
            if exists:
                return _urls_for_league(lid)

    # `[:2]` LIMITs the count query — we only need 0 / 1 / many.
    try:
        league_ids = list(League.objects.values_list("id", flat=True)[:2])
    except DatabaseError:
        logger.debug(
            "league_nav: single-League branch query raised inside broken "
            "transaction; falling through to list URL",
            exc_info=True,
        )
        league_ids = []
    if len(league_ids) == 1:
        return _urls_for_league(league_ids[0])

    return _all_list()


def app_mode(request: HttpRequest) -> dict[str, str]:
    """LG-01h — path-prefix mode detector.

    Returns ``{"app_mode": "league"}`` when the request path starts with
    ``/leagues/`` or ``/seasons/``; returns ``{"app_mode": "sandbox"}``
    otherwise. Defensive: a ``RequestFactory()``-built request with no
    ``.path`` attribute (or an empty string) falls through to the
    sandbox branch.

    The two literal values ``"league"`` and ``"sandbox"`` and the
    context key ``app_mode`` are locked by the LG-01h seam contract.
    """
    path = getattr(request, "path", "/") or "/"
    if path.startswith("/leagues/") or path.startswith("/seasons/"):
        return {"app_mode": "league"}
    return {"app_mode": "sandbox"}
