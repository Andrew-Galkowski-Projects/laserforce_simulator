"""LG-01f / LG-01h / LG-01k — Project-wide context processors.

Exposes two processors:

* :func:`league_nav` — LG-01k resolves the 23-entry top-bar links list +
  the leading Dashboard URL for league-mode rendering. The processor
  reuses the LG-01h three-step League resolution chain (session pin →
  single League → fallback) and the LG-01f displayed-Season chain
  (active → most-recent completed → ``None``). On success the dict
  carries ``top_bar_links`` (the 23-entry output of
  ``_build_league_sidebar_links(league, displayed_season,
  sidebar_active=None)``) and ``top_bar_dashboard_url`` (resolved to
  the picked League's dashboard). On fallback both keys revert to an
  empty list and the ``league_list`` URL respectively.
* :func:`app_mode` — LG-01k 3-mode path-prefix detector returning
  ``{"app_mode": "start" | "league" | "sandbox"}`` so ``base.html``
  can branch its top-bar navigation between the start mode (path
  exactly ``/``), the league-context mode (paths under ``/leagues/``
  or ``/seasons/``), and the sandbox default.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import DatabaseError
from django.http import HttpRequest
from django.urls import reverse

logger = logging.getLogger(__name__)


def league_nav(request: HttpRequest) -> dict[str, Any]:
    """LG-01k — resolve the top-bar links list + dashboard URL.

    Returns ``{"top_bar_links": list[dict], "top_bar_dashboard_url": str}``.

    When the 3-step League resolution chain succeeds:

      * ``top_bar_links`` is the 23-entry output of
        ``_build_league_sidebar_links(league, displayed_season,
        sidebar_active=None)``.
      * ``top_bar_dashboard_url`` is
        ``reverse("league_dashboard", kwargs={"league_id": league.id})``.

    When the chain falls back (zero or 2+ Leagues with no session pin,
    a stale session pin pointing at a deleted League, or a
    ``DatabaseError`` inside a broken transaction):

      * ``top_bar_links`` is ``[]``.
      * ``top_bar_dashboard_url`` is ``reverse("league_list")``.

    The displayed-Season chain (used to feed the helper) is:
    ``league.active_season`` first, then the most-recent completed
    Season, then ``None`` — the helper itself handles ``None`` by
    emitting Standings / Schedule entries with ``url=None,
    disabled=True`` (LG-01f rule).
    """
    # Local imports keep the test-time fresh-import of unrelated apps
    # cheap; settings registration loads this module on every render so
    # we still want League / helper visible at function scope, not module
    # scope (avoids the ``core`` ↔ ``matches`` apps-loading cycle).
    from matches.models import League, Season
    from matches.league_views import (
        _build_league_sidebar_links,
        _build_play_controls_context,
    )

    list_url = reverse("league_list")

    # NAV-01 §2 — the topnav ``Play ▾`` dropdown renders ONLY on a league-prefix
    # path (the ``app_mode == "league"`` rule: ``/leagues/`` or ``/seasons/``).
    # Off-league pages skip the play-control ORM work entirely (the keys are
    # ABSENT, never read).
    request_path = getattr(request, "path", "") or ""
    is_league_path = request_path.startswith("/leagues/") or request_path.startswith(
        "/seasons/"
    )

    def _fallback() -> dict[str, Any]:
        """Empty-links + list-page-dashboard fallback shape."""
        return {"top_bar_links": [], "top_bar_dashboard_url": list_url}

    def _resolve_displayed_season(league_obj: League) -> "Season | None":
        """LG-01f / LG-01h displayed-Season chain — defensive on DB errors."""
        try:
            displayed = league_obj.active_season
        except DatabaseError:
            logger.debug(
                "league_nav: league.active_season raised inside broken "
                "transaction; using None as displayed_season",
                exc_info=True,
            )
            return None
        if displayed is not None:
            return displayed
        try:
            return league_obj.seasons.filter(state="completed").order_by("-id").first()
        except DatabaseError:
            logger.debug(
                "league_nav: fallback completed-season lookup raised inside "
                "broken transaction; using None as displayed_season",
                exc_info=True,
            )
            return None

    def _success(league_obj: League) -> dict[str, Any]:
        """Build the success shape for the resolved League."""
        displayed = _resolve_displayed_season(league_obj)
        try:
            links = _build_league_sidebar_links(
                league_obj, displayed, sidebar_active=None
            )
        except DatabaseError:
            logger.debug(
                "league_nav: _build_league_sidebar_links raised inside broken "
                "transaction; falling through to list URL",
                exc_info=True,
            )
            return _fallback()
        dashboard_url = reverse("league_dashboard", kwargs={"league_id": league_obj.id})
        result: dict[str, Any] = {
            "top_bar_links": links,
            "top_bar_dashboard_url": dashboard_url,
        }
        # NAV-01 §2 — merge the 9 play keys + the 2 URL/id keys ONLY on a
        # league-prefix path. Off-league pages leave them ABSENT (the topnav
        # ``Play ▾`` dropdown renders only in the league branch). Wrapped in the
        # same defensive ``DatabaseError`` guard the file already uses.
        if is_league_path:
            try:
                play_keys = _build_play_controls_context(league_obj, displayed)
            except DatabaseError:
                logger.debug(
                    "league_nav: _build_play_controls_context raised inside "
                    "broken transaction; omitting play keys",
                    exc_info=True,
                )
            else:
                result.update(play_keys)
                result["play_displayed_season_id"] = (
                    displayed.id if displayed is not None else None
                )
                result["play_league_id"] = league_obj.id
        return result

    session = getattr(request, "session", None)
    last_league_id = session.get("last_league_id") if session is not None else None

    if last_league_id is not None:
        try:
            lid = int(last_league_id)
        except (TypeError, ValueError):
            lid = None
        if lid is not None:
            try:
                league_obj = League.objects.filter(pk=lid).first()
            except DatabaseError:
                logger.debug(
                    "league_nav: session-pin League lookup raised inside "
                    "broken transaction; falling through to list URL",
                    exc_info=True,
                )
                league_obj = None
            if league_obj is not None:
                return _success(league_obj)

    # ``[:2]`` LIMITs the count query — we only need 0 / 1 / many.
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
        try:
            league_obj = League.objects.filter(pk=league_ids[0]).first()
        except DatabaseError:
            logger.debug(
                "league_nav: single-League fetch raised inside broken "
                "transaction; falling through to list URL",
                exc_info=True,
            )
            league_obj = None
        if league_obj is not None:
            return _success(league_obj)

    return _fallback()


def app_mode(request: HttpRequest) -> dict[str, str]:
    """LG-01k — 3-mode path-prefix detector.

    Returns ``{"app_mode": "start" | "league" | "sandbox"}``.

    Locked path-prefix rule, applied in this exact order:

        1. ``path == "/"`` (exact match) ⇒ ``"start"``.
        2. ``path.startswith("/leagues/")`` or
           ``path.startswith("/seasons/")`` ⇒ ``"league"``.
        3. Everything else (including empty string, missing ``.path``
           attribute, ``/teams/``, ``/players/``, ``/matches/``,
           ``/maps/``, ``/help/*``, ``/tools/*``, any unknown path)
           ⇒ ``"sandbox"``.

    Defensive: a ``RequestFactory()``-built request with no ``.path``
    attribute (or an empty ``""`` string) falls through to ``"sandbox"``
    — only an explicit ``"/"`` string resolves to ``"start"``.

    The three literal values ``"start"`` / ``"league"`` / ``"sandbox"``
    and the context key ``app_mode`` are locked by the LG-01k seam
    contract.
    """
    path = getattr(request, "path", None)
    if path == "/":
        return {"app_mode": "start"}
    if path and (path.startswith("/leagues/") or path.startswith("/seasons/")):
        return {"app_mode": "league"}
    return {"app_mode": "sandbox"}


def watch_list(request: HttpRequest) -> dict:
    """LG-06f — expose the watched-player id set to every league-screen template.

    Returns ``{"watched_player_ids": set[int]}``.

    Resolves ``league_id`` from ``request.resolver_match.kwargs.get("league_id")``
    defensively (``getattr(request, "resolver_match", None)`` — ``None`` when
    there is no match, e.g. a 404 before URL resolution or a
    ``RequestFactory()``-built request). Off-League (no ``resolver_match``, or
    no ``"league_id"`` kwarg) ⇒ ``{"watched_player_ids": set()}``.

    Reuses ``matches.league_views._watched_player_ids`` for the read (lazy
    import to avoid the ``core`` ↔ ``matches`` apps-loading cycle — mirrors the
    ``league_nav`` precedent).
    """
    resolver_match = getattr(request, "resolver_match", None)
    if resolver_match is None:
        return {"watched_player_ids": set()}
    kwargs = getattr(resolver_match, "kwargs", None) or {}
    league_id = kwargs.get("league_id")
    if league_id is None:
        return {"watched_player_ids": set()}
    try:
        league_id_int = int(league_id)
    except (TypeError, ValueError):
        return {"watched_player_ids": set()}

    from matches.league_views import _watched_player_ids

    return {"watched_player_ids": _watched_player_ids(request, league_id_int)}
