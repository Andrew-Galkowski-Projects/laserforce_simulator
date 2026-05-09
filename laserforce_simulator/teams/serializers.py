from rest_framework import serializers

from teams.models import Player, Team

_PLAYER_STAT_FIELDS = [
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
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
]

_TEAM_BASE_FIELDS = [
    "id",
    "name",
    "created_date",
    "wins",
    "losses",
    "slot_commander",
    "slot_heavy",
    "slot_scout_1",
    "slot_scout_2",
    "slot_medic",
    "slot_ammo",
]


class PlayerInlineSerializer(serializers.ModelSerializer):
    """Minimal player representation used in team list responses."""

    class Meta:
        model = Player
        fields = ["id", "name", "preferred_roles"]


class PlayerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Player
        fields = ["id", "team", "name", "preferred_roles"] + _PLAYER_STAT_FIELDS


class TeamListSerializer(serializers.ModelSerializer):
    """Slim team serializer for list responses — players include id/name only."""

    players = PlayerInlineSerializer(many=True, read_only=True)

    class Meta:
        model = Team
        fields = _TEAM_BASE_FIELDS + ["players"]


class TeamSerializer(serializers.ModelSerializer):
    """Full team detail including all player stats."""

    players = PlayerSerializer(many=True, read_only=True)

    class Meta:
        model = Team
        fields = _TEAM_BASE_FIELDS + ["players"]
