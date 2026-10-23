"""ANG-06f T1 -- ``GET /api/rounds/<pk>/playback/``: the Round's Playback
snapshots plus the beta map payload (spec #305, ADR-0044)."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from laserforce_simulator.testing import assert_owner_only
from matches.models import GameEvent, GameRound, PlayerRoundState
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_playback_map import _make_arena_map

User = get_user_model()

_FAST_TICKS = 40


class _PlaybackApiCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang06f-playback-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, self.red_players = make_team_with_slots("PbApiRed")
        self.blue, self.blue_players = make_team_with_slots("PbApiBlue")

    def make_round(self, **kwargs) -> GameRound:
        game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue, **kwargs
        )
        PlayerRoundState.objects.create(
            game_round=game_round,
            player=self.red_players["commander"],
            team_color="red",
            role="commander",
            starting_lives=15,
            starting_shots=30,
            starting_missiles=5,
            cell_row=0,
            cell_col=0,
        )
        PlayerRoundState.objects.create(
            game_round=game_round,
            player=self.blue_players["scout"],
            team_color="blue",
            role="scout",
            starting_lives=15,
            starting_shots=30,
            cell_row=3,
            cell_col=3,
        )
        return game_round

    def data(self, game_round: GameRound) -> dict:
        response = self.api.get(f"/api/rounds/{game_round.pk}/playback/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        return body["data"]


class TestPlaybackShape(_PlaybackApiCase):
    def test_exact_top_level_keys(self) -> None:
        self.assertEqual(
            list(self.data(self.make_round())),
            [
                "duration_ticks",
                "not_targetable_ticks",
                "respawn_ticks",
                "players",
                "map",
            ],
        )

    def test_eventless_round_returns_only_tick_zero_snapshots(self) -> None:
        data = self.data(self.make_round())
        self.assertEqual(
            (
                data["duration_ticks"],
                data["not_targetable_ticks"],
                data["respawn_ticks"],
            ),
            (1800, 8, 16),
        )
        self.assertEqual(
            [p["id"] for p in data["players"]],
            [self.red_players["commander"].pk, self.blue_players["scout"].pk],
        )
        commander = data["players"][0]["snapshots"]
        self.assertEqual(len(commander), 1)
        self.assertEqual(
            commander[0],
            {
                "tick": 0,
                "score": 0,
                "lives": 15,
                "shots": 30,
                "missiles": 5,
                "sp": 0,
                "tags": 0,
                "misses": 0,
                "downed_at": None,
                "eliminated": False,
                "rapid_fire": False,
                "nuking": False,
            },
        )

    def test_map_is_null_for_a_mapless_round(self) -> None:
        self.assertIsNone(self.data(self.make_round())["map"])

    def test_map_carries_arena_map_for_a_mapped_round(self) -> None:
        arena_map, zone_size = _make_arena_map("PbApiArena")
        game_round = self.make_round(arena_map=arena_map, zone_size=zone_size)
        payload = self.data(game_round)["map"]
        self.assertEqual(
            payload["arena_map"], {"id": arena_map.pk, "name": "PbApiArena"}
        )
        self.assertEqual(payload["zone_size"], zone_size)
        self.assertEqual(
            sorted(p["id"] for p in payload["players"]),
            sorted([self.red_players["commander"].pk, self.blue_players["scout"].pk]),
        )


class TestPlaybackQueryCount(_PlaybackApiCase):
    def test_query_count_does_not_grow_with_events(self) -> None:
        """No per-event queries (as ``/timeline/``, ANG-06d retro check)."""
        game_round = self.make_round()
        cmd = self.red_players["commander"]
        scout = self.blue_players["scout"]
        url = f"/api/rounds/{game_round.pk}/playback/"

        def log(tick: int, event_type: str) -> None:
            GameEvent.objects.create(
                game_round=game_round,
                timestamp=tick,
                event_type=event_type,
                actor=cmd,
                target=scout,
                metadata={"actor_shots": 30 - tick, "target_lives": 15},
            )

        log(1, "tag")
        with CaptureQueriesContext(connection) as few:
            self.assertEqual(self.api.get(url).status_code, 200)
        for tick in range(2, 32):
            log(tick, "tag")
            log(tick, "miss")
        with self.assertNumQueries(len(few.captured_queries)):
            self.assertEqual(self.api.get(url).status_code, 200)


class TestPlaybackAccess(_PlaybackApiCase):
    def test_round_is_owner_only(self) -> None:
        game_round = self.make_round(manager=self.user_a)
        assert_owner_only(
            f"/api/rounds/{game_round.pk}/playback/",
            owner=self.user_a,
            other=self.user_b,
        )

    def test_missing_round_is_404(self) -> None:
        response = self.api.get("/api/rounds/999999/playback/")
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(response.json()["errors"][0]["code"], "not_found")

    def test_route_name(self) -> None:
        self.assertEqual(
            reverse("gameround-playback", args=[7]), "/api/rounds/7/playback/"
        )


class TestPlaybackSavedRound(TestCase):
    """A real batch-saved (``scores``-tier) Round is upgraded to ``full``."""

    def test_upgrades_scores_round_to_full(self) -> None:
        api = APIClient()
        api.force_login(get_shared_manager())
        red, _ = make_team_with_slots("PbSavRed")
        blue, _ = make_team_with_slots("PbSavBlue")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            rounds = BatchSimulator().save_games(red, blue, [[12345, False]], 1)
            game_round = GameRound.objects.get(pk=rounds[0].pk)
            self.assertEqual(game_round.fidelity, "scores")
            self.assertEqual(game_round.events.count(), 0)
            response = api.get(f"/api/rounds/{game_round.pk}/playback/")
        self.assertEqual(response.status_code, 200, response.content)
        game_round.refresh_from_db()
        self.assertEqual(game_round.fidelity, "full")
        players = response.json()["data"]["players"]
        self.assertEqual(len(players), game_round.player_states.count())
        self.assertTrue(any(len(p["snapshots"]) > 1 for p in players))
