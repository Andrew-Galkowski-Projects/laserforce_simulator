"""ANG-01e T1 -- the Account watch-list API (``/api/leagues/<id>/watch-list/``).

Seam 1 of the ANG-01e spec (#180): driven through DRF ``APIClient``. Asserts
status codes, the envelope, ``meta`` (``season_scope`` + page keys), the error
codes and the ``WatchedPlayer`` rows a write leaves behind -- never simulated
point totals (rounds are hand-built with fixed stats).
"""

from __future__ import annotations

from matches.models import WatchedPlayer
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    add_round_states,
    make_league,
    make_teams,
    ranked_career_league,
)


def _player_url(league_id: int, player_id: int) -> str:
    return f"/api/leagues/{league_id}/watch-list/{player_id}/"


class WatchPlayerPutTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "WLPut", 2, manager=self.user_a
        )
        self.player = self.teams[0].active_players[0]

    def test_put_watches_player_for_account(self) -> None:
        data = self.ok(self.api.put(_player_url(self.league.id, self.player.id)))
        self.assertEqual(data["watched"], True)
        self.assertEqual(data["player"]["id"], self.player.id)
        self.assertEqual(data["player"]["name"], self.player.name)
        self.assertTrue(
            WatchedPlayer.objects.filter(
                account=self.user_a, league=self.league, player=self.player
            ).exists()
        )

    def test_put_is_idempotent(self) -> None:
        url = _player_url(self.league.id, self.player.id)
        self.ok(self.api.put(url))
        data = self.ok(self.api.put(url))
        self.assertTrue(data["watched"])
        self.assertEqual(
            WatchedPlayer.objects.filter(
                account=self.user_a, league=self.league
            ).count(),
            1,
        )

    def test_put_player_outside_league_is_400(self) -> None:
        outsider = make_teams("WLOut", 1)[0].active_players[0]
        self.error(
            self.api.put(_player_url(self.league.id, outsider.id)),
            400,
            "player_not_in_league",
            "player_id",
        )
        self.assertFalse(WatchedPlayer.objects.exists())

    def test_put_on_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.put(_player_url(foreign.id, self.player.id)), 404)

    def test_put_another_accounts_player_is_404(self) -> None:
        self.teams[1].manager = self.user_b
        self.teams[1].save(update_fields=["manager"])
        foreign_player = self.teams[1].active_players[0]
        self.errors(self.api.put(_player_url(self.league.id, foreign_player.id)), 404)
        self.assertFalse(WatchedPlayer.objects.exists())

    def test_put_unknown_player_is_404(self) -> None:
        self.errors(self.api.put(_player_url(self.league.id, 999999)), 404)

    def test_put_anonymous_is_403(self) -> None:
        self.errors(self.anon.put(_player_url(self.league.id, self.player.id)), 403)

    def test_put_leaves_session_untouched(self) -> None:
        self.ok(self.api.put(_player_url(self.league.id, self.player.id)))
        self.assert_session_untouched()

    def test_put_free_agent_pool_player_is_allowed(self) -> None:
        pool = make_teams("WLPool", 1)[0]
        self.league.free_agent_pool = pool
        self.league.save(update_fields=["free_agent_pool"])
        free_agent = pool.active_players[0]
        data = self.ok(self.api.put(_player_url(self.league.id, free_agent.id)))
        self.assertTrue(data["watched"])


class WatchPlayerDeleteTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "WLDel", 2, manager=self.user_a
        )
        self.player = self.teams[0].active_players[0]
        self.other = self.teams[1].active_players[0]

    def test_delete_unwatches_player(self) -> None:
        self.ok(self.api.put(_player_url(self.league.id, self.player.id)))
        self.ok(self.api.put(_player_url(self.league.id, self.other.id)))
        data = self.ok(self.api.delete(_player_url(self.league.id, self.player.id)))
        self.assertEqual(data, {"player": data["player"], "watched": False})
        self.assertEqual(data["player"]["id"], self.player.id)
        self.assertEqual(
            list(
                WatchedPlayer.objects.filter(account=self.user_a).values_list(
                    "player_id", flat=True
                )
            ),
            [self.other.id],
        )

    def test_delete_is_idempotent(self) -> None:
        data = self.ok(self.api.delete(_player_url(self.league.id, self.player.id)))
        self.assertFalse(data["watched"])

    def test_delete_on_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.delete(_player_url(foreign.id, self.player.id)), 404)

    def test_delete_anonymous_is_403(self) -> None:
        self.errors(self.anon.delete(_player_url(self.league.id, self.player.id)), 403)

    def test_clear_removes_only_this_accounts_list_for_this_league(self) -> None:
        other_league, _, other_teams = ranked_career_league(
            "WLDel2", 2, manager=self.user_a
        )
        self.ok(self.api.put(_player_url(self.league.id, self.player.id)))
        self.ok(self.api.put(_player_url(self.league.id, self.other.id)))
        self.ok(
            self.api.put(
                _player_url(other_league.id, other_teams[0].active_players[0].id)
            )
        )
        WatchedPlayer.objects.create(
            account=self.user_b, league=self.league, player=self.player
        )
        data = self.ok(self.api.delete(f"/api/leagues/{self.league.id}/watch-list/"))
        self.assertEqual(data, {"removed": 2})
        self.assertFalse(
            WatchedPlayer.objects.filter(
                account=self.user_a, league=self.league
            ).exists()
        )
        self.assertEqual(WatchedPlayer.objects.filter(account=self.user_a).count(), 1)
        self.assertEqual(WatchedPlayer.objects.filter(account=self.user_b).count(), 1)

    def test_clear_empty_list_removes_zero(self) -> None:
        data = self.ok(self.api.delete(f"/api/leagues/{self.league.id}/watch-list/"))
        self.assertEqual(data, {"removed": 0})

    def test_clear_on_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.delete(f"/api/leagues/{foreign.id}/watch-list/"), 404)

    def test_clear_anonymous_is_403(self) -> None:
        self.errors(self.anon.delete(f"/api/leagues/{self.league.id}/watch-list/"), 403)


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


class WatchListGetTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = ranked_career_league(
            "WLGet", 2, manager=self.user_a
        )
        add_round_states(self.season, self.teams[0], self.teams[1])
        red = self.teams[0]
        # Fixture points rise by slot: commander 1000 .. ammo 1050.
        self.low = red.slot_commander
        self.high = red.slot_ammo
        pool = make_teams("WLGetPool", 1)[0]
        self.league.free_agent_pool = pool
        self.league.save(update_fields=["free_agent_pool"])
        self.free_agent = pool.active_players[0]
        for player in (self.low, self.high, self.free_agent):
            self.ok(self.api.put(_player_url(self.league.id, player.id)))
        self.url = f"/api/leagues/{self.league.id}/watch-list/"

    def get(self, query: str = ""):
        response = self.api.get(f"{self.url}?{query}" if query else self.url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        return body["data"], body["meta"]

    def test_rows_are_zero_filled_player_stat_rows(self) -> None:
        rows, meta = self.get()
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(set(row), STAT_ROW_KEYS)
            self.assertTrue(row["watched"])
            self.assertEqual(set(row["stats"]), STATS_KEYS)
        by_id = {row["player"]["id"]: row for row in rows}
        self.assertEqual(set(by_id), {self.low.id, self.high.id, self.free_agent.id})
        self.assertEqual(by_id[self.free_agent.id]["games"], 0)
        self.assertEqual(by_id[self.high.id]["games"], 1)
        self.assertEqual(
            by_id[self.high.id]["team"],
            {"id": self.teams[0].id, "name": self.teams[0].name},
        )
        self.assertEqual(by_id[self.high.id]["role"], "ammo")

    def test_meta_carries_season_scope_and_page_keys(self) -> None:
        _, meta = self.get()
        self.assertEqual(set(meta), PAGE_KEYS | {"season_scope"})
        self.assertEqual(meta["count"], 3)
        self.assertEqual(
            meta["season_scope"],
            {
                "selected": {"id": self.season.id, "name": self.season.name},
                "options": [{"id": self.season.id, "name": self.season.name}],
            },
        )

    def test_default_ordering_is_points_descending(self) -> None:
        rows, _ = self.get()
        ids = [row["player"]["id"] for row in rows]
        self.assertEqual(ids, [self.high.id, self.low.id, self.free_agent.id])

    def test_ordering_ascending_key(self) -> None:
        rows, _ = self.get("ordering=points_scored")
        ids = [row["player"]["id"] for row in rows]
        self.assertEqual(ids, [self.free_agent.id, self.low.id, self.high.id])

    def test_paging(self) -> None:
        rows, meta = self.get("page_size=2&page=2")
        self.assertEqual(len(rows), 1)
        self.assertEqual(meta["page"], 2)
        self.assertEqual(meta["total_pages"], 2)

    def test_career_scope(self) -> None:
        rows, meta = self.get("season=career")
        self.assertEqual(meta["season_scope"]["selected"], "career")
        self.assertEqual(len(rows), 3)

    def test_rate_per_game_keeps_row_shape(self) -> None:
        rows, _ = self.get("rate=per_game")
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(set(row["stats"]), STATS_KEYS)

    def test_unknown_season_is_400(self) -> None:
        self.error(
            self.api.get(f"{self.url}?season=999999"), 400, "invalid_choice", "season"
        )

    def test_another_leagues_season_is_400(self) -> None:
        _, other_season, _ = ranked_career_league("WLGetX", 2, manager=self.user_a)
        self.error(
            self.api.get(f"{self.url}?season={other_season.id}"),
            400,
            "invalid_choice",
            "season",
        )

    def test_bad_rate_is_400(self) -> None:
        self.error(
            self.api.get(f"{self.url}?rate=per_hour"), 400, "invalid_choice", "rate"
        )

    def test_bad_ordering_is_400(self) -> None:
        self.error(
            self.api.get(f"{self.url}?ordering=-bogus"),
            400,
            "invalid_choice",
            "ordering",
        )

    def test_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(self.api.get(f"/api/leagues/{foreign.id}/watch-list/"), 404)

    def test_anonymous_is_403(self) -> None:
        self.errors(self.anon.get(self.url), 403)

    def test_does_not_touch_session(self) -> None:
        self.get()
        self.assert_session_untouched()

    def test_other_accounts_list_is_independent(self) -> None:
        WatchedPlayer.objects.create(
            account=self.user_b, league=self.league, player=self.teams[1].slot_ammo
        )
        rows, _ = self.get()
        self.assertNotIn(
            self.teams[1].slot_ammo.id, {row["player"]["id"] for row in rows}
        )


class WatchListEmptyStateTests(LeagueApiTestCase):
    def test_league_with_no_season_is_empty(self) -> None:
        league = make_league("WLEmpty", manager=self.user_a)
        response = self.api.get(f"/api/leagues/{league.id}/watch-list/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["data"], [])
        self.assertEqual(
            body["meta"]["season_scope"], {"selected": None, "options": []}
        )
        self.assertEqual(body["meta"]["count"], 0)
