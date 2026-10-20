"""ANG-01e T3 -- the League player page API (``/api/leagues/<id>/players/<pid>/``).

Seam 1 of the ANG-01e spec (#180): driven through DRF ``APIClient``. Asserts
status codes, the envelope and the response shape / ordering / counts -- never
simulated point totals (rounds are hand-built with fixed stats).
"""

from __future__ import annotations

from datetime import date

from matches import development
from matches.models import PlayerSeasonRating
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    add_round_states,
    make_completed_season,
    make_teams,
    ranked_career_league,
)

_BIO_KEYS = {
    "age",
    "height",
    "home_site",
    "started_playing_age",
    "total_games",
    "preferred_roles",
}


def _url(league_id: int, player_id: int) -> str:
    return f"/api/leagues/{league_id}/players/{player_id}/"


class LeaguePlayerPageTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "LPP", 2, manager=self.user_a
        )
        add_round_states(self.season, self.teams[0], self.teams[1])
        self.player = self.teams[0].slot_commander

    def test_happy_path_shape(self) -> None:
        data = self.ok(self.api.get(_url(self.league.id, self.player.id)))
        self.assertEqual(
            set(data),
            {
                "player",
                "watched",
                "overall_rating",
                "potential",
                "bio",
                "stats",
                "seasons",
                "career",
                "awards",
                "ratings_history",
            },
        )
        self.assertEqual(data["player"]["id"], self.player.id)
        self.assertEqual(data["player"]["team"]["id"], self.teams[0].id)
        self.assertFalse(data["watched"])
        self.assertEqual(set(data["bio"]), _BIO_KEYS)
        self.assertEqual(data["bio"]["age"], self.player.age)
        self.assertEqual(
            set(data["stats"]), {n.lower() for n in development.STAT_FIELDS}
        )
        self.assertEqual(
            data["stats"]["offensive_synergy"], self.player.Offensive_synergy
        )
        self.assertEqual(len(data["stats"]), 19)
        self.assertAlmostEqual(data["overall_rating"], self.player.overall_rating)

        self.assertEqual(len(data["seasons"]), 1)
        row = data["seasons"][0]
        self.assertEqual(set(row), {"season", "team", "games", "stats"})
        self.assertEqual(row["season"], {"id": self.season.id, "name": "Season 1"})
        self.assertEqual(
            row["team"], {"id": self.teams[0].id, "name": self.teams[0].name}
        )
        self.assertEqual(row["games"], 1)
        self.assertIn("points_scored", row["stats"])
        self.assertEqual(set(data["career"]), {"team", "games", "stats"})
        self.assertEqual(data["career"]["games"], 1)

    def test_watched_reflects_accounts_watch_list(self) -> None:
        self.api.put(f"/api/leagues/{self.league.id}/watch-list/{self.player.id}/")
        data = self.ok(self.api.get(_url(self.league.id, self.player.id)))
        self.assertTrue(data["watched"])

    def test_seasons_newest_first_and_career_spans_league(self) -> None:
        newer = make_completed_season(
            self.league,
            self.teams,
            name="Season 2",
            start_date=date(2026, 1, 1),
            ranked=False,
        )
        add_round_states(newer, self.teams[1], self.teams[0])
        data = self.ok(self.api.get(_url(self.league.id, self.player.id)))
        self.assertEqual(
            [row["season"]["id"] for row in data["seasons"]],
            [newer.id, self.season.id],
        )
        self.assertEqual(data["career"]["games"], 2)

    def test_top_scorer_wins_an_award(self) -> None:
        # ``add_round_states`` gives the ammo slot (idx 5) the most points.
        ammo = self.teams[0].slot_ammo
        data = self.ok(self.api.get(_url(self.league.id, ammo.id)))
        self.assertEqual(len(data["awards"]), 1)
        self.assertEqual(
            data["awards"][0]["season"], {"id": self.season.id, "name": "Season 1"}
        )
        self.assertTrue(data["awards"][0]["award_labels"])

    def test_ratings_history_oldest_first(self) -> None:
        newer = make_completed_season(
            self.league,
            self.teams,
            name="Season 2",
            start_date=date(2026, 1, 1),
            ranked=False,
        )
        stats = {name: 50 for name in development.STAT_FIELDS}
        for season, age in ((newer, 31), (self.season, 30)):
            PlayerSeasonRating.objects.create(
                player=self.player,
                season=season,
                age=age,
                overall_rating=50.0,
                **stats,
            )
        data = self.ok(self.api.get(_url(self.league.id, self.player.id)))
        history = data["ratings_history"]
        self.assertEqual(
            [h["season"]["id"] for h in history], [self.season.id, newer.id]
        )
        self.assertEqual([h["age"] for h in history], [30, 31])
        self.assertEqual(
            set(history[0]), {"season", "age", "overall_rating", "potential", "stats"}
        )
        self.assertEqual(len(history[0]["stats"]), 19)
        self.assertEqual(history[0]["stats"]["offensive_synergy"], 50)
        self.assertNotIn("Offensive_synergy", history[0]["stats"])

    def test_player_with_no_games_here_is_lenient_200(self) -> None:
        free_agent = make_teams("LPPFree", 1)[0].slot_commander
        data = self.ok(self.api.get(_url(self.league.id, free_agent.id)))
        self.assertEqual(data["player"]["id"], free_agent.id)
        self.assertEqual(data["seasons"], [])
        self.assertIsNone(data["career"])
        self.assertEqual(data["awards"], [])
        self.assertEqual(data["ratings_history"], [])
        self.assertEqual(len(data["stats"]), 19)

    def test_league_with_no_season_is_lenient_200(self) -> None:
        from matches.tests.league_api_fixtures import make_league

        empty = make_league("LPP Empty", manager=self.user_a)
        data = self.ok(self.api.get(_url(empty.id, self.player.id)))
        self.assertEqual(data["seasons"], [])
        self.assertIsNone(data["career"])

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(_url(foreign.id, self.player.id)), 404)

    def test_another_accounts_player_is_404(self) -> None:
        self.teams[1].manager = self.user_b
        self.teams[1].save(update_fields=["manager"])
        foreign_player = self.teams[1].slot_commander
        self.errors(self.api.get(_url(self.league.id, foreign_player.id)), 404)

    def test_missing_player_is_404(self) -> None:
        self.errors(self.api.get(_url(self.league.id, 999999)), 404)

    def test_missing_league_is_404(self) -> None:
        self.errors(self.api.get(_url(999999, self.player.id)), 404)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.errors(self.anon.get(_url(self.league.id, self.player.id)), 401)

    def test_leaves_session_untouched(self) -> None:
        self.ok(self.api.get(_url(self.league.id, self.player.id)))
        self.assert_session_untouched()
