"""UX-02 — the "You" badge and the link / unlink buttons on the sandbox pages.

Seam contract: `.claude/worktrees/ux-02-seam-contract.md` §5 (tag), §6
(buttons), §8 rows 1-4 (sandbox badge sites) and §9 (test boundary).

Assertions stay on the §9 public surface only: the substring `you-badge` on a
page and the locked DOM ids `self-insert-link-form` / `self-insert-link-button`
/ `self-insert-unlink-form` / `self-insert-unlink-button`. Template line
placement and Bootstrap classes other than `you-badge` are internal.

Fixture warning (§9): every Team that should badge is stamped
`manager = get_shared_manager()` — the Account the autouse fixture logs
`self.client` in as. An Unmanaged Team never resolves a Self-insert.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from accounts.self_insert import set_self_insert
from conftest import get_shared_manager
from teams.models import Player, Team

BADGE = "you-badge"
LINK_FORM = 'id="self-insert-link-form"'
LINK_BUTTON = 'id="self-insert-link-button"'
UNLINK_FORM = 'id="self-insert-unlink-form"'
UNLINK_BUTTON = 'id="self-insert-unlink-button"'


def _make_slotted_team(name: str, *, manager=None) -> tuple[Team, list[Player]]:
    """A Team with six slotted (active) Players plus one bench Player."""
    team = Team.objects.create(name=name, manager=manager)
    players = [Player.objects.create(team=team, name=f"{name} P{i}") for i in range(7)]
    team.slot_commander = players[0]
    team.slot_heavy = players[1]
    team.slot_scout_1 = players[2]
    team.slot_scout_2 = players[3]
    team.slot_medic = players[4]
    team.slot_ammo = players[5]
    team.save()
    return team, players


class _BadgeFixture(TestCase):
    def setUp(self) -> None:
        self.me = get_shared_manager()
        self.team, self.players = _make_slotted_team("Owned Squad", manager=self.me)
        self.active_player = self.players[2]
        self.bench_player = self.players[6]

    def _get(self, url: str) -> str:
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()

    def _player_detail_url(self, player: Player) -> str:
        return reverse("player_detail", args=[player.team_id, player.id])


# ===========================================================================
# Sandbox player detail — badge + both button states
# ===========================================================================


class TestSandboxPlayerDetail(_BadgeFixture):
    def test_badge_present_when_linked(self) -> None:
        set_self_insert(self.me, self.active_player)
        html = self._get(self._player_detail_url(self.active_player))
        self.assertIn(BADGE, html)

    def test_no_badge_on_a_different_player(self) -> None:
        set_self_insert(self.me, self.active_player)
        html = self._get(self._player_detail_url(self.players[0]))
        self.assertNotIn(BADGE, html)

    def test_link_button_when_owned_and_not_linked(self) -> None:
        html = self._get(self._player_detail_url(self.active_player))
        self.assertIn(LINK_FORM, html)
        self.assertIn(LINK_BUTTON, html)
        self.assertNotIn(UNLINK_FORM, html)
        self.assertNotIn(UNLINK_BUTTON, html)
        self.assertIn(reverse("self_insert_link", args=[self.active_player.id]), html)

    def test_link_button_on_other_owned_player_when_already_linked(self) -> None:
        set_self_insert(self.me, self.active_player)
        html = self._get(self._player_detail_url(self.players[0]))
        self.assertIn(LINK_FORM, html)
        self.assertNotIn(UNLINK_FORM, html)

    def test_unlink_button_when_linked(self) -> None:
        set_self_insert(self.me, self.active_player)
        html = self._get(self._player_detail_url(self.active_player))
        self.assertIn(UNLINK_FORM, html)
        self.assertIn(UNLINK_BUTTON, html)
        self.assertNotIn(LINK_FORM, html)
        self.assertNotIn(LINK_BUTTON, html)
        self.assertIn(reverse("self_insert_unlink"), html)

    def test_neither_button_when_unmanaged(self) -> None:
        team, players = _make_slotted_team("Open Squad")
        html = self._get(self._player_detail_url(players[0]))
        self.assertNotIn(LINK_FORM, html)
        self.assertNotIn(LINK_BUTTON, html)
        self.assertNotIn(UNLINK_FORM, html)
        self.assertNotIn(UNLINK_BUTTON, html)

    def test_drifted_link_shows_no_badge_and_no_unlink(self) -> None:
        set_self_insert(self.me, self.active_player)
        Team.objects.filter(pk=self.team.pk).update(manager=None)
        html = self._get(self._player_detail_url(self.active_player))
        self.assertNotIn(BADGE, html)
        self.assertNotIn(UNLINK_FORM, html)
        self.assertNotIn(LINK_FORM, html)

    def test_link_button_round_trip(self) -> None:
        url = self._player_detail_url(self.active_player)
        response = self.client.post(
            reverse("self_insert_link", args=[self.active_player.id]),
            {"next": url},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], url)
        html = self._get(url)
        self.assertIn(BADGE, html)
        self.assertIn(UNLINK_FORM, html)


# ===========================================================================
# Sandbox team detail roster
# ===========================================================================


class TestSandboxTeamDetail(_BadgeFixture):
    def test_badge_exactly_once_on_active_row(self) -> None:
        set_self_insert(self.me, self.active_player)
        html = self._get(reverse("team_detail", args=[self.team.id]))
        self.assertEqual(html.count(BADGE), 1)

    def test_badge_exactly_once_on_bench_row(self) -> None:
        set_self_insert(self.me, self.bench_player)
        html = self._get(reverse("team_detail", args=[self.team.id]))
        self.assertEqual(html.count(BADGE), 1)

    def test_badge_sits_on_the_linked_row(self) -> None:
        set_self_insert(self.me, self.active_player)
        html = self._get(reverse("team_detail", args=[self.team.id]))
        badge_at = html.index(BADGE)
        # The badge follows the linked Player's name closely (same row).
        window = html[max(0, badge_at - 400) : badge_at]
        self.assertIn(self.active_player.name, window)

    def test_no_badge_on_another_team(self) -> None:
        set_self_insert(self.me, self.active_player)
        other_team, _ = _make_slotted_team("Other Owned", manager=self.me)
        html = self._get(reverse("team_detail", args=[other_team.id]))
        self.assertNotIn(BADGE, html)


# ===========================================================================
# /players/ list and career stats
# ===========================================================================


class TestSandboxPlayerList(_BadgeFixture):
    def test_badge_present_when_linked(self) -> None:
        set_self_insert(self.me, self.active_player)
        html = self._get(reverse("player_list"))
        self.assertEqual(html.count(BADGE), 1)


class TestSandboxPlayerCareerStats(_BadgeFixture):
    def test_badge_present_when_linked(self) -> None:
        set_self_insert(self.me, self.active_player)
        html = self._get(reverse("player_career_stats", args=[self.active_player.id]))
        self.assertIn(BADGE, html)

    def test_no_badge_for_other_player(self) -> None:
        set_self_insert(self.me, self.active_player)
        html = self._get(reverse("player_career_stats", args=[self.players[0].id]))
        self.assertNotIn(BADGE, html)


# ===========================================================================
# Regression guard — an unlinked Account sees no badge anywhere
# ===========================================================================


class TestUnlinkedAccountSeesNoBadge(_BadgeFixture):
    def _urls(self) -> list[str]:
        return [
            self._player_detail_url(self.active_player),
            reverse("team_detail", args=[self.team.id]),
            reverse("player_list"),
            reverse("player_career_stats", args=[self.active_player.id]),
        ]

    def test_unlinked_account_sees_no_badge(self) -> None:
        for url in self._urls():
            with self.subTest(url=url):
                self.assertNotIn(BADGE, self._get(url))

    def test_drifted_account_sees_no_badge(self) -> None:
        set_self_insert(self.me, self.active_player)
        Team.objects.filter(pk=self.team.pk).update(manager=None)
        for url in self._urls():
            with self.subTest(url=url):
                self.assertNotIn(BADGE, self._get(url))

    def test_unlinked_after_unlink_sees_no_badge(self) -> None:
        set_self_insert(self.me, self.active_player)
        self.client.post(reverse("self_insert_unlink"))
        for url in self._urls():
            with self.subTest(url=url):
                self.assertNotIn(BADGE, self._get(url))
