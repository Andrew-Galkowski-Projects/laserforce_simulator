"""ANG-06e-1 -- ``GET /api/rounds/<pk>/timeline/``: the whole Event log in one
unpaginated call (spec #300).

Rows are built by remapping ``round_playback_payload``; hand-made
``GameEvent`` rows keep every assertion independent of simulated totals.
"""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import GameEvent, GameRound, PlayerRoundState
from matches.services import round_playback_payload
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

User = get_user_model()

_FAST_TICKS = 40


def _saved_round(red, blue, *, seed: int = 12345) -> GameRound:
    """A real batch-saved round: ``scores`` fidelity + roster snapshot."""
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        rounds = BatchSimulator().save_games(red, blue, [[seed, False]], 1)
    game_round = GameRound.objects.get(pk=rounds[0].pk)
    assert game_round.fidelity == "scores"
    return game_round


_EVENT_KEYS = [
    "tick",
    "event_type",
    "points",
    "description",
    "actor_id",
    "actor_name",
    "actor_side",
    "target_id",
    "target_name",
    "target_side",
    "metadata",
]


class _TimelineTestCase(TestCase):
    """A hand-built round: two red and two blue PlayerRoundStates."""

    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang06e-timeline-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.red, self.red_players = make_team_with_slots("TlRed")
        self.blue, self.blue_players = make_team_with_slots("TlBlue")
        self.game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.red_cmd = self.red_players["commander"]
        self.red_med = self.red_players["medic"]
        self.blue_hvy = self.blue_players["heavy"]
        self.blue_ammo = self.blue_players["ammo"]
        for player, color, role in (
            (self.red_cmd, "red", "commander"),
            (self.blue_hvy, "blue", "heavy"),
            (self.red_med, "red", "medic"),
            (self.blue_ammo, "blue", "ammo"),
        ):
            PlayerRoundState.objects.create(
                game_round=self.game_round,
                player=player,
                team_color=color,
                role=role,
            )

    def event(self, **kwargs) -> GameEvent:
        kwargs.setdefault("timestamp", 10)
        kwargs.setdefault("event_type", "tag")
        kwargs.setdefault("actor", self.red_cmd)
        return GameEvent.objects.create(game_round=self.game_round, **kwargs)

    def data(self, game_round: GameRound | None = None) -> dict:
        game_round = game_round or self.game_round
        response = self.api.get(f"/api/rounds/{game_round.pk}/timeline/")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        return body["data"]


