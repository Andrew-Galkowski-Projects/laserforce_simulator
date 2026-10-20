"""ANG-01f — Tournament domain errors and the shared write guards.

Raised by the ``tournament_services`` modules and translated by the HTML views
(flash + redirect) and the API (``tournament_api_views.base``) alike. The
guards check in a fixed order — embedded first, then state — so a write on an
embedded locked Tournament always reports ``TournamentEmbeddedError``.

"Embedded" (:func:`matches.tournament_services.detail.is_embedded`) means the
Tournament is referenced by a ``SeasonPhase`` or carries a ``season_phase``
(regional / Worlds): it is played only through its Season (ADR-0040).
"""

from __future__ import annotations

from matches.models import Tournament
from matches.tournament_services.detail import is_embedded


class TournamentEmbeddedError(Exception):
    """A write on an in-League Tournament outside its Season."""

    def __init__(
        self,
        message: str = (
            "This Tournament belongs to a League Season and is played through "
            "that Season."
        ),
    ) -> None:
        super().__init__(message)


class TournamentNotSetupError(Exception):
    """A setup-only write on a locked or completed Tournament."""

    def __init__(self, message: str = "The Tournament is no longer in setup.") -> None:
        super().__init__(message)


class TournamentNotActiveError(Exception):
    """A play action on a Tournament that is not active."""

    def __init__(self, message: str = "The Tournament is not active.") -> None:
        super().__init__(message)


class NoPlayableNodeError(Exception):
    """Play-next found no ready Bracket node."""

    def __init__(self, message: str = "No Match is ready to play.") -> None:
        super().__init__(message)


class PlayInProgressError(Exception):
    """The Tournament already has an active play run (``.job_id`` is the live Job)."""

    def __init__(self, job_id: str) -> None:
        super().__init__("The Tournament already has a play run in progress.")
        self.job_id = job_id


class BrokerUnavailableError(Exception):
    """The task broker refused the play-all enqueue (nothing started)."""

    def __init__(
        self, message: str = "The background task queue is unavailable."
    ) -> None:
        super().__init__(message)


class DrawNotReadyError(Exception):
    """The Player pool cannot be drawn (``str(exc)`` is the user-facing rule)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def require_sandbox(tournament: Tournament) -> None:
    """Raise ``TournamentEmbeddedError`` for an embedded Tournament."""
    if is_embedded(tournament):
        raise TournamentEmbeddedError()


def require_setup(tournament: Tournament) -> None:
    """Sandbox first, then ``TournamentNotSetupError`` unless in ``setup``."""
    require_sandbox(tournament)
    if tournament.state != "setup":
        raise TournamentNotSetupError()


def require_active(tournament: Tournament) -> None:
    """Sandbox first, then ``TournamentNotActiveError`` unless ``active``."""
    require_sandbox(tournament)
    if tournament.state != "active":
        raise TournamentNotActiveError()
