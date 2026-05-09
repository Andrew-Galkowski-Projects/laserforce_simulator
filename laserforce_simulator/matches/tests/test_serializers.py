import pytest

from matches.models import GameEvent, GameRound, Match, PlayerRoundState
from matches.serializers import (
    GameEventSerializer,
    GameRoundListSerializer,
    GameRoundSerializer,
    MatchSerializer,
    PlayerRoundStateSerializer,
)
from matches.tests.conftest import make_team_with_slots


@pytest.mark.django_db
class TestMatchSerializer:
    def setup_method(self):
        self.red, _ = make_team_with_slots("Red")
        self.blue, _ = make_team_with_slots("Blue")
        self.match = Match.objects.create(
            team_red=self.red,
            team_blue=self.blue,
            match_type="friendly",
        )

    def test_includes_core_fields(self):
        data = MatchSerializer(self.match).data
        for field in (
            "id",
            "team_red",
            "team_blue",
            "match_type",
            "date_played",
            "is_completed",
            "winner",
        ):
            assert field in data, f"Missing field: {field}"

    def test_includes_score_fields(self):
        data = MatchSerializer(self.match).data
        for field in (
            "red_round1_points",
            "blue_round1_points",
            "red_round2_points",
            "blue_round2_points",
        ):
            assert field in data, f"Missing score field: {field}"

    def test_includes_round_ids_not_nested(self):
        data = MatchSerializer(self.match).data
        assert "round_ids" in data
        assert isinstance(data["round_ids"], list)

    def test_round_ids_lists_associated_rounds(self):
        round_ = GameRound.objects.create(
            match=self.match, round_number=1, team_red=self.red, team_blue=self.blue
        )
        data = MatchSerializer(self.match).data
        assert round_.pk in data["round_ids"]


@pytest.mark.django_db
class TestGameRoundListSerializer:
    def setup_method(self):
        self.red, _ = make_team_with_slots("Red")
        self.blue, _ = make_team_with_slots("Blue")
        self.game_round = GameRound.objects.create(
            round_number=1,
            team_red=self.red,
            team_blue=self.blue,
            event_log="some legacy text",
        )

    def test_excludes_player_states(self):
        data = GameRoundListSerializer(self.game_round).data
        assert "player_states" not in data

    def test_excludes_event_log(self):
        data = GameRoundListSerializer(self.game_round).data
        assert "event_log" not in data

    def test_includes_round_fields(self):
        data = GameRoundListSerializer(self.game_round).data
        for field in (
            "id",
            "round_number",
            "red_points",
            "blue_points",
            "is_completed",
        ):
            assert field in data, f"Missing field: {field}"


@pytest.mark.django_db
class TestGameRoundSerializer:
    def setup_method(self):
        self.red, self.players = make_team_with_slots("Red")
        self.blue, _ = make_team_with_slots("Blue")
        self.game_round = GameRound.objects.create(
            round_number=1,
            team_red=self.red,
            team_blue=self.blue,
            event_log="some legacy text",
        )

    def test_excludes_event_log(self):
        data = GameRoundSerializer(self.game_round).data
        assert "event_log" not in data

    def test_includes_score_and_status_fields(self):
        data = GameRoundSerializer(self.game_round).data
        for field in (
            "id",
            "round_number",
            "red_points",
            "blue_points",
            "is_completed",
            "winner",
        ):
            assert field in data, f"Missing field: {field}"

    def test_includes_player_states_list(self):
        data = GameRoundSerializer(self.game_round).data
        assert "player_states" in data
        assert isinstance(data["player_states"], list)

    def test_player_states_reflect_db_records(self):
        player = self.players["commander"]
        PlayerRoundState.objects.create(
            game_round=self.game_round,
            player=player,
            team_color="red",
            role="Commander",
        )
        data = GameRoundSerializer(self.game_round).data
        assert len(data["player_states"]) == 1
        assert data["player_states"][0]["role"] == "Commander"


@pytest.mark.django_db
class TestPlayerRoundStateSerializer:
    def setup_method(self):
        self.red, self.players = make_team_with_slots("Red")
        self.blue, _ = make_team_with_slots("Blue")
        self.game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.state = PlayerRoundState.objects.create(
            game_round=self.game_round,
            player=self.players["heavy"],
            team_color="red",
            role="Heavy",
            points_scored=1500,
            tags_made=10,
        )

    def test_excludes_game_round_fk(self):
        data = PlayerRoundStateSerializer(self.state).data
        assert "game_round" not in data

    def test_includes_tracking_fields(self):
        data = PlayerRoundStateSerializer(self.state).data
        for field in (
            "id",
            "player",
            "team_color",
            "role",
            "points_scored",
            "tags_made",
            "shots_missed",
            "times_tagged",
        ):
            assert field in data, f"Missing field: {field}"

    def test_stat_values_are_correct(self):
        data = PlayerRoundStateSerializer(self.state).data
        assert data["points_scored"] == 1500
        assert data["tags_made"] == 10


@pytest.mark.django_db
class TestGameEventSerializer:
    def setup_method(self):
        self.red, self.players = make_team_with_slots("Red")
        self.blue, self.blue_players = make_team_with_slots("Blue")
        self.game_round = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        self.event = GameEvent.objects.create(
            game_round=self.game_round,
            timestamp=42,
            event_type="tag",
            actor=self.players["commander"],
            target=self.blue_players["scout"],
            points_awarded=100,
            description="Commander tags Scout",
        )

    def test_excludes_game_round_fk(self):
        data = GameEventSerializer(self.event).data
        assert "game_round" not in data

    def test_includes_event_fields(self):
        data = GameEventSerializer(self.event).data
        for field in (
            "id",
            "timestamp",
            "event_type",
            "actor",
            "target",
            "points_awarded",
            "description",
            "metadata",
        ):
            assert field in data, f"Missing field: {field}"

    def test_field_values_are_correct(self):
        data = GameEventSerializer(self.event).data
        assert data["timestamp"] == 42
        assert data["event_type"] == "tag"
        assert data["points_awarded"] == 100
