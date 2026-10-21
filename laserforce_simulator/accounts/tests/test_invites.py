"""Invite-only registration: an Account can only be created by redeeming a
redeemable ``Invite``: not expired, with uses left (``max_uses``, default 1),
and, when email-locked, only for that address.

``RegisterForm`` enforces it, so the HTML ``register`` view and
``POST /api/accounts/register/`` share one rule.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.forms import (
    EXPIRED_INVITE_MESSAGE,
    INVALID_INVITE_MESSAGE,
    RegisterForm,
)
from accounts.models import Invite, InviteRedemption

User = get_user_model()

PASSWORD = "Str0ng-Passphrase-9"


def _data(code: str, email: str = "invitee@example.com") -> dict[str, str]:
    return {
        "email": email,
        "password1": PASSWORD,
        "password2": PASSWORD,
        "invite": code,
    }


class TestInviteModel(TestCase):
    def test_new_invites_get_distinct_random_codes(self) -> None:
        first, second = Invite.objects.create(), Invite.objects.create()
        self.assertGreaterEqual(len(first.code), 16)
        self.assertNotEqual(first.code, second.code)

    def test_new_invite_is_single_use_never_expiring_and_redeemable(self) -> None:
        invite = Invite.objects.create()
        self.assertEqual(invite.max_uses, 1)
        self.assertEqual(invite.use_count, 0)
        self.assertIsNone(invite.expires_at)
        self.assertTrue(invite.is_redeemable)

    def test_invite_past_its_expiry_is_not_redeemable(self) -> None:
        invite = Invite.objects.create(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertTrue(invite.is_expired)
        self.assertFalse(invite.is_redeemable)

    def test_invite_with_no_uses_left_is_not_redeemable(self) -> None:
        invite = Invite.objects.create(max_uses=2, use_count=2)
        self.assertEqual(invite.uses_left, 0)
        self.assertFalse(invite.is_redeemable)

    def test_email_locked_invite_cannot_have_several_uses(self) -> None:
        """An email can register only once, so extra uses could never be spent."""
        invite = Invite(email="friend@example.com", max_uses=5)
        with self.assertRaises(ValidationError):
            invite.full_clean()

    def test_max_uses_must_be_at_least_one(self) -> None:
        with self.assertRaises(ValidationError):
            Invite(max_uses=0).full_clean()


class TestRegisterFormInvite(TestCase):
    def test_valid_invite_creates_account_and_consumes_invite(self) -> None:
        invite = Invite.objects.create()
        form = RegisterForm(data=_data(invite.code))
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        invite.refresh_from_db()
        self.assertEqual(invite.use_count, 1)
        redemption = InviteRedemption.objects.get(invite=invite)
        self.assertEqual(redemption.user, user)
        self.assertIsNotNone(redemption.redeemed_at)

    def test_missing_invite_is_an_invite_error(self) -> None:
        data = _data("")
        form = RegisterForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("invite", form.errors)

    def test_unknown_code_is_an_invite_error(self) -> None:
        form = RegisterForm(data=_data("not-a-real-code"))
        self.assertFalse(form.is_valid())
        self.assertIn("invite", form.errors)

    def test_used_invite_cannot_be_redeemed_twice(self) -> None:
        invite = Invite.objects.create()
        RegisterForm(data=_data(invite.code, "first@example.com")).save()
        form = RegisterForm(data=_data(invite.code, "second@example.com"))
        self.assertFalse(form.is_valid())
        self.assertIn("invite", form.errors)

    def test_multi_use_invite_admits_exactly_max_uses_accounts(self) -> None:
        invite = Invite.objects.create(max_uses=3)
        for n in range(3):
            form = RegisterForm(data=_data(invite.code, f"member{n}@example.com"))
            self.assertTrue(form.is_valid(), form.errors)
            form.save()
        form = RegisterForm(data=_data(invite.code, "one-too-many@example.com"))
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["invite"], [INVALID_INVITE_MESSAGE])
        invite.refresh_from_db()
        self.assertEqual(invite.use_count, 3)
        self.assertEqual(
            sorted(invite.redemptions.values_list("user__email", flat=True)),
            ["member0@example.com", "member1@example.com", "member2@example.com"],
        )

    def test_expired_invite_is_refused_with_its_own_message(self) -> None:
        invite = Invite.objects.create(
            max_uses=10, expires_at=timezone.now() - timedelta(seconds=1)
        )
        form = RegisterForm(data=_data(invite.code))
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors["invite"], [EXPIRED_INVITE_MESSAGE])

    def test_invite_expiring_later_is_accepted(self) -> None:
        invite = Invite.objects.create(expires_at=timezone.now() + timedelta(days=1))
        form = RegisterForm(data=_data(invite.code))
        self.assertTrue(form.is_valid(), form.errors)

    def test_last_use_taken_after_validation_rolls_the_account_back(self) -> None:
        """Two people racing for the last use: the loser's form validated, but
        the conditional claim fails and their Account must not survive."""
        invite = Invite.objects.create(max_uses=1)
        form = RegisterForm(data=_data(invite.code, "loser@example.com"))
        self.assertTrue(form.is_valid(), form.errors)
        Invite.objects.filter(pk=invite.pk).update(use_count=1)
        with self.assertRaises(ValidationError):
            form.save()
        self.assertFalse(User.objects.filter(email="loser@example.com").exists())

    def test_deleting_the_account_does_not_give_the_use_back(self) -> None:
        invite = Invite.objects.create()
        RegisterForm(data=_data(invite.code)).save()
        User.objects.get(email="invitee@example.com").delete()
        invite.refresh_from_db()
        self.assertEqual(invite.use_count, 1)
        self.assertFalse(invite.is_redeemable)

    def test_email_locked_invite_rejects_another_email(self) -> None:
        invite = Invite.objects.create(email="friend@example.com")
        form = RegisterForm(data=_data(invite.code, "stranger@example.com"))
        self.assertFalse(form.is_valid())
        self.assertIn("invite", form.errors)

    def test_email_locked_invite_accepts_its_email_case_insensitively(self) -> None:
        invite = Invite.objects.create(email="friend@example.com")
        form = RegisterForm(data=_data(invite.code, "Friend@Example.com"))
        self.assertTrue(form.is_valid(), form.errors)


class TestRegisterViewInvite(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.client.logout()
        self.url = reverse("register")

    def test_invite_query_param_prefills_the_field(self) -> None:
        invite = Invite.objects.create()
        body = self.client.get(f"{self.url}?invite={invite.code}").content.decode()
        self.assertIn(f'value="{invite.code}"', body)

    def test_post_without_invite_creates_nothing(self) -> None:
        response = self.client.post(self.url, _data(""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="invitee@example.com").exists())

    def test_post_with_invite_creates_the_account(self) -> None:
        invite = Invite.objects.create()
        response = self.client.post(self.url, _data(invite.code))
        self.assertRedirects(response, reverse("landing"))
        self.assertTrue(User.objects.filter(email="invitee@example.com").exists())


class TestRegisterApiInvite(TestCase):
    url = reverse("api_accounts_register")

    def test_api_register_without_invite_is_400_on_invite(self) -> None:
        response = APIClient().post(self.url, _data(""), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("invite", [e["field"] for e in response.json()["errors"]])
        self.assertFalse(User.objects.filter(email="invitee@example.com").exists())

    def test_api_register_with_invite_is_201_and_consumes_it(self) -> None:
        invite = Invite.objects.create()
        response = APIClient().post(self.url, _data(invite.code), format="json")
        self.assertEqual(response.status_code, 201)
        invite.refresh_from_db()
        self.assertEqual(invite.use_count, 1)


class TestInviteAdmin(TestCase):
    def test_changelist_shows_an_absolute_signup_link(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        invite = Invite.objects.create()
        self.client.force_login(admin)
        body = self.client.get(reverse("admin:accounts_invite_changelist")).content
        self.assertIn(
            f"http://testserver/accounts/register/?invite={invite.code}",
            body.decode(),
        )

    def test_used_up_invite_shows_no_link(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        invite = Invite.objects.create(use_count=1)
        self.client.force_login(admin)
        body = self.client.get(reverse("admin:accounts_invite_changelist")).content
        self.assertNotIn(f"?invite={invite.code}", body.decode())

    def test_expired_invite_shows_no_link(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        invite = Invite.objects.create(expires_at=timezone.now() - timedelta(days=1))
        self.client.force_login(admin)
        body = self.client.get(reverse("admin:accounts_invite_changelist")).content
        self.assertNotIn(f"?invite={invite.code}", body.decode())

    def test_changelist_shows_uses_out_of_max(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        Invite.objects.create(max_uses=10, use_count=3)
        self.client.force_login(admin)
        response = self.client.get(reverse("admin:accounts_invite_changelist"))
        self.assertContains(response, "3 / 10")

    def test_change_page_lists_who_redeemed_it(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        invite = Invite.objects.create(max_uses=5)
        RegisterForm(data=_data(invite.code, "joined@example.com")).save()
        self.client.force_login(admin)
        url = reverse("admin:accounts_invite_change", args=[invite.pk])
        self.assertContains(self.client.get(url), "joined@example.com")

    def test_adding_a_shared_expiring_invite(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin:accounts_invite_add"),
            {
                "email": "",
                "note": "league night",
                "max_uses": "10",
                "expires_at_0": "2099-01-31",
                "expires_at_1": "18:00:00",
            },
            follow=True,
        )
        invite = Invite.objects.get(note="league night")
        self.assertEqual(invite.max_uses, 10)
        self.assertEqual(invite.expires_at.year, 2099)
        self.assertContains(response, f"?invite={invite.code}")

    def test_admin_refuses_email_lock_with_several_uses(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        self.client.force_login(admin)
        self.client.post(
            reverse("admin:accounts_invite_add"),
            {"email": "friend@example.com", "note": "", "max_uses": "3"},
        )
        self.assertFalse(Invite.objects.filter(email="friend@example.com").exists())

    def test_change_page_renders(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        invite = Invite.objects.create(note="for a friend")
        self.client.force_login(admin)
        url = reverse("admin:accounts_invite_change", args=[invite.pk])
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_adding_an_invite_shows_its_signup_link(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        self.client.force_login(admin)
        response = self.client.post(
            reverse("admin:accounts_invite_add"),
            {"email": "friend@example.com", "note": "", "max_uses": "1"},
            follow=True,
        )
        invite = Invite.objects.get(email="friend@example.com")
        self.assertContains(
            response, f"http://testserver/accounts/register/?invite={invite.code}"
        )
