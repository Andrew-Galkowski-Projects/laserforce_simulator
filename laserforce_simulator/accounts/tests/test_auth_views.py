"""UX-01 — the auth surfaces: register / login / logout / password change,
the global login gate, and the top-nav auth control.

Seam contract: `.claude/worktrees/ux-01-seam-contract.md` §4 (settings + the
`@login_not_required` exemption list), §9 (URLs, forms, views, templates and
the locked DOM ids) and §11.3 (test boundary).

Two non-obvious, locked details this module pins:

* the login POST key is **`username`**, carrying an email value (§9.2);
* sign-out is a **POST form**, not a link — Django 5.x `LogoutView` rejects GET
  (§9.6).

The project's autouse `force_login_shared_manager` fixture logs every
DB-backed test in, so anywhere anonymity is the point this module calls
`self.client.logout()` explicitly.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from conftest import get_shared_manager

User = get_user_model()

# §9.5 — locked DOM ids, per template.
LOGIN_IDS: tuple[str, ...] = (
    "login-form",
    "login-email",
    "login-password",
    "login-submit",
    "login-register-link",
)
REGISTER_IDS: tuple[str, ...] = (
    "register-form",
    "register-email",
    "register-password1",
    "register-password2",
    "register-submit",
    "register-login-link",
)
PASSWORD_CHANGE_IDS: tuple[str, ...] = (
    "password-change-form",
    "password-change-old-password",
    "password-change-new-password1",
    "password-change-new-password2",
    "password-change-submit",
)
PASSWORD_CHANGE_DONE_IDS: tuple[str, ...] = (
    "password-change-done-notice",
    "password-change-done-home-link",
)

# §9.6 — the top-nav auth control.
TOPNAV_AUTHENTICATED_IDS: tuple[str, ...] = (
    "account-nav-link",
    "account-signed-in-as",
    "account-password-change-link",
    "account-sign-out-form",
    "account-sign-out-button",
)
TOPNAV_ANONYMOUS_IDS: tuple[str, ...] = (
    "account-sign-in-link",
    "account-register-link",
)


# ---------------------------------------------------------------------------
# URL wiring (§9.1)
# ---------------------------------------------------------------------------


class TestAuthUrlWiring(TestCase):
    """§9.1 — five flat URL names at the locked paths, and no password reset."""

    def test_login_path(self) -> None:
        self.assertEqual(reverse("login"), "/accounts/login/")

    def test_logout_path(self) -> None:
        self.assertEqual(reverse("logout"), "/accounts/logout/")

    def test_register_path(self) -> None:
        self.assertEqual(reverse("register"), "/accounts/register/")

    def test_password_change_path(self) -> None:
        self.assertEqual(reverse("password_change"), "/accounts/password-change/")

    def test_password_change_done_path(self) -> None:
        self.assertEqual(
            reverse("password_change_done"), "/accounts/password-change/done/"
        )

    def test_no_password_reset_routes(self) -> None:
        """§9.1 / §14 — password reset is deferred with OAuth."""
        from django.urls import NoReverseMatch

        for name in (
            "password_reset",
            "password_reset_done",
            "password_reset_confirm",
            "password_reset_complete",
        ):
            with self.subTest(name=name):
                with self.assertRaises(NoReverseMatch):
                    reverse(name)


# ---------------------------------------------------------------------------
# Register (§9.3)
# ---------------------------------------------------------------------------


class TestRegisterView(TestCase):
    """§9.3 — open self-registration: happy path plus three invalid inputs."""

    def setUp(self) -> None:
        super().setUp()
        self.client.logout()
        self.url = reverse("register")

    def test_get_returns_200(self) -> None:
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_get_uses_the_locked_template(self) -> None:
        self.assertTemplateUsed(self.client.get(self.url), "accounts/register.html")

    def test_get_renders_every_locked_dom_id(self) -> None:
        body = self.client.get(self.url).content.decode()
        for dom_id in REGISTER_IDS:
            with self.subTest(dom_id=dom_id):
                self.assertIn(f'id="{dom_id}"', body)

    def test_post_valid_creates_the_account(self) -> None:
        response = self.client.post(
            self.url,
            {
                "email": "brand-new@example.com",
                "password1": "Str0ng-Passphrase-9",
                "password2": "Str0ng-Passphrase-9",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.filter(email="brand-new@example.com").exists())

    def test_post_valid_redirects_to_the_landing_page(self) -> None:
        response = self.client.post(
            self.url,
            {
                "email": "redirect@example.com",
                "password1": "Str0ng-Passphrase-9",
                "password2": "Str0ng-Passphrase-9",
            },
        )
        self.assertRedirects(response, reverse("landing"))

    def test_post_valid_logs_the_new_account_in(self) -> None:
        self.client.post(
            self.url,
            {
                "email": "auto-login@example.com",
                "password1": "Str0ng-Passphrase-9",
                "password2": "Str0ng-Passphrase-9",
            },
        )
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["user"].is_authenticated)
        self.assertEqual(response.context["user"].email, "auto-login@example.com")

    def test_post_duplicate_email_rerenders_with_status_200(self) -> None:
        User.objects.create_user(
            email="taken@example.com", password="Str0ng-Passphrase-9"
        )
        response = self.client.post(
            self.url,
            {
                "email": "taken@example.com",
                "password1": "Another-Str0ng-9",
                "password2": "Another-Str0ng-9",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(email="taken@example.com").count(), 1)
        self.assertTrue(response.context["form"].errors)

    def test_post_password_mismatch_rerenders_and_creates_nothing(self) -> None:
        response = self.client.post(
            self.url,
            {
                "email": "mismatch@example.com",
                "password1": "Str0ng-Passphrase-9",
                "password2": "Different-Passphrase-9",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="mismatch@example.com").exists())
        self.assertIn("password2", response.context["form"].errors)

    def test_post_weak_password_rerenders_and_creates_nothing(self) -> None:
        """`UserCreationForm` runs `AUTH_PASSWORD_VALIDATORS`."""
        response = self.client.post(
            self.url,
            {
                "email": "weak@example.com",
                "password1": "password",
                "password2": "password",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="weak@example.com").exists())
        self.assertTrue(response.context["form"].errors)

    def test_post_missing_email_rerenders_and_creates_nothing(self) -> None:
        before = User.objects.count()
        response = self.client.post(
            self.url,
            {
                "email": "",
                "password1": "Str0ng-Passphrase-9",
                "password2": "Str0ng-Passphrase-9",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), before)

    def test_register_is_reachable_while_anonymous(self) -> None:
        """§4.2 — `register` is `@login_not_required`."""
        self.assertEqual(self.client.get(self.url).status_code, 200)


# ---------------------------------------------------------------------------
# Login (§9.2, §9.3)
# ---------------------------------------------------------------------------


class TestLoginView(TestCase):
    """§9.2 — the POST key is `username`, carrying an email value."""

    def setUp(self) -> None:
        super().setUp()
        self.client.logout()
        self.url = reverse("login")
        self.password = "Str0ng-Passphrase-9"
        self.user = User.objects.create_user(
            email="signin@example.com", password=self.password
        )

    def test_get_returns_200_while_anonymous(self) -> None:
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_get_uses_the_locked_template(self) -> None:
        self.assertTemplateUsed(self.client.get(self.url), "accounts/login.html")

    def test_get_renders_every_locked_dom_id(self) -> None:
        body = self.client.get(self.url).content.decode()
        for dom_id in LOGIN_IDS:
            with self.subTest(dom_id=dom_id):
                self.assertIn(f'id="{dom_id}"', body)

    def test_post_valid_credentials_redirects_to_landing(self) -> None:
        response = self.client.post(
            self.url, {"username": "signin@example.com", "password": self.password}
        )
        self.assertRedirects(response, reverse("landing"))

    def test_post_valid_credentials_authenticates_the_session(self) -> None:
        self.client.post(
            self.url, {"username": "signin@example.com", "password": self.password}
        )
        response = self.client.get(reverse("landing"))
        self.assertTrue(response.context["user"].is_authenticated)
        self.assertEqual(response.context["user"].email, "signin@example.com")

    def test_post_wrong_password_rerenders_with_status_200(self) -> None:
        response = self.client.post(
            self.url, {"username": "signin@example.com", "password": "wrong-Pass-9"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)

    def test_post_unknown_email_rerenders_with_status_200(self) -> None:
        response = self.client.post(
            self.url, {"username": "nobody@example.com", "password": self.password}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)

    def test_failed_login_renders_the_error_container(self) -> None:
        response = self.client.post(
            self.url, {"username": "signin@example.com", "password": "wrong-Pass-9"}
        )
        self.assertIn('id="login-errors"', response.content.decode())

    def test_post_empty_credentials_rerenders_with_status_200(self) -> None:
        response = self.client.post(self.url, {"username": "", "password": ""})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)

    def test_email_key_is_not_accepted_in_place_of_username(self) -> None:
        """Locked: Django keeps the field NAME `username` (§9.2)."""
        response = self.client.post(
            self.url, {"email": "signin@example.com", "password": self.password}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)

    def test_authenticated_user_is_redirected_away(self) -> None:
        """`redirect_authenticated_user=True` (§9.3)."""
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)


# ---------------------------------------------------------------------------
# Logout (§9.3, §9.6)
# ---------------------------------------------------------------------------


class TestLogoutView(TestCase):
    """§9.6 — sign-out is POST-only in Django 5.x."""

    def test_post_logs_out_and_redirects_to_login(self) -> None:
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))

    def test_post_clears_the_session(self) -> None:
        self.client.post(reverse("logout"))
        response = self.client.get(reverse("landing"))
        # Anonymous again -> the login gate bounces us.
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_get_is_rejected(self) -> None:
        """A `<a href>` sign-out would break; the nav control is a form."""
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)


# ---------------------------------------------------------------------------
# Password change (§9.3)
# ---------------------------------------------------------------------------


class TestPasswordChangeView(TestCase):
    """§9.3 — password change plus its done page."""

    def setUp(self) -> None:
        super().setUp()
        self.password = "Str0ng-Passphrase-9"
        self.user = User.objects.create_user(
            email="changer@example.com", password=self.password
        )
        self.client.force_login(self.user)
        self.url = reverse("password_change")

    def test_get_returns_200(self) -> None:
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_get_uses_the_locked_template(self) -> None:
        self.assertTemplateUsed(
            self.client.get(self.url), "accounts/password_change.html"
        )

    def test_get_renders_every_locked_dom_id(self) -> None:
        body = self.client.get(self.url).content.decode()
        for dom_id in PASSWORD_CHANGE_IDS:
            with self.subTest(dom_id=dom_id):
                self.assertIn(f'id="{dom_id}"', body)

    def test_post_valid_redirects_to_done(self) -> None:
        response = self.client.post(
            self.url,
            {
                "old_password": self.password,
                "new_password1": "Rotated-Passphrase-7",
                "new_password2": "Rotated-Passphrase-7",
            },
        )
        self.assertRedirects(response, reverse("password_change_done"))

    def test_post_valid_actually_changes_the_password(self) -> None:
        self.client.post(
            self.url,
            {
                "old_password": self.password,
                "new_password1": "Rotated-Passphrase-7",
                "new_password2": "Rotated-Passphrase-7",
            },
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Rotated-Passphrase-7"))

    def test_post_wrong_old_password_rerenders_with_status_200(self) -> None:
        response = self.client.post(
            self.url,
            {
                "old_password": "not-the-password",
                "new_password1": "Rotated-Passphrase-7",
                "new_password2": "Rotated-Passphrase-7",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("old_password", response.context["form"].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_post_mismatched_new_passwords_rerenders(self) -> None:
        response = self.client.post(
            self.url,
            {
                "old_password": self.password,
                "new_password1": "Rotated-Passphrase-7",
                "new_password2": "Different-Passphrase-7",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("new_password2", response.context["form"].errors)

    def test_post_weak_new_password_rerenders(self) -> None:
        response = self.client.post(
            self.url,
            {
                "old_password": self.password,
                "new_password1": "password",
                "new_password2": "password",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)


class TestPasswordChangeDoneView(TestCase):
    """§9.5 — the done page's two locked ids."""

    def test_get_returns_200(self) -> None:
        self.assertEqual(
            self.client.get(reverse("password_change_done")).status_code, 200
        )

    def test_get_uses_the_locked_template(self) -> None:
        self.assertTemplateUsed(
            self.client.get(reverse("password_change_done")),
            "accounts/password_change_done.html",
        )

    def test_get_renders_every_locked_dom_id(self) -> None:
        body = self.client.get(reverse("password_change_done")).content.decode()
        for dom_id in PASSWORD_CHANGE_DONE_IDS:
            with self.subTest(dom_id=dom_id):
                self.assertIn(f'id="{dom_id}"', body)


