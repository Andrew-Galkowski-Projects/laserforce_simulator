"""UX-02 — the Account's Self-insert (User -> Player one-to-one link).

Seam contract: `.claude/worktrees/ux-02-seam-contract.md` §1 (field), §2
(`accounts/self_insert.py`), §3 (link / unlink views + flash strings), §4
(context processor), §5 (`you_badge` tag), §7 (nav "My player") and §9 (test
boundary).

Boundary discipline (§9): this module asserts only the public surface — the
`User.self_insert` field and its `self_insert_of` reverse, the four helpers,
the two URL names + status codes + redirects + the four flash strings, the two
context keys, the exact `YOU_BADGE_HTML` / `""` tag output and the locked DOM
id `account-my-player-link`. `_safe_next` and query shapes (beyond "zero
queries when unlinked") are internal and never asserted.

Fixture warning (§9): Teams built here are **Unmanaged** unless a test stamps
`manager`. Anything that expects a successful link, a non-None
`get_self_insert` or a badge first stamps `team.manager = get_shared_manager()`
— the Account the autouse fixture logs `self.client` in as.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.context_processors import self_insert as self_insert_context
from accounts.self_insert import (
    clear_self_insert,
    get_self_insert,
    is_self_insert_eligible,
    set_self_insert,
)
from accounts.templatetags.self_insert_tags import YOU_BADGE_HTML
from conftest import get_shared_manager
from teams.models import Player, Team, get_free_agents_team

User = get_user_model()

# §3 — the four locked flash strings (the success one for link is per-Player).
MSG_NOT_ELIGIBLE = "Only a Player on a Team you manage can be your Self-insert."
MSG_NO_SELF_INSERT = "You have no Self-insert to unlink."
MSG_UNLINKED = "Self-insert unlinked."


def _link_success_message(player: Player) -> str:
    return f'"{player.name}" is now your Self-insert.'


EXPECTED_BADGE = (
    '<span class="badge text-bg-primary ms-1 you-badge" '
    'title="Your Self-insert">You</span>'
)


def _messages(response: HttpResponse) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def _reload(user: "User") -> "User":
    return User.objects.get(pk=user.pk)


class _SelfInsertFixture(TestCase):
    """Shared Accounts / Teams / Players.

    * `self.me` — the shared Account (the one `self.client` is logged in as).
    * `self.other` — a second, foreign Account.
    * `self.my_team` / `self.my_player` / `self.my_player_2` — managed by `me`.
    * `self.unmanaged_team` / `self.unmanaged_player` — `manager IS NULL`.
    * `self.foreign_team` / `self.foreign_player` — managed by `other`.
    """

    def setUp(self) -> None:
        self.me = get_shared_manager()
        self.other = User.objects.create_user(
            email="other-self-insert@example.com", password="pw-other-1234!"
        )
        self.my_team = Team.objects.create(name="Mine", manager=self.me)
        self.my_player = Player.objects.create(team=self.my_team, name="Me Myself")
        self.my_player_2 = Player.objects.create(team=self.my_team, name="Alt Me")
        self.unmanaged_team = Team.objects.create(name="Nobody's")
        self.unmanaged_player = Player.objects.create(
            team=self.unmanaged_team, name="Orphan"
        )
        self.foreign_team = Team.objects.create(name="Theirs", manager=self.other)
        self.foreign_player = Player.objects.create(
            team=self.foreign_team, name="Stranger"
        )

    def _raw_link(self, user: "User", player: Player | None) -> None:
        """Write the column directly, bypassing every helper and rule."""
        User.objects.filter(pk=user.pk).update(self_insert=player)


# ===========================================================================
# §1 — the field
# ===========================================================================


class TestSelfInsertField(_SelfInsertFixture):
    def test_default_is_unlinked(self) -> None:
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_link_round_trips_and_reverse_accessor(self) -> None:
        self.me.self_insert = self.my_player
        self.me.save(update_fields=["self_insert"])
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)
        self.assertEqual(
            Player.objects.get(pk=self.my_player.pk).self_insert_of.pk, self.me.pk
        )

    def test_reverse_accessor_raises_when_unlinked(self) -> None:
        with self.assertRaises(Player.self_insert_of.RelatedObjectDoesNotExist):
            Player.objects.get(pk=self.my_player.pk).self_insert_of

    def test_player_delete_sets_null(self) -> None:
        self._raw_link(self.me, self.my_player)
        self.my_player.delete()
        user = _reload(self.me)
        self.assertIsNone(user.self_insert_id)
        self.assertTrue(User.objects.filter(pk=self.me.pk).exists())

    def test_team_delete_sets_null(self) -> None:
        self._raw_link(self.me, self.my_player)
        self.my_team.delete()
        self.assertFalse(Player.objects.filter(pk=self.my_player.pk).exists())
        self.assertIsNone(_reload(self.me).self_insert_id)
        self.assertTrue(User.objects.filter(pk=self.me.pk).exists())

    def test_one_to_one_rejects_double_link(self) -> None:
        self._raw_link(self.me, self.my_player)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._raw_link(self.other, self.my_player)


# ===========================================================================
# §2 — get_self_insert (the drift guard)
# ===========================================================================


class TestGetSelfInsert(_SelfInsertFixture):
    def test_none_user_returns_none(self) -> None:
        self.assertIsNone(get_self_insert(None))

    def test_anonymous_returns_none(self) -> None:
        self.assertIsNone(get_self_insert(AnonymousUser()))

    def test_unlinked_returns_none_with_zero_queries(self) -> None:
        user = _reload(self.me)
        with self.assertNumQueries(0):
            self.assertIsNone(get_self_insert(user))

    def test_owned_link_returns_player(self) -> None:
        self._raw_link(self.me, self.my_player)
        result = get_self_insert(_reload(self.me))
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.my_player.pk)

    def test_drift_team_made_unmanaged_returns_none_without_write(self) -> None:
        self._raw_link(self.me, self.my_player)
        Team.objects.filter(pk=self.my_team.pk).update(manager=None)
        self.assertIsNone(get_self_insert(_reload(self.me)))
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)

    def test_drift_team_handed_to_other_account_returns_none_without_write(
        self,
    ) -> None:
        self._raw_link(self.me, self.my_player)
        Team.objects.filter(pk=self.my_team.pk).update(manager=self.other)
        self.assertIsNone(get_self_insert(_reload(self.me)))
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)

    def test_drift_player_moved_to_free_agents_returns_none(self) -> None:
        self._raw_link(self.me, self.my_player)
        Player.objects.filter(pk=self.my_player.pk).update(team=get_free_agents_team())
        self.assertIsNone(get_self_insert(_reload(self.me)))
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)

    def test_drifted_link_reappears_when_ownership_returns(self) -> None:
        self._raw_link(self.me, self.my_player)
        Team.objects.filter(pk=self.my_team.pk).update(manager=None)
        self.assertIsNone(get_self_insert(_reload(self.me)))
        Team.objects.filter(pk=self.my_team.pk).update(manager=self.me)
        result = get_self_insert(_reload(self.me))
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.my_player.pk)


# ===========================================================================
# §2 — is_self_insert_eligible (STRICT)
# ===========================================================================


class TestIsSelfInsertEligible(_SelfInsertFixture):
    def test_owned_player_is_eligible(self) -> None:
        self.assertTrue(is_self_insert_eligible(self.my_player, self.me))

    def test_unmanaged_player_is_not_eligible(self) -> None:
        self.assertFalse(is_self_insert_eligible(self.unmanaged_player, self.me))

    def test_foreign_player_is_not_eligible(self) -> None:
        self.assertFalse(is_self_insert_eligible(self.foreign_player, self.me))

    def test_free_agents_player_is_not_eligible(self) -> None:
        fa = Player.objects.create(team=get_free_agents_team(), name="Floater")
        self.assertFalse(is_self_insert_eligible(fa, self.me))

    def test_anonymous_is_not_eligible(self) -> None:
        self.assertFalse(is_self_insert_eligible(self.my_player, AnonymousUser()))

    def test_none_user_is_not_eligible(self) -> None:
        self.assertFalse(is_self_insert_eligible(self.my_player, None))


# ===========================================================================
# §2 — set_self_insert / clear_self_insert
# ===========================================================================


class TestSetAndClearSelfInsert(_SelfInsertFixture):
    def test_set_links(self) -> None:
        set_self_insert(self.me, self.my_player)
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)

    def test_set_is_idempotent(self) -> None:
        set_self_insert(self.me, self.my_player)
        set_self_insert(self.me, self.my_player)
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)

    def test_second_set_replaces_first(self) -> None:
        set_self_insert(self.me, self.my_player)
        set_self_insert(self.me, self.my_player_2)
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player_2.id)
        with self.assertRaises(Player.self_insert_of.RelatedObjectDoesNotExist):
            Player.objects.get(pk=self.my_player.pk).self_insert_of

    def test_stale_link_on_other_account_is_cleared(self) -> None:
        # `other` holds a drifted link to a Player `me` now strictly owns.
        self._raw_link(self.other, self.my_player)
        set_self_insert(self.me, self.my_player)
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)
        self.assertIsNone(_reload(self.other).self_insert_id)

    def test_set_touches_nothing_else(self) -> None:
        set_self_insert(self.me, self.my_player)
        player = Player.objects.get(pk=self.my_player.pk)
        self.assertEqual(player.team_id, self.my_team.id)
        self.assertEqual(player.name, "Me Myself")
        self.assertEqual(Team.objects.get(pk=self.my_team.pk).manager_id, self.me.pk)

    def test_clear_unlinks(self) -> None:
        set_self_insert(self.me, self.my_player)
        clear_self_insert(_reload(self.me))
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_clear_removes_drifted_raw_column(self) -> None:
        self._raw_link(self.me, self.my_player)
        Team.objects.filter(pk=self.my_team.pk).update(manager=None)
        clear_self_insert(_reload(self.me))
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_clear_does_not_delete_player(self) -> None:
        set_self_insert(self.me, self.my_player)
        clear_self_insert(_reload(self.me))
        self.assertTrue(Player.objects.filter(pk=self.my_player.pk).exists())


# ===========================================================================
# §3 — the link / unlink views
# ===========================================================================


class TestSelfInsertRoutes(_SelfInsertFixture):
    def test_link_url_path(self) -> None:
        self.assertEqual(
            reverse("self_insert_link", args=[self.my_player.id]),
            f"/accounts/self-insert/{self.my_player.id}/link/",
        )

    def test_unlink_url_path(self) -> None:
        self.assertEqual(reverse("self_insert_unlink"), "/accounts/self-insert/unlink/")

    def test_link_get_is_405(self) -> None:
        response = self.client.get(
            reverse("self_insert_link", args=[self.my_player.id])
        )
        self.assertEqual(response.status_code, 405)
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_unlink_get_is_405(self) -> None:
        self._raw_link(self.me, self.my_player)
        response = self.client.get(reverse("self_insert_unlink"))
        self.assertEqual(response.status_code, 405)
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)

    def test_anonymous_link_post_redirects_to_login(self) -> None:
        self.client.logout()
        response = self.client.post(
            reverse("self_insert_link", args=[self.my_player.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("login")))
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_anonymous_unlink_post_redirects_to_login(self) -> None:
        self._raw_link(self.me, self.my_player)
        self.client.logout()
        response = self.client.post(reverse("self_insert_unlink"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("login")))
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)


class TestSelfInsertLinkView(_SelfInsertFixture):
    def _post_link(self, player_id: int, **data: str) -> HttpResponse:
        return self.client.post(reverse("self_insert_link", args=[player_id]), data)

    def _player_url(self, player: Player) -> str:
        return reverse("player_detail", args=[player.team_id, player.id])

    def test_link_owned_player_succeeds(self) -> None:
        response = self._post_link(self.my_player.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self._player_url(self.my_player))
        self.assertIn(_link_success_message(self.my_player), _messages(response))
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)

    def test_link_unmanaged_player_is_refused_without_write(self) -> None:
        response = self._post_link(self.unmanaged_player.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self._player_url(self.unmanaged_player))
        self.assertIn(MSG_NOT_ELIGIBLE, _messages(response))
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_link_unmanaged_player_keeps_existing_link(self) -> None:
        set_self_insert(self.me, self.my_player)
        self._post_link(self.unmanaged_player.id)
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)

    def test_link_foreign_player_is_404(self) -> None:
        response = self._post_link(self.foreign_player.id)
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_link_nonexistent_player_is_404(self) -> None:
        response = self._post_link(9_999_999)
        self.assertEqual(response.status_code, 404)
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_second_link_replaces_first(self) -> None:
        self._post_link(self.my_player.id)
        response = self._post_link(self.my_player_2.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player_2.id)

    def test_link_clears_stale_link_on_other_account(self) -> None:
        self._raw_link(self.other, self.my_player)
        response = self._post_link(self.my_player.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)
        self.assertIsNone(_reload(self.other).self_insert_id)

    def test_next_is_honoured(self) -> None:
        target = reverse("team_detail", args=[self.my_team.id])
        response = self._post_link(self.my_player.id, next=target)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], target)

    def test_next_is_honoured_on_refusal(self) -> None:
        target = reverse("team_detail", args=[self.unmanaged_team.id])
        response = self._post_link(self.unmanaged_player.id, next=target)
        self.assertEqual(response["Location"], target)

    def test_off_host_next_is_ignored(self) -> None:
        response = self._post_link(
            self.my_player.id, next="https://evil.example.com/steal/"
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self._player_url(self.my_player))
        self.assertEqual(_reload(self.me).self_insert_id, self.my_player.id)

    def test_scheme_relative_off_host_next_is_ignored(self) -> None:
        response = self._post_link(self.my_player.id, next="//evil.example.com/")
        self.assertEqual(response["Location"], self._player_url(self.my_player))


class TestSelfInsertUnlinkView(_SelfInsertFixture):
    def test_unlink_clears_and_flashes_success(self) -> None:
        set_self_insert(self.me, self.my_player)
        response = self.client.post(reverse("self_insert_unlink"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("landing"))
        self.assertIn(MSG_UNLINKED, _messages(response))
        self.assertIsNone(_reload(self.me).self_insert_id)
        self.assertTrue(Player.objects.filter(pk=self.my_player.pk).exists())

    def test_unlink_when_none_flashes_info(self) -> None:
        response = self.client.post(reverse("self_insert_unlink"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("landing"))
        self.assertIn(MSG_NO_SELF_INSERT, _messages(response))
        self.assertNotIn(MSG_UNLINKED, _messages(response))
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_unlink_clears_drifted_link(self) -> None:
        self._raw_link(self.me, self.my_player)
        Team.objects.filter(pk=self.my_team.pk).update(manager=None)
        response = self.client.post(reverse("self_insert_unlink"))
        self.assertIn(MSG_UNLINKED, _messages(response))
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_unlink_honours_next(self) -> None:
        set_self_insert(self.me, self.my_player)
        target = reverse("player_detail", args=[self.my_team.id, self.my_player.id])
        response = self.client.post(reverse("self_insert_unlink"), {"next": target})
        self.assertEqual(response["Location"], target)

    def test_unlink_off_host_next_falls_back_to_landing(self) -> None:
        set_self_insert(self.me, self.my_player)
        response = self.client.post(
            reverse("self_insert_unlink"), {"next": "https://evil.example.com/"}
        )
        self.assertEqual(response["Location"], reverse("landing"))
        self.assertIsNone(_reload(self.me).self_insert_id)

    def test_unlink_does_not_touch_other_account(self) -> None:
        set_self_insert(self.me, self.my_player)
        other_player = Player.objects.create(team=self.foreign_team, name="Theirs1")
        set_self_insert(self.other, other_player)
        self.client.post(reverse("self_insert_unlink"))
        self.assertEqual(_reload(self.other).self_insert_id, other_player.id)


# ===========================================================================
# §4 — the context processor
# ===========================================================================


class TestSelfInsertContextProcessor(_SelfInsertFixture):
    def test_request_without_user_attribute(self) -> None:
        request = RequestFactory().get("/")
        self.assertFalse(hasattr(request, "user"))
        with self.assertNumQueries(0):
            context = self_insert_context(request)
        self.assertEqual(
            context, {"self_insert_player": None, "self_insert_player_id": None}
        )

    def test_anonymous_request(self) -> None:
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        context = self_insert_context(request)
        self.assertIsNone(context["self_insert_player"])
        self.assertIsNone(context["self_insert_player_id"])

    def test_unlinked_account_zero_queries(self) -> None:
        request = RequestFactory().get("/")
        request.user = _reload(self.me)
        with self.assertNumQueries(0):
            context = self_insert_context(request)
        self.assertIsNone(context["self_insert_player"])
        self.assertIsNone(context["self_insert_player_id"])

    def test_linked_account(self) -> None:
        set_self_insert(self.me, self.my_player)
        request = RequestFactory().get("/")
        request.user = _reload(self.me)
        context = self_insert_context(request)
        self.assertEqual(context["self_insert_player"].pk, self.my_player.pk)
        self.assertEqual(context["self_insert_player_id"], self.my_player.id)

    def test_drifted_link(self) -> None:
        set_self_insert(self.me, self.my_player)
        Team.objects.filter(pk=self.my_team.pk).update(manager=None)
        request = RequestFactory().get("/")
        request.user = _reload(self.me)
        context = self_insert_context(request)
        self.assertIsNone(context["self_insert_player"])
        self.assertIsNone(context["self_insert_player_id"])

    def test_keys_reach_rendered_page_context(self) -> None:
        set_self_insert(self.me, self.my_player)
        response = self.client.get(reverse("team_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["self_insert_player_id"], self.my_player.id)
        self.assertEqual(response.context["self_insert_player"].pk, self.my_player.pk)


# ===========================================================================
# §5 — the you_badge template tag
# ===========================================================================


def _render_badge(player_id_expr: str, context: dict) -> str:
    template = Template(
        "{% load self_insert_tags %}{% you_badge " + player_id_expr + " %}"
    )
    return template.render(Context(context))


class TestYouBadgeTag(TestCase):
    def test_constant_is_the_locked_markup(self) -> None:
        self.assertEqual(YOU_BADGE_HTML, EXPECTED_BADGE)

    def test_match_renders_exact_badge(self) -> None:
        out = _render_badge("pid", {"pid": 5, "self_insert_player_id": 5})
        self.assertEqual(out, EXPECTED_BADGE)

    def test_badge_is_not_escaped(self) -> None:
        out = _render_badge("pid", {"pid": 5, "self_insert_player_id": 5})
        self.assertNotIn("&lt;", out)

    def test_string_id_matches_int(self) -> None:
        out = _render_badge("pid", {"pid": "5", "self_insert_player_id": 5})
        self.assertEqual(out, EXPECTED_BADGE)

    def test_mismatch_renders_empty(self) -> None:
        out = _render_badge("pid", {"pid": 6, "self_insert_player_id": 5})
        self.assertEqual(out, "")

    def test_missing_context_key_renders_empty(self) -> None:
        self.assertEqual(_render_badge("pid", {"pid": 5}), "")

    def test_none_context_value_renders_empty(self) -> None:
        out = _render_badge("pid", {"pid": 5, "self_insert_player_id": None})
        self.assertEqual(out, "")

    def test_none_player_id_renders_empty(self) -> None:
        out = _render_badge("pid", {"pid": None, "self_insert_player_id": 5})
        self.assertEqual(out, "")

    def test_empty_string_player_id_renders_empty(self) -> None:
        out = _render_badge("pid", {"pid": "", "self_insert_player_id": 5})
        self.assertEqual(out, "")

    def test_non_numeric_player_id_renders_empty(self) -> None:
        out = _render_badge('"abc"', {"self_insert_player_id": 5})
        self.assertEqual(out, "")

    def test_undefined_variable_renders_empty(self) -> None:
        self.assertEqual(_render_badge("nope", {"self_insert_player_id": 5}), "")


# ===========================================================================
# §7 — nav "My player"
# ===========================================================================


class TestMyPlayerNavLink(_SelfInsertFixture):
    NAV_ID = 'id="account-my-player-link"'

    def _page(self) -> str:
        response = self.client.get(reverse("team_list"))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_present_with_player_detail_href_when_linked(self) -> None:
        set_self_insert(self.me, self.my_player)
        html = self._page()
        self.assertIn(self.NAV_ID, html)
        href = reverse("player_detail", args=[self.my_team.id, self.my_player.id])
        start = html.index(self.NAV_ID)
        tag = html[html.rfind("<a", 0, start) : html.index(">", start) + 1]
        self.assertIn(f'href="{href}"', tag)
        self.assertIn("My player", html)

    def test_absent_when_unlinked(self) -> None:
        self.assertNotIn(self.NAV_ID, self._page())

    def test_absent_when_drifted(self) -> None:
        set_self_insert(self.me, self.my_player)
        Team.objects.filter(pk=self.my_team.pk).update(manager=None)
        self.assertNotIn(self.NAV_ID, self._page())

    def test_absent_when_anonymous(self) -> None:
        set_self_insert(self.me, self.my_player)
        self.client.logout()
        response = self.client.get(reverse("login"))
        self.assertNotIn(self.NAV_ID, response.content.decode())

    def test_nav_link_targets_a_reachable_page(self) -> None:
        set_self_insert(self.me, self.my_player)
        href = reverse("player_detail", args=[self.my_team.id, self.my_player.id])
        self.assertEqual(self.client.get(href).status_code, 200)
