from __future__ import annotations

import secrets
from typing import Any

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    """UX-01 — email-keyed manager for the custom `User`.

    `AbstractUser`'s default manager is keyed on `username`, which this model
    drops. Both creators take `email` as the first positional argument.
    """

    use_in_migrations = True

    def _create_user(
        self, email: str, password: "str | None", **extra_fields: Any
    ) -> "User":
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(
        self, email: str, password: "str | None" = None, **extra_fields: Any
    ) -> "User":
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(
        self, email: str, password: "str | None" = None, **extra_fields: Any
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """UX-01 — the **Account**. Email-first; `username` is dropped entirely.

    NAMING: an Account becomes a row's **Manager** by way of the `manager` FK on
    each Ownership root. It is NEVER the **Owner** — that is the fictional boss
    of ADR-0026 (`OwnerEvaluation`, `owner_mood.py`), which has no login.
    """

    username = None  # type: ignore[assignment]
    email = models.EmailField("email address", unique=True)
    # UX-02 — the Account's Self-insert (GLOSSARY.md). A vanity *link* to an
    # existing Player: never moves, enrols or re-simulates it. SET_NULL so deleting
    # the Player (or its Team, which cascades the Player) just leaves no Self-insert.
    self_insert = models.OneToOneField(
        "teams.Player",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="self_insert_of",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()  # type: ignore[assignment]

    def __str__(self) -> str:
        return self.email


def _new_invite_code() -> str:
    return secrets.token_urlsafe(16)


class Invite(models.Model):
    """A code that lets up to ``max_uses`` people register an Account, until
    ``expires_at`` (never, when blank).

    Registration is invite-only: ``RegisterForm`` refuses a missing, unknown,
    used-up or expired code, and an ``email``-locked invite for any other
    address. Staff create invites in Django admin, which shows the sign-up link.
    ``use_count`` only ever grows, so deleting an Account never gives a use back.
    """

    code = models.CharField(
        max_length=64, unique=True, default=_new_invite_code, editable=False
    )
    email = models.EmailField(
        blank=True, help_text="Optional: only this address may use the invite."
    )
    note = models.CharField(max_length=200, blank=True)
    max_uses = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="How many Accounts this invite can create.",
    )
    use_count = models.PositiveIntegerField(default=0, editable=False)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Optional: the link stops working after this."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.email or self.note or self.code

    def clean(self) -> None:
        if self.email and self.max_uses > 1:
            raise ValidationError(
                {
                    "max_uses": "An email-locked invite can only be used once: "
                    "that address can register only one Account."
                }
            )

    @property
    def uses_left(self) -> int:
        return max(self.max_uses - self.use_count, 0)

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_redeemable(self) -> bool:
        return self.uses_left > 0 and not self.is_expired


class InviteRedemption(models.Model):
    """One Account created through an ``Invite``. ``user`` goes NULL when that
    Account is deleted; the row stays, as the record of a use spent."""

    invite = models.ForeignKey(
        Invite, on_delete=models.CASCADE, related_name="redemptions"
    )
    user = models.OneToOneField(
        User,
        null=True,
        on_delete=models.SET_NULL,
        related_name="invite_redemption",
    )
    redeemed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["redeemed_at"]

    def __str__(self) -> str:
        return f"{self.user or 'deleted Account'} via {self.invite}"
