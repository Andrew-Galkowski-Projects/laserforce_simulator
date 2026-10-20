"""ANG-02 - the Angular dev origin may call /api/ cross-origin.

Driven over real HTTP with an ``Origin`` header; asserts on response headers
and status only. ``override_settings`` pins the origin lists so the tests do
not depend on ``.env`` or ``DEBUG``.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from teams.models import Team

DEV_ORIGIN = "http://localhost:4200"
OTHER_ORIGIN = "http://evil.example"

dev_origin_only = override_settings(
    CORS_ALLOWED_ORIGINS=[DEV_ORIGIN], CSRF_TRUSTED_ORIGINS=[DEV_ORIGIN]
)


@dev_origin_only
class TestCorsOnApi(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.api = APIClient()
        self.api.force_login(get_shared_manager())

    def test_allowed_origin_gets_allow_origin_and_credentials(self) -> None:
        response = self.api.get("/api/teams/", HTTP_ORIGIN=DEV_ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], DEV_ORIGIN)
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")

    def test_disallowed_origin_gets_no_allow_origin(self) -> None:
        response = self.api.get("/api/teams/", HTTP_ORIGIN=OTHER_ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_append_slash_redirect_carries_cors_headers(self) -> None:
        """CommonMiddleware swaps the 404 for a redirect in its response
        phase; CorsMiddleware must sit above it or the redirect loses the
        headers and the browser blocks it."""
        response = self.api.get("/api/teams", HTTP_ORIGIN=DEV_ORIGIN)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/api/teams/")
        self.assertEqual(response["Access-Control-Allow-Origin"], DEV_ORIGIN)

    def test_non_api_path_gets_no_cors_headers(self) -> None:
        response = Client().get(reverse("login"), HTTP_ORIGIN=DEV_ORIGIN)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)
        self.assertNotIn("Access-Control-Allow-Credentials", response)


@dev_origin_only
class TestAnonymousPreflight(TestCase):
    """The browser's preflight carries no cookie; it must not hit the login
    redirect (LoginRequiredMiddleware) or DRF's 403."""

    def test_anonymous_preflight_is_200_with_cors_headers(self) -> None:
        response = APIClient().options(
            "/api/teams/",
            HTTP_ORIGIN=DEV_ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-csrftoken",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], DEV_ORIGIN)
        self.assertEqual(response["Access-Control-Allow-Credentials"], "true")
        self.assertIn("POST", response["Access-Control-Allow-Methods"])
        self.assertIn("x-csrftoken", response["Access-Control-Allow-Headers"])


@dev_origin_only
class TestCrossOriginWriteCsrf(TestCase):
    """A logged-in, CSRF-enforcing client writing from the dev origin with a
    valid token is accepted; the same write from an untrusted origin is a
    CSRF 403."""

    TOKEN = "a" * 32

    def setUp(self) -> None:
        super().setUp()
        self.api = APIClient(enforce_csrf_checks=True)
        self.api.force_login(get_shared_manager())
        self.api.cookies[settings.CSRF_COOKIE_NAME] = self.TOKEN

    def _post(self, origin: str, name: str) -> Any:
        return self.api.post(
            "/api/teams/",
            {"name": name},
            format="json",
            HTTP_ORIGIN=origin,
            HTTP_REFERER=f"{origin}/teams",
            HTTP_X_CSRFTOKEN=self.TOKEN,
        )

    def test_trusted_origin_write_is_not_rejected_by_csrf(self) -> None:
        response = self._post(DEV_ORIGIN, "CorsWrite")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response["Access-Control-Allow-Origin"], DEV_ORIGIN)
        self.assertTrue(Team.objects.filter(name="CorsWrite").exists())

    def test_untrusted_origin_write_is_csrf_403(self) -> None:
        response = self._post(OTHER_ORIGIN, "EvilWrite")
        self.assertEqual(response.status_code, 403)
        error = response.json()["errors"][0]
        self.assertEqual(error["code"], "permission_denied")
        self.assertIn("CSRF Failed", error["message"])
        self.assertIn("Origin checking failed", error["message"])
        self.assertFalse(Team.objects.filter(name="EvilWrite").exists())
