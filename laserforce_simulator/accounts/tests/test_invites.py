"""Invite-only registration: an Account can only be created by redeeming an
unused ``Invite`` (single-use, optionally locked to one email).

``RegisterForm`` enforces it, so the HTML ``register`` view and
``POST /api/accounts/register/`` share one rule.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.forms import RegisterForm
from accounts.models import Invite

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

    def test_new_invite_is_unused(self) -> None:
        invite = Invite.objects.create()
        self.assertIsNone(invite.used_by)
        self.assertIsNone(invite.used_at)


class TestRegisterFormInvite(TestCase):
    def test_valid_invite_creates_account_and_consumes_invite(self) -> None:
        invite = Invite.objects.create()
        form = RegisterForm(data=_data(invite.code))
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        invite.refresh_from_db()
        self.assertEqual(invite.used_by, user)
        self.assertIsNotNone(invite.used_at)

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
        self.assertIsNotNone(invite.used_at)


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

    def test_used_invite_shows_no_link(self) -> None:
        admin = User.objects.create_superuser("boss@example.com", PASSWORD)
        invite = Invite.objects.create(used_by=admin)
        self.client.force_login(admin)
        body = self.client.get(reverse("admin:accounts_invite_changelist")).content
        self.assertNotIn(f"?invite={invite.code}", body.decode())

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
            {"email": "friend@example.com", "note": ""},
            follow=True,
        )
        invite = Invite.objects.get(email="friend@example.com")
        self.assertContains(
            response, f"http://testserver/accounts/register/?invite={invite.code}"
        )
