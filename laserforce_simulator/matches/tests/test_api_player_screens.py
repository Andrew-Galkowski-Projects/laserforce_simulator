"""ANG-01e T2 -- the Player list screens API (``/api/leagues/<id>/...``).

Seam 1 of the ANG-01e spec (#180), driven through DRF ``APIClient``: the
``player-stats/``, ``player-ratings/``, ``free-agents/`` and ``leaders/``
screens. Asserts status codes, the envelope, ``meta`` (``season_scope`` + page
keys), the error codes, row shape and ordering -- never simulated point totals
(rounds are hand-built with fixed stats).
"""

from __future__ import annotations

from matches.models import WatchedPlayer
from matches.tests.league_api_fixtures import (
    LEADER_KEYS,
    LeagueApiTestCase,
    add_round_states,
    make_league,
    make_teams,
    ranked_career_league,
)

STAT_ROW_KEYS = {"player", "watched", "team", "role", "games", "stats"}
STATS_KEYS = {
    "points_scored",
    "mvp",
    "tags_made",
    "times_tagged",
    "accuracy",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
    "tag_ratio",
    "survival",
}
PAGE_KEYS = {"page", "page_size", "count", "total_pages"}
RATING_ROW_KEYS = {
    "player",
    "watched",
    "preferred_roles",
    "overall_rating",
    "potential",
    "age",
    "height",
    "home_site",
    "started_playing_age",
    "total_games",
    "stats",
}
RATING_STATS_KEYS = {
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    "decision_making",
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    "communication",
    "teamwork",
    "offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
}


class _RatingsScreenMixin:
    """Shared ratings-screen checks over ``self.url`` / ``self.players``."""

    def get(self, query: str = "") -> tuple[list, dict]:
        response = self.api.get(f"{self.url}?{query}" if query else self.url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        return body["data"], body["meta"]

    def ids(self, query: str) -> list[int]:
        rows, _ = self.get(f"{query}&page_size=100")
        return [row["player"]["id"] for row in rows]

    def test_rows_have_rating_shape(self) -> None:
        rows, meta = self.get("page_size=100")
        self.assertEqual(set(meta), PAGE_KEYS)
        self.assertEqual(meta["count"], len(self.players))
        for row in rows:
            self.assertEqual(set(row), RATING_ROW_KEYS)
            self.assertEqual(set(row["stats"]), RATING_STATS_KEYS)
            self.assertFalse(row["watched"])
        by_id = {row["player"]["id"]: row for row in rows}
        player = self.players[0]
        row = by_id[player.id]
        self.assertAlmostEqual(row["overall_rating"], player.overall_rating)
        self.assertEqual(row["stats"]["offensive_synergy"], player.Offensive_synergy)
        self.assertEqual(row["preferred_roles"], player.preferred_roles)

    def test_watched_flag(self) -> None:
        player = self.players[0]
        WatchedPlayer.objects.create(
            account=self.user_a, league=self.league, player=player
        )
        rows, _ = self.get("page_size=100")
        self.assertEqual(
            {row["player"]["id"] for row in rows if row["watched"]}, {player.id}
        )

    def test_ordering_by_speed_both_ways(self) -> None:
        self.set_stat("speed", [30, 90, 60])
        a, b, c = self.players[:3]
        ids = self.ids("ordering=speed")
        self.assertLess(ids.index(a.id), ids.index(c.id))
        self.assertLess(ids.index(c.id), ids.index(b.id))
        ids = self.ids("ordering=-speed")
        self.assertLess(ids.index(b.id), ids.index(c.id))
        self.assertLess(ids.index(c.id), ids.index(a.id))

    def test_potential_sorts_nulls_last_both_ways(self) -> None:
        for player in self.players:
            player.potential = None
            player.save(update_fields=["potential"])
        a, b = self.players[:2]
        a.potential = 40.0
        a.save(update_fields=["potential"])
        b.potential = 80.0
        b.save(update_fields=["potential"])
        self.assertEqual(self.ids("ordering=potential")[:2], [a.id, b.id])
        self.assertEqual(self.ids("ordering=-potential")[:2], [b.id, a.id])

    def test_preferred_roles_ordering(self) -> None:
        a, b = self.players[:2]
        a.preferred_roles = ["zz_last"]
        a.save(update_fields=["preferred_roles"])
        b.preferred_roles = ["aa_first"]
        b.save(update_fields=["preferred_roles"])
        ids = self.ids("ordering=-preferred_roles")
        self.assertEqual(ids[0], a.id)
        ids = self.ids("ordering=preferred_roles")
        self.assertEqual(ids.index(b.id) < ids.index(a.id), True)

    def test_bad_ordering_is_400(self) -> None:
        for bad in ("points_scored", "-bogus"):
            with self.subTest(ordering=bad):
                self.error(
                    self.api.get(f"{self.url}?ordering={bad}"),
                    400,
                    "invalid_choice",
                    "ordering",
                )

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.errors(self.anon.get(self.url), 401)

    def test_does_not_touch_session(self) -> None:
        self.get()
        self.assert_session_untouched()

    def set_stat(self, field: str, values: list[int]) -> None:
        for player, value in zip(self.players, values):
            setattr(player, field, value)
            player.save(update_fields=[field])


class PlayerRatingsTests(_RatingsScreenMixin, LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "PR", 2, manager=self.user_a
        )
        self.players = [p for team in self.teams for p in team.players.order_by("id")]
        self.url = f"/api/leagues/{self.league.id}/player-ratings/"

    def test_default_ordering_is_team_then_name(self) -> None:
        rows, _ = self.get("page_size=100")
        keys = [(row["player"]["team"]["name"], row["player"]["name"]) for row in rows]
        self.assertEqual(keys, sorted(keys))

    def test_team_filter(self) -> None:
        red = self.teams[0]
        rows, meta = self.get(f"team_id={red.id}&page_size=100")
        self.assertEqual(meta["count"], red.players.count())
        self.assertEqual({row["player"]["team"]["id"] for row in rows}, {red.id})

    def test_unenrolled_team_id_is_400(self) -> None:
        outsider = make_teams("PROut", 1)[0]
        self.error(
            self.api.get(f"{self.url}?team_id={outsider.id}"),
            400,
            "invalid_choice",
            "team_id",
        )

    def test_season_param_is_not_accepted(self) -> None:
        rows, meta = self.get(f"season={self.season.id}")
        self.assertNotIn("season_scope", meta)

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(f"/api/leagues/{foreign.id}/player-ratings/"), 404)

    def test_league_with_no_season_is_empty(self) -> None:
        league = make_league("PREmpty", manager=self.user_a)
        response = self.api.get(f"/api/leagues/{league.id}/player-ratings/")
        self.assertEqual(self.ok(response), [])
        self.assertEqual(response.json()["meta"]["count"], 0)


