"""DESK-01: the Launch code sign-in (spec #486).

The desktop URLconf is switched in with ``override_settings``; the launch-code
view is anonymous, so every request here goes through a fresh ``APIClient``
that the autouse login fixture never touched.
"""

from __future__ import annotations

import threading
from typing import Any

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from accounts.token_api_views import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH
from desktop_app.launch_code import mint_launch_code, spend_launch_code

LOGIN_PATH = "/api/accounts/desktop-login/"
SPENT_BODY = "This launch link has been used. Restart the app."


class SpendTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("local@desktop.invalid")

    def test_a_code_is_spent_once(self) -> None:
        code = mint_launch_code(self.user)
        self.assertEqual(spend_launch_code(code), self.user.pk)
        self.assertIsNone(spend_launch_code(code))

    def test_unknown_and_empty_codes_spend_nothing(self) -> None:
        mint_launch_code(self.user)
        self.assertIsNone(spend_launch_code("not-a-code"))
        self.assertIsNone(spend_launch_code(""))

    def test_each_mint_is_a_new_code(self) -> None:
        self.assertNotEqual(mint_launch_code(self.user), mint_launch_code(self.user))

    def test_two_threads_spending_one_code_have_one_winner(self) -> None:
        code = mint_launch_code(self.user)
        results: list[int | None] = []
        start = threading.Barrier(8)

        def spend() -> None:
            start.wait()
            results.append(spend_launch_code(code))

        threads = [threading.Thread(target=spend) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count(self.user.pk), 1)
        self.assertEqual(results.count(None), 7)


@override_settings(
    ROOT_URLCONF="desktop_app.urls", DEBUG=False, REFRESH_COOKIE_SECURE=False
)
class DesktopLoginViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("local@desktop.invalid")

    def _get(self, query: str = "", addr: str = "127.0.0.1") -> Any:
        return APIClient().get(f"{LOGIN_PATH}{query}", REMOTE_ADDR=addr)

    def _assert_refused(self, response: Any) -> None:
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content.decode(), SPENT_BODY)
        self.assertNotIn(REFRESH_COOKIE_NAME, response.cookies)

    def test_a_valid_code_sets_the_refresh_cookie_and_redirects_home(self) -> None:
        response = self._get(f"?code={mint_launch_code(self.user)}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")
        cookie = response.cookies[REFRESH_COOKIE_NAME]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertEqual(cookie["path"], REFRESH_COOKIE_PATH)
        self.assertFalse(cookie["secure"])

    def test_the_cookie_refreshes_into_an_access_token_for_the_account(self) -> None:
        cookie = self._get(f"?code={mint_launch_code(self.user)}").cookies[
            REFRESH_COOKIE_NAME
        ]
        client = APIClient()
        client.cookies[REFRESH_COOKIE_NAME] = cookie.value
        refreshed = client.post("/api/token/refresh/")
        self.assertEqual(refreshed.status_code, 200, refreshed.content)
        access = refreshed.json()["data"]["access"]
        me = APIClient().get("/api/accounts/me/", HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(me.json()["data"]["email"], "local@desktop.invalid")

    def test_a_reused_code_is_refused(self) -> None:
        code = mint_launch_code(self.user)
        self.assertEqual(self._get(f"?code={code}").status_code, 302)
        self._assert_refused(self._get(f"?code={code}"))

    def test_a_missing_code_is_refused(self) -> None:
        self._assert_refused(self._get())

    def test_an_empty_code_is_refused(self) -> None:
        self._assert_refused(self._get("?code="))

    def test_a_wrong_code_is_refused(self) -> None:
        mint_launch_code(self.user)
        self._assert_refused(self._get("?code=wrong"))

    def test_a_remote_address_is_refused_without_spending_the_code(self) -> None:
        code = mint_launch_code(self.user)
        self._assert_refused(self._get(f"?code={code}", addr="10.0.0.5"))
        self.assertEqual(self._get(f"?code={code}").status_code, 302)

    def test_ipv6_loopback_is_accepted(self) -> None:
        response = self._get(f"?code={mint_launch_code(self.user)}", addr="::1")
        self.assertEqual(response.status_code, 302)

    def test_a_next_parameter_never_moves_the_redirect(self) -> None:
        for target in ("//evil.com", "https://evil.com/", "/teams/"):
            with self.subTest(target=target):
                code = mint_launch_code(self.user)
                response = self._get(f"?code={code}&next={target}")
                self.assertEqual(response["Location"], "/")

    def test_post_is_not_allowed(self) -> None:
        code = mint_launch_code(self.user)
        response = APIClient().post(
            f"{LOGIN_PATH}?code={code}", REMOTE_ADDR="127.0.0.1"
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(self._get(f"?code={code}").status_code, 302)


class HostedURLconfTests(TestCase):
    def test_the_hosted_deploy_has_no_launch_code_route(self) -> None:
        code = mint_launch_code(User.objects.create_user("x@example.com"))
        response = APIClient().get(f"{LOGIN_PATH}?code={code}")
        self.assertEqual(response.status_code, 404)
