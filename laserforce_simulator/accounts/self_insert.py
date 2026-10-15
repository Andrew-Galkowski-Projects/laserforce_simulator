"""UX-02 — the Account's **Self-insert** (CONTEXT.md).

A Self-insert is a vanity *link* from an Account to one existing Player. It
never moves, enrols or re-simulates that Player. This module is a domain
feature built *on* the ownership seam in ``accounts/permissions.py``; it is not
part of that seam.

Public surface (exactly four functions):

* :func:`get_self_insert` — read-only drift guard.
* :func:`is_self_insert_eligible` — STRICT eligibility (Team managed by the
  Account; Unmanaged Teams excluded).
* :func:`set_self_insert` — link (replaces any previous link).
* :func:`clear_self_insert` — unlink.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db import transaction

from teams.models import Player

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser
    from django.contrib.auth.models import AnonymousUser

    from .models import User

__all__ = [
    "get_self_insert",
    "is_self_insert_eligible",
    "set_self_insert",
    "clear_self_insert",
]


def get_self_insert(
    user: "AbstractBaseUser | AnonymousUser | None",
) -> "Player | None":
    """Return the Account's Self-insert Player, or ``None``.

    ``None`` when the user is missing / anonymous, when no link is stored (zero
    queries — the column is already on the loaded row), or when the link has
    **drifted** (the Player's Team is no longer managed by this Account).

    Never writes: a drifted link stays in the column and reappears if
    ownership returns. Does not touch the ``user.self_insert`` cache.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    player_id = getattr(user, "self_insert_id", None)
    if player_id is None:
        return None
    return (
        Player.objects.select_related("team")
        .filter(pk=player_id, team__manager_id=user.pk)
        .first()
    )


def is_self_insert_eligible(
    player: Player, user: "AbstractBaseUser | AnonymousUser | None"
) -> bool:
    """STRICT: ``True`` iff ``user`` is authenticated and manages ``player.team``.

    Unmanaged Teams (``manager_id is None``) are never eligible.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    manager_id = player.team.manager_id
    return manager_id is not None and manager_id == user.pk


def set_self_insert(user: "User", player: Player) -> None:
    """Link ``player`` as ``user``'s Self-insert, replacing any previous link.

    Precondition (caller-checked): ``is_self_insert_eligible(player, user)``.
    Any other Account's (drifted) link to the same Player is cleared first so
    the OneToOne unique constraint never fires.
    """
    from .models import User as UserModel

    with transaction.atomic():
        UserModel.objects.filter(self_insert=player).exclude(pk=user.pk).update(
            self_insert=None
        )
        user.self_insert = player
        user.save(update_fields=["self_insert"])


def clear_self_insert(user: "User") -> None:
    """Clear ``user``'s Self-insert column (even when the link had drifted)."""
    user.self_insert = None
    user.save(update_fields=["self_insert"])
