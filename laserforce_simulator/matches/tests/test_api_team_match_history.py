"""ANG-06-9 T1 -- ``GET /api/teams/<id>/history/`` (Team match history).

A detail action on ``TeamViewSet`` over ``build_team_match_history``. The
Team resolves through ``get_object()`` (cross-Account 404); its Matches and
Rounds are filtered through ``owned_match_q`` / ``owned_game_round_q``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from laserforce_simulator.testing import assert_owner_only
from matches.models import (
    BracketNode,
    GameRound,
    Match,
    PlayerRoundState,
    SeriesMatch,
    Tournament,
    TournamentPlayerEntry,
)
from teams.models import Player, Team, get_free_agents_team

User = get_user_model()

_DATA_KEYS = {
    "team",
    "scope_options",
    "show_scope_filter",
    "has_any_games",
    "stats",
    "role_columns",
    "roster",
    "opponents",
    "matches",
    "single_rounds",
}


def _at(day: int) -> datetime:
    return datetime(2026, 3, day, 12, 0, tzinfo=timezone.utc)


def _match(red: Team, blue: Team, *, day: int = 1, **fields) -> Match:
    match = Match.objects.create(team_red=red, team_blue=blue, **fields)
    Match.objects.filter(pk=match.pk).update(date_played=_at(day))
    match.refresh_from_db()
    return match


def _round(red: Team, blue: Team, *, day: int = 1, **fields) -> GameRound:
    game_round = GameRound.objects.create(
        team_red=red, team_blue=blue, round_number=1, is_completed=True, **fields
    )
    GameRound.objects.filter(pk=game_round.pk).update(date_played=_at(day))
    game_round.refresh_from_db()
    return game_round


def _state(game_round: GameRound, player: Player, *, color: str, role: str, **stats):
    return PlayerRoundState.objects.create(
        game_round=game_round, player=player, team_color=color, role=role, **stats
    )


def _tournament_match(red: Team, blue: Team, name: str, **fields) -> tuple:
    match = _match(red, blue, **fields)
    tourney = Tournament.objects.create(name=name)
    node = BracketNode.objects.create(tournament=tourney, bracket_round=1, position=0)
    SeriesMatch.objects.create(node=node, match=match, game_number=1)
    return match, tourney


class _HistoryTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang069-history-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user_a)
        self.team = Team.objects.create(name="Hist", manager=self.user_a)
        self.foe = Team.objects.create(name="Foe")

    def history(self, team: Team | None = None, params=None):
        team = team or self.team
        return self.api.get(f"/api/teams/{team.pk}/history/", params or {})

    def data(self, team: Team | None = None, params=None) -> dict:
        response = self.history(team, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]


class TestRouteAndAccess(_HistoryTestCase):
    def test_route_name(self) -> None:
        self.assertEqual(
            reverse("team-history", args=[self.team.pk]),
            f"/api/teams/{self.team.pk}/history/",
        )

    def test_anonymous_is_401(self) -> None:
        response = APIClient().get(f"/api/teams/{self.team.pk}/history/")
        self.assertEqual(response.status_code, 401)

    def test_cross_account_team_is_404(self) -> None:
        assert_owner_only(
            f"/api/teams/{self.team.pk}/history/",
            owner=self.user_a,
            other=self.user_b,
        )

    def test_missing_team_is_404(self) -> None:
        response = self.api.get("/api/teams/999999/history/")
        self.assertEqual(response.status_code, 404)


class TestShape(_HistoryTestCase):
    def test_empty_team_shape(self) -> None:
        response = self.history()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        data = body["data"]
        self.assertEqual(set(data), _DATA_KEYS)
        self.assertEqual(data["team"], {"id": self.team.pk, "name": "Hist"})
        self.assertEqual(data["scope_options"], [])
        self.assertIs(data["show_scope_filter"], False)
        self.assertIs(data["has_any_games"], False)
        self.assertEqual(data["matches"], [])
        self.assertEqual(data["single_rounds"], [])
        self.assertEqual(data["opponents"], [])
        self.assertEqual(data["roster"], [])
        self.assertEqual(
            data["stats"],
            {
                "total_matches": 0,
                "match_wins": 0,
                "match_losses": 0,
                "match_ties": 0,
                "total_rounds": 0,
                "round_wins": 0,
                "round_losses": 0,
                "round_ties": 0,
            },
        )

    def test_role_columns(self) -> None:
        self.assertEqual(
            self.data()["role_columns"],
            [
                {"role": "commander", "label": "Commander"},
                {"role": "heavy", "label": "Heavy Weapons"},
                {"role": "scout", "label": "Scout"},
                {"role": "ammo", "label": "Ammo Carrier"},
                {"role": "medic", "label": "Medic"},
            ],
        )

    def test_match_row_from_blue_side(self) -> None:
        match = _match(
            self.foe,
            self.team,
            winner=self.team,
            is_completed=True,
            red_round1_points=100,
            blue_round1_points=300,
            red_round2_points=400,
            blue_round2_points=200,
            blue_bonus_points=10,
        )
        data = self.data()
        self.assertEqual(
            data["matches"],
            [
                {
                    "id": match.pk,
                    "opponent": {"id": self.foe.pk, "name": "Foe"},
                    "result": "won",
                    "team_points": 510,
                    "opponent_points": 500,
                    "team_rounds_won": 1,
                    "opponent_rounds_won": 1,
                    "date_played": "2026-03-01T12:00:00Z",
                    "is_completed": True,
                }
            ],
        )
        self.assertIs(data["has_any_games"], True)

    def test_match_results_lost_and_null_winner_tie(self) -> None:
        _match(self.team, self.foe, day=1, winner=self.foe)
        _match(self.team, self.foe, day=2)
        data = self.data()
        self.assertEqual([m["result"] for m in data["matches"]], ["tie", "lost"])
        stats = data["stats"]
        self.assertEqual(
            (
                stats["total_matches"],
                stats["match_wins"],
                stats["match_losses"],
                stats["match_ties"],
            ),
            (2, 0, 1, 1),
        )

    def test_single_round_row_and_round_stats(self) -> None:
        game_round = _round(
            self.team, self.foe, red_points=4000, blue_points=3000, winner=self.team
        )
        _round(self.foe, self.team, day=2, red_points=5, blue_points=2)
        _round(self.foe, self.team, day=3, red_points=3, blue_points=3)
        data = self.data()
        self.assertEqual(len(data["single_rounds"]), 3)
        self.assertEqual(
            data["single_rounds"][2],
            {
                "id": game_round.pk,
                "opponent": {"id": self.foe.pk, "name": "Foe"},
                "result": "won",
                "team_points": 4000,
                "opponent_points": 3000,
                "date_played": "2026-03-01T12:00:00Z",
            },
        )
        # Django's Single Round cards badge Won / Lost / Tie as well.
        self.assertEqual(
            [r["result"] for r in data["single_rounds"]], ["tie", "lost", "won"]
        )
        blue_side = data["single_rounds"][1]
        self.assertEqual(
            (blue_side["team_points"], blue_side["opponent_points"]), (2, 5)
        )
        stats = data["stats"]
        self.assertEqual(
            (
                stats["total_rounds"],
                stats["round_wins"],
                stats["round_losses"],
                stats["round_ties"],
            ),
            (3, 1, 1, 1),
        )

    def test_lists_are_newest_first(self) -> None:
        older = _match(self.team, self.foe, day=1)
        newer = _match(self.team, self.foe, day=5)
        r_old = _round(self.team, self.foe, day=2)
        r_new = _round(self.team, self.foe, day=6)
        data = self.data()
        self.assertEqual([m["id"] for m in data["matches"]], [newer.pk, older.pk])
        self.assertEqual([r["id"] for r in data["single_rounds"]], [r_new.pk, r_old.pk])

    def test_opponents_unique_and_sorted_by_name(self) -> None:
        zed = Team.objects.create(name="Zed")
        _match(self.team, zed)
        _match(self.team, self.foe)
        _round(self.foe, self.team)
        self.assertEqual(
            self.data()["opponents"],
            [{"id": self.foe.pk, "name": "Foe"}, {"id": zed.pk, "name": "Zed"}],
        )


class TestScopes(_HistoryTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.free = _match(self.team, self.foe, day=1)
        self.cup_match, self.cup = _tournament_match(
            self.team, self.foe, "Spring Cup", day=2
        )
        self.lone = _round(self.team, self.foe, day=3)

    def match_ids(self, params=None) -> set[int]:
        return {m["id"] for m in self.data(params=params)["matches"]}

    def test_absent_scope_selects_every_scope(self) -> None:
        data = self.data()
        self.assertEqual(
            data["scope_options"],
            [
                {"key": "freeform", "label": "Free-form matches", "selected": True},
                {"key": f"t{self.cup.pk}", "label": "Spring Cup", "selected": True},
            ],
        )
        self.assertIs(data["show_scope_filter"], True)
        self.assertEqual(self.match_ids(), {self.free.pk, self.cup_match.pk})
        self.assertEqual(len(data["single_rounds"]), 1)

    def test_repeated_scope_is_the_selection(self) -> None:
        data = self.data(params={"scope": [f"t{self.cup.pk}"]})
        self.assertEqual({m["id"] for m in data["matches"]}, {self.cup_match.pk})
        self.assertEqual(data["single_rounds"], [])
        self.assertEqual([o["selected"] for o in data["scope_options"]], [False, True])

    def test_freeform_scope_keeps_standalone_rounds(self) -> None:
        data = self.data(params={"scope": ["freeform"]})
        self.assertEqual({m["id"] for m in data["matches"]}, {self.free.pk})
        self.assertEqual([r["id"] for r in data["single_rounds"]], [self.lone.pk])

    def test_empty_scope_selects_none(self) -> None:
        response = self.api.get(f"/api/teams/{self.team.pk}/history/?scope=")
        data = response.json()["data"]
        self.assertEqual(data["matches"], [])
        self.assertEqual(data["single_rounds"], [])
        self.assertIs(data["has_any_games"], True)
        self.assertEqual([o["selected"] for o in data["scope_options"]], [False, False])

    def test_unknown_scope_keys_are_ignored(self) -> None:
        data = self.data(params={"scope": ["freeform", "t999999", "bogus"]})
        self.assertEqual({m["id"] for m in data["matches"]}, {self.free.pk})
        self.assertEqual(
            [o["key"] for o in data["scope_options"]],
            ["freeform", f"t{self.cup.pk}"],
        )

    def test_only_unknown_keys_select_none(self) -> None:
        self.assertEqual(self.match_ids({"scope": ["bogus"]}), set())

    def test_single_scope_hides_filter(self) -> None:
        lonely = Team.objects.create(name="Lonely")
        _match(lonely, self.foe)
        self.assertIs(self.data(lonely)["show_scope_filter"], False)


class TestRoster(_HistoryTestCase):
    def test_members_first_then_games_desc_then_name_with_merc_flag(self) -> None:
        bea = Player.objects.create(team=self.team, name="Bea")
        abe = Player.objects.create(team=self.team, name="Abe")
        idle = Player.objects.create(team=self.team, name="Cal")
        merc = Player.objects.create(team=self.foe, name="Aaron Merc")
        r1 = _round(self.team, self.foe, day=1)
        r2 = _round(self.foe, self.team, day=2)
        _state(r1, bea, color="red", role="commander", points_scored=8000)
        _state(r2, bea, color="blue", role="scout", points_scored=4000)
        _state(r1, abe, color="red", role="heavy", points_scored=3000)
        _state(r2, merc, color="blue", role="medic", points_scored=1000)
        _state(r2, merc, color="blue", role="medic", points_scored=1000)
        _state(r1, merc, color="red", role="ammo", points_scored=500)

        roster = self.data()["roster"]
        self.assertEqual(
            [(r["player"]["name"], r["games_played"], r["is_merc"]) for r in roster],
            [
                ("Bea", 2, False),
                ("Abe", 1, False),
                ("Cal", 0, False),
                ("Aaron Merc", 3, True),
            ],
        )
        self.assertEqual(roster[0]["player"], {"id": bea.pk, "name": "Bea"})
        self.assertEqual(roster[2]["player"]["id"], idle.pk)

    def test_role_cells_and_zero_fill(self) -> None:
        bea = Player.objects.create(team=self.team, name="Bea")
        idle = Player.objects.create(team=self.team, name="Cal")
        r1 = _round(self.team, self.foe, day=1)
        r2 = _round(self.team, self.foe, day=2)
        _state(r1, bea, color="red", role="commander", points_scored=8000)
        _state(r2, bea, color="red", role="commander", points_scored=6000)
        roster = {r["player"]["id"]: r for r in self.data()["roster"]}
        commander = roster[bea.pk]["roles"][0]
        self.assertEqual(commander["role"], "commander")
        self.assertEqual(commander["games"], 2)
        self.assertEqual(commander["avg_score"], 7000)
        self.assertIsInstance(commander["avg_mvp"], float)
        self.assertEqual(
            [c["role"] for c in roster[bea.pk]["roles"]],
            ["commander", "heavy", "scout", "ammo", "medic"],
        )
        self.assertEqual(
            roster[bea.pk]["roles"][1],
            {"role": "heavy", "games": 0, "avg_mvp": None, "avg_score": None},
        )
        self.assertEqual(roster[idle.pk]["games_played"], 0)
        self.assertTrue(
            all(
                c["games"] == 0 and c["avg_mvp"] is None
                for c in roster[idle.pk]["roles"]
            )
        )

    def test_opponent_side_state_excluded(self) -> None:
        ringer = Player.objects.create(team=self.foe, name="Ringer")
        r1 = _round(self.team, self.foe)
        _state(r1, ringer, color="blue", role="commander", points_scored=999)
        self.assertEqual(self.data()["roster"], [])

    def test_draw_team_roster_from_tournament_entries(self) -> None:
        draw = Team.objects.create(name="Draw", is_draw_team=True)
        pool = get_free_agents_team()
        drawn = Player.objects.create(team=pool, name="Drawn")
        benched = Player.objects.create(team=pool, name="Benched")
        tourney = Tournament.objects.create(name="Random Draw")
        for player in (drawn, benched):
            TournamentPlayerEntry.objects.create(
                tournament=tourney, player=player, drawn_team=draw
            )
        r1 = _round(draw, self.foe)
        _state(r1, drawn, color="red", role="scout", points_scored=5000)
        roster = self.data(draw)["roster"]
        self.assertEqual(
            [(r["player"]["name"], r["games_played"], r["is_merc"]) for r in roster],
            [("Drawn", 1, False), ("Benched", 0, False)],
        )

    def test_roster_follows_scope(self) -> None:
        bea = Player.objects.create(team=self.team, name="Bea")
        cup_match, cup = _tournament_match(self.team, self.foe, "Cup")
        in_cup = _round(self.team, self.foe, match=cup_match)
        _state(in_cup, bea, color="red", role="scout", points_scored=10)
        loose = _round(self.team, self.foe)
        _state(loose, bea, color="red", role="scout", points_scored=10)
        roster = self.data(params={"scope": [f"t{cup.pk}"]})["roster"]
        self.assertEqual(roster[0]["games_played"], 1)


class TestOwnership(_HistoryTestCase):
    """Another Account's games between Unmanaged Teams never leak."""

    def setUp(self) -> None:
        super().setUp()
        self.shared = Team.objects.create(name="Shared")
        self.player = Player.objects.create(team=self.shared, name="Sharer")
        self.mine = _match(self.shared, self.foe, day=1, manager=self.user_a)
        self.theirs = _match(self.shared, self.foe, day=2, manager=self.user_b)
        self.unmanaged = _match(self.shared, self.foe, day=3)
        self.my_round = _round(self.shared, self.foe, day=4, manager=self.user_a)
        self.their_round = _round(self.shared, self.foe, day=5, manager=self.user_b)
        their_match_round = _round(self.shared, self.foe, match=self.theirs)
        _state(their_match_round, self.player, color="red", role="scout")
        _state(self.their_round, self.player, color="red", role="scout")

    def test_other_accounts_match_and_rounds_absent(self) -> None:
        data = self.data(self.shared)
        self.assertEqual(
            {m["id"] for m in data["matches"]}, {self.mine.pk, self.unmanaged.pk}
        )
        self.assertEqual([r["id"] for r in data["single_rounds"]], [self.my_round.pk])
        self.assertEqual(data["roster"][0]["games_played"], 0)
        self.assertEqual(data["stats"]["total_matches"], 2)

    def test_only_other_accounts_games_means_no_games(self) -> None:
        lonely = Team.objects.create(name="Lonely")
        _match(lonely, self.foe, manager=self.user_b)
        _round(lonely, self.foe, manager=self.user_b)
        data = self.data(lonely)
        self.assertIs(data["has_any_games"], False)
        self.assertEqual(data["scope_options"], [])
