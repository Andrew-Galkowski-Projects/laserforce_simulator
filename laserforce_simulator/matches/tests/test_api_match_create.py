"""ANG-01b -- ``POST /api/matches/`` and ``POST /api/rounds/`` (seam contract §6 #1/#2).

Both viewsets stay ``ReadOnlyModelViewSet`` and gain a ``create`` that binds
``MatchSetupForm`` / ``SingleRoundSetupForm`` (``user=request.user``), runs the
sandbox through ``create_sandbox_match`` / ``create_sandbox_round`` and answers
201 with the detail serializer. The row is stamped to the requesting Account.

Every simulating test caps ``BatchSimulator.ROUND_TICKS``; no simulated point
total is ever asserted.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conftest import get_shared_manager
from core.models import ArenaMap
from matches.models import GameRound, Match
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import FREE_AGENTS_TEAM_NAME, Team

User = get_user_model()

_FAST_TICKS = 40

_GAME_ROUND_DETAIL_KEYS = {
    "id",
    "match",
    "round_number",
    "team_red",
    "team_blue",
    "date_played",
    "red_points",
    "blue_points",
    "red_team_eliminated",
    "blue_team_eliminated",
    "eliminated_at",
    "winner",
    "is_completed",
    # ANG-06c: the three name fields joined the detail contract.
    "team_red_name",
    "team_blue_name",
    "winner_name",
    "player_states",
}


def _codes_fields(response) -> list[tuple[str, str | None]]:
    return [(e["code"], e["field"]) for e in response.json()["errors"]]


class _CreateTestCase(TestCase):
    """Account A (the shared manager) drives the API; B owns foreign rows."""

    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang01b-create-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, _ = make_team_with_slots("CreateRed")
        self.blue, _ = make_team_with_slots("CreateBlue")

    def assert_error_envelope(self, response, status: int) -> list[dict]:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"errors", "meta"})
        self.assertEqual(body["meta"], {})
        self.assertTrue(body["errors"])
        return body["errors"]

    def foreign_team(self) -> Team:
        team, _ = make_team_with_slots("CreateForeign")
        team.manager = self.user_b
        team.save()
        return team

    def free_agents_with_valid_roster(self) -> Team:
        """A Free Agents pool Team that *does* have a valid roster, so only
        ``Team.objects.regular()`` can be what excludes it."""
        team, _ = make_team_with_slots("CreateFA")
        team.name = FREE_AGENTS_TEAM_NAME
        team.save()
        self.assertTrue(team.is_valid_roster)
        return team


# ---------------------------------------------------------------------------
# POST /api/matches/
# ---------------------------------------------------------------------------


class TestMatchCreate(_CreateTestCase):
    def _post(self, payload) -> object:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            return self.api.post("/api/matches/", payload, format="json")

    def test_create_returns_201_match_envelope(self) -> None:
        response = self._post(
            {
                "team_red": self.red.pk,
                "team_blue": self.blue.pk,
                "match_type": "friendly",
            }
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        data = body["data"]
        for key in (
            "id",
            "team_red",
            "team_blue",
            "match_type",
            "date_played",
            "is_completed",
            "round_ids",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["team_red"], self.red.pk)
        self.assertEqual(data["team_blue"], self.blue.pk)
        self.assertEqual(data["match_type"], "friendly")
        self.assertEqual(len(data["round_ids"]), 2)

    def test_create_carries_names_and_totals(self) -> None:
        """ANG-06c: the 201 body carries the names and the four totals, which
        equal the saved Match's properties (no simulated total is asserted)."""
        response = self._post(
            {
                "team_red": self.red.pk,
                "team_blue": self.blue.pk,
                "match_type": "friendly",
            }
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["team_red_name"], "CreateRed Team")
        self.assertEqual(data["team_blue_name"], "CreateBlue Team")
        match = Match.objects.get(pk=data["id"])
        self.assertEqual(
            data["winner_name"], match.winner.name if match.winner else None
        )
        self.assertEqual(data["red_total_points"], match.red_total_points)
        self.assertEqual(data["blue_total_points"], match.blue_total_points)
        self.assertEqual(data["red_rounds_won"], match.red_rounds_won)
        self.assertEqual(data["blue_rounds_won"], match.blue_rounds_won)

    def test_create_stamps_requesting_account(self) -> None:
        response = self._post(
            {
                "team_red": self.red.pk,
                "team_blue": self.blue.pk,
                "match_type": "tournament",
            }
        )
        self.assertEqual(response.status_code, 201, response.content)
        match = Match.objects.get(pk=response.json()["data"]["id"])
        self.assertEqual(match.manager_id, self.user_a.pk)
        self.assertEqual(match.match_type, "tournament")

    def test_created_match_is_readable_by_detail(self) -> None:
        response = self._post(
            {
                "team_red": self.red.pk,
                "team_blue": self.blue.pk,
                "match_type": "friendly",
            }
        )
        pk = response.json()["data"]["id"]
        self.assertEqual(self.api.get(f"/api/matches/{pk}/").status_code, 200)

    def test_created_match_is_invisible_to_another_account(self) -> None:
        response = self._post(
            {
                "team_red": self.red.pk,
                "team_blue": self.blue.pk,
                "match_type": "friendly",
            }
        )
        pk = response.json()["data"]["id"]
        other = APIClient()
        other.force_login(self.user_b)
        self.assertEqual(other.get(f"/api/matches/{pk}/").status_code, 404)

    def test_missing_fields_are_400_required(self) -> None:
        before = Match.objects.count()
        response = self._post({})
        self.assert_error_envelope(response, 400)
        self.assertEqual(
            sorted(_codes_fields(response), key=lambda cf: cf[1]),
            [
                ("required", "match_type"),
                ("required", "team_blue"),
                ("required", "team_red"),
            ],
        )
        self.assertEqual(Match.objects.count(), before)

    def test_match_type_has_no_default(self) -> None:
        response = self._post({"team_red": self.red.pk, "team_blue": self.blue.pk})
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("required", "match_type")])

    def test_bad_match_type_is_invalid_choice(self) -> None:
        response = self._post(
            {"team_red": self.red.pk, "team_blue": self.blue.pk, "match_type": "bogus"}
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "match_type")])

    def test_same_team_is_400_invalid_null_field(self) -> None:
        before = Match.objects.count()
        response = self._post(
            {
                "team_red": self.red.pk,
                "team_blue": self.red.pk,
                "match_type": "friendly",
            }
        )
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "A team cannot play against itself!",
                    "field": None,
                }
            ],
        )
        self.assertEqual(Match.objects.count(), before)

    def test_foreign_team_is_invalid_choice(self) -> None:
        foreign = self.foreign_team()
        before = Match.objects.count()
        response = self._post(
            {
                "team_red": foreign.pk,
                "team_blue": self.blue.pk,
                "match_type": "friendly",
            }
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "team_red")])
        self.assertEqual(Match.objects.count(), before)

    def test_unknown_team_is_invalid_choice(self) -> None:
        response = self._post(
            {"team_red": self.red.pk, "team_blue": 999999, "match_type": "friendly"}
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "team_blue")])

    def test_free_agents_team_is_invalid_choice(self) -> None:
        fa = self.free_agents_with_valid_roster()
        response = self._post(
            {"team_red": self.red.pk, "team_blue": fa.pk, "match_type": "friendly"}
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "team_blue")])

    def test_invalid_roster_team_is_invalid_choice(self) -> None:
        empty = Team.objects.create(name="No Slots")
        response = self._post(
            {"team_red": empty.pk, "team_blue": self.blue.pk, "match_type": "friendly"}
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "team_red")])

    def test_unconfirmed_arena_map_is_invalid_choice(self) -> None:
        arena_map = ArenaMap.objects.create(
            name="Unconfirmed", img_width=10, img_height=10
        )
        response = self._post(
            {
                "team_red": self.red.pk,
                "team_blue": self.blue.pk,
                "match_type": "friendly",
                "arena_map": arena_map.pk,
            }
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "arena_map")])

    def test_simulator_value_error_is_400_invalid(self) -> None:
        """Failure mode: the simulator refusing the matchup surfaces as a
        null-field ``invalid`` error carrying its message."""

        def _refuse(self, *args, **kwargs):
            raise ValueError("Simulator refused this matchup.")

        before = Match.objects.count()
        with patch.object(BatchSimulator, "simulate_match", _refuse):
            response = self._post(
                {
                    "team_red": self.red.pk,
                    "team_blue": self.blue.pk,
                    "match_type": "friendly",
                }
            )
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "Simulator refused this matchup.",
                    "field": None,
                }
            ],
        )
        self.assertEqual(Match.objects.count(), before)

    def test_non_object_json_body_is_parse_error(self) -> None:
        response = self._post([1, 2, 3])
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(errors[0]["code"], "parse_error")
        self.assertIsNone(errors[0]["field"])

    def test_detail_write_methods_still_405(self) -> None:
        match = Match.objects.create(
            team_red=self.red, team_blue=self.blue, match_type="friendly"
        )
        url = f"/api/matches/{match.pk}/"
        for method in ("put", "patch", "delete"):
            response = getattr(self.api, method)(url, {}, format="json")
            errors = self.assert_error_envelope(response, 405)
            self.assertEqual(errors[0]["code"], "method_not_allowed")
            self.assertIn("Allow", response.headers)
            self.assertNotIn(method.upper(), response.headers["Allow"])
        self.assertTrue(Match.objects.filter(pk=match.pk).exists())

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        before = Match.objects.count()
        response = APIClient().post(
            "/api/matches/",
            {
                "team_red": self.red.pk,
                "team_blue": self.blue.pk,
                "match_type": "friendly",
            },
            format="json",
        )
        errors = self.assert_error_envelope(response, 401)
        self.assertEqual(errors[0]["code"], "not_authenticated")
        self.assertEqual(Match.objects.count(), before)


