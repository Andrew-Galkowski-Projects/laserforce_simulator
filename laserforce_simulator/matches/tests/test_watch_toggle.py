"""LG-06f — tests for the ``watch_list_toggle`` endpoint.

``POST /leagues/<int:league_id>/players/watch-list/toggle/`` (URL name
``watch_list_toggle``, CSRF-protected, POST-only). Flips a Player's
membership in this League's session watch list and returns
``{"watched": bool, "player_id": int}`` (the NEW state after the flip).

Seam contract §1 / §2 / §10 ``test_watch_toggle.py`` boundary:

* **add** — player not in list ⇒ ``{"watched": true}`` + session gains the id
  under ``str(league_id)``.
* **remove** — player in list ⇒ ``{"watched": false}`` + session drops the id.
* **per-League isolation** — toggling in League A does not affect League B's
  list (``watch_lists`` keyed by ``str(league_id)``).
* **405** on GET; **400** on invalid ``player_id`` (``{"error": ...}``);
  **400** on unknown (non-existent) ``player_id``; **404** on missing League.
* **CSRF** — a POST without the token is rejected (the endpoint is NOT
  exempt). Exercised with ``Client(enforce_csrf_checks=True)``.

These tests assert the ``session["watch_lists"]`` shape
(``{str(league_id): [ids]}``). They are written test-first against the
LG-06f seam contract; they FAIL until the Code agent lands the view + URL.
"""

from __future__ import annotations

import json
from datetime import date

from django.test import Client, TestCase
from django.urls import reverse

from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots


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
        watch_lists = self.client.session["watch_lists"]
        self.assertEqual(watch_lists, {str(self.league.id): [self.player.id]})

    def test_second_toggle_removes_and_returns_watched_false(self) -> None:
        self.client.post(_toggle_url(self.league.id), {"player_id": self.player.id})
        resp = self.client.post(
            _toggle_url(self.league.id), {"player_id": self.player.id}
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertFalse(data["watched"])
        self.assertEqual(data["player_id"], self.player.id)

    def test_remove_drops_id_from_session(self) -> None:
        self.client.post(_toggle_url(self.league.id), {"player_id": self.player.id})
        self.client.post(_toggle_url(self.league.id), {"player_id": self.player.id})
        watch_lists = self.client.session.get("watch_lists", {})
        self.assertEqual(watch_lists.get(str(self.league.id), []), [])


# ---------------------------------------------------------------------------
# Per-League isolation
# ---------------------------------------------------------------------------


class TestWatchTogglePerLeagueIsolation(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.league_a = _make_league("LeagueA")
        self.league_b = _make_league("LeagueB")
        self.season_a, teams_a = _make_active_season(self.league_a, n_teams=2)
        self.season_b, teams_b = _make_active_season(self.league_b, n_teams=2)
        self.player = teams_a[0].active_players[0]

    def test_toggle_in_a_does_not_touch_b(self) -> None:
        self.client.post(_toggle_url(self.league_a.id), {"player_id": self.player.id})
        watch_lists = self.client.session["watch_lists"]
        self.assertIn(str(self.league_a.id), watch_lists)
        self.assertNotIn(str(self.league_b.id), watch_lists)

    def test_same_player_independent_per_league(self) -> None:
        # Watch the same player id in both leagues; lists are separate keys.
        self.client.post(_toggle_url(self.league_a.id), {"player_id": self.player.id})
        self.client.post(_toggle_url(self.league_b.id), {"player_id": self.player.id})
        watch_lists = self.client.session["watch_lists"]
        self.assertEqual(
            watch_lists,
            {
                str(self.league_a.id): [self.player.id],
                str(self.league_b.id): [self.player.id],
            },
        )

    def test_remove_in_a_leaves_b_intact(self) -> None:
        self.client.post(_toggle_url(self.league_a.id), {"player_id": self.player.id})
        self.client.post(_toggle_url(self.league_b.id), {"player_id": self.player.id})
        # Remove from A.
        self.client.post(_toggle_url(self.league_a.id), {"player_id": self.player.id})
        watch_lists = self.client.session["watch_lists"]
        self.assertEqual(watch_lists.get(str(self.league_a.id), []), [])
        self.assertEqual(watch_lists.get(str(self.league_b.id), []), [self.player.id])


# ---------------------------------------------------------------------------
# Method / validation / 404
# ---------------------------------------------------------------------------


class TestWatchToggleErrors(TestCase):
    def setUp(self) -> None:
        self.client = Client()
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

    def test_unknown_player_id_does_not_mutate_session(self) -> None:
        self.client.post(_toggle_url(self.league.id), {"player_id": 999999})
        watch_lists = self.client.session.get("watch_lists", {})
        self.assertEqual(watch_lists.get(str(self.league.id), []), [])

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
