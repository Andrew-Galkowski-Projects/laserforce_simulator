"""ANG-06d-A -- the read-only ``scoreboard`` on ``GET /api/rounds/<id>/`` and
``round_ids`` ordered by ``round_number`` on the Match API (spec #295,
ticket #296).

Every row is hand-built (no simulated totals), so the assertions pin real
values.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import GameRound, Match, PlayerRoundState
from matches.round_summary import PLAYER_ROW_KEYS
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

User = get_user_model()


class _ScoreboardTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang06d-scoreboard-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, self.red_players = make_team_with_slots("SbRed")
        self.blue, self.blue_players = make_team_with_slots("SbBlue")
        self.game_round = GameRound.objects.create(
            round_number=1,
            team_red=self.red,
            team_blue=self.blue,
            manager=self.user_a,
            red_points=4000,
            blue_points=1000,
            is_completed=True,
        )

    def state(
        self, player: Player, color: str, role: str, points: int, lives: int = 3
    ) -> PlayerRoundState:
        return PlayerRoundState.objects.create(
            game_round=self.game_round,
            player=player,
            team_color=color,
            role=role,
            points_scored=points,
            final_lives=lives,
        )

    def scoreboard(self) -> dict:
        response = self.api.get(f"/api/rounds/{self.game_round.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]["scoreboard"]


class TestScoreboardShape(_ScoreboardTestCase):
    def test_detail_carries_scoreboard_with_both_sides(self) -> None:
        self.state(self.red_players["commander"], "red", "commander", 3000)
        self.state(self.blue_players["heavy"], "blue", "heavy", 1000)
        board = self.scoreboard()
        self.assertEqual(set(board), {"red", "blue"})
        for side in ("red", "blue"):
            self.assertEqual(
                set(board[side]), {"players", "survivors", "eliminated", "elim_bonus"}
            )

    def test_list_rows_have_no_scoreboard(self) -> None:
        rows = self.api.get("/api/rounds/").json()["data"]
        row = next(r for r in rows if r["id"] == self.game_round.pk)
        self.assertNotIn("scoreboard", row)

    def test_row_is_the_28_keys_plus_player_id(self) -> None:
        cmd = self.red_players["commander"]
        self.state(cmd, "red", "commander", 3000)
        row = self.scoreboard()["red"]["players"][0]
        self.assertEqual(set(row), set(PLAYER_ROW_KEYS) | {"player_id"})
        self.assertEqual(len(row), 29)
        self.assertEqual(row["player_id"], cmd.pk)
        self.assertEqual(row["name"], cmd.name)
        self.assertEqual(row["points_scored"], 3000)

    def test_rows_grouped_by_team_color_with_a_borrowed_player(self) -> None:
        """LG-07: a drawn-team round borrows a Player whose ``Player.team`` is
        neither Side; the row still lands on the Side it played."""
        other = Team.objects.create(name="SbOther Team")
        borrowed = Player.objects.create(team=other, name="SbBorrowed")
        self.state(borrowed, "red", "scout", 500)
        self.state(self.red_players["heavy"], "blue", "heavy", 700)
        board = self.scoreboard()
        self.assertEqual([r["name"] for r in board["red"]["players"]], ["SbBorrowed"])
        self.assertEqual([r["name"] for r in board["blue"]["players"]], ["SbRed heavy"])

    def test_rows_ordered_by_points_desc_then_role_then_name(self) -> None:
        p = self.red_players
        self.state(p["scout"], "red", "scout", 1000)
        self.state(p["scout_2"], "red", "scout", 1000)
        self.state(p["ammo"], "red", "ammo", 1000)
        self.state(p["commander"], "red", "commander", 5000)
        names = [r["name"] for r in self.scoreboard()["red"]["players"]]
        self.assertEqual(
            names, ["SbRed commander", "SbRed ammo", "SbRed scout1", "SbRed scout2"]
        )


class TestScoreboardSurvivorsAndBonus(_ScoreboardTestCase):
    def test_survivors_count_players_with_lives_left(self) -> None:
        self.state(self.red_players["commander"], "red", "commander", 3000, lives=4)
        self.state(self.red_players["heavy"], "red", "heavy", 2000, lives=0)
        self.state(self.red_players["medic"], "red", "medic", 100, lives=1)
        self.state(self.blue_players["heavy"], "blue", "heavy", 1000, lives=2)
        board = self.scoreboard()
        self.assertEqual(board["red"]["survivors"], 2)
        self.assertEqual(board["blue"]["survivors"], 1)
        self.assertFalse(board["red"]["eliminated"])
        self.assertFalse(board["blue"]["eliminated"])

    def test_elim_bonus_only_on_the_side_whose_opponent_is_eliminated(self) -> None:
        self.state(self.red_players["commander"], "red", "commander", 3000, lives=2)
        self.state(self.blue_players["heavy"], "blue", "heavy", 1000, lives=0)
        self.state(self.blue_players["medic"], "blue", "medic", 500, lives=0)
        board = self.scoreboard()
        self.assertTrue(board["blue"]["eliminated"])
        self.assertFalse(board["red"]["eliminated"])
        self.assertEqual(board["red"]["elim_bonus"], BatchSimulator.elimination_bonus)
        self.assertEqual(board["blue"]["elim_bonus"], 0)
        self.assertEqual(board["blue"]["survivors"], 0)

    def test_no_bonus_when_neither_side_is_eliminated(self) -> None:
        self.state(self.red_players["commander"], "red", "commander", 3000, lives=2)
        self.state(self.blue_players["heavy"], "blue", "heavy", 1000, lives=1)
        board = self.scoreboard()
        self.assertEqual(board["red"]["elim_bonus"], 0)
        self.assertEqual(board["blue"]["elim_bonus"], 0)

    def test_a_side_with_no_rows_is_not_eliminated(self) -> None:
        """A Side with no player rows (e.g. a round with missing states) is not
        a wipe, so the other Side earns no elimination bonus."""
        self.state(self.red_players["commander"], "red", "commander", 3000, lives=2)
        board = self.scoreboard()
        self.assertEqual(board["blue"]["players"], [])
        self.assertFalse(board["blue"]["eliminated"])
        self.assertEqual(board["red"]["elim_bonus"], 0)

    def test_round_page_shows_no_bonus_against_an_empty_side(self) -> None:
        self.state(self.red_players["commander"], "red", "commander", 3000, lives=2)
        self.client.force_login(self.user_a)
        response = self.client.get(
            reverse("game_round_detail", args=[self.game_round.pk])
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("14000", html)
        self.assertNotIn("(10000)", html)
        self.assertNotIn("Eliminated!", html)


class TestQueryCounts(_ScoreboardTestCase):
    """The list / retrieve query counts do not grow with the row count."""

    def _count(self, url: str) -> int:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            response = self.api.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        return len(ctx.captured_queries)

    def _add_match(self) -> None:
        match = Match.objects.create(
            team_red=self.red, team_blue=self.blue, manager=self.user_a
        )
        for number in (1, 2):
            GameRound.objects.create(
                match=match,
                round_number=number,
                team_red=self.red,
                team_blue=self.blue,
                manager=self.user_a,
            )

    def test_match_list_is_constant_in_the_match_count(self) -> None:
        self._add_match()
        one = self._count("/api/matches/")
        for _ in range(3):
            self._add_match()
        self.assertEqual(self._count("/api/matches/"), one)

    def test_round_retrieve_is_constant_in_the_player_count(self) -> None:
        url = f"/api/rounds/{self.game_round.pk}/"
        self.state(self.red_players["commander"], "red", "commander", 3000)
        self.state(self.blue_players["heavy"], "blue", "heavy", 1000)
        two = self._count(url)
        for role in ("scout", "ammo", "medic"):
            self.state(self.red_players[role], "red", role, 500)
            self.state(self.blue_players[role], "blue", role, 500)
        self.assertEqual(self._count(url), two)
        # The scoreboard reads the ``player_states`` prefetch: no per-Side
        # queries on top of session + user + round + prefetch.
        self.assertEqual(two, 4)

    def test_prefetched_rows_keep_the_scoreboard_order(self) -> None:
        p = self.red_players
        self.state(p["scout_2"], "red", "scout", 1000)
        self.state(p["scout"], "red", "scout", 1000)
        self.state(p["ammo"], "red", "ammo", 1000)
        self.state(p["commander"], "red", "commander", 5000)
        names = [r["name"] for r in self.scoreboard()["red"]["players"]]
        self.assertEqual(
            names, ["SbRed commander", "SbRed ammo", "SbRed scout1", "SbRed scout2"]
        )


class TestScoreboardOwnership(_ScoreboardTestCase):
    def test_cross_account_round_is_404(self) -> None:
        foreign = GameRound.objects.create(
            round_number=1,
            team_red=self.red,
            team_blue=self.blue,
            manager=self.user_b,
            is_completed=True,
        )
        response = self.api.get(f"/api/rounds/{foreign.pk}/")
        self.assertEqual(response.status_code, 404)


class TestMatchRoundIdsOrder(_ScoreboardTestCase):
    def test_round_ids_follow_round_number_not_id(self) -> None:
        match = Match.objects.create(
            team_red=self.red, team_blue=self.blue, manager=self.user_a
        )
        # Round 2 is created first, so it has the lower id.
        round_2 = GameRound.objects.create(
            match=match, round_number=2, team_red=self.blue, team_blue=self.red
        )
        round_1 = GameRound.objects.create(
            match=match, round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.assertLess(round_2.pk, round_1.pk)
        data = self.api.get(f"/api/matches/{match.pk}/").json()["data"]
        self.assertEqual(data["round_ids"], [round_1.pk, round_2.pk])
