"""ANG-01g — the Accounts API (spec #199, ticket #200).

``GET /api/accounts/me/``, ``POST /api/accounts/register/``,
``POST /api/accounts/me/password/`` and ``PUT`` / ``DELETE
/api/accounts/me/self-insert/``. Exercised at the HTTP seam through an
``APIClient`` force-logged-in as the shared Account ("A"); "B" is a second
Account whose rows must be invisible.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from teams.models import Player, Team

User = get_user_model()

ME_URL = reverse("api_accounts_me")


class _Base(TestCase):
    def setUp(self) -> None:
        self.user = get_shared_manager()
        self.api = APIClient()
        self.api.force_login(self.user)

    def _managed_player(self, name: str = "Mine", manager: object = None) -> Player:
        team = Team.objects.create(name=f"{name} Team", manager=manager or self.user)
        return Player.objects.create(team=team, name=name)


class MeTests(_Base):
    def test_me_without_self_insert(self) -> None:
        response = self.api.get(ME_URL)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(
            set(data), {"id", "email", "date_joined", "is_staff", "self_insert"}
        )
        self.assertEqual(data["id"], self.user.pk)
        self.assertEqual(data["email"], self.user.email)
        self.assertFalse(data["is_staff"])
        self.assertIsNone(data["self_insert"])

    def test_me_with_self_insert_is_full_player(self) -> None:
        player = self._managed_player()
        self.user.self_insert = player
        self.user.save()
        data = self.api.get(ME_URL).json()["data"]
        self.assertEqual(data["self_insert"]["id"], player.pk)
        self.assertEqual(data["self_insert"]["name"], "Mine")
        self.assertEqual(data["self_insert"]["team"], player.team_id)
        self.assertIn("preferred_roles", data["self_insert"])

    def test_drifted_self_insert_reads_null(self) -> None:
        player = self._managed_player()
        self.user.self_insert = player
        self.user.save()
        Team.objects.filter(pk=player.team_id).update(manager=None)
        data = self.api.get(ME_URL).json()["data"]
        self.assertIsNone(data["self_insert"])


REGISTER_URL = reverse("api_accounts_register")
_GOOD_PASSWORD = "Sturdy-Passw0rd-42"


def _register_body(email: str = "new@example.com", **overrides: str) -> dict:
    body = {"email": email, "password1": _GOOD_PASSWORD, "password2": _GOOD_PASSWORD}
    body.update(overrides)
    return body


class RegisterTests(TestCase):
    def setUp(self) -> None:
        self.api = APIClient()

    def test_register_creates_account_and_does_not_log_in(self) -> None:
        response = self.api.post(REGISTER_URL, _register_body(), format="json")
        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        new_user = User.objects.get(email="new@example.com")
        self.assertEqual(data["id"], new_user.pk)
        self.assertEqual(data["email"], "new@example.com")
        self.assertIsNone(data["self_insert"])
        self.assertTrue(new_user.check_password(_GOOD_PASSWORD))
        self.assertEqual(self.api.get(ME_URL).status_code, 403)

    def test_duplicate_email_is_400_on_email(self) -> None:
        User.objects.create_user("new@example.com", "whatever-pass-1")
        response = self.api.post(REGISTER_URL, _register_body(), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", [e["field"] for e in response.json()["errors"]])

    def test_mismatched_passwords_is_400_on_password2(self) -> None:
        body = _register_body(password2="Different-Passw0rd-42")
        response = self.api.post(REGISTER_URL, body, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("password2", [e["field"] for e in response.json()["errors"]])
        self.assertFalse(User.objects.filter(email="new@example.com").exists())

    def test_weak_password_is_400(self) -> None:
        body = _register_body(password1="123", password2="123")
        response = self.api.post(REGISTER_URL, body, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(email="new@example.com").exists())

    def test_non_object_body_is_400_parse_error(self) -> None:
        response = self.api.post(REGISTER_URL, ["nope"], format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"][0]["code"], "parse_error")

    def test_logged_in_caller_keeps_own_session(self) -> None:
        me = get_shared_manager()
        self.api.force_login(me)
        response = self.api.post(REGISTER_URL, _register_body(), format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.api.get(ME_URL).json()["data"]["id"], me.pk)


PASSWORD_URL = reverse("api_accounts_password")
_OLD_PASSWORD = "Original-Passw0rd-1"
_NEW_PASSWORD = "Brand-New-Passw0rd-2"


class PasswordChangeTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("pw@example.com", _OLD_PASSWORD)
        self.api = APIClient()
        self.api.force_login(self.user)

    def _body(self, old: str = _OLD_PASSWORD, **overrides: str) -> dict:
        body = {
            "old_password": old,
            "new_password1": _NEW_PASSWORD,
            "new_password2": _NEW_PASSWORD,
        }
        body.update(overrides)
        return body

    def test_change_is_204_and_session_survives(self) -> None:
        response = self.api.post(PASSWORD_URL, self._body(), format="json")
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(_NEW_PASSWORD))
        self.assertEqual(self.api.get(ME_URL).status_code, 200)

    def test_wrong_old_password_is_400(self) -> None:
        response = self.api.post(
            PASSWORD_URL, self._body(old="not-the-password"), format="json"
        )
        self.assertEqual(response.status_code, 400)
        fields = [e["field"] for e in response.json()["errors"]]
        self.assertIn("old_password", fields)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(_OLD_PASSWORD))

    def test_mismatched_new_passwords_is_400(self) -> None:
        body = self._body(new_password2="Other-Passw0rd-3")
        response = self.api.post(PASSWORD_URL, body, format="json")
        self.assertEqual(response.status_code, 400)
        fields = [e["field"] for e in response.json()["errors"]]
        self.assertIn("new_password2", fields)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(_OLD_PASSWORD))


SELF_INSERT_URL = reverse("api_accounts_self_insert")
_NOT_ELIGIBLE_MESSAGE = "Only a Player on a Team you manage can be your Self-insert."


class SelfInsertPutTests(_Base):
    def _put(self, body: object) -> Any:
        return self.api.put(SELF_INSERT_URL, body, format="json")

    def _assert_player_field_error(self, response: Any) -> None:
        self.assertEqual(response.status_code, 400)
        errors = response.json()["errors"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["field"], "player")

    def test_link_returns_me_with_self_insert(self) -> None:
        player = self._managed_player()
        response = self._put({"player": player.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["self_insert"]["id"], player.pk)
        self.user.refresh_from_db()
        self.assertEqual(self.user.self_insert_id, player.pk)

    def test_link_replaces_previous(self) -> None:
        first = self._managed_player("First")
        second = self._managed_player("Second")
        self._put({"player": first.pk})
        response = self._put({"player": second.pk})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.self_insert_id, second.pk)

    def test_relink_same_player_is_idempotent(self) -> None:
        player = self._managed_player()
        self._put({"player": player.pk})
        response = self._put({"player": player.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["self_insert"]["id"], player.pk)

    def test_missing_player_is_400_required(self) -> None:
        response = self._put({})
        self._assert_player_field_error(response)
        self.assertEqual(response.json()["errors"][0]["code"], "required")

    def test_non_integer_player_is_400_invalid(self) -> None:
        for bad in ("abc", "5", 1.5, True, None, [1]):
            with self.subTest(bad=bad):
                response = self._put({"player": bad})
                self._assert_player_field_error(response)
                self.assertEqual(response.json()["errors"][0]["code"], "invalid")

    def test_non_object_body_is_400_parse_error(self) -> None:
        response = self._put([1])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["errors"][0]["code"], "parse_error")

    def test_nonexistent_player_is_404(self) -> None:
        response = self._put({"player": 999999})
        self.assertEqual(response.status_code, 404)

    def test_cross_account_player_is_404_and_nothing_written(self) -> None:
        other = User.objects.create_user("b@example.com", "B-Passw0rd-xyz")
        theirs = self._managed_player("Theirs", manager=other)
        response = self._put({"player": theirs.pk})
        self.assertEqual(response.status_code, 404)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.self_insert_id)

    def test_unmanaged_player_is_400_not_eligible(self) -> None:
        team = Team.objects.create(name="Unmanaged Team")
        player = Player.objects.create(team=team, name="Loose")
        response = self._put({"player": player.pk})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["errors"],
            [{"code": "not_eligible", "message": _NOT_ELIGIBLE_MESSAGE, "field": None}],
        )
        self.user.refresh_from_db()
        self.assertIsNone(self.user.self_insert_id)

    def test_link_never_changes_player_team(self) -> None:
        player = self._managed_player()
        team_id = player.team_id
        self._put({"player": player.pk})
        player.refresh_from_db()
        self.assertEqual(player.team_id, team_id)

    def test_linking_takes_over_another_accounts_drifted_link(self) -> None:
        other = User.objects.create_user("b@example.com", "B-Passw0rd-xyz")
        player = self._managed_player()
        other.self_insert = player
        other.save()
        response = self._put({"player": player.pk})
        self.assertEqual(response.status_code, 200)
        other.refresh_from_db()
        self.assertIsNone(other.self_insert_id)
        self.user.refresh_from_db()
        self.assertEqual(self.user.self_insert_id, player.pk)


class SelfInsertDeleteTests(_Base):
    def test_delete_clears_link(self) -> None:
        player = self._managed_player()
        self.user.self_insert = player
        self.user.save()
        response = self.api.delete(SELF_INSERT_URL)
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.self_insert_id)
        self.assertTrue(Player.objects.filter(pk=player.pk).exists())

    def test_delete_without_link_is_204(self) -> None:
        response = self.api.delete(SELF_INSERT_URL)
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.self_insert_id)

    def test_delete_clears_drifted_link(self) -> None:
        player = self._managed_player()
        self.user.self_insert = player
        self.user.save()
        Team.objects.filter(pk=player.team_id).update(manager=None)
        response = self.api.delete(SELF_INSERT_URL)
        self.assertEqual(response.status_code, 204)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.self_insert_id)


class AnonymousTests(TestCase):
    def test_authenticated_endpoints_are_403_not_authenticated(self) -> None:
        api = APIClient()
        calls = [
            ("get", ME_URL),
            ("post", PASSWORD_URL),
            ("put", SELF_INSERT_URL),
            ("delete", SELF_INSERT_URL),
        ]
        for method, url in calls:
            with self.subTest(method=method, url=url):
                response = getattr(api, method)(url, {}, format="json")
                self.assertEqual(response.status_code, 403)
                errors = response.json()["errors"]
                self.assertEqual(errors[0]["code"], "not_authenticated")
