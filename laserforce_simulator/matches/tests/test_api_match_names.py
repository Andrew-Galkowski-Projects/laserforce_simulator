"""ANG-06c T1 -- names and totals on the Match / GameRound API, and the
``?standalone=`` filter on ``/api/rounds/`` (spec #289, ticket #290).

Additive read-only fields: ``team_red_name`` / ``team_blue_name`` /
``winner_name`` (null on a tie) on the Match and both GameRound serializers,
and the four Match totals from the existing ``Match`` properties.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import GameRound, Match
from matches.tests.conftest import make_team_with_slots

User = get_user_model()


class _NamesTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang06c-names-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, _ = make_team_with_slots("NamesRed")
        self.blue, _ = make_team_with_slots("NamesBlue")

    def split_match(self) -> Match:
        """Red wins round 1, Blue wins round 2; Blue takes it on points."""
        return Match.objects.create(
            team_red=self.red,
            team_blue=self.blue,
            manager=self.user_a,
            red_round1_points=5000,
            blue_round1_points=3000,
            red_round2_points=2000,
            blue_round2_points=6000,
            red_bonus_points=0,
            blue_bonus_points=10000,
            winner=self.blue,
            is_completed=True,
        )

    def standalone_round(self, manager=None, tie: bool = False) -> GameRound:
        """A completed Single Round; Red wins unless ``tie`` (save() derives
        the winner from the points)."""
        return GameRound.objects.create(
            round_number=1,
            team_red=self.red,
            team_blue=self.blue,
            manager=manager or self.user_a,
            red_points=4000,
            blue_points=4000 if tie else 1000,
            is_completed=True,
        )


class TestMatchNames(_NamesTestCase):
    def test_detail_carries_team_and_winner_names(self) -> None:
        match = self.split_match()
        data = self.api.get(f"/api/matches/{match.pk}/").json()["data"]
        self.assertEqual(data["team_red_name"], "NamesRed Team")
        self.assertEqual(data["team_blue_name"], "NamesBlue Team")
        self.assertEqual(data["winner_name"], "NamesBlue Team")

    def test_list_carries_team_and_winner_names(self) -> None:
        match = self.split_match()
        rows = self.api.get("/api/matches/").json()["data"]
        row = next(r for r in rows if r["id"] == match.pk)
        self.assertEqual(row["team_red_name"], "NamesRed Team")
        self.assertEqual(row["team_blue_name"], "NamesBlue Team")
        self.assertEqual(row["winner_name"], "NamesBlue Team")

    def test_winner_name_is_null_on_a_tie(self) -> None:
        match = Match.objects.create(
            team_red=self.red, team_blue=self.blue, manager=self.user_a
        )
        data = self.api.get(f"/api/matches/{match.pk}/").json()["data"]
        self.assertIsNone(data["winner"])
        self.assertIsNone(data["winner_name"])

    def test_totals_and_rounds_won_for_a_split_match(self) -> None:
        match = self.split_match()
        data = self.api.get(f"/api/matches/{match.pk}/").json()["data"]
        # Literals from the fixture: 5000 + 2000 + 0 and 3000 + 6000 + 10000.
        self.assertEqual(data["red_total_points"], 7000)
        self.assertEqual(data["blue_total_points"], 19000)
        self.assertEqual(data["red_rounds_won"], 1)
        self.assertEqual(data["blue_rounds_won"], 1)
        # And they are the model properties, not a re-derivation.
        self.assertEqual(data["red_total_points"], match.red_total_points)
        self.assertEqual(data["blue_total_points"], match.blue_total_points)
        self.assertEqual(data["red_rounds_won"], match.red_rounds_won)
        self.assertEqual(data["blue_rounds_won"], match.blue_rounds_won)

    def test_list_carries_totals(self) -> None:
        match = self.split_match()
        rows = self.api.get("/api/matches/").json()["data"]
        row = next(r for r in rows if r["id"] == match.pk)
        self.assertEqual(row["red_total_points"], 7000)
        self.assertEqual(row["blue_total_points"], 19000)
        self.assertEqual(row["red_rounds_won"], 1)
        self.assertEqual(row["blue_rounds_won"], 1)


class TestRoundNames(_NamesTestCase):
    def test_detail_carries_team_and_winner_names(self) -> None:
        game_round = self.standalone_round()
        data = self.api.get(f"/api/rounds/{game_round.pk}/").json()["data"]
        self.assertEqual(data["team_red_name"], "NamesRed Team")
        self.assertEqual(data["team_blue_name"], "NamesBlue Team")
        self.assertEqual(data["winner_name"], "NamesRed Team")

    def test_list_carries_team_and_winner_names(self) -> None:
        game_round = self.standalone_round()
        rows = self.api.get("/api/rounds/").json()["data"]
        row = next(r for r in rows if r["id"] == game_round.pk)
        self.assertEqual(row["team_red_name"], "NamesRed Team")
        self.assertEqual(row["team_blue_name"], "NamesBlue Team")
        self.assertEqual(row["winner_name"], "NamesRed Team")

    def test_winner_name_is_null_on_a_tie(self) -> None:
        game_round = self.standalone_round(tie=True)
        data = self.api.get(f"/api/rounds/{game_round.pk}/").json()["data"]
        self.assertIsNone(data["winner_name"])
        rows = self.api.get("/api/rounds/").json()["data"]
        row = next(r for r in rows if r["id"] == game_round.pk)
        self.assertIsNone(row["winner_name"])


class TestStandaloneFilter(_NamesTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.single = self.standalone_round()
        self.match = self.split_match()
        self.match_round = GameRound.objects.create(
            match=self.match, round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.foreign_single = self.standalone_round(manager=self.user_b)

    def ids(self, query: str) -> set[int]:
        response = self.api.get(f"/api/rounds/{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return {row["id"] for row in response.json()["data"]}

    def test_true_returns_only_single_rounds(self) -> None:
        ids = self.ids("?standalone=true")
        self.assertIn(self.single.pk, ids)
        self.assertNotIn(self.match_round.pk, ids)

    def test_false_returns_only_match_rounds(self) -> None:
        ids = self.ids("?standalone=false")
        self.assertIn(self.match_round.pk, ids)
        self.assertNotIn(self.single.pk, ids)

    def test_absent_returns_both(self) -> None:
        ids = self.ids("")
        self.assertIn(self.single.pk, ids)
        self.assertIn(self.match_round.pk, ids)

    def test_cross_account_single_round_never_appears(self) -> None:
        self.assertNotIn(self.foreign_single.pk, self.ids("?standalone=true"))
        self.assertNotIn(self.foreign_single.pk, self.ids("?standalone=false"))
