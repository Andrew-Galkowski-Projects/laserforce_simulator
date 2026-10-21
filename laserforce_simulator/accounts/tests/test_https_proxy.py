"""Self-hosting behind a TLS-terminating proxy (Tailscale Funnel, Fly).

The proxy speaks HTTPS to the browser and plain HTTP to gunicorn, and marks the
hop with ``X-Forwarded-Proto: https``. Django must trust that header, or the
CSRF origin check compares the browser's ``https://`` Origin against an
``http://`` request and rejects every form POST, the login included.
"""

from __future__ import annotations

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from conftest import SHARED_MANAGER_EMAIL, get_shared_manager

PROXY_HOST = "laserforce.example.ts.net"
PASSWORD = "proxy-test-password"


@override_settings(ALLOWED_HOSTS=[PROXY_HOST], CSRF_TRUSTED_ORIGINS=[])
class TestLoginBehindHttpsProxy(TestCase):
    def setUp(self) -> None:
        super().setUp()
        user = get_shared_manager()
        user.set_password(PASSWORD)
        user.save()
        self.client = Client(enforce_csrf_checks=True, HTTP_HOST=PROXY_HOST)

    def _post_login(self, **headers: str) -> int:
        self.client.get(reverse("login"), **headers)
        token = self.client.cookies["csrftoken"].value
        response = self.client.post(
            reverse("login"),
            {"username": SHARED_MANAGER_EMAIL, "password": PASSWORD},
            HTTP_ORIGIN=f"https://{PROXY_HOST}",
            HTTP_X_CSRFTOKEN=token,
            **headers,
        )
        return response.status_code

    def test_login_post_via_https_proxy_passes_csrf(self) -> None:
        status = self._post_login(HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(status, 302)

    def test_https_origin_without_proxy_header_is_rejected(self) -> None:
        """Without the header the request really is plain HTTP, so an
        ``https://`` Origin is a cross-origin POST and must still 403."""
        status = self._post_login()
        self.assertEqual(status, 403)