# ---------------------------------------------------------------------------
# The global login gate (§4)
# ---------------------------------------------------------------------------


class TestLoginGate(TestCase):
    """§4 — `LoginRequiredMiddleware` bounces anonymous HTML requests."""

    #: One representative gated URL per app, all with no view args.
    GATED_URL_NAMES: tuple[str, ...] = (
        "landing",
        "team_list",
        "player_list",
        "match_list",
        "league_list",
        "tournament_list",
        "map_list",
    )

    def setUp(self) -> None:
        super().setUp()
        self.client.logout()

    def test_anonymous_access_redirects_to_login_url(self) -> None:
        for name in self.GATED_URL_NAMES:
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(
                    response.url.startswith(reverse("login")),
                    f"{name} redirected to {response.url!r}, not the login page",
                )

    def test_redirect_carries_the_next_parameter(self) -> None:
        response = self.client.get(reverse("team_list"))
        self.assertIn("next=", response.url)

    def test_authenticated_access_is_allowed(self) -> None:
        self.client.force_login(get_shared_manager())
        for name in self.GATED_URL_NAMES:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_maps_routes_are_gated_but_unfiltered(self) -> None:
        """§2.4 — `/maps/` stays authenticated; ownership does not apply."""
        response = self.client.get(reverse("map_list"))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(get_shared_manager())
        self.assertEqual(self.client.get(reverse("map_list")).status_code, 200)

    def test_exempt_auth_surfaces_are_not_gated(self) -> None:
        """§4.2 — the exemption list, checked end to end."""
        for name in ("login", "register"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_password_change_is_still_login_protected_by_django(self) -> None:
        """§4.2 — the `@login_not_required` exemption lifts the *middleware*
        only. Django's own `PasswordChangeView` / `PasswordChangeDoneView`
        carry `LoginRequiredMixin`, so an anonymous visitor is still bounced
        to `LOGIN_URL` — which is the correct behaviour, and the reason the
        exemption cannot make these two pages public.
        """
        for name in ("password_change", "password_change_done"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(reverse("login")))


class TestApiIsUnauthorisedNotRedirected(TestCase):
    """§4.2 / §8.1 — an anonymous API call gets **403 JSON**, never a 302."""

    API_PATHS: tuple[str, ...] = (
        "/api/teams/",
        "/api/players/",
        "/api/matches/",
        "/api/rounds/",
    )

    def setUp(self) -> None:
        super().setUp()
        self.client.logout()

    def test_anonymous_api_get_returns_403(self) -> None:
        for path in self.API_PATHS:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403)

    def test_anonymous_api_response_is_not_a_redirect(self) -> None:
        for path in self.API_PATHS:
            with self.subTest(path=path):
                self.assertNotEqual(self.client.get(path).status_code, 302)

    def test_anonymous_api_response_is_json(self) -> None:
        response = self.client.get("/api/matches/")
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")

    def test_authenticated_api_get_returns_200(self) -> None:
        self.client.force_login(get_shared_manager())
        for path in self.API_PATHS:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)


