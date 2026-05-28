"""LG-01f — Project-wide context processors.

Currently exposes a single processor :func:`league_nav` which resolves
the top-bar History link target per the seam contract's three-step
resolution chain (session pin → single League → fallback to list).
"""

from __future__ import annotations

import logging

from django.db import DatabaseError
from django.http import HttpRequest
from django.urls import reverse

logger = logging.getLogger(__name__)


def league_nav(request: HttpRequest) -> dict[str, str]:
    """LG-01f — resolve the top-bar History link's per-user target.

    Resolution chain (locked):

        1. ``request.session["last_league_id"]``: if present AND the
           League still exists, return ``reverse("league_history",
           kwargs={"league_id": lid})``.
        2. Otherwise, if exactly one League exists in the DB, return
           ``reverse("league_history", kwargs={"league_id": <that
           league's id>})``.
        3. Otherwise (zero or 2+ Leagues, no session pin), return
           ``reverse("league_list")``.

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
                return {
                    "top_bar_history_url": reverse(
                        "league_history", kwargs={"league_id": lid}
                    )
                }

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
        return {
            "top_bar_history_url": reverse(
                "league_history", kwargs={"league_id": league_ids[0]}
            )
        }

    return {"top_bar_history_url": reverse("league_list")}
