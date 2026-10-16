"""ANG-01b -- player career + role benchmarks API (seam contract §5 + §6 #9/#10).

``GET /api/players/<pk>/career/`` resolves the Player through the owned
queryset, validates ``BenchmarkParamsForm`` and returns ``build_career_stats``
output plus a flattened player ref. ``GET /api/players/benchmarks/`` returns
``build_role_benchmarks`` output minus the template-only ``benchmarks_by_role``.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import Player
from teams.role_benchmarks import ROLES, STAT_KEYS

User = get_user_model()

_CAREER_KEYS = {
    "player",
    "total_rounds",
    "has_rounds",
    "career",
    "per_role",
    "trend",
    "min_rounds",
    "display",
    "stat_keys",
    "per_role_with_benchmarks",
}
_BENCHMARK_KEYS = {
    "min_rounds",
    "display",
    "roles",
    "stat_keys",
    "benchmarks",
    "any_data",
}
_BENCHMARK_ROW_KEYS = {"stat", "mean", "median", "p25", "p75", "p90", "n"}


class _CareerTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang01b-career-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, self.red_players = make_team_with_slots("CarRed")
        self.blue, _ = make_team_with_slots("CarBlue")
        self.player = self.red_players["commander"]

    def errors(self, response, status: int) -> list[dict]:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"errors", "meta"})
        return body["errors"]

    def codes_fields(self, response, status: int = 400):
        return [(e["code"], e["field"]) for e in self.errors(response, status)]

    def play_one_round(self) -> None:
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            BatchSimulator().save_games(self.red, self.blue, [[2468, False]], 1)


class TestRouteNames(TestCase):
    def test_route_names(self) -> None:
        self.assertEqual(reverse("player-career", args=[5]), "/api/players/5/career/")
        self.assertEqual(reverse("player-benchmarks"), "/api/players/benchmarks/")


# ---------------------------------------------------------------------------
# Career
# ---------------------------------------------------------------------------


class TestPlayerCareer(_CareerTestCase):
    def career(self, player_id: int, **params):
        return self.api.get(f"/api/players/{player_id}/career/", params)

    def test_no_rounds_data_keys_and_defaults(self) -> None:
        response = self.career(self.player.pk)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        data = body["data"]
        self.assertEqual(set(data), _CAREER_KEYS)
        self.assertEqual(data["min_rounds"], 5)
        self.assertEqual(data["display"], "mean")
        self.assertEqual(data["total_rounds"], 0)
        self.assertFalse(data["has_rounds"])
        self.assertEqual(data["per_role_with_benchmarks"], [])
        self.assertIsInstance(data["stat_keys"], list)

    def test_player_ref_flattened(self) -> None:
        data = self.career(self.player.pk).json()["data"]
        self.assertEqual(
            data["player"],
            {
                "id": self.player.pk,
                "name": self.player.name,
                "team": {"id": self.red.pk, "name": self.red.name},
            },
        )

    def test_with_rounds(self) -> None:
        self.play_one_round()
        data = self.career(self.player.pk).json()["data"]
        self.assertEqual(data["total_rounds"], 1)
        self.assertTrue(data["has_rounds"])
        self.assertTrue(data["per_role_with_benchmarks"])
        self.assertIsInstance(data["trend"], list)

    def test_params_echoed(self) -> None:
        data = self.career(self.player.pk, threshold=3, display="median").json()["data"]
        self.assertEqual(data["min_rounds"], 3)
        self.assertEqual(data["display"], "median")

    def test_threshold_zero_allowed(self) -> None:
        response = self.career(self.player.pk, threshold=0)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["min_rounds"], 0)

    def test_non_int_threshold_is_invalid(self) -> None:
        self.assertEqual(
            self.codes_fields(self.career(self.player.pk, threshold="lots")),
            [("invalid", "threshold")],
        )

    def test_negative_threshold_is_min_value(self) -> None:
        self.assertEqual(
            self.codes_fields(self.career(self.player.pk, threshold=-1)),
            [("min_value", "threshold")],
        )

    def test_bad_display_is_invalid_choice(self) -> None:
        self.assertEqual(
            self.codes_fields(self.career(self.player.pk, display="mode")),
            [("invalid_choice", "display")],
        )

    def test_foreign_player_is_404(self) -> None:
        foreign, _ = make_team_with_slots("CarForeign")
        foreign.manager = self.user_b
        foreign.save()
        foreign_player = Player.objects.filter(team=foreign).first()
        errors = self.errors(self.career(foreign_player.pk), 404)
        self.assertEqual(errors[0]["code"], "not_found")
        self.assertIsNone(errors[0]["field"])

    def test_unknown_player_is_404(self) -> None:
        self.assertEqual(self.errors(self.career(999999), 404)[0]["code"], "not_found")

    def test_unknown_player_404_beats_bad_params(self) -> None:
        """``get_object()`` runs before the param Form for the career action."""
        self.assertEqual(
            self.errors(self.career(999999, threshold=-1), 404)[0]["code"], "not_found"
        )

    def test_post_is_405(self) -> None:
        response = self.api.post(
            f"/api/players/{self.player.pk}/career/", {}, format="json"
        )
        self.assertEqual(response.status_code, 405)

    def test_anonymous_is_403(self) -> None:
        response = APIClient().get(f"/api/players/{self.player.pk}/career/")
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


class TestRoleBenchmarks(_CareerTestCase):
    def benchmarks(self, **params):
        return self.api.get("/api/players/benchmarks/", params)

    def test_data_keys_and_defaults(self) -> None:
        response = self.benchmarks()
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(set(data), _BENCHMARK_KEYS)
        self.assertNotIn("benchmarks_by_role", data)
        self.assertEqual(data["min_rounds"], 5)
        self.assertEqual(data["display"], "mean")
        self.assertEqual(data["roles"], list(ROLES))
        self.assertEqual(data["stat_keys"], list(STAT_KEYS))

    def test_benchmarks_shape(self) -> None:
        data = self.benchmarks().json()["data"]
        self.assertEqual(set(data["benchmarks"]), set(ROLES))
        for role in ROLES:
            rows = data["benchmarks"][role]
            self.assertEqual(len(rows), len(STAT_KEYS))
            for row in rows:
                self.assertEqual(set(row), _BENCHMARK_ROW_KEYS)

    def test_any_data_false_when_empty(self) -> None:
        self.assertFalse(self.benchmarks().json()["data"]["any_data"])

    def test_params_echoed(self) -> None:
        data = self.benchmarks(threshold=1, display="median").json()["data"]
        self.assertEqual(data["min_rounds"], 1)
        self.assertEqual(data["display"], "median")

    def test_non_int_threshold_is_invalid(self) -> None:
        self.assertEqual(
            self.codes_fields(self.benchmarks(threshold="1.5")),
            [("invalid", "threshold")],
        )

    def test_negative_threshold_is_min_value(self) -> None:
        self.assertEqual(
            self.codes_fields(self.benchmarks(threshold=-4)),
            [("min_value", "threshold")],
        )

    def test_bad_display_is_invalid_choice(self) -> None:
        self.assertEqual(
            self.codes_fields(self.benchmarks(display="max")),
            [("invalid_choice", "display")],
        )

    def test_not_swallowed_by_pk_route(self) -> None:
        response = self.api.get("/api/players/benchmarks/")
        self.assertEqual(response.status_code, 200, response.content)

    def test_anonymous_is_403(self) -> None:
        self.assertEqual(APIClient().get("/api/players/benchmarks/").status_code, 403)
