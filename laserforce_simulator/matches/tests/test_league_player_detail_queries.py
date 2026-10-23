"""PERF-01 T4 -- ``build_player_detail`` runs a constant number of queries.

The League player page (API ``league-player`` action and the HTML screen) used
to run a PlayerRoundState query, an ``exists()`` and an award-set read per
Season. These tests pin the batched path: the query count does not grow with
the Seasons the Player played, and a Player who played only some Seasons gets
exactly the per-Season rows and awards badges the per-Season path gave.
"""

from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from matches.league_screen_services import build_player_detail
from matches.league_services import _player_award_labels, compute_season_award_set
from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.tests.conftest import make_team_with_slots
from matches.tests.league_api_fixtures import add_round_states
from teams.models import Player, Team

ROW_KEYS = {"year", "season_id", "team_name", "team_id", "games", "stats"}


def _completed_season(league: League, name: str, teams: list[Team]) -> Season:
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=date(2025, 1, 1),
        schedule_format="single_round_robin",
        state="completed",
        starting_team_ids_json=sorted(t.id for t in teams),
    )
    season.teams.add(*teams)
    return season


def _round(
    season: Season,
    red: Team,
    blue: Team,
    states: list[tuple[Player, str, dict]],
) -> None:
    match = Match.objects.create(
        team_red=red, team_blue=blue, season=season, is_completed=True
    )
    game_round = GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=red,
        team_blue=blue,
        red_points=100,
        blue_points=80,
        is_completed=True,
    )
    for player, color, kwargs in states:
        PlayerRoundState.objects.create(
            game_round=game_round, player=player, team_color=color, **kwargs
        )


def _count(fn) -> int:
    with CaptureQueriesContext(connection) as ctx:
        fn()
    return len(ctx.captured_queries)


class TestPlayerDetailQueryCount(TestCase):
    """The query count is the same for 1 Season played as for 4."""

    def setUp(self) -> None:
        self.league = League.objects.create(name="PerfT4 League")
        self.team_a, _ = make_team_with_slots("PerfT4A")
        self.team_b, _ = make_team_with_slots("PerfT4B")
        self.player = self.team_a.slot_commander
        self._add_season()

    def _add_season(self) -> None:
        n = Season.objects.filter(league=self.league).count() + 1
        season = _completed_season(self.league, f"S{n}", [self.team_a, self.team_b])
        add_round_states(season, self.team_a, self.team_b)
        add_round_states(season, self.team_b, self.team_a)

    def _warm_count(self, fn) -> int:
        fn()  # first read writes the completed Seasons' summary snapshots
        return _count(fn)

    def test_build_player_detail_constant_in_seasons_played(self) -> None:
        def call() -> None:
            build_player_detail(self.league, self.player)

        small = self._warm_count(call)
        for _ in range(3):
            self._add_season()
        large = self._warm_count(call)
        self.assertEqual(len(build_player_detail(self.league, self.player).rs_rows), 4)
        self.assertEqual(small, large)

    def test_html_player_page_constant_in_seasons_played(self) -> None:
        url = reverse("league_player_detail", args=[self.league.id, self.player.id])

        def call() -> None:
            self.assertEqual(self.client.get(url).status_code, 200)

        small = self._warm_count(call)
        for _ in range(3):
            self._add_season()
        large = self._warm_count(call)
        self.assertEqual(small, large)


class TestPlayerDetailSomeSeasons(TestCase):
    """A Player who skipped the middle Season: rows and awards only for the
    Seasons played, newest first, with the Team he played for in each."""

    def setUp(self) -> None:
        self.league = League.objects.create(name="PerfT4 Some")
        self.team_a, _ = make_team_with_slots("SomeA")
        self.team_b, _ = make_team_with_slots("SomeB")
        self.team_c, _ = make_team_with_slots("SomeC")
        self.star = self.team_a.slot_commander
        rival = self.team_b.slot_commander
        teams = [self.team_a, self.team_b, self.team_c]
        self.s1 = _completed_season(self.league, "S1", teams)
        self.s2 = _completed_season(self.league, "S2", teams)
        self.s3 = _completed_season(self.league, "S3", teams)
        # S1: the star plays for Team A (two Rounds) and tops Most Points.
        for _ in range(2):
            _round(
                self.s1,
                self.team_a,
                self.team_b,
                [
                    (self.star, "red", {"role": "commander", "points_scored": 9000}),
                    (rival, "blue", {"role": "commander", "points_scored": 10}),
                ],
            )
        # S2: the star sits out.
        _round(
            self.s2,
            self.team_b,
            self.team_c,
            [(rival, "red", {"role": "commander", "points_scored": 500})],
        )
        # S3: the star plays for Team C (blue side), one Round, and loses.
        _round(
            self.s3,
            self.team_b,
            self.team_c,
            [
                (rival, "red", {"role": "commander", "points_scored": 9000}),
                (self.star, "blue", {"role": "commander", "points_scored": 20}),
            ],
        )

    def test_rs_rows_only_for_seasons_played_newest_first(self) -> None:
        detail = build_player_detail(self.league, self.star)
        self.assertEqual(
            [r["season_id"] for r in detail.rs_rows], [self.s3.id, self.s1.id]
        )
        s3_row, s1_row = detail.rs_rows
        self.assertEqual((s3_row["team_id"], s3_row["games"]), (self.team_c.id, 1))
        self.assertEqual((s1_row["team_id"], s1_row["games"]), (self.team_a.id, 2))
        self.assertEqual(s1_row["stats"]["points_scored"], 18000)
        self.assertEqual(s3_row["stats"]["points_scored"], 20)
        for row in detail.rs_rows:
            self.assertEqual(set(row), ROW_KEYS)

    def test_career_row_aggregates_every_season(self) -> None:
        career = build_player_detail(self.league, self.star).career_row
        self.assertIsNotNone(career)
        self.assertEqual(set(career), ROW_KEYS)
        self.assertEqual(career["games"], 3)
        self.assertEqual(career["stats"]["points_scored"], 18020)
        # Last row wins: the most recent Round was for Team C.
        self.assertEqual(career["team_id"], self.team_c.id)

    def test_awards_match_per_season_award_sets(self) -> None:
        detail = build_player_detail(self.league, self.star)
        expected = []
        for season in (self.s3, self.s2, self.s1):
            if not PlayerRoundState.objects.filter(
                game_round__match__season=season, player=self.star
            ).exists():
                continue
            labels = _player_award_labels(
                compute_season_award_set(season), self.star.id
            )
            if labels:
                expected.append(
                    {
                        "season_id": season.id,
                        "season_name": season.name,
                        "award_labels": labels,
                    }
                )
        self.assertEqual(detail.player_awards, expected)
        by_season = {a["season_id"]: a["award_labels"] for a in expected}
        self.assertNotIn(self.s2.id, by_season)
        self.assertIn("Most Points", by_season[self.s1.id])
