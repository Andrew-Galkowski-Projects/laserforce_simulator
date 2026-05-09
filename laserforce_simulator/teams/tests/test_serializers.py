import pytest

from teams.models import Player, Team
from teams.serializers import PlayerSerializer, TeamListSerializer, TeamSerializer


@pytest.mark.django_db
class TestPlayerSerializer:
    def setup_method(self):
        self.team = Team.objects.create(name="Serializer Team")
        self.player = Player.objects.create(
            team=self.team,
            name="Test Player",
            accuracy=75,
            survival=60,
            speed=80,
        )

    def test_includes_identity_fields(self):
        data = PlayerSerializer(self.player).data
        assert data["id"] == self.player.pk
        assert data["name"] == "Test Player"

    def test_includes_team_fk(self):
        data = PlayerSerializer(self.player).data
        assert data["team"] == self.team.pk

    def test_includes_all_stat_fields(self):
        data = PlayerSerializer(self.player).data
        expected_stats = [
            "accuracy",
            "survival",
            "stamina",
            "speed",
            "decision_making",
            "positioning",
            "game_awareness",
            "player_awareness",
            "resource_awareness",
            "flexibility",
            "adaptability",
            "communication",
            "teamwork",
            "Offensive_synergy",
            "defensive_synergy",
            "midfield_synergy",
            "resupply_synergy",
            "resupply_efficiency",
            "special_usage",
        ]
        for stat in expected_stats:
            assert stat in data, f"Missing stat field: {stat}"

    def test_stat_values_are_correct(self):
        data = PlayerSerializer(self.player).data
        assert data["accuracy"] == 75
        assert data["survival"] == 60
        assert data["speed"] == 80

    def test_includes_preferred_roles(self):
        data = PlayerSerializer(self.player).data
        assert "preferred_roles" in data


@pytest.mark.django_db
class TestTeamSerializer:
    def setup_method(self):
        self.team = Team.objects.create(name="Team Alpha", wins=5, losses=2)
        self.player1 = Player.objects.create(team=self.team, name="Player One")
        self.player2 = Player.objects.create(team=self.team, name="Player Two")

    def test_includes_identity_and_record_fields(self):
        data = TeamSerializer(self.team).data
        assert data["id"] == self.team.pk
        assert data["name"] == "Team Alpha"
        assert data["wins"] == 5
        assert data["losses"] == 2

    def test_includes_nested_players(self):
        data = TeamSerializer(self.team).data
        assert "players" in data
        names = [p["name"] for p in data["players"]]
        assert "Player One" in names
        assert "Player Two" in names

    def test_nested_players_include_stats(self):
        data = TeamSerializer(self.team).data
        player_data = data["players"][0]
        assert "accuracy" in player_data

    def test_includes_slot_fields(self):
        data = TeamSerializer(self.team).data
        for slot in (
            "slot_commander",
            "slot_heavy",
            "slot_scout_1",
            "slot_scout_2",
            "slot_medic",
            "slot_ammo",
        ):
            assert slot in data, f"Missing slot field: {slot}"

    def test_empty_slots_are_null(self):
        data = TeamSerializer(self.team).data
        assert data["slot_commander"] is None

    def test_filled_slot_returns_pk(self):
        self.team.slot_commander = self.player1
        self.team.save()
        data = TeamSerializer(self.team).data
        assert data["slot_commander"] == self.player1.pk


@pytest.mark.django_db
class TestTeamListSerializer:
    def setup_method(self):
        self.team = Team.objects.create(name="List Team")
        self.player = Player.objects.create(team=self.team, name="Slim Player")

    def test_players_are_inline(self):
        data = TeamListSerializer(self.team).data
        assert "players" in data
        player_data = data["players"][0]
        assert "id" in player_data
        assert "name" in player_data
        assert "preferred_roles" in player_data

    def test_players_omit_full_stats(self):
        data = TeamListSerializer(self.team).data
        player_data = data["players"][0]
        assert "accuracy" not in player_data
        assert "stamina" not in player_data

    def test_includes_slot_and_record_fields(self):
        data = TeamListSerializer(self.team).data
        for field in ("id", "name", "wins", "losses", "slot_commander"):
            assert field in data, f"Missing field: {field}"
