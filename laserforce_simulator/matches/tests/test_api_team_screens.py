"""ANG-01e T4 -- the team screens API (``/api/leagues/<id>/...``).

Seam 1 of the ANG-01e spec (#180), driven through DRF ``APIClient``:
``teams/<team_id>/history/``, ``teams/<team_id>/history/players/``,
``teams/<team_id>/roster/``, ``team-stats/``, ``power-rankings/`` and
``game-log/``. Asserts status codes, the envelope, ``meta``, error codes,
structure and ordering -- never simulated point totals (every Round is
hand-built with fixed points).
"""

from __future__ import annotations

from datetime import date

from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    add_round_states,
    make_completed_season,
    make_league,
    make_teams,
    ranked_career_league,
)


def _url(league_id: int, path: str) -> str:
    return f"/api/leagues/{league_id}/{path}"


class TeamRosterTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "TRos", 2, manager=self.user_a
        )
        self.team = self.teams[1]

    def roster_url(self, team_id: int, league_id: int | None = None) -> str:
        return _url(league_id or self.league.id, f"teams/{team_id}/roster/")

    def test_roster_lists_starters_by_role_and_bench(self) -> None:
        data = self.ok(self.api.get(self.roster_url(self.team.id)))
        self.assertEqual(set(data), {"team", "starters", "bench"})
        self.assertEqual(data["team"], {"id": self.team.id, "name": self.team.name})
        expected = [(role, p.id) for role, p in self.team.active_roster]
        self.assertEqual(
            [(row["role"], row["player"]["id"]) for row in data["starters"]], expected
        )
        for row in data["starters"]:
            self.assertEqual(set(row), {"role", "player", "watched"})
            self.assertFalse(row["watched"])
        self.assertEqual(
            [row["player"]["id"] for row in data["bench"]],
            [p.id for p in self.team.bench_players],
        )

    def test_roster_marks_watched_players(self) -> None:
        starter = self.team.active_players[0]
        self.ok(self.api.put(_url(self.league.id, f"watch-list/{starter.id}/")))
        data = self.ok(self.api.get(self.roster_url(self.team.id)))
        watched = {row["player"]["id"] for row in data["starters"] if row["watched"]}
        self.assertEqual(watched, {starter.id})

    def test_team_not_enrolled_in_displayed_season_is_404(self) -> None:
        outsider = make_teams("TRosOut", 1)[0]
        self.errors(self.api.get(self.roster_url(outsider.id)), 404)

    def test_league_without_season_is_404(self) -> None:
        empty = make_league("TRos Empty", manager=self.user_a)
        self.errors(self.api.get(self.roster_url(self.team.id, empty.id)), 404)

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(self.roster_url(self.team.id, foreign.id)), 404)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.errors(self.anon.get(self.roster_url(self.team.id)), 401)

    def test_leaves_session_untouched(self) -> None:
        self.ok(self.api.get(self.roster_url(self.team.id)))
        self.assert_session_untouched()


class TeamHistoryTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season1, self.teams = ranked_career_league(
            "THis", 2, manager=self.user_a
        )
        # A second (newer) completed Season: the displayed one.
        self.season2 = make_completed_season(
            self.league, self.teams, name="Season 2", start_date=date(2026, 1, 1)
        )
        self.team = self.teams[1]

    def history_url(self, team_id: int, league_id: int | None = None) -> str:
        return _url(league_id or self.league.id, f"teams/{team_id}/history/")

    def test_history_returns_overall_and_seasons_year_descending(self) -> None:
        data = self.ok(self.api.get(self.history_url(self.team.id)))
        self.assertEqual(set(data), {"team", "overall", "seasons"})
        self.assertEqual(data["team"], {"id": self.team.id, "name": self.team.name})
        # teams[1] loses both Rounds of its one Match in each Season.
        self.assertEqual(
            data["overall"],
            {
                "wins": 0,
                "losses": 4,
                "ties": 0,
                "playoff_appearances": 0,
                "championships": 0,
            },
        )
        self.assertEqual(
            data["seasons"],
            [
                {
                    "season": {"id": self.season2.id, "name": "Season 2"},
                    "year": 2026,
                    "wins": 0,
                    "losses": 2,
                    "ties": 0,
                    "rank": 2,
                },
                {
                    "season": {"id": self.season1.id, "name": "Season 1"},
                    "year": 2025,
                    "wins": 0,
                    "losses": 2,
                    "ties": 0,
                    "rank": 2,
                },
            ],
        )

    def test_history_team_not_enrolled_is_404(self) -> None:
        outsider = make_teams("THisOut", 1)[0]
        self.errors(self.api.get(self.history_url(outsider.id)), 404)

    def test_history_league_without_season_is_404(self) -> None:
        empty = make_league("THis Empty", manager=self.user_a)
        self.errors(self.api.get(self.history_url(self.team.id, empty.id)), 404)

    def test_history_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(self.history_url(self.team.id, foreign.id)), 404)

    def test_history_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.errors(self.anon.get(self.history_url(self.team.id)), 401)


class TeamHistoryPlayersTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "THPl", 2, manager=self.user_a
        )
        self.team = self.teams[1]
        add_round_states(self.season, self.teams[0], self.team)
        self.players = list(self.team.active_players)

    def players_url(
        self, team_id: int, query: str = "", league_id: int | None = None
    ) -> str:
        return _url(
            league_id or self.league.id, f"teams/{team_id}/history/players/{query}"
        )

    def test_players_default_to_name_order_and_page(self) -> None:
        self.ok(self.api.put(_url(self.league.id, f"watch-list/{self.players[0].id}/")))
        rows, meta = self.ok_page(self.api.get(self.players_url(self.team.id)))
        self.assertEqual(meta["count"], 6)
        self.assertEqual(
            [row["player"]["id"] for row in rows],
            [p.id for p in sorted(self.players, key=lambda p: (p.name, p.id))],
        )
        row = rows[0]
        self.assertEqual(
            set(row),
            {
                "player",
                "watched",
                "on_team",
                "games_played",
                "last_season_year",
                "stats",
            },
        )
        self.assertEqual(
            set(row["stats"]),
            {
                "points_scored",
                "tags_made",
                "times_tagged",
                "missiles_landed",
                "resupplies_given",
                "specials_used",
            },
        )
        self.assertTrue(row["on_team"])
        self.assertEqual(row["games_played"], 1)
        self.assertEqual(row["last_season_year"], 2025)
        watched = {r["player"]["id"] for r in rows if r["watched"]}
        self.assertEqual(watched, {self.players[0].id})

    def test_players_ordering_descending_stat(self) -> None:
        rows, _ = self.ok_page(
            self.api.get(self.players_url(self.team.id, "?ordering=-points_scored"))
        )
        points = [row["stats"]["points_scored"] for row in rows]
        self.assertEqual(points, sorted(points, reverse=True))

    def test_players_page_size(self) -> None:
        rows, meta = self.ok_page(
            self.api.get(self.players_url(self.team.id, "?page_size=4&page=2"))
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(meta["total_pages"], 2)

    def test_players_bad_ordering_is_400(self) -> None:
        self.error(
            self.api.get(self.players_url(self.team.id, "?ordering=bogus")),
            400,
            "invalid_choice",
            "ordering",
        )

    def test_players_team_not_enrolled_is_404(self) -> None:
        outsider = make_teams("THPlOut", 1)[0]
        self.errors(self.api.get(self.players_url(outsider.id)), 404)

    def test_players_league_without_season_is_404(self) -> None:
        empty = make_league("THPl Empty", manager=self.user_a)
        self.errors(
            self.api.get(self.players_url(self.team.id, league_id=empty.id)), 404
        )

    def test_players_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(
            self.api.get(self.players_url(self.team.id, league_id=foreign.id)), 404
        )

    def test_players_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.errors(self.anon.get(self.players_url(self.team.id)), 401)


TEAM_STAT_KEYS = {
    "team_id",
    "team_name",
    "rounds_played",
    "avg_points_for",
    "avg_points_against",
    "avg_margin",
    "avg_survivors",
    "total_tags_landed",
    "total_times_tagged",
    "base_captures",
    "missiles_fired",
    "missiles_hit",
    "nukes_fired",
    "nukes_landed",
    "cancelled_nukes",
    "team",
}


class SeasonScopedScreenMixin:
    """Shared Season-scope cases for ``team-stats/`` / ``power-rankings/`` /
    ``game-log/``: the subclass sets ``path`` and builds a two-Season League
    (``self.season1`` old, ``self.season2`` displayed)."""

    path = ""

    def get_body(self, query: str = "", league_id: int | None = None) -> dict:
        response = self.api.get(_url(league_id or self.league.id, self.path + query))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_meta_season_scope_defaults_to_displayed_season(self) -> None:
        meta = self.get_body()["meta"]
        self.assertEqual(
            meta["season_scope"],
            {
                "selected": {"id": self.season2.id, "name": self.season2.name},
                "options": [
                    {"id": self.season2.id, "name": self.season2.name},
                    {"id": self.season1.id, "name": self.season1.name},
                ],
            },
        )

    def test_career_scope(self) -> None:
        meta = self.get_body("?season=career")["meta"]
        self.assertEqual(meta["season_scope"]["selected"], "career")

    def test_past_season_scope(self) -> None:
        meta = self.get_body(f"?season={self.season1.id}")["meta"]
        self.assertEqual(meta["season_scope"]["selected"]["id"], self.season1.id)

    def test_unknown_season_is_400(self) -> None:
        self.error(
            self.api.get(_url(self.league.id, self.path + "?season=999999")),
            400,
            "invalid_choice",
            "season",
        )

    def test_bad_ordering_is_400(self) -> None:
        self.error(
            self.api.get(_url(self.league.id, self.path + "?ordering=bogus")),
            400,
            "invalid_choice",
            "ordering",
        )

    def test_league_without_season_is_empty(self) -> None:
        empty = make_league(f"{self.path} Empty", manager=self.user_a)
        body = self.get_body(league_id=empty.id)
        self.assertEqual(body["data"], [])
        self.assertEqual(
            body["meta"]["season_scope"], {"selected": None, "options": []}
        )

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(_url(foreign.id, self.path)), 404)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.errors(self.anon.get(_url(self.league.id, self.path)), 401)

    def test_leaves_session_untouched(self) -> None:
        self.get_body()
        self.assert_session_untouched()


