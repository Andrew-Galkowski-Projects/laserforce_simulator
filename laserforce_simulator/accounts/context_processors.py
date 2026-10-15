"""UX-02 — expose the Account's Self-insert to every template."""

from __future__ import annotations

import logging
from typing import Any

from django.db import DatabaseError
from django.http import HttpRequest

from .self_insert import get_self_insert

logger = logging.getLogger(__name__)


def self_insert(request: HttpRequest) -> dict[str, Any]:
    """Return ``{"self_insert_player": Player | None, "self_insert_player_id": int | None}``.

    Anonymous, a request with no ``user`` attribute, an unlinked or drifted
    link, or a ``DatabaseError`` inside a broken transaction ⇒ both ``None``.
    """
    try:
        player = get_self_insert(getattr(request, "user", None))
    except DatabaseError:
        logger.debug(
            "self_insert: lookup raised inside broken transaction; using None",
            exc_info=True,
        )
        player = None
    return {
        "self_insert_player": player,
        "self_insert_player_id": player.id if player is not None else None,
    }
