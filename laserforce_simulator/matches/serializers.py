from rest_framework import serializers

from matches.models import GameEvent, GameRound, Match, PlayerRoundState

_GAME_ROUND_FIELDS = [
    "id",
    "match",
    "round_number",
    "team_red",
    "team_blue",
    "date_played",
    "red_points",
    "blue_points",
    "red_team_eliminated",
    "blue_team_eliminated",
    "eliminated_at",
    "winner",
    "is_completed",
]


class GameEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = GameEvent
        exclude = ["game_round"]


class PlayerRoundStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlayerRoundState
        exclude = ["game_round"]


class GameRoundListSerializer(serializers.ModelSerializer):
    """Slim round serializer for list responses — omits player_states."""

    class Meta:
        model = GameRound
        fields = _GAME_ROUND_FIELDS


class GameRoundSerializer(serializers.ModelSerializer):
    """Full round detail including all player states."""

    player_states = PlayerRoundStateSerializer(many=True, read_only=True)

    class Meta:
        model = GameRound
        # event_log is a legacy text dump; structured data is served via /events/
        fields = _GAME_ROUND_FIELDS + ["player_states"]


class MatchSerializer(serializers.ModelSerializer):
    round_ids = serializers.PrimaryKeyRelatedField(
        many=True, read_only=True, source="game_rounds"
    )

    class Meta:
        model = Match
        fields = "__all__"
