"""ANG-01b -- ``GET /api/rounds/compare/`` (seam contract §5 + §6 #6).

Params bind ``CompareRoundsParamsForm`` (strict ints, distinct ids). The view
resolves both rounds through ``get_owned_or_404`` AFTER validation, rejects a
pair with no shared Team, then returns ``build_round_comparison`` output
(verbatim, JSON-encoded) plus flattened round refs and ``stat_keys``.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.utils.encoders import JSONEncoder

from conftest import get_shared_manager
from matches.models import GameRound
from matches.round_comparison import COMPARE_STAT_KEYS
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

User = get_user_model()

_FAST_TICKS = 40

_DATA_KEYS = {"round_a", "round_b", "stat_keys", "team_ids", "points_series", "deltas"}
_ROUND_REF_KEYS = {
    "id",
    "round_number",
    "match_id",
    "date_played",
    "team_red",
    "team_blue",
}
_SERIES_KEYS = {"team_id", "team_name", "a", "b"}


def _json_roundtrip(value):
    return json.loads(json.dumps(value, cls=JSONEncoder))


def _saved_round(red, blue, seed: int) -> GameRound:
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        rounds = BatchSimulator().save_games(red, blue, [[seed, False]], 1)
    return GameRound.objects.get(pk=rounds[0].pk)


class _CompareTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang01b-compare-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, _ = make_team_with_slots("CmpRed")
        self.blue, _ = make_team_with_slots("CmpBlue")
        self.green, _ = make_team_with_slots("CmpGreen")
        self.round_a = _saved_round(self.red, self.blue, 111)
        self.round_b = _saved_round(self.red, self.green, 222)

    def compare(self, **params):
        return self.api.get("/api/rounds/compare/", params)

    def errors(self, response, status: int) -> list[dict]:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"errors", "meta"})
        return body["errors"]

    def codes_fields(self, response, status: int = 400):
        return [(e["code"], e["field"]) for e in self.errors(response, status)]


class TestCompareRoute(TestCase):
    def test_route_name(self) -> None:
        self.assertEqual(reverse("gameround-compare"), "/api/rounds/compare/")


class TestCompareHappyPath(_CompareTestCase):
    def test_data_key_set(self) -> None:
        response = self.compare(round_a=self.round_a.pk, round_b=self.round_b.pk)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertEqual(set(body["data"]), _DATA_KEYS)

    def test_round_refs_are_flattened(self) -> None:
        data = self.compare(round_a=self.round_a.pk, round_b=self.round_b.pk).json()[
            "data"
        ]
        for key, game_round in (("round_a", self.round_a), ("round_b", self.round_b)):
            ref = data[key]
            self.assertEqual(set(ref), _ROUND_REF_KEYS)
            self.assertEqual(ref["id"], game_round.pk)
            self.assertEqual(ref["round_number"], game_round.round_number)
            self.assertIsNone(ref["match_id"])
            self.assertIsInstance(ref["date_played"], str)
            self.assertEqual(
                ref["team_red"],
                {"id": game_round.team_red_id, "name": game_round.team_red.name},
            )
            self.assertEqual(
                ref["team_blue"],
                {"id": game_round.team_blue_id, "name": game_round.team_blue.name},
            )

    def test_stat_keys_and_shared_team(self) -> None:
        data = self.compare(round_a=self.round_a.pk, round_b=self.round_b.pk).json()[
            "data"
        ]
        self.assertEqual(data["stat_keys"], list(COMPARE_STAT_KEYS))
        self.assertEqual(data["team_ids"], [self.red.pk])
        self.assertEqual(len(data["points_series"]), 1)
        series = data["points_series"][0]
        self.assertEqual(set(series), _SERIES_KEYS)
        self.assertEqual(series["team_id"], self.red.pk)
        self.assertEqual(series["team_name"], self.red.name)

    def test_payload_equals_build_round_comparison(self) -> None:
        from matches.services import build_round_comparison

        data = self.compare(round_a=self.round_a.pk, round_b=self.round_b.pk).json()[
            "data"
        ]
        expected = build_round_comparison(self.round_a, self.round_b, self.user_a)
        self.assertEqual(data["deltas"], _json_roundtrip(expected["deltas"]))
        self.assertEqual(
            data["points_series"], _json_roundtrip(expected["points_series"])
        )
        self.assertEqual(data["team_ids"], expected["team_ids"])

    def test_both_teams_shared_when_same_pairing(self) -> None:
        other = _saved_round(self.blue, self.red, 333)
        data = self.compare(round_a=self.round_a.pk, round_b=other.pk).json()["data"]
        self.assertEqual(data["team_ids"], sorted([self.red.pk, self.blue.pk]))
        self.assertEqual(len(data["points_series"]), 2)


class TestCompareErrors(_CompareTestCase):
    def test_missing_round_b_is_required(self) -> None:
        self.assertEqual(
            self.codes_fields(self.compare(round_a=self.round_a.pk)),
            [("required", "round_b")],
        )

    def test_missing_both_is_required_twice(self) -> None:
        self.assertEqual(
            sorted(self.codes_fields(self.compare()), key=lambda cf: cf[1]),
            [("required", "round_a"), ("required", "round_b")],
        )

    def test_non_int_is_invalid(self) -> None:
        self.assertEqual(
            self.codes_fields(self.compare(round_a="abc", round_b=self.round_b.pk)),
            [("invalid", "round_a")],
        )

    def test_below_one_is_min_value(self) -> None:
        self.assertEqual(
            self.codes_fields(self.compare(round_a=self.round_a.pk, round_b=0)),
            [("min_value", "round_b")],
        )

    def test_same_round_is_invalid_null_field(self) -> None:
        errors = self.errors(
            self.compare(round_a=self.round_a.pk, round_b=self.round_a.pk), 400
        )
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "Pick two different rounds to compare.",
                    "field": None,
                }
            ],
        )

    def test_same_unknown_id_twice_is_400_not_404(self) -> None:
        errors = self.errors(self.compare(round_a=999999, round_b=999999), 400)
        self.assertEqual(errors[0]["code"], "invalid")
        self.assertIsNone(errors[0]["field"])

    def test_no_shared_team_is_invalid(self) -> None:
        yellow, _ = make_team_with_slots("CmpYellow")
        purple, _ = make_team_with_slots("CmpPurple")
        unrelated = GameRound.objects.create(
            round_number=1, team_red=yellow, team_blue=purple
        )
        errors = self.errors(
            self.compare(round_a=self.round_a.pk, round_b=unrelated.pk), 400
        )
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "These rounds share no team, so there is nothing to compare.",
                    "field": None,
                }
            ],
        )

    def test_unknown_round_is_404(self) -> None:
        errors = self.errors(self.compare(round_a=self.round_a.pk, round_b=999999), 404)
        self.assertEqual(errors[0]["code"], "not_found")

    def test_foreign_round_is_404(self) -> None:
        foreign = GameRound.objects.create(
            round_number=1,
            team_red=self.red,
            team_blue=self.blue,
            manager=self.user_b,
        )
        errors = self.errors(
            self.compare(round_a=self.round_a.pk, round_b=foreign.pk), 404
        )
        self.assertEqual(errors[0]["code"], "not_found")
        self.assertIsNone(errors[0]["field"])

    def test_unowned_shared_team_is_404(self) -> None:
        """Two Unmanaged rounds that share a Team owned by another Account."""
        theirs, _ = make_team_with_slots("CmpTheirs")
        theirs.manager = self.user_b
        theirs.save()
        r1 = GameRound.objects.create(
            round_number=1, team_red=theirs, team_blue=self.blue
        )
        r2 = GameRound.objects.create(
            round_number=1, team_red=theirs, team_blue=self.green
        )
        errors = self.errors(self.compare(round_a=r1.pk, round_b=r2.pk), 404)
        self.assertEqual(errors[0]["code"], "not_found")

    def test_post_is_405(self) -> None:
        response = self.api.post("/api/rounds/compare/", {}, format="json")
        self.assertEqual(response.status_code, 405)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        response = APIClient().get(
            "/api/rounds/compare/",
            {"round_a": self.round_a.pk, "round_b": self.round_b.pk},
        )
        self.assertEqual(response.status_code, 401)
