"""ANG-03 (ADR-0041) - JWT ahead of the session on every /api/ request.

``JWTAuthentication`` is the first authentication class, so an anonymous
request is a 401 carrying ``WWW-Authenticate: Bearer`` (the Angular client's
signal to refresh); ``SessionAuthentication`` stays second, so the browsable
API and session clients keep working.
"""

from __future__ import annotations

from django.test import TestCase
from rest_framework.test import APIClient

from conftest import get_shared_manager

TEAMS_URL = "/api/teams/"


class TestAnonymousApiRequest(TestCase):
    def test_anonymous_request_is_401_with_bearer_challenge(self) -> None:
        response = APIClient().get(TEAMS_URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["WWW-Authenticate"], 'Bearer realm="api"')
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertEqual(body["errors"][0]["code"], "not_authenticated")

    def test_garbage_bearer_token_is_401(self) -> None:
        response = APIClient().get(TEAMS_URL, HTTP_AUTHORIZATION="Bearer not-a-jwt")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response["WWW-Authenticate"])
        # simplejwt raises a ``{"detail", "code", "messages"}`` dict; the
        # envelope collapses it to one entry rather than one per dict key.
        errors = response.json()["errors"]
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0]["code"], "token_not_valid")
        self.assertEqual(errors[0]["field"], None)
        self.assertTrue(errors[0]["message"])


class TestSessionStillAccepted(TestCase):
    def test_session_logged_in_client_gets_200(self) -> None:
        api = APIClient()
        api.force_login(get_shared_manager())
        response = api.get(TEAMS_URL)
        self.assertEqual(response.status_code, 200)
