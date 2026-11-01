"""UX-01 — the API side of the global login gate.

The register / login / logout / password-change template views and the top-nav
auth control went with the template UI (ANG-08 D1, ADR-0047). What stays is
the guarantee that an anonymous API call is a 401 JSON envelope, never the
login gate's 302. The gate's own redirect (now to the Admin login) is pinned in
``core/tests.py`` ``ServeMediaTests``.

The project's autouse `force_login_shared_manager` fixture logs every
DB-backed test in, so anywhere anonymity is the point this module calls
`self.client.logout()` explicitly.
"""

from __future__ import annotations

from django.test import TestCase

from conftest import get_shared_manager


class TestApiIsUnauthorisedNotRedirected(TestCase):
    """§4.2 / §8.1 — an anonymous API call gets **401 JSON** (ADR-0041), never a 302."""

    API_PATHS: tuple[str, ...] = (
        "/api/teams/",
        "/api/players/",
        "/api/matches/",
        "/api/rounds/",
        "/api/maps/",
    )

    def setUp(self) -> None:
        super().setUp()
        self.client.logout()

    def test_anonymous_api_get_returns_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        for path in self.API_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 401)

    def test_anonymous_api_response_is_not_a_redirect(self) -> None:
        for path in self.API_PATHS:
            with self.subTest(path=path):
                self.assertNotEqual(self.client.get(path).status_code, 302)

    def test_anonymous_api_response_is_json(self) -> None:
        response = self.client.get("/api/matches/")
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")

    def test_anonymous_api_body_is_not_authenticated_envelope(self) -> None:
        """ANG-01a — the 401 body is the error envelope with code
        ``not_authenticated`` (ADR-0041: JWTAuthentication first, so 401)."""
        for path in self.API_PATHS:
            with self.subTest(path=path):
                body = self.client.get(path).json()
                self.assertEqual(body["errors"][0]["code"], "not_authenticated")
                self.assertIsNone(body["errors"][0]["field"])
                self.assertEqual(body["meta"], {})

    def test_authenticated_api_get_returns_200(self) -> None:
        self.client.force_login(get_shared_manager())
        for path in self.API_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
