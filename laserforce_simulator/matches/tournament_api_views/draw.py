"""ANG-01f — the Player-pool and Draw write actions of ``TournamentViewSet``
(ticket ANG-01f-4). The ``pool/`` GET lives in ``base``."""

from __future__ import annotations


class DrawActionsMixin:
    """Pool add / generate / import / remove, run the Draw, edit a draw entry."""
