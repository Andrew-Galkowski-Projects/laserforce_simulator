"""ANG-01b -- team + player head-to-head API (seam contract §5 + §6 #7/#8).

``GET /api/teams/head-to-head/`` binds ``TeamH2HParamsForm`` and returns
``build_team_h2h`` output plus flattened team refs; ``GET
/api/players/head-to-head/`` binds ``PlayerH2HParamsForm`` and returns
``build_player_h2h`` output plus flattened player refs. Validation runs BEFORE
the ownership lookup, so the same unknown id twice is a 400, not a 404.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import GameRound
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

User = get_user_model()

_FAST_TICKS = 40

_TEAM_H2H_KEYS = {
    "team_a",
    "team_b",
    "provenance",
    "date_from",
    "date_to",
    "match_record",
    "round_record",
    "score_margin",
    "avg_survivors",
    "top_impactful",
    "per_map_breakdown",
    "detail_list",
    "margin_series",
    "cumulative_wl_series",
}
_PLAYER_H2H_KEYS = {
    "player_a",
    "player_b",
    "role",
    "provenance",
    "date_from",
    "date_to",
    "round_record",
    "score_margin",
    "tag_stats",
    "per_role_breakdown",
    "per_map_breakdown",
    "detail_list",
    "margin_series",
    "cumulative_wl_series",
}


def _saved_round(red, blue, seed: int = 4242) -> GameRound:
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        rounds = BatchSimulator().save_games(red, blue, [[seed, False]], 1)
    return GameRound.objects.get(pk=rounds[0].pk)


class _H2HTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang01b-h2h-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, self.red_players = make_team_with_slots("H2HRed")
        self.blue, self.blue_players = make_team_with_slots("H2HBlue")

    def errors(self, response, status: int) -> list[dict]:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"errors", "meta"})
        return body["errors"]

    def codes_fields(self, response, status: int = 400):
        return [(e["code"], e["field"]) for e in self.errors(response, status)]

    def foreign_team(self) -> Team:
        team, _ = make_team_with_slots("H2HForeign")
        team.manager = self.user_b
        team.save()
        return team


class TestRouteNames(TestCase):
    def test_route_names(self) -> None:
        self.assertEqual(reverse("team-head-to-head"), "/api/teams/head-to-head/")
        self.assertEqual(reverse("player-head-to-head"), "/api/players/head-to-head/")


# ---------------------------------------------------------------------------
# Team head-to-head
# ---------------------------------------------------------------------------


class TestTeamH2H(_H2HTestCase):
    def h2h(self, **params):
        return self.api.get("/api/teams/head-to-head/", params)

    def test_empty_corpus_data_keys_and_defaults(self) -> None:
        response = self.h2h(team_a=self.red.pk, team_b=self.blue.pk)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        data = body["data"]
        self.assertEqual(set(data), _TEAM_H2H_KEYS)
        self.assertEqual(data["provenance"], "all")
        self.assertIsNone(data["date_from"])
        self.assertIsNone(data["date_to"])
        self.assertEqual(data["round_record"]["n"], 0)
        self.assertEqual(data["detail_list"], [])

    def test_team_refs_flattened(self) -> None:
        data = self.h2h(team_a=self.red.pk, team_b=self.blue.pk).json()["data"]
        self.assertEqual(data["team_a"], {"id": self.red.pk, "name": self.red.name})
        self.assertEqual(data["team_b"], {"id": self.blue.pk, "name": self.blue.name})

    def test_counts_a_played_round(self) -> None:
        _saved_round(self.red, self.blue)
        data = self.h2h(team_a=self.red.pk, team_b=self.blue.pk).json()["data"]
        self.assertEqual(data["round_record"]["n"], 1)
        self.assertEqual(len(data["detail_list"]), 1)
        self.assertEqual(len(data["margin_series"]), 1)

    def test_side_agnostic_pairing(self) -> None:
        _saved_round(self.blue, self.red)
        data = self.h2h(team_a=self.red.pk, team_b=self.blue.pk).json()["data"]
        self.assertEqual(data["round_record"]["n"], 1)

    def test_provenance_and_dates_echoed(self) -> None:
        data = self.h2h(
            team_a=self.red.pk,
            team_b=self.blue.pk,
            provenance="sim",
            **{"from": "2020-01-02", "to": "2099-12-31"},
        ).json()["data"]
        self.assertEqual(data["provenance"], "sim")
        self.assertEqual(data["date_from"], "2020-01-02")
        self.assertEqual(data["date_to"], "2099-12-31")

    def test_date_range_excludes_rounds(self) -> None:
        _saved_round(self.red, self.blue)
        data = self.h2h(
            team_a=self.red.pk, team_b=self.blue.pk, **{"to": "2000-01-01"}
        ).json()["data"]
        self.assertEqual(data["round_record"]["n"], 0)

    def test_not_swallowed_by_pk_route(self) -> None:
        """No params -> the action's 400, never the detail route's 404."""
        self.assertEqual(
            sorted(self.codes_fields(self.h2h()), key=lambda cf: cf[1]),
            [("required", "team_a"), ("required", "team_b")],
        )

    def test_non_int_is_invalid(self) -> None:
        self.assertEqual(
            self.codes_fields(self.h2h(team_a="x", team_b=self.blue.pk)),
            [("invalid", "team_a")],
        )

    def test_below_one_is_min_value(self) -> None:
        self.assertEqual(
            self.codes_fields(self.h2h(team_a=self.red.pk, team_b=-3)),
            [("min_value", "team_b")],
        )

    def test_bad_provenance_is_invalid_choice(self) -> None:
        self.assertEqual(
            self.codes_fields(
                self.h2h(team_a=self.red.pk, team_b=self.blue.pk, provenance="fake")
            ),
            [("invalid_choice", "provenance")],
        )

    def test_bad_from_date_is_invalid(self) -> None:
        self.assertEqual(
            self.codes_fields(
                self.h2h(team_a=self.red.pk, team_b=self.blue.pk, **{"from": "nope"})
            ),
            [("invalid", "from")],
        )

    def test_bad_to_date_is_invalid(self) -> None:
        self.assertEqual(
            self.codes_fields(
                self.h2h(
                    team_a=self.red.pk, team_b=self.blue.pk, **{"to": "31/12/2020"}
                )
            ),
            [("invalid", "to")],
        )

    def test_same_team_is_invalid_null_field(self) -> None:
        errors = self.errors(self.h2h(team_a=self.red.pk, team_b=self.red.pk), 400)
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "Pick two different teams to compare.",
                    "field": None,
                }
            ],
        )

    def test_same_unknown_id_twice_is_400_not_404(self) -> None:
        errors = self.errors(self.h2h(team_a=999999, team_b=999999), 400)
        self.assertEqual((errors[0]["code"], errors[0]["field"]), ("invalid", None))

    def test_unknown_team_is_404(self) -> None:
        errors = self.errors(self.h2h(team_a=self.red.pk, team_b=999999), 404)
        self.assertEqual(errors[0]["code"], "not_found")

    def test_foreign_team_is_404(self) -> None:
        foreign = self.foreign_team()
        errors = self.errors(self.h2h(team_a=foreign.pk, team_b=self.blue.pk), 404)
        self.assertEqual(errors[0]["code"], "not_found")
        self.assertIsNone(errors[0]["field"])

    def test_anonymous_is_403(self) -> None:
        response = APIClient().get(
            "/api/teams/head-to-head/", {"team_a": self.red.pk, "team_b": self.blue.pk}
        )
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Player head-to-head
# ---------------------------------------------------------------------------


class TestPlayerH2H(_H2HTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.pa = self.red_players["commander"]
        self.pb = self.blue_players["commander"]

    def h2h(self, **params):
        return self.api.get("/api/players/head-to-head/", params)

    def test_empty_corpus_zeroed_aggregates(self) -> None:
        response = self.h2h(player_a=self.pa.pk, player_b=self.pb.pk)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(set(data), _PLAYER_H2H_KEYS)
        self.assertEqual(data["round_record"]["n"], 0)
        self.assertEqual(data["detail_list"], [])
        self.assertEqual(data["margin_series"], [])

    def test_defaults(self) -> None:
        data = self.h2h(player_a=self.pa.pk, player_b=self.pb.pk).json()["data"]
        self.assertIsNone(data["role"])
        self.assertEqual(data["provenance"], "all")
        self.assertIsNone(data["date_from"])
        self.assertIsNone(data["date_to"])

    def test_player_refs_flattened_with_team_ref(self) -> None:
        data = self.h2h(player_a=self.pa.pk, player_b=self.pb.pk).json()["data"]
        self.assertEqual(
            data["player_a"],
            {
                "id": self.pa.pk,
                "name": self.pa.name,
                "team": {"id": self.red.pk, "name": self.red.name},
            },
        )
        self.assertEqual(
            data["player_b"]["team"], {"id": self.blue.pk, "name": self.blue.name}
        )

    def test_counts_opposing_round(self) -> None:
        _saved_round(self.red, self.blue)
        data = self.h2h(player_a=self.pa.pk, player_b=self.pb.pk).json()["data"]
        self.assertEqual(data["round_record"]["n"], 1)
        self.assertEqual(len(data["detail_list"]), 1)

    def test_same_team_round_excluded(self) -> None:
        _saved_round(self.red, self.blue)
        teammate = self.red_players["heavy"]
        data = self.h2h(player_a=self.pa.pk, player_b=teammate.pk).json()["data"]
        self.assertEqual(data["round_record"]["n"], 0)

    def test_role_echoed(self) -> None:
        data = self.h2h(player_a=self.pa.pk, player_b=self.pb.pk, role="heavy").json()[
            "data"
        ]
        self.assertEqual(data["role"], "heavy")

    def test_bad_role_is_invalid_choice(self) -> None:
        self.assertEqual(
            self.codes_fields(
                self.h2h(player_a=self.pa.pk, player_b=self.pb.pk, role="goalie")
            ),
            [("invalid_choice", "role")],
        )

    def test_bad_provenance_is_invalid_choice(self) -> None:
        self.assertEqual(
            self.codes_fields(
                self.h2h(player_a=self.pa.pk, player_b=self.pb.pk, provenance="x")
            ),
            [("invalid_choice", "provenance")],
        )

    def test_bad_date_is_invalid(self) -> None:
        self.assertEqual(
            self.codes_fields(
                self.h2h(player_a=self.pa.pk, player_b=self.pb.pk, **{"from": "bad"})
            ),
            [("invalid", "from")],
        )

    def test_missing_player_b_is_required(self) -> None:
        self.assertEqual(
            self.codes_fields(self.h2h(player_a=self.pa.pk)),
            [("required", "player_b")],
        )

    def test_non_int_and_min_value(self) -> None:
        self.assertEqual(
            self.codes_fields(self.h2h(player_a="q", player_b=0)),
            [("invalid", "player_a"), ("min_value", "player_b")],
        )

    def test_same_player_is_invalid_null_field(self) -> None:
        errors = self.errors(self.h2h(player_a=self.pa.pk, player_b=self.pa.pk), 400)
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "Pick two different players to compare.",
                    "field": None,
                }
            ],
        )

    def test_same_unknown_id_twice_is_400_not_404(self) -> None:
        errors = self.errors(self.h2h(player_a=888888, player_b=888888), 400)
        self.assertEqual((errors[0]["code"], errors[0]["field"]), ("invalid", None))

    def test_unknown_player_is_404(self) -> None:
        errors = self.errors(self.h2h(player_a=self.pa.pk, player_b=888888), 404)
        self.assertEqual(errors[0]["code"], "not_found")

    def test_foreign_player_is_404(self) -> None:
        foreign_team = self.foreign_team()
        foreign_player = Player.objects.filter(team=foreign_team).first()
        errors = self.errors(
            self.h2h(player_a=foreign_player.pk, player_b=self.pb.pk), 404
        )
        self.assertEqual(errors[0]["code"], "not_found")

    def test_not_swallowed_by_pk_route(self) -> None:
        response = self.h2h()
        self.assertEqual(response.status_code, 400, response.content)
