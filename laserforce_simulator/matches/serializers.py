from rest_framework import serializers

from matches.api_refs import league_ref, season_ref, team_ref
from matches.models import (
    Conference,
    GameEvent,
    GameRound,
    League,
    Match,
    PlayerRoundState,
    Season,
    SeasonPhase,
    Tournament,
)
from matches.tournament_services.detail import format_label, is_embedded

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


# ---------------------------------------------------------------------------
# ANG-01d-1 — League / Season (output only; writes bind Django Forms).
# ---------------------------------------------------------------------------


class LeagueSerializer(serializers.ModelSerializer):
    current_team = serializers.SerializerMethodField()
    active_season = serializers.SerializerMethodField()

    class Meta:
        model = League
        fields = [
            "id",
            "name",
            "mode",
            "state",
            "visibility",
            "finance_enabled",
            "challenge_fired_luxury_tax",
            "manager",
            "current_team",
            "free_agent_pool",
            "active_season",
            "created_at",
        ]

    def get_current_team(self, obj: League) -> dict | None:
        return team_ref(obj.current_team) if obj.current_team_id is not None else None

    def get_active_season(self, obj: League) -> dict | None:
        season = obj.active_season
        return season_ref(season) if season is not None else None


_SEASON_LIST_FIELDS = [
    "id",
    "league",
    "name",
    "start_date",
    "state",
    "schedule_format",
    "map_mode",
    "champion_team",
    "created_at",
]


class SeasonListSerializer(serializers.ModelSerializer):
    league = serializers.SerializerMethodField()
    champion_team = serializers.SerializerMethodField()

    class Meta:
        model = Season
        fields = _SEASON_LIST_FIELDS

    def get_league(self, obj: Season) -> dict:
        return league_ref(obj.league)

    def get_champion_team(self, obj: Season) -> dict | None:
        if obj.champion_team_id is None:
            return None
        return team_ref(obj.champion_team)


class SeasonPhaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = SeasonPhase
        fields = [
            "id",
            "ordinal",
            "phase_type",
            "schedule_format",
            "tournament_mode",
            "tournament_format",
            "tournament_cut",
            "final_series_length",
            "semifinal_series_length",
            "quarterfinal_series_length",
            "earlier_series_length",
            "wb_advancers",
            "lb_advancers",
            "swiss_rounds",
            "tournament",
        ]


class ConferenceSerializer(serializers.ModelSerializer):
    team_ids = serializers.PrimaryKeyRelatedField(
        many=True, read_only=True, source="teams"
    )

    class Meta:
        model = Conference
        fields = ["id", "name", "ordinal", "team_ids"]


class SeasonSerializer(SeasonListSerializer):
    """Season detail: list fields + enrolment / map / snapshot / phases /
    conferences. The 01d-2 play state (``active_play_job_id`` /
    ``play_cancel_requested``) is deliberately excluded."""

    team_ids = serializers.PrimaryKeyRelatedField(
        many=True, read_only=True, source="teams"
    )
    map_pool_ids = serializers.PrimaryKeyRelatedField(
        many=True, read_only=True, source="map_pool"
    )
    phases = SeasonPhaseSerializer(many=True, read_only=True)
    conferences = ConferenceSerializer(many=True, read_only=True)

    class Meta:
        model = Season
        fields = _SEASON_LIST_FIELDS + [
            "team_ids",
            "map_pool_ids",
            "starting_team_ids_json",
            "starting_map_pool_ids_json",
            "map_rotation_ids_json",
            "starting_map_rotation_ids_json",
            "phases",
            "conferences",
        ]


class TournamentSerializer(serializers.ModelSerializer):
    """ANG-01f — a Tournament's own fields (output only).

    ``format_label`` is the derived display label ("Random Draw" for a
    Player-pool draw played as RR->DE); ``embedded`` is true for an in-League
    Tournament, whose ``season`` ref is set (null for a sandbox Tournament).
    ``active_play_job_id`` is play-run state and is not exposed here.
    """

    format_label = serializers.SerializerMethodField()
    champion = serializers.SerializerMethodField()
    embedded = serializers.SerializerMethodField()
    season = serializers.SerializerMethodField()

    class Meta:
        model = Tournament
        fields = [
            "id",
            "name",
            "format",
            "format_label",
            "state",
            "team_assembly",
            "role_assignment_mode",
            "champion",
            "embedded",
            "season",
            "final_series_length",
            "semifinal_series_length",
            "quarterfinal_series_length",
            "earlier_series_length",
            "wb_advancers",
            "lb_advancers",
            "swiss_rounds",
            "manager",
            "created_at",
        ]

    def get_format_label(self, obj: Tournament) -> str:
        return format_label(obj)

    def get_champion(self, obj: Tournament) -> dict | None:
        return team_ref(obj.champion) if obj.champion_id is not None else None

    def get_embedded(self, obj: Tournament) -> bool:
        # The list queryset annotates it; a lone instance (detail) asks.
        annotated = getattr(obj, "annotated_embedded", None)
        return is_embedded(obj) if annotated is None else bool(annotated)

    def get_season(self, obj: Tournament) -> dict | None:
        if obj.season_phase_id is not None:
            return season_ref(obj.season_phase.season)
        if "season_phases" in getattr(obj, "_prefetched_objects_cache", {}):
            # Prefetched (list): already ordered by ``ordinal`` with the Season.
            phases = list(obj.season_phases.all())
            phase = phases[0] if phases else None
        else:
            phase = obj.season_phases.select_related("season").first()
        return season_ref(phase.season) if phase is not None else None