# ---------------------------------------------------------------------------
# POST /api/rounds/
# ---------------------------------------------------------------------------


class TestRoundCreate(_CreateTestCase):
    def _post(self, payload) -> object:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            return self.api.post("/api/rounds/", payload, format="json")

    def test_create_returns_201_detail_serializer(self) -> None:
        response = self._post({"team_red": self.red.pk, "team_blue": self.blue.pk})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        data = body["data"]
        self.assertEqual(set(data), _GAME_ROUND_DETAIL_KEYS)
        self.assertIsNone(data["match"])
        self.assertEqual(data["team_red"], self.red.pk)
        self.assertEqual(data["team_blue"], self.blue.pk)
        self.assertEqual(len(data["player_states"]), 12)

    def test_create_stamps_requesting_account(self) -> None:
        response = self._post({"team_red": self.red.pk, "team_blue": self.blue.pk})
        self.assertEqual(response.status_code, 201, response.content)
        game_round = GameRound.objects.get(pk=response.json()["data"]["id"])
        self.assertEqual(game_round.manager_id, self.user_a.pk)
        self.assertIsNone(game_round.match_id)

    def test_missing_fields_are_400_required(self) -> None:
        before = GameRound.objects.count()
        response = self._post({})
        self.assert_error_envelope(response, 400)
        self.assertEqual(
            sorted(_codes_fields(response), key=lambda cf: cf[1]),
            [("required", "team_blue"), ("required", "team_red")],
        )
        self.assertEqual(GameRound.objects.count(), before)

    def test_same_team_is_400_invalid_null_field(self) -> None:
        response = self._post({"team_red": self.blue.pk, "team_blue": self.blue.pk})
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "A team cannot play against itself!",
                    "field": None,
                }
            ],
        )

    def test_foreign_team_is_invalid_choice(self) -> None:
        foreign = self.foreign_team()
        response = self._post({"team_red": self.red.pk, "team_blue": foreign.pk})
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "team_blue")])

    def test_free_agents_team_is_invalid_choice(self) -> None:
        fa = self.free_agents_with_valid_roster()
        response = self._post({"team_red": fa.pk, "team_blue": self.blue.pk})
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "team_red")])

    def test_invalid_roster_team_is_invalid_choice(self) -> None:
        empty = Team.objects.create(name="Round No Slots")
        response = self._post({"team_red": self.red.pk, "team_blue": empty.pk})
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "team_blue")])

    def test_simulator_value_error_is_400_invalid(self) -> None:
        def _refuse(self, *args, **kwargs):
            raise ValueError("Single round refused.")

        before = GameRound.objects.count()
        with patch.object(BatchSimulator, "simulate_single_round_detailed", _refuse):
            response = self._post({"team_red": self.red.pk, "team_blue": self.blue.pk})
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(
            errors,
            [{"code": "invalid", "message": "Single round refused.", "field": None}],
        )
        self.assertEqual(GameRound.objects.count(), before)

    def test_non_object_json_body_is_parse_error(self) -> None:
        response = self._post(["not", "an", "object"])
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(errors[0]["code"], "parse_error")

    def test_detail_write_methods_still_405(self) -> None:
        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        url = f"/api/rounds/{game_round.pk}/"
        for method in ("put", "patch", "delete"):
            response = getattr(self.api, method)(url, {}, format="json")
            errors = self.assert_error_envelope(response, 405)
            self.assertEqual(errors[0]["code"], "method_not_allowed")
            self.assertIn("Allow", response.headers)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        response = APIClient().post(
            "/api/rounds/",
            {"team_red": self.red.pk, "team_blue": self.blue.pk},
            format="json",
        )
        errors = self.assert_error_envelope(response, 401)
        self.assertEqual(errors[0]["code"], "not_authenticated")
