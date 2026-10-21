"""UXP-14 -- role benchmarks are scoped to the requesting Account (ADR-0038).

The benchmark population is the Account's own + Unmanaged Rounds (the same
``owned_game_round_q`` visibility the rest of the Sandbox uses). Another
Account's Rounds never feed this Account's numbers -- not on the HTML page,
not on ``GET /api/players/benchmarks/``, not on the career-page overlay, and
not through the shared cache.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import GameRound, PlayerRoundState
from teams.models import Player, Team
from teams.role_benchmarks_cache import get_all_benchmark_data
from teams.services import build_career_stats, build_role_benchmarks

User = get_user_model()

_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "uxp14-benchmark-ownership",
    }
}


def _player(team_name: str, manager: object | None) -> tuple[Team, Player]:
    team = Team.objects.create(name=team_name, manager=manager)
    player = Player.objects.create(team=team, name=f"{team_name} Cmdr")
    team.slot_commander = player
    team.save()
    return team, player


def _round(
    team: Team, player: Player, manager: object | None, points: int
) -> PlayerRoundState:
    game_round = GameRound.objects.create(
        round_number=1, team_red=team, team_blue=team, manager=manager
    )
    return PlayerRoundState.objects.create(
        game_round=game_round,
        player=player,
        team_color="red",
        role="commander",
        points_scored=points,
        tags_made=5,
        times_tagged=3,
        shots_missed=4,
        final_lives=5,
    )


def _commander_points_n(payload: dict) -> int:
    row = next(
        r for r in payload["benchmarks"]["commander"] if r["stat"] == "points_scored"
    )
    return row["n"]


@override_settings(CACHES=_LOCMEM)
class TestBenchmarksScopedToAccount(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="uxp14-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.team_a, self.player_a = _player("UXP14 A", self.user_a)
        _round(self.team_a, self.player_a, self.user_a, 1000)

    def test_builder_excludes_other_accounts_rounds(self) -> None:
        own = build_role_benchmarks(min_rounds=0, display="mean", user=self.user_a)
        other = build_role_benchmarks(min_rounds=0, display="mean", user=self.user_b)
        self.assertTrue(own["any_data"])
        self.assertEqual(_commander_points_n(own), 1)
        self.assertFalse(other["any_data"])
        self.assertEqual(_commander_points_n(other), 0)

    def test_unmanaged_rounds_are_visible_to_every_account(self) -> None:
        team_u, player_u = _player("UXP14 Unmanaged", None)
        _round(team_u, player_u, None, 500)
        other = build_role_benchmarks(min_rounds=0, display="mean", user=self.user_b)
        own = build_role_benchmarks(min_rounds=0, display="mean", user=self.user_a)
        self.assertEqual(_commander_points_n(other), 1)
        self.assertEqual(_commander_points_n(own), 2)

    def test_html_page_hides_other_accounts_rounds(self) -> None:
        client = self.client
        client.force_login(self.user_b)
        response = client.get(reverse("role_benchmarks") + "?threshold=0")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["any_data"])
        client.force_login(self.user_a)
        response = client.get(reverse("role_benchmarks") + "?threshold=0")
        self.assertTrue(response.context["any_data"])

    def test_api_hides_other_accounts_rounds(self) -> None:
        api = APIClient()
        api.force_login(self.user_b)
        data = api.get("/api/players/benchmarks/", {"threshold": 0}).json()["data"]
        self.assertFalse(data["any_data"])
        api.force_login(self.user_a)
        data = api.get("/api/players/benchmarks/", {"threshold": 0}).json()["data"]
        self.assertTrue(data["any_data"])

    def test_career_overlay_population_excludes_other_accounts(self) -> None:
        # B's own player, compared against B's visible population only.
        team_b, player_b = _player("UXP14 B", self.user_b)
        _round(team_b, player_b, self.user_b, 2000)
        result = build_career_stats(
            player_b, min_rounds=0, display="mean", user=self.user_b
        )
        cell = result["per_role_with_benchmarks"][0]["benchmarks_by_stat"]["avg_points"]
        self.assertEqual(cell["n"], 1)
        self.assertEqual(cell["benchmark_mean"], 2000.0)


@override_settings(CACHES=_LOCMEM)
class TestBenchmarkCacheIsPerAccount(TestCase):
    def setUp(self) -> None:
        cache.clear()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="uxp14-cache-b@example.com", password="Str0ng-Passphrase-9"
        )
        team_a, player_a = _player("UXP14 Cache A", self.user_a)
        _round(team_a, player_a, self.user_a, 1000)

    def test_cache_populated_by_one_account_does_not_leak(self) -> None:
        samples_a, rounds_a = get_all_benchmark_data(user=self.user_a)
        self.assertEqual(len(samples_a[("commander", "points_scored")]), 1)
        # Same cache version: B must not be served A's cached cells.
        samples_b, rounds_b = get_all_benchmark_data(user=self.user_b)
        self.assertEqual(samples_b[("commander", "points_scored")], [])
        self.assertEqual(rounds_b["commander"], {})
        # And A's cached cells are still A's.
        samples_a2, _ = get_all_benchmark_data(user=self.user_a)
        self.assertEqual(samples_a2, samples_a)

    def test_cache_populated_by_other_account_first_does_not_leak(self) -> None:
        samples_b, _ = get_all_benchmark_data(user=self.user_b)
        self.assertEqual(samples_b[("commander", "points_scored")], [])
        samples_a, _ = get_all_benchmark_data(user=self.user_a)
        self.assertEqual(len(samples_a[("commander", "points_scored")]), 1)
