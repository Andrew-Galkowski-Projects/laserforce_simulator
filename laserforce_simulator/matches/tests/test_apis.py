import pytest
from rest_framework.test import APIClient

from matches.models import GameEvent, GameRound, Match, PlayerRoundState
from matches.tests.conftest import make_team_with_slots


@pytest.mark.django_db
class TestMatchAPI:
    """API-01: Read-only REST endpoints for /api/matches/."""

    def setup_method(self):
        self.client = APIClient()
        self.red, _ = make_team_with_slots("Red")
        self.blue, _ = make_team_with_slots("Blue")
        self.match = Match.objects.create(
            team_red=self.red, team_blue=self.blue, match_type="friendly"
        )

    def test_list_returns_200(self):
        assert self.client.get("/api/matches/").status_code == 200

    def test_list_is_paginated(self):
        data = self.client.get("/api/matches/").json()
        assert "count" in data and "results" in data

    def test_detail_returns_200(self):
        assert self.client.get(f"/api/matches/{self.match.pk}/").status_code == 200

    def test_detail_includes_expected_fields(self):
        data = self.client.get(f"/api/matches/{self.match.pk}/").json()
        for field in (
            "id",
            "team_red",
            "team_blue",
            "match_type",
            "date_played",
            "is_completed",
            "round_ids",
        ):
            assert field in data, f"Missing field: {field}"

    def test_detail_404_for_missing(self):
        assert self.client.get("/api/matches/99999/").status_code == 404

    def test_detail_round_ids_populated_when_rounds_exist(self):
        round_ = GameRound.objects.create(
            match=self.match, round_number=1, team_red=self.red, team_blue=self.blue
        )
        data = self.client.get(f"/api/matches/{self.match.pk}/").json()
        assert round_.pk in data["round_ids"]

    def test_write_methods_not_allowed(self):
        assert self.client.post("/api/matches/", {}, format="json").status_code == 405
        assert self.client.delete(f"/api/matches/{self.match.pk}/").status_code == 405


@pytest.mark.django_db
class TestGameRoundAPI:
    """API-01: Read-only REST endpoints for /api/rounds/."""

    def setup_method(self):
        self.client = APIClient()
        self.red, self.players = make_team_with_slots("Red")
        self.blue, _ = make_team_with_slots("Blue")
        self.game_round = GameRound.objects.create(
            round_number=1,
            team_red=self.red,
            team_blue=self.blue,
        )
        self.state = PlayerRoundState.objects.create(
            game_round=self.game_round,
            player=self.players["commander"],
            team_color="red",
            role="Commander",
            points_scored=2000,
        )
        self.event = GameEvent.objects.create(
            game_round=self.game_round,
            timestamp=10,
            event_type="tag",
            actor=self.players["commander"],
            points_awarded=100,
        )

    def test_list_returns_200(self):
        assert self.client.get("/api/rounds/").status_code == 200

    def test_list_is_paginated(self):
        data = self.client.get("/api/rounds/").json()
        assert "count" in data and "results" in data

    def test_detail_returns_200(self):
        assert self.client.get(f"/api/rounds/{self.game_round.pk}/").status_code == 200

    def test_detail_includes_player_states(self):
        data = self.client.get(f"/api/rounds/{self.game_round.pk}/").json()
        assert "player_states" in data
        assert len(data["player_states"]) == 1
        assert data["player_states"][0]["role"] == "Commander"

    def test_detail_excludes_event_log(self):
        data = self.client.get(f"/api/rounds/{self.game_round.pk}/").json()
        assert "event_log" not in data

    def test_detail_includes_score_fields(self):
        data = self.client.get(f"/api/rounds/{self.game_round.pk}/").json()
        for field in (
            "id",
            "round_number",
            "red_points",
            "blue_points",
            "is_completed",
        ):
            assert field in data, f"Missing field: {field}"

    def test_detail_404_for_missing(self):
        assert self.client.get("/api/rounds/99999/").status_code == 404

    def test_events_action_returns_200(self):
        assert (
            self.client.get(f"/api/rounds/{self.game_round.pk}/events/").status_code
            == 200
        )

    def test_events_action_is_paginated(self):
        data = self.client.get(f"/api/rounds/{self.game_round.pk}/events/").json()
        assert "count" in data and "results" in data

    def test_events_action_returns_correct_events(self):
        data = self.client.get(f"/api/rounds/{self.game_round.pk}/events/").json()
        assert data["count"] == 1
        assert data["results"][0]["event_type"] == "tag"
        assert data["results"][0]["timestamp"] == 10

    def test_events_action_excludes_game_round_fk(self):
        data = self.client.get(f"/api/rounds/{self.game_round.pk}/events/").json()
        assert "game_round" not in data["results"][0]

    def test_events_ordered_by_timestamp(self):
        GameEvent.objects.create(
            game_round=self.game_round,
            timestamp=5,
            event_type="miss",
            actor=self.players["commander"],
            points_awarded=0,
        )
        data = self.client.get(f"/api/rounds/{self.game_round.pk}/events/").json()
        timestamps = [e["timestamp"] for e in data["results"]]
        assert timestamps == sorted(timestamps)

    def test_events_for_nonexistent_round_returns_404(self):
        assert self.client.get("/api/rounds/99999/events/").status_code == 404

    def test_list_excludes_player_states(self):
        data = self.client.get("/api/rounds/").json()
        assert data["count"] >= 1
        assert "player_states" not in data["results"][0]

    def test_write_methods_not_allowed(self):
        assert self.client.post("/api/rounds/", {}, format="json").status_code == 405
