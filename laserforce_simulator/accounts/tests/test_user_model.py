"""UX-01 — tests for the custom **Account** model (`accounts.User`).

Seam contract: `.claude/worktrees/ux-01-seam-contract.md` §1.3 (the model) and
§11.3 (test boundary: `USERNAME_FIELD`, `REQUIRED_FIELDS`, `username is None`,
email uniqueness, `UserManager.create_user` / `create_superuser` behaviour and
their `ValueError`s).

NAMING: an Account becomes a row's **Manager** via the `manager` FK on each
Ownership root. It is never the **Owner** — that is the fictional boss of
ADR-0026, which has no login.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import User, UserManager


class TestUserModelShape(TestCase):
    """§1.3 — the locked field/attribute shape of `accounts.User`."""

    def test_auth_user_model_is_accounts_user(self) -> None:
        self.assertIs(get_user_model(), User)

    def test_username_attribute_is_none(self) -> None:
        """`AbstractUser.username` is dropped entirely."""
        self.assertIsNone(User.username)

    def test_username_is_not_a_concrete_field(self) -> None:
        with self.assertRaises(FieldDoesNotExist):
            User._meta.get_field("username")

    def test_username_field_is_email(self) -> None:
        self.assertEqual(User.USERNAME_FIELD, "email")

    def test_required_fields_is_empty(self) -> None:
        self.assertEqual(User.REQUIRED_FIELDS, [])

    def test_email_is_unique(self) -> None:
        self.assertTrue(User._meta.get_field("email").unique)

    def test_manager_is_the_custom_user_manager(self) -> None:
        self.assertIsInstance(User.objects, UserManager)

    def test_str_is_the_email(self) -> None:
        user = User.objects.create_user(email="str@example.com", password="pw-Str-1234")
        self.assertEqual(str(user), "str@example.com")

    def test_no_extra_profile_fields(self) -> None:
        """The model adds no profile columns beyond `AbstractUser`'s."""
        names = {f.name for f in User._meta.get_fields()}
        for unexpected in ("display_name", "avatar", "bio", "manager", "owner"):
            self.assertNotIn(unexpected, names)


class TestUserManagerCreateUser(TestCase):
    """§1.3 — `UserManager.create_user` happy path and failure mode."""

    def test_create_user_persists_and_hashes_password(self) -> None:
        user = User.objects.create_user(
            email="new@example.com", password="pw-Create-1234"
        )
        self.assertEqual(user.email, "new@example.com")
        self.assertTrue(user.check_password("pw-Create-1234"))
        self.assertNotEqual(user.password, "pw-Create-1234")
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_create_user_defaults_are_not_staff_not_superuser(self) -> None:
        user = User.objects.create_user(
            email="plain@example.com", password="pw-Plain-1234"
        )
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_create_user_normalises_the_email_domain(self) -> None:
        """`BaseUserManager.normalize_email` lowercases the domain part."""
        user = User.objects.create_user(
            email="Mixed@EXAMPLE.COM", password="pw-Mixed-1234"
        )
        self.assertEqual(user.email, "Mixed@example.com")

    def test_create_user_accepts_a_none_password(self) -> None:
        """`password=None` sets an unusable password rather than raising."""
        user = User.objects.create_user(email="nopw@example.com", password=None)
        self.assertFalse(user.has_usable_password())

    def test_create_user_without_email_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            User.objects.create_user(email="", password="pw-Empty-1234")
        self.assertIn("email", str(ctx.exception).lower())

    def test_create_user_with_none_email_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            User.objects.create_user(email=None, password="pw-None-1234")

    def test_duplicate_email_is_rejected(self) -> None:
        User.objects.create_user(email="dup@example.com", password="pw-Dup-1234")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create_user(
                    email="dup@example.com", password="pw-Dup2-1234"
                )


class TestUserManagerCreateSuperuser(TestCase):
    """§1.3 — `UserManager.create_superuser` happy path and both `ValueError`s."""

    def test_create_superuser_sets_both_flags(self) -> None:
        user = User.objects.create_superuser(
            email="root@example.com", password="pw-Root-1234"
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.email, "root@example.com")

    def test_create_superuser_rejects_is_staff_false(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            User.objects.create_superuser(
                email="badstaff@example.com",
                password="pw-Bad-1234",
                is_staff=False,
            )
        self.assertIn("is_staff", str(ctx.exception))

    def test_create_superuser_rejects_is_superuser_false(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            User.objects.create_superuser(
                email="badsuper@example.com",
                password="pw-Bad-1234",
                is_superuser=False,
            )
        self.assertIn("is_superuser", str(ctx.exception))

    def test_create_superuser_without_email_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            User.objects.create_superuser(email="", password="pw-Bad-1234")