# ---------------------------------------------------------------------------
# The top-nav auth control (§9.6)
# ---------------------------------------------------------------------------


class TestTopnavAuthControl(TestCase):
    """§9.6 — one partial, two branches."""

    def test_authenticated_branch_renders_every_locked_id(self) -> None:
        body = self.client.get(reverse("landing")).content.decode()
        for dom_id in TOPNAV_AUTHENTICATED_IDS:
            with self.subTest(dom_id=dom_id):
                self.assertIn(f'id="{dom_id}"', body)

    def test_authenticated_branch_hides_the_anonymous_controls(self) -> None:
        body = self.client.get(reverse("landing")).content.decode()
        for dom_id in TOPNAV_ANONYMOUS_IDS:
            with self.subTest(dom_id=dom_id):
                self.assertNotIn(f'id="{dom_id}"', body)

    def test_authenticated_branch_shows_the_account_email(self) -> None:
        body = self.client.get(reverse("landing")).content.decode()
        self.assertIn(get_shared_manager().email, body)

    def test_sign_out_is_a_post_form_not_a_link(self) -> None:
        """Locked (§9.6): `<a href="{% url 'logout' %}">` breaks under Django 5.x."""
        body = self.client.get(reverse("landing")).content.decode()
        self.assertIn('id="account-sign-out-form"', body)
        self.assertNotIn(f'<a href="{reverse("logout")}"', body)

    def test_sign_out_form_carries_a_csrf_token(self) -> None:
        body = self.client.get(reverse("landing")).content.decode()
        self.assertIn("csrfmiddlewaretoken", body)

    def test_anonymous_branch_renders_on_an_ungated_page(self) -> None:
        self.client.logout()
        body = self.client.get(reverse("login")).content.decode()
        for dom_id in TOPNAV_ANONYMOUS_IDS:
            with self.subTest(dom_id=dom_id):
                self.assertIn(f'id="{dom_id}"', body)

    def test_anonymous_branch_hides_the_authenticated_controls(self) -> None:
        self.client.logout()
        body = self.client.get(reverse("login")).content.decode()
        for dom_id in TOPNAV_AUTHENTICATED_IDS:
            with self.subTest(dom_id=dom_id):
                self.assertNotIn(f'id="{dom_id}"', body)

    def test_password_change_link_points_at_the_password_change_url(self) -> None:
        body = self.client.get(reverse("landing")).content.decode()
        self.assertIn(reverse("password_change"), body)
