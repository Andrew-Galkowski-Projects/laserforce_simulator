from __future__ import annotations

from typing import Any

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


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

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()  # type: ignore[assignment]

    def __str__(self) -> str:
        return self.email
