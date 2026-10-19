"""LG-06f — tests for the ``watch_list_toggle`` endpoint.

``POST /leagues/<int:league_id>/players/watch-list/toggle/`` (URL name
``watch_list_toggle``, CSRF-protected, POST-only). Flips a Player's
membership in this League's session watch list and returns
``{"watched": bool, "player_id": int}`` (the NEW state after the flip).

Seam contract §1 / §2 / §10 ``test_watch_toggle.py`` boundary:

* **add** — player not in list ⇒ ``{"watched": true}`` + a ``WatchedPlayer``
  row for (Account, League, Player) (ANG-01e: the list lives on the Account).
* **remove** — player in list ⇒ ``{"watched": false}`` + the row is deleted.
* **per-League isolation** — toggling in League A does not affect League B's
  list; another Account's list is independent.
* **player outside the League** ⇒ 400 ``{"error": "player not in league"}``.
* **405** on GET; **400** on invalid ``player_id`` (``{"error": ...}``);
  **400** on unknown (non-existent) ``player_id``; **404** on missing League.
* **CSRF** — a POST without the token is rejected (the endpoint is NOT
  exempt). Exercised with ``Client(enforce_csrf_checks=True)``.

These tests assert the Account's ``WatchedPlayer`` rows (ANG-01e); the
browser session is no longer read or written.
"""

from __future__ import annotations

import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from matches.models import League, Season, WatchedPlayer
from matches.tests.conftest import make_team_with_slots
from conftest import get_shared_manager


def _watched(league: League) -> list[int]:
    """The shared manager's watched Player ids for ``league`` (ANG-01e)."""
    return list(
        WatchedPlayer.objects.filter(
            account=get_shared_manager(), league=league
        ).values_list("player_id", flat=True)
    )


