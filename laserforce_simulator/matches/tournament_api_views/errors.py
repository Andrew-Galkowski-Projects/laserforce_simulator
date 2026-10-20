"""ANG-01f — the Tournaments API's conflict ``APIException``s and the one
translator from the ``tournament_services`` domain errors.

A leaf module: it imports nothing from the other ``tournament_api_views``
modules, so every action mixin and ``base`` import it at module top level.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import APIException, ValidationError

from matches.tournament_services.errors import (
    BrokerUnavailableError,
    DrawNotReadyError,
    NoPlayableNodeError,
    PlayInProgressError,
    TournamentEmbeddedError,
    TournamentNotActiveError,
    TournamentNotSetupError,
)

# ---------------------------------------------------------------------------
# State conflicts (named codes; the envelope handler shapes them).
# ---------------------------------------------------------------------------


class TournamentEmbedded(APIException):
    """409 — a write on an in-League Tournament (played through its Season)."""

    status_code = 409
    default_code = "tournament_embedded"
    default_detail = str(TournamentEmbeddedError())


class TournamentNotSetup(APIException):
    """409 — a setup-only write on a locked or completed Tournament."""

    status_code = 409
    default_code = "tournament_not_setup"
    default_detail = str(TournamentNotSetupError())


class TournamentNotActive(APIException):
    """409 — a play action on a Tournament that is not active."""

    status_code = 409
    default_code = "tournament_not_active"
    default_detail = str(TournamentNotActiveError())


class NoPlayableNode(APIException):
    """409 — play-next found no ready Match."""

    status_code = 409
    default_code = "no_playable_node"
    default_detail = str(NoPlayableNodeError())


class PlayInProgress(APIException):
    """409 — the Tournament already has an active play run (ADR-0040).

    The detail is keyed ``job:<id>`` so the envelope ``field`` names the live
    Job the client should poll instead.
    """

    status_code = 409
    default_code = "play_in_progress"
    default_detail = "The Tournament already has a play run in progress."

    def __init__(self, job_id: str) -> None:
        super().__init__(detail={f"job:{job_id}": self.default_detail})


class BrokerUnavailable(APIException):
    """503 — the Celery broker refused the play-all enqueue (nothing started)."""

    status_code = 503
    default_code = "broker_unavailable"
    default_detail = str(BrokerUnavailableError())


# One mapping from each ``tournament_services.errors`` exception to its API
# exception (each factory takes the caught error).
_SERVICE_ERRORS: dict[type[Exception], Callable[[Any], APIException]] = {
    TournamentEmbeddedError: lambda exc: TournamentEmbedded(str(exc)),
    TournamentNotSetupError: lambda exc: TournamentNotSetup(str(exc)),
    TournamentNotActiveError: lambda exc: TournamentNotActive(str(exc)),
    NoPlayableNodeError: lambda exc: NoPlayableNode(str(exc)),
    PlayInProgressError: lambda exc: PlayInProgress(exc.job_id),
    BrokerUnavailableError: lambda exc: BrokerUnavailable(str(exc)),
    DrawNotReadyError: lambda exc: ValidationError(str(exc)),
    # The lock's model ValidationError (format rules) — 400 ``invalid``, field null.
    DjangoValidationError: lambda exc: ValidationError(exc.messages),
}


@contextmanager
def translate_tournament_errors() -> Iterator[None]:
    """Re-raise a Tournament service error as its API exception.

    409 ``tournament_embedded`` / ``tournament_not_setup`` /
    ``tournament_not_active`` / ``no_playable_node``; 409 ``play_in_progress``
    with ``field = job:<id>``; 503 ``broker_unavailable``; ``DrawNotReadyError``
    and the lock's ``ValidationError`` ⇒ 400 ``invalid`` with field null.
    Anything else propagates unchanged.
    """
    try:
        yield
    except tuple(_SERVICE_ERRORS) as exc:
        factory = next(
            _SERVICE_ERRORS[cls] for cls in type(exc).__mro__ if cls in _SERVICE_ERRORS
        )
        raise factory(exc) from exc
