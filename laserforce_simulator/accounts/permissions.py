"""UX-01 — Ownership resolution and the permission seam (ADR-0038).

A row is an **Ownership root** exactly when its parent FK is null; every other
row derives its **Manager** by traversing its non-null parent FK. Access is
granted when the resolved root's ``manager_id`` is the requesting Account, or
is NULL (an **Unmanaged row** — readable AND writable by any authenticated
Account). Cross-Account access raises **Http404, never 403**: another Account's
row must not be distinguishable from a nonexistent one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from django.core.exceptions import FieldDoesNotExist
from django.db.models import Model, Q, QuerySet
from django.http import Http404, HttpRequest
from django.shortcuts import get_object_or_404

from matches.models import (
    BracketNode,
    Conference,
    GameEvent,
    GameRound,
    League,
    Match,
    OwnerEvaluation,
    PlayerRoundState,
    PlayerSeasonRating,
    Season,
    SeasonPhase,
    SeriesMatch,
    TeamSeasonFinance,
    Tournament,
    TournamentParticipant,
    TournamentPlayerEntry,
)
from teams.models import Player, Team

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

_M = TypeVar("_M", bound=Model)

ROOT_MODELS: tuple[type[Model], ...] = (Team, League, Tournament, Match, GameRound)
"""The five Ownership roots, in contract order: Team, League, Tournament, Match, GameRound."""

# Model -> the field name of its ownership parent. Models absent from this
# table that carry ``manager`` are always roots; models absent from it that do
# NOT carry ``manager`` (ArenaMap and the six map-config models) have no
# ownership axis at all.
#
# NOTE: ``TeamSeasonFinance.team`` and ``OwnerEvaluation.team_managed`` are
# SET_NULL and are NOT the ownership parent — the season / league FK is.
# Likewise ``PlayerSeasonRating.player`` is not; ``season`` is, because a
# rating is League data.
_PARENT_FIELD: dict[type[Model], str] = {
    # Conditional roots — self when the parent FK is NULL.
    Match: "season",
    GameRound: "match",
    # Derived rows.
    Player: "team",
    Season: "league",
    Conference: "season",
    SeasonPhase: "season",
    PlayerSeasonRating: "season",
    OwnerEvaluation: "league",
    TeamSeasonFinance: "season",
    PlayerRoundState: "game_round",
    GameEvent: "game_round",
    TournamentParticipant: "tournament",
    BracketNode: "tournament",
    SeriesMatch: "node",
    TournamentPlayerEntry: "tournament",
}


_MAX_TRAVERSAL_DEPTH = 8  # deepest real chain is GameEvent -> ... -> League (4 hops)


def _has_manager(model: type[Model]) -> bool:
    """True when ``model`` declares a concrete ``manager`` field."""
    try:
        model._meta.get_field("manager")
    except FieldDoesNotExist:
        return False
    return True


def _has_ownership_axis(model: type[Model]) -> bool:
    """True when ``model`` participates in ownership at all.

    False only for rows that are deliberately **shared reference data** --
    ``core.ArenaMap`` and the six map-config models, which neither carry
    ``manager`` nor appear in ``_PARENT_FIELD``. This is what lets
    ``is_owned_by`` distinguish "no ownership axis, allow" from "had an
    ownership chain that dead-ended, deny", so a future ``_PARENT_FIELD``
    entry with a nullable parent FK fails **closed** rather than open.
    """
    return model in _PARENT_FIELD or _has_manager(model)


def ownership_root(obj: Model) -> "Model | None":
    """Return the **Ownership root** of ``obj``, or ``None`` when it has no
    ownership axis (an ArenaMap or a map-config row).

    A row is its own root when it carries ``manager`` AND either has no
    ownership parent field or that parent FK is NULL.
    """
    current: "Model | None" = obj
    for _ in range(_MAX_TRAVERSAL_DEPTH):
        if current is None:
            return None
        parent_field = _PARENT_FIELD.get(type(current))
        if _has_manager(type(current)) and (
            parent_field is None or getattr(current, f"{parent_field}_id") is None
        ):
            return current
        if parent_field is None:
            return None
        current = getattr(current, parent_field)
    return None


def _root_join_path(model: type[Model]) -> str:
    """The ``select_related`` lookup spanning ``model``'s ownership chain.

    ``""`` for a model that is always its own root or has no ownership axis;
    otherwise the ``__``-joined parent-field chain, e.g. ``GameRound`` gives
    ``"match__season__league"``.
    """
    parts: list[str] = []
    current = model
    for _ in range(_MAX_TRAVERSAL_DEPTH):
        parent_field = _PARENT_FIELD.get(current)
        if parent_field is None:
            break
        parts.append(parent_field)
        try:
            current = current._meta.get_field(parent_field).related_model
        except FieldDoesNotExist:  # pragma: no cover - table is hand-maintained
            break
        if current is None:  # pragma: no cover - defensive
            break
    return "__".join(parts)


def _with_root_joined(model: "type[_M] | QuerySet[_M]") -> "QuerySet[_M]":
    """``model`` as a queryset with its ownership chain ``select_related``-ed."""
    qs = model._default_manager.all() if isinstance(model, type) else model
    path = _root_join_path(qs.model)
    return qs.select_related(path) if path else qs


def is_owned_by(obj: Model, user: "AbstractBaseUser | None") -> bool:
    """True when ``user`` may read AND write ``obj``.

    A row with no ownership axis (ArenaMap) is always True — shared reference
    data. An **Unmanaged row** (root ``manager`` NULL) is always True.

    A row that *does* have an ownership axis but whose chain dead-ends fails
    **closed**. Unreachable today (every derived model's parent FK is non-null;
    the only two nullable ones, ``Match.season`` and ``GameRound.match``, sit on
    models that carry ``manager`` and so resolve to themselves), but it keeps a
    future ``_PARENT_FIELD`` entry from silently granting access.
    """
    root = ownership_root(obj)
    if root is None:
        return not _has_ownership_axis(type(obj))
    if root.manager_id is None:
        return True
    return (
        user is not None
        and getattr(user, "is_authenticated", False)
        and root.manager_id == user.pk
    )


def get_owned_or_404(
    model: "type[_M] | QuerySet[_M]", request: HttpRequest, **lookup: object
) -> _M:
    """`get_object_or_404` plus an ownership gate.

    Resolves the row, walks to its **Ownership root**, and raises ``Http404``
    unless the root's ``manager_id`` is ``request.user.id`` OR is ``None`` (an
    **Unmanaged row**).

    Returns **the resolved row**, NOT its root.

    404 — never 403 — so another Account's row is indistinguishable from one
    that does not exist.

    The ownership chain is ``select_related``-ed up front, so resolving a deep
    row costs **one** query rather than one per hop (a ``GameRound`` otherwise
    cost three extra: ``Match`` → ``Season`` → ``League``).
    """
    obj = get_object_or_404(_with_root_joined(model), **lookup)
    if not is_owned_by(obj, getattr(request, "user", None)):
        raise Http404("No %s matches the given query." % obj._meta.object_name)
    return obj


def owned_queryset(
    qs: QuerySet[_M], user: "AbstractBaseUser | None", *, path: str = ""
) -> QuerySet[_M]:
    """Filter ``qs`` to rows the Account may see: its own plus **Unmanaged rows**.

    ``path`` is the ORM lookup prefix from the queryset's model to the root that
    carries ``manager`` — ``""`` (the default) for a root model itself,
    ``"team"`` for ``Player``, ``"season__league"`` for ``Season``-scoped rows.

    NOT VALID for ``Match`` or ``GameRound`` — their roots are conditional; use
    ``owned_match_q`` / ``owned_game_round_q`` instead. Passing one raises
    ``ValueError`` rather than silently filtering on the wrong column.
    """
    if not path and qs.model in (Match, GameRound):
        raise ValueError(
            f"{qs.model.__name__} has a conditional Ownership root; use "
            f"owned_{'match' if qs.model is Match else 'game_round'}_q() instead."
        )
    prefix = f"{path}__" if path else ""
    return qs.filter(
        Q(**{f"{prefix}manager": user}) | Q(**{f"{prefix}manager__isnull": True})
    )


def owned_match_q(user: "AbstractBaseUser | None") -> Q:
    """Predicate for `Match`: flat on a sandbox Match, traversed for a Season Match."""
    return (Q(season__isnull=True) & (Q(manager=user) | Q(manager__isnull=True))) | (
        Q(season__isnull=False)
        & (Q(season__league__manager=user) | Q(season__league__manager__isnull=True))
    )


def owned_game_round_q(user: "AbstractBaseUser | None") -> Q:
    """Predicate for `GameRound`: flat when standalone, else through its Match."""
    return (
        (Q(match__isnull=True) & (Q(manager=user) | Q(manager__isnull=True)))
        | (
            Q(match__isnull=False)
            & Q(match__season__isnull=True)
            & (Q(match__manager=user) | Q(match__manager__isnull=True))
        )
        | (
            Q(match__isnull=False)
            & Q(match__season__isnull=False)
            & (
                Q(match__season__league__manager=user)
                | Q(match__season__league__manager__isnull=True)
            )
        )
    )


def stamp_manager(obj: _M, user: "AbstractBaseUser | None") -> _M:
    """Set ``obj.manager`` to ``user`` and persist just that column.

    Used where the row is created by code that has no ``request`` — the
    `BatchSimulator` return values in the sandbox create views. An
    unauthenticated ``user`` leaves the row **Unmanaged**. Returns ``obj``.
    """
    obj.manager = (
        user
        if (user is not None and getattr(user, "is_authenticated", False))
        else None
    )
    obj.save(update_fields=["manager"])
    return obj


def manager_or_none(request: HttpRequest) -> "AbstractBaseUser | None":
    """``request.user`` when authenticated, else ``None`` (an **Unmanaged row**)."""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None
