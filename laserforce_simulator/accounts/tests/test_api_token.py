"""ANG-03 (spec #207, ticket #208, ADR-0041) - the JWT token endpoints.

``POST /api/token/`` (login), ``POST /api/token/refresh/`` and
``POST /api/token/logout/``. Exercised at the HTTP seam through an anonymous
``APIClient``: the access token comes back in the body, the refresh token
only in the httpOnly ``refresh_token`` cookie.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()

OBTAIN_URL = reverse("api_token_obtain")
REFRESH_URL = reverse("api_token_refresh")
LOGOUT_URL = reverse("api_token_logout")
ME_URL = reverse("api_accounts_me")

COOKIE = "refresh_token"
EMAIL = "jwt@example.com"
PASSWORD = "Sturdy-Passw0rd-42"
SEVEN_DAYS = 7 * 24 * 60 * 60


class _Base(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(email=EMAIL, password=PASSWORD)
        self.api = APIClient()

    def _login(self, **body: Any) -> Any:
        payload = {"email": EMAIL, "password": PASSWORD}
        payload.update(body)
        return self.api.post(OBTAIN_URL, payload, format="json")

    def _me_with(self, access: str) -> Any:
        return APIClient().get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {access}")

    def _refresh_with(self, value: str | None) -> Any:
        client = APIClient()
        if value is not None:
            client.cookies[COOKIE] = value
        return client.post(REFRESH_URL)

    def _assert_error(self, response: Any, status: int) -> None:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertTrue(body["errors"])
        self.assertNotIn("data", body)


class LoginTests(_Base):
    def test_login_returns_only_access_in_body(self) -> None:
        response = self._login()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body["data"]), {"access"})
        self.assertNotIn("refresh", response.content.decode())

    def test_access_token_authenticates_the_api(self) -> None:
        access = self._login().json()["data"]["access"]
        response = self._me_with(access)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["email"], EMAIL)

    def test_refresh_cookie_attributes(self) -> None:
        cookie = self._login().cookies[COOKIE]
        self.assertTrue(cookie.value)
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/api/token/")
        self.assertEqual(int(cookie["max-age"]), SEVEN_DAYS)

    @override_settings(DEBUG=False)
    def test_cookie_is_secure_outside_debug(self) -> None:
        self.assertTrue(self._login().cookies[COOKIE]["secure"])

    @override_settings(DEBUG=True)
    def test_cookie_is_not_secure_in_debug(self) -> None:
        self.assertFalse(self._login().cookies[COOKIE]["secure"])

    def test_login_updates_last_login(self) -> None:
        self.assertIsNone(self.user.last_login)
        self._login()
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.last_login)

    def test_login_creates_no_session(self) -> None:
        # The autouse fixture already logged ``self.client`` in, so compare.
        sessions_before = Session.objects.count()
        response = self._login()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sessionid", response.cookies)
        self.assertEqual(Session.objects.count(), sessions_before)

    def test_wrong_password_is_401(self) -> None:
        self._assert_error(self._login(password="wrong"), 401)

    def test_inactive_account_is_401(self) -> None:
        self.user.is_active = False
        self.user.save()
        self._assert_error(self._login(), 401)

    def test_missing_fields_are_400(self) -> None:
        response = self.api.post(OBTAIN_URL, {}, format="json")
        self._assert_error(response, 400)
        fields = {error["field"] for error in response.json()["errors"]}
        self.assertEqual(fields, {"email", "password"})

    def test_access_token_lives_fifteen_minutes(self) -> None:
        access = AccessToken(self._login().json()["data"]["access"])
        self.assertEqual(access["exp"] - access["iat"], 15 * 60)

    def test_login_needs_no_csrf_token(self) -> None:
        # A session-logged-in browser: SessionAuthentication would demand CSRF.
        api = APIClient(enforce_csrf_checks=True)
        api.force_login(self.user)
        response = api.post(
            OBTAIN_URL, {"email": EMAIL, "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)


class RefreshTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.old = self._login().cookies[COOKIE].value

    def test_refresh_returns_new_access_and_rotated_cookie(self) -> None:
        response = self._refresh_with(self.old)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()["data"]), {"access"})
        self.assertNotIn("refresh", response.content.decode())
        cookie = response.cookies[COOKIE]
        self.assertNotEqual(cookie.value, self.old)
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], "/api/token/")
        self.assertEqual(int(cookie["max-age"]), SEVEN_DAYS)
        access = response.json()["data"]["access"]
        self.assertEqual(self._me_with(access).status_code, 200)

    def test_new_cookie_refreshes_again(self) -> None:
        new = self._refresh_with(self.old).cookies[COOKIE].value
        self.assertEqual(self._refresh_with(new).status_code, 200)

    def test_reused_cookie_is_401(self) -> None:
        self.assertEqual(self._refresh_with(self.old).status_code, 200)
        self._assert_error(self._refresh_with(self.old), 401)

    def test_no_cookie_is_401(self) -> None:
        self._assert_error(self._refresh_with(None), 401)

    def test_garbage_cookie_is_401(self) -> None:
        self._assert_error(self._refresh_with("not-a-jwt"), 401)

    def test_refresh_in_body_is_ignored(self) -> None:
        response = APIClient().post(REFRESH_URL, {"refresh": self.old}, format="json")
        self._assert_error(response, 401)

    def test_refresh_needs_no_csrf_token(self) -> None:
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.cookies[COOKIE] = self.old
        self.assertEqual(client.post(REFRESH_URL).status_code, 200)


class LogoutTests(_Base):
    def test_logout_blacklists_and_deletes_cookie(self) -> None:
        old = self._login().cookies[COOKIE].value
        client = APIClient()
        client.cookies[COOKIE] = old
        response = client.post(LOGOUT_URL)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        cookie = response.cookies[COOKIE]
        self.assertEqual(cookie.value, "")
        self.assertEqual(cookie["path"], "/api/token/")
        self.assertEqual(int(cookie["max-age"]), 0)
        self._assert_error(self._refresh_with(old), 401)

    def test_logout_without_cookie_is_204(self) -> None:
        response = APIClient().post(LOGOUT_URL)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.cookies[COOKIE].value, "")

    def test_logout_needs_no_csrf_token(self) -> None:
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(LOGOUT_URL).status_code, 204)

    def test_logout_with_garbage_cookie_is_204(self) -> None:
        client = APIClient()
        client.cookies[COOKIE] = "not-a-jwt"
        self.assertEqual(client.post(LOGOUT_URL).status_code, 204)