class FreeAgentsTests(_RatingsScreenMixin, LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "FA", 2, manager=self.user_a
        )
        self.pool = make_teams("FAPool", 1)[0]
        self.league.free_agent_pool = self.pool
        self.league.save(update_fields=["free_agent_pool"])
        self.players = list(self.pool.players.order_by("id"))
        self.url = f"/api/leagues/{self.league.id}/free-agents/"

    def test_only_pool_players_are_listed(self) -> None:
        rows, _ = self.get("page_size=100")
        self.assertEqual(
            {row["player"]["id"] for row in rows}, {p.id for p in self.players}
        )

    def test_default_ordering_is_team_then_name(self) -> None:
        rows, _ = self.get("page_size=100")
        names = [row["player"]["name"] for row in rows]
        self.assertEqual(names, sorted(names))

    def test_paging(self) -> None:
        rows, meta = self.get("page_size=2&page=2")
        self.assertEqual(len(rows), 2)
        self.assertEqual(meta["page"], 2)
        self.assertEqual(meta["count"], len(self.players))

    def test_team_id_is_not_a_param(self) -> None:
        _, meta = self.get(f"team_id={self.teams[0].id}")
        self.assertEqual(meta["count"], len(self.players))

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(f"/api/leagues/{foreign.id}/free-agents/"), 404)

    def test_league_with_no_season_is_empty(self) -> None:
        league = make_league("FAEmpty", manager=self.user_a)
        league.free_agent_pool = make_teams("FAEmptyPool", 1)[0]
        league.save(update_fields=["free_agent_pool"])
        response = self.api.get(f"/api/leagues/{league.id}/free-agents/")
        self.assertEqual(self.ok(response), [])
        self.assertEqual(response.json()["meta"]["count"], 0)


BOARDS = {"avg_tags", "avg_score", "fewest_tagged", "tag_ratio"}


class LeadersTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "LD", 2, manager=self.user_a
        )
        add_round_states(self.season, self.teams[0], self.teams[1])
        self.url = f"/api/leagues/{self.league.id}/leaders/"

    def get(self, query: str = "") -> tuple[dict, dict]:
        response = self.api.get(f"{self.url}?{query}" if query else self.url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        return body["data"], body["meta"]

    def test_four_boards_of_leader_rows(self) -> None:
        data, meta = self.get()
        self.assertEqual(set(data), BOARDS)
        self.assertEqual(set(meta), {"season_scope"})
        for board, rows in data.items():
            self.assertEqual(len(rows), 10, board)
            for row in rows:
                self.assertEqual(set(row), LEADER_KEYS | {"player", "watched"})
                self.assertEqual(row["player"]["id"], row["player_id"])

    def test_boards_are_in_rank_order(self) -> None:
        data, _ = self.get()
        for board, rows in data.items():
            ranks = [row["rank"] for row in rows]
            self.assertEqual(ranks, sorted(ranks), board)
        # Fixture tags rise by slot (commander 10 .. ammo 15): ammo leads.
        self.assertEqual(data["avg_tags"][0]["role"], "ammo")

    def test_watched_flag(self) -> None:
        player = self.teams[0].slot_ammo
        WatchedPlayer.objects.create(
            account=self.user_a, league=self.league, player=player
        )
        data, _ = self.get()
        watched = {
            row["player_id"] for rows in data.values() for row in rows if row["watched"]
        }
        self.assertEqual(watched, {player.id})

    def test_career_scope(self) -> None:
        data, meta = self.get("season=career")
        self.assertEqual(meta["season_scope"]["selected"], "career")
        self.assertEqual(len(data["avg_tags"]), 10)

    def test_explicit_season(self) -> None:
        _, meta = self.get(f"season={self.season.id}")
        self.assertEqual(
            meta["season_scope"]["selected"],
            {"id": self.season.id, "name": self.season.name},
        )

    def test_unknown_season_is_400(self) -> None:
        self.error(
            self.api.get(f"{self.url}?season=999999"), 400, "invalid_choice", "season"
        )

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(f"/api/leagues/{foreign.id}/leaders/"), 404)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.errors(self.anon.get(self.url), 401)

    def test_does_not_touch_session(self) -> None:
        self.get()
        self.assert_session_untouched()

    def test_league_with_no_season_is_empty(self) -> None:
        league = make_league("LDEmpty", manager=self.user_a)
        response = self.api.get(f"/api/leagues/{league.id}/leaders/")
        data = self.ok(response)
        self.assertEqual(data, {board: [] for board in BOARDS})
        self.assertEqual(
            response.json()["meta"],
            {"season_scope": {"selected": None, "options": []}},
        )


class PlayerStatsTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "PS", 2, manager=self.user_a
        )
        add_round_states(self.season, self.teams[0], self.teams[1])
        self.url = f"/api/leagues/{self.league.id}/player-stats/"

    def get(self, query: str = "") -> tuple[list, dict]:
        response = self.api.get(f"{self.url}?{query}" if query else self.url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        return body["data"], body["meta"]

    def test_rows_have_player_stat_shape(self) -> None:
        rows, meta = self.get()
        self.assertEqual(len(rows), 12)
        for row in rows:
            self.assertEqual(set(row), STAT_ROW_KEYS)
            self.assertEqual(set(row["stats"]), STATS_KEYS)
            self.assertFalse(row["watched"])
        self.assertEqual(set(meta), PAGE_KEYS | {"season_scope"})
        self.assertEqual(meta["count"], 12)

    def test_default_ordering_is_points_descending(self) -> None:
        rows, _ = self.get()
        points = [row["stats"]["points_scored"] for row in rows]
        self.assertEqual(points, sorted(points, reverse=True))
        self.assertEqual(rows[0]["role"], "ammo")

    def test_ordering_by_name_ascending(self) -> None:
        rows, _ = self.get("ordering=name&page_size=100")
        names = [row["player"]["name"] for row in rows]
        self.assertEqual(names, sorted(names))

    def test_team_filter(self) -> None:
        red = self.teams[0]
        rows, meta = self.get(f"team_id={red.id}")
        self.assertEqual(meta["count"], 6)
        self.assertEqual({row["team"]["id"] for row in rows}, {red.id})

    def test_watched_flag_reads_account_list(self) -> None:
        player = self.teams[0].slot_ammo
        WatchedPlayer.objects.create(
            account=self.user_a, league=self.league, player=player
        )
        WatchedPlayer.objects.create(
            account=self.user_b, league=self.league, player=self.teams[1].slot_ammo
        )
        rows, _ = self.get()
        watched = {row["player"]["id"] for row in rows if row["watched"]}
        self.assertEqual(watched, {player.id})

    def test_career_scope(self) -> None:
        rows, meta = self.get("season=career")
        self.assertEqual(meta["season_scope"]["selected"], "career")
        self.assertEqual(len(rows), 12)

    def test_rate_per_game_keeps_shape(self) -> None:
        rows, _ = self.get("rate=per_10")
        for row in rows:
            self.assertEqual(set(row["stats"]), STATS_KEYS)

    def test_bad_params_are_400(self) -> None:
        unenrolled = make_teams("PSOut", 1)[0]
        for query, code, field in (
            ("season=999999", "invalid_choice", "season"),
            ("rate=per_hour", "invalid_choice", "rate"),
            ("team_id=abc", "invalid", "team_id"),
            (f"team_id={unenrolled.id}", "invalid_choice", "team_id"),
            ("ordering=-overall_rating", "invalid_choice", "ordering"),
        ):
            with self.subTest(query=query):
                self.error(self.api.get(f"{self.url}?{query}"), 400, code, field)

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(f"/api/leagues/{foreign.id}/player-stats/"), 404)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.errors(self.anon.get(self.url), 401)

    def test_does_not_touch_session(self) -> None:
        self.get()
        self.assert_session_untouched()

    def test_league_with_no_season_is_empty(self) -> None:
        league = make_league("PSEmpty", manager=self.user_a)
        response = self.api.get(f"/api/leagues/{league.id}/player-stats/")
        data = self.ok(response)
        self.assertEqual(data, [])
        meta = response.json()["meta"]
        self.assertEqual(meta["season_scope"], {"selected": None, "options": []})
        self.assertEqual(meta["count"], 0)