def _make_league(name: str = "ToggleLeague") -> League:
    return League.objects.create(name=name)


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}{name}T{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _toggle_url(league_id: int) -> str:
    return reverse("watch_list_toggle", args=[league_id])


# ---------------------------------------------------------------------------
# Happy path — add / remove / response shape
# ---------------------------------------------------------------------------


class TestWatchToggleAddRemove(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(get_shared_manager())
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = teams
        self.player = self.team_a.active_players[0]

    def test_toggle_url_reverses(self) -> None:
        self.assertEqual(
            _toggle_url(self.league.id),
            f"/leagues/{self.league.id}/players/watch-list/toggle/",
        )

    def test_add_returns_watched_true(self) -> None:
        resp = self.client.post(
            _toggle_url(self.league.id), {"player_id": self.player.id}
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["watched"])
        self.assertEqual(data["player_id"], self.player.id)

    def test_add_writes_id_under_league_key(self) -> None:
        self.client.post(_toggle_url(self.league.id), {"player_id": self.player.id})
        self.assertEqual(_watched(self.league), [self.player.id])
        self.assertNotIn("watch_lists", self.client.session)

    def test_watch_persists_for_account_across_sessions(self) -> None:
        # ANG-01e: the list lives on the Account, not the browser session.
        self.client.post(_toggle_url(self.league.id), {"player_id": self.player.id})
        other_browser = Client()
        other_browser.force_login(get_shared_manager())
        resp = other_browser.post(
            _toggle_url(self.league.id), {"player_id": self.player.id}
        )
        self.assertFalse(json.loads(resp.content)["watched"])

    def test_player_outside_league_returns_400(self) -> None:
        outsider_team, _ = make_team_with_slots("ToggleOutsider")
        outsider = outsider_team.active_players[0]
        resp = self.client.post(_toggle_url(self.league.id), {"player_id": outsider.id})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content), {"error": "player not in league"})
        self.assertEqual(_watched(self.league), [])

    def test_other_accounts_list_is_independent(self) -> None:
        other = get_user_model().objects.create_user(
            email="toggle-other@example.com", password="Str0ng-Passphrase-9"
        )
        WatchedPlayer.objects.create(
            account=other, league=self.league, player=self.player
        )
        resp = self.client.post(
            _toggle_url(self.league.id), {"player_id": self.player.id}
        )
        self.assertTrue(json.loads(resp.content)["watched"])
        self.assertEqual(
            WatchedPlayer.objects.filter(account=other, league=self.league).count(), 1
        )

    def test_second_toggle_removes_and_returns_watched_false(self) -> None:
        self.client.post(_toggle_url(self.league.id), {"player_id": self.player.id})
        resp = self.client.post(
            _toggle_url(self.league.id), {"player_id": self.player.id}
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertFalse(data["watched"])
        self.assertEqual(data["player_id"], self.player.id)

    def test_remove_drops_id_from_account_list(self) -> None:
        self.client.post(_toggle_url(self.league.id), {"player_id": self.player.id})
        self.client.post(_toggle_url(self.league.id), {"player_id": self.player.id})
        self.assertEqual(_watched(self.league), [])


# ---------------------------------------------------------------------------
# Per-League isolation
# ---------------------------------------------------------------------------


class TestWatchTogglePerLeagueIsolation(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(get_shared_manager())
        self.league_a = _make_league("LeagueA")
        self.league_b = _make_league("LeagueB")
        self.season_a, teams_a = _make_active_season(self.league_a, n_teams=2)
        self.season_b, teams_b = _make_active_season(self.league_b, n_teams=2)
        self.player = teams_a[0].active_players[0]

    def _make_free_agent_in_b(self) -> None:
        # The player is a free agent in League B too, so B accepts the watch.
        self.league_b.free_agent_pool = self.player.team
        self.league_b.save(update_fields=["free_agent_pool"])

    def test_toggle_in_a_does_not_touch_b(self) -> None:
        self.client.post(_toggle_url(self.league_a.id), {"player_id": self.player.id})
        self.assertEqual(_watched(self.league_a), [self.player.id])
        self.assertEqual(_watched(self.league_b), [])

    def test_same_player_independent_per_league(self) -> None:
        # Watch the same player id in both leagues; lists are separate rows.
        self._make_free_agent_in_b()
        self.client.post(_toggle_url(self.league_a.id), {"player_id": self.player.id})
        self.client.post(_toggle_url(self.league_b.id), {"player_id": self.player.id})
        self.assertEqual(_watched(self.league_a), [self.player.id])
        self.assertEqual(_watched(self.league_b), [self.player.id])

    def test_remove_in_a_leaves_b_intact(self) -> None:
        self._make_free_agent_in_b()
        self.client.post(_toggle_url(self.league_a.id), {"player_id": self.player.id})
        self.client.post(_toggle_url(self.league_b.id), {"player_id": self.player.id})
        # Remove from A.
        self.client.post(_toggle_url(self.league_a.id), {"player_id": self.player.id})
        self.assertEqual(_watched(self.league_a), [])
        self.assertEqual(_watched(self.league_b), [self.player.id])


# ---------------------------------------------------------------------------
# Method / validation / 404
# ---------------------------------------------------------------------------


class TestWatchToggleErrors(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.client.force_login(get_shared_manager())
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.player = teams[0].active_players[0]

    def test_get_returns_405(self) -> None:
        resp = self.client.get(_toggle_url(self.league.id))
        self.assertEqual(resp.status_code, 405)

    def test_invalid_player_id_returns_400(self) -> None:
        resp = self.client.post(
            _toggle_url(self.league.id), {"player_id": "not-an-int"}
        )
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn("error", data)

    def test_missing_player_id_returns_400(self) -> None:
        resp = self.client.post(_toggle_url(self.league.id), {})
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn("error", data)

    def test_unknown_player_id_returns_400(self) -> None:
        resp = self.client.post(_toggle_url(self.league.id), {"player_id": 999999})
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn("error", data)

    def test_unknown_player_id_does_not_mutate_list(self) -> None:
        self.client.post(_toggle_url(self.league.id), {"player_id": 999999})
        self.assertEqual(_watched(self.league), [])

    def test_missing_league_returns_404(self) -> None:
        resp = self.client.post(_toggle_url(999999), {"player_id": self.player.id})
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# CSRF enforcement
# ---------------------------------------------------------------------------


class TestWatchToggleCsrf(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=2)
        self.player = teams[0].active_players[0]

    def test_post_without_csrf_token_rejected(self) -> None:
        # An enforcing client + no token ⇒ 403 (endpoint is NOT csrf_exempt).
        enforcing = Client(enforce_csrf_checks=True)
        resp = enforcing.post(
            _toggle_url(self.league.id), {"player_id": self.player.id}
        )
        self.assertEqual(resp.status_code, 403)