def _two_season_league(test: LeagueApiTestCase, prefix: str) -> None:
    """``teams[0]`` beats ``teams[1]`` beats ``teams[2]`` in both Seasons."""
    test.league, test.season1, test.teams = ranked_career_league(
        prefix, 3, manager=test.user_a
    )
    test.season2 = make_completed_season(
        test.league, test.teams, name="Season 2", start_date=date(2026, 1, 1)
    )


class TeamStatsTests(SeasonScopedScreenMixin, LeagueApiTestCase):
    path = "team-stats/"

    def setUp(self) -> None:
        super().setUp()
        _two_season_league(self, "TSt")

    def test_rows_default_to_team_name_order(self) -> None:
        data = self.get_body()["data"]
        self.assertEqual(
            [row["team"]["id"] for row in data],
            [t.id for t in sorted(self.teams, key=lambda t: t.name)],
        )
        for row in data:
            self.assertEqual(set(row), TEAM_STAT_KEYS)
            self.assertEqual(row["team"]["id"], row["team_id"])
        # Each Team plays 2 Matches x 2 Rounds in the displayed Season.
        self.assertEqual([row["rounds_played"] for row in data], [4, 4, 4])

    def test_career_counts_both_seasons(self) -> None:
        data = self.get_body("?season=career")["data"]
        self.assertEqual([row["rounds_played"] for row in data], [8, 8, 8])

    def test_ordering_descending_margin(self) -> None:
        data = self.get_body("?ordering=-avg_margin")["data"]
        self.assertEqual(
            [row["team"]["id"] for row in data], [t.id for t in self.teams]
        )


POWER_ROW_KEYS = {
    "rank",
    "team_id",
    "team_name",
    "mean_rating",
    "win_pct",
    "avg_score_diff",
    "norm_rating",
    "norm_win_pct",
    "norm_score_diff",
    "power_score",
    "team",
}


class PowerRankingsTests(SeasonScopedScreenMixin, LeagueApiTestCase):
    path = "power-rankings/"

    def setUp(self) -> None:
        super().setUp()
        _two_season_league(self, "PRk")

    def test_rows_default_to_rank_order(self) -> None:
        data = self.get_body()["data"]
        self.assertEqual([row["rank"] for row in data], [1, 2, 3])
        for row in data:
            self.assertEqual(set(row), POWER_ROW_KEYS)
            self.assertEqual(row["team"]["id"], row["team_id"])
        # teams[0] wins every Match, teams[2] loses every Match.
        self.assertEqual(
            [row["win_pct"] for row in data if row["team_id"] == self.teams[0].id],
            [1.0],
        )
        self.assertEqual(
            [row["win_pct"] for row in data if row["team_id"] == self.teams[2].id],
            [0.0],
        )

    def test_ordering_by_team_name_descending_power(self) -> None:
        data = self.get_body("?ordering=-team")["data"]
        self.assertEqual(
            [row["team"]["id"] for row in data],
            [t.id for t in sorted(self.teams, key=lambda t: t.name, reverse=True)],
        )


class GameLogTests(SeasonScopedScreenMixin, LeagueApiTestCase):
    path = "game-log/"

    def setUp(self) -> None:
        super().setUp()
        _two_season_league(self, "GLg")

    def test_rows_default_to_chronological_order(self) -> None:
        data = self.get_body()["data"]
        # 3 Matches x 2 Rounds in the displayed Season.
        self.assertEqual(len(data), 6)
        for row in data:
            self.assertEqual(set(row), {"round", "red_points", "blue_points", "winner"})
        round_ids = [row["round"]["id"] for row in data]
        self.assertEqual(round_ids, sorted(round_ids))
        self.assertEqual(
            set(data[0]["round"]),
            {"id", "round_number", "match_id", "date_played", "team_red", "team_blue"},
        )

    def test_career_counts_both_seasons(self) -> None:
        self.assertEqual(len(self.get_body("?season=career")["data"]), 12)

    def test_team_filter(self) -> None:
        team = self.teams[2]
        data = self.get_body(f"?team_id={team.id}")["data"]
        self.assertEqual(len(data), 4)
        for row in data:
            self.assertIn(
                team.id,
                {row["round"]["team_red"]["id"], row["round"]["team_blue"]["id"]},
            )

    def test_unenrolled_team_filter_is_400(self) -> None:
        outsider = make_teams("GLgOut", 1)[0]
        self.error(
            self.api.get(_url(self.league.id, f"game-log/?team_id={outsider.id}")),
            400,
            "invalid_choice",
            "team_id",
        )

    def test_malformed_team_filter_is_400(self) -> None:
        self.errors(self.api.get(_url(self.league.id, "game-log/?team_id=abc")), 400)

    def test_ordering_descending_round_number(self) -> None:
        data = self.get_body("?ordering=-matchday")["data"]
        numbers = [row["round"]["round_number"] for row in data]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