class TestTimelineShape(_TimelineTestCase):
    def test_data_keys(self) -> None:
        self.assertEqual(
            list(self.data()), ["events", "players", "highlights", "summary"]
        )

    def test_event_row_has_exactly_the_eleven_keys_in_order(self) -> None:
        self.event(target=self.blue_hvy, points_awarded=100)
        events = self.data()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(list(events[0]), _EVENT_KEYS)

    def test_event_row_values(self) -> None:
        self.event(
            timestamp=37,
            event_type="tag",
            actor=self.red_cmd,
            target=self.blue_hvy,
            points_awarded=100,
            description="TlRed commander tagged TlBlue heavy",
            metadata={"lives": 4},
        )
        self.assertEqual(
            self.data()["events"][0],
            {
                "tick": 37,
                "event_type": "tag",
                "points": 100,
                "description": "TlRed commander tagged TlBlue heavy",
                "actor_id": self.red_cmd.pk,
                "actor_name": "TlRed commander",
                "actor_side": "red",
                "target_id": self.blue_hvy.pk,
                "target_name": "TlBlue heavy",
                "target_side": "blue",
                "metadata": {"lives": 4},
            },
        )

    def test_targetless_event_has_null_target_fields_and_empty_metadata(
        self,
    ) -> None:
        self.event(event_type="miss", actor=self.blue_ammo)
        row = self.data()["events"][0]
        self.assertIsNone(row["target_id"])
        self.assertIsNone(row["target_name"])
        self.assertIsNone(row["target_side"])
        self.assertEqual(row["metadata"], {})
        self.assertEqual(row["actor_side"], "blue")

    def test_sides_come_from_team_color_not_the_players_team(self) -> None:
        """A borrowed player (LG-07 drawn teams): a red Team's Player playing
        on the blue Side of this round reads ``blue``."""
        borrowed = self.red_players["heavy"]
        PlayerRoundState.objects.create(
            game_round=self.game_round,
            player=borrowed,
            team_color="blue",
            role="heavy",
        )
        self.event(actor=borrowed, target=self.red_cmd)
        row = self.data()["events"][0]
        self.assertEqual(row["actor_side"], "blue")
        self.assertEqual(row["target_side"], "red")

    def test_movement_and_base_capture_rows_are_included(self) -> None:
        self.event(timestamp=1, event_type="movement")
        self.event(timestamp=2, event_type="base_capture", actor=self.blue_hvy)
        self.event(timestamp=3, event_type="combo_resupply", actor=self.red_med)
        self.event(timestamp=4, event_type="missile_dodge", actor=self.blue_ammo)
        self.assertEqual(
            [e["event_type"] for e in self.data()["events"]],
            ["movement", "base_capture", "combo_resupply", "missile_dodge"],
        )

    def test_events_in_timestamp_then_insertion_order(self) -> None:
        self.event(timestamp=20, description="late")
        self.event(timestamp=5, description="tie-first")
        self.event(timestamp=5, description="tie-second")
        self.event(timestamp=0, description="start")
        self.assertEqual(
            [e["description"] for e in self.data()["events"]],
            ["start", "tie-first", "tie-second", "late"],
        )


class TestTimelinePlayersHighlightsSummary(_TimelineTestCase):
    def test_players_in_player_state_order_with_four_keys(self) -> None:
        self.assertEqual(
            self.data()["players"],
            [
                {
                    "id": self.red_cmd.pk,
                    "name": "TlRed commander",
                    "side": "red",
                    "role": "commander",
                },
                {
                    "id": self.blue_hvy.pk,
                    "name": "TlBlue heavy",
                    "side": "blue",
                    "role": "heavy",
                },
                {
                    "id": self.red_med.pk,
                    "name": "TlRed medic",
                    "side": "red",
                    "role": "medic",
                },
                {
                    "id": self.blue_ammo.pk,
                    "name": "TlBlue ammo",
                    "side": "blue",
                    "role": "ammo",
                },
            ],
        )

    def test_highlights_passed_through_unchanged(self) -> None:
        highlights = [
            {
                "kind": "first_elimination",
                "tick": 120,
                "team": "blue",
                "actor": "TlBlue heavy",
                "target": "TlRed commander",
                "points": 0,
                "label": "First elimination",
            }
        ]
        self.game_round.highlights_json = highlights
        self.game_round.save()
        self.assertEqual(self.data()["highlights"], highlights)

    def test_highlights_empty_list_when_null(self) -> None:
        self.assertIsNone(self.game_round.highlights_json)
        self.assertEqual(self.data()["highlights"], [])

    def test_summary_counts(self) -> None:
        self.event(event_type="tag", target=self.blue_hvy)
        self.event(event_type="tag", target=self.blue_ammo)
        self.event(event_type="movement")
        self.event(event_type="miss")
        self.event(event_type="missiled", target=self.blue_hvy)
        self.event(event_type="elimination", target=self.blue_hvy)
        self.event(event_type="resupply_ammo", actor=self.blue_ammo)
        self.event(event_type="resupply_lives", actor=self.red_med)
        self.event(event_type="special")
        expected = {
            "total_events": 9,
            "tags": 2,
            "moves": 1,
            "missiles": 1,
            "misses": 1,
            "eliminations": 1,
            "resupplies": 2,
            "specials": 1,
        }
        self.assertEqual(self.game_round.get_event_summary(), expected)
        self.assertEqual(self.data()["summary"], expected)


class TestTimelineAccess(_TimelineTestCase):
    def assert_404(self, response) -> None:
        self.assertEqual(response.status_code, 404, response.content)
        errors = response.json()["errors"]
        self.assertEqual(errors[0]["code"], "not_found")

    def test_foreign_round_is_404(self) -> None:
        foreign = GameRound.objects.create(
            round_number=1,
            team_red=self.red,
            team_blue=self.blue,
            manager=self.user_b,
        )
        self.assert_404(self.api.get(f"/api/rounds/{foreign.pk}/timeline/"))

    def test_missing_round_is_404(self) -> None:
        self.assert_404(self.api.get("/api/rounds/999999/timeline/"))

    def test_anonymous_is_401(self) -> None:
        response = APIClient().get(f"/api/rounds/{self.game_round.pk}/timeline/")
        self.assertEqual(response.status_code, 401)

    def test_post_is_405(self) -> None:
        response = self.api.post(
            f"/api/rounds/{self.game_round.pk}/timeline/", {}, format="json"
        )
        self.assertEqual(response.status_code, 405, response.content)
        self.assertEqual(response.json()["errors"][0]["code"], "method_not_allowed")


class TestTimelineQueryCount(_TimelineTestCase):
    def test_query_count_does_not_grow_with_events(self) -> None:
        """No per-event queries (ANG-06d retro check)."""
        url = f"/api/rounds/{self.game_round.pk}/timeline/"
        self.event(target=self.blue_hvy)
        with CaptureQueriesContext(connection) as few:
            self.assertEqual(self.api.get(url).status_code, 200)
        for tick in range(30):
            self.event(timestamp=tick, actor=self.blue_ammo, target=self.red_med)
            self.event(timestamp=tick, event_type="miss", actor=self.red_med)
        with self.assertNumQueries(len(few.captured_queries)):
            self.assertEqual(self.api.get(url).status_code, 200)


class TestTimelineSavedRound(TestCase):
    """A real batch-saved (``scores``-tier) round, upgraded to ``full``."""

    def setUp(self) -> None:
        super().setUp()
        self.api = APIClient()
        self.api.force_login(get_shared_manager())
        red, _ = make_team_with_slots("TlSavRed")
        blue, _ = make_team_with_slots("TlSavBlue")
        self.game_round = _saved_round(red, blue)

    def get_data(self) -> dict:
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.api.get(f"/api/rounds/{self.game_round.pk}/timeline/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]

    def test_upgrades_scores_round_to_full(self) -> None:
        self.assertEqual(self.game_round.events.count(), 0)
        data = self.get_data()
        self.game_round.refresh_from_db()
        self.assertEqual(self.game_round.fidelity, "full")
        self.assertGreater(len(data["events"]), 0)
        self.assertEqual(len(data["events"]), self.game_round.events.count())

    def test_events_agree_row_for_row_with_round_playback_payload(self) -> None:
        """Drift guard: the API timeline and the Django Event log page are
        built from the same rows."""
        events = self.get_data()["events"]
        self.game_round.refresh_from_db()
        payload_rows, _ = round_playback_payload(self.game_round)
        self.assertEqual(len(events), len(payload_rows))
        for event, row in zip(events, payload_rows):
            self.assertEqual(
                (
                    event["event_type"],
                    event["tick"],
                    event["points"],
                    event["actor_id"],
                    event["actor_name"],
                    event["target_id"],
                    event["target_name"],
                ),
                (
                    row["type"],
                    row["ts"],
                    row["pts"],
                    row["aid"],
                    row["an"],
                    None if row["tid"] == -1 else row["tid"],
                    None if row["tid"] == -1 else row["tn"],
                ),
            )


class TestTimelineRoute(TestCase):
    def test_route_name(self) -> None:
        self.assertEqual(
            reverse("gameround-timeline", args=[7]), "/api/rounds/7/timeline/"
        )
