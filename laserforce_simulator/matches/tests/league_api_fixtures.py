"""ANG-01d-1 -- shared fixtures for the League lifecycle + reads API tests.

Test-support only (not collected: the name matches no ``python_files`` glob).
Imports nothing from ``matches.league_services`` / ``matches.league_api_views``
so it loads before those modules exist.

Every completed Season is HAND-BUILT (Match + GameRound rows with fixed points,
``state="completed"``, ``starting_team_ids_json`` set) so the standings order is
deterministic and no simulator runs. Tests assert structure, codes, counts and
FK identity -- never simulated point totals.
"""

from __future__ import annotations

import io
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from conftest import get_shared_manager
from core.models import ArenaMap, MapBaseConfig, MapZoneConfig
from matches.models import (
    Conference,
    GameRound,
    League,
    Match,
    OwnerEvaluation,
    PlayerRoundState,
    Season,
    SeasonPhase,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

User = get_user_model()

# ---------------------------------------------------------------------------
# Key sets (seam contract sections 4 and 6)
# ---------------------------------------------------------------------------

LEAGUE_KEYS = {
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
}
LEAGUE_CREATE_KEYS = LEAGUE_KEYS | {"draft_season", "conference_ids"}

SEASON_LIST_KEYS = {
    "id",
    "league",
    "name",
    "start_date",
    "state",
    "schedule_format",
    "map_mode",
    "champion_team",
    "created_at",
}
SEASON_DETAIL_KEYS = SEASON_LIST_KEYS | {
    "team_ids",
    "map_pool_ids",
    "starting_team_ids_json",
    "starting_map_pool_ids_json",
    "map_rotation_ids_json",
    "starting_map_rotation_ids_json",
    "phases",
    "conferences",
}
PHASE_KEYS = {
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
}
CONFERENCE_SERIALIZER_KEYS = {"id", "name", "ordinal", "team_ids"}

REF_KEYS = {"id", "name"}

TEMPLATE_KEYS = {
    "key",
    "label",
    "num_teams",
    "phases",
    "finance_enabled",
    "challenge_fired_luxury_tax",
    "mean",
    "std_dev",
    "map_mode",
}

# The 17 StandingsRow fields, in dataclass order, plus the flattened team ref.
STANDINGS_FIELDS = [
    "team_id",
    "matches_played",
    "wins",
    "losses",
    "ties",
    "league_points",
    "round_wins",
    "total_score",
    "rank",
    "match_streak",
    "match_l5",
    "round_streak",
    "round_l5",
    "red_wlt",
    "blue_wlt",
    "red_points_for",
    "blue_points_for",
]
STANDINGS_ROW_KEYS = set(STANDINGS_FIELDS) | {"team"}

# The dashboard snippet rows: the 9 frozen snippet keys + team ref.
SNIPPET_ROW_KEYS = {
    "team_id",
    "matches_played",
    "wins",
    "losses",
    "ties",
    "league_points",
    "round_wins",
    "total_score",
    "rank",
    "team",
}
LEADER_KEYS = {
    "player_id",
    "player_name",
    "role",
    "team_id",
    "team_name",
    "value",
    "games_played",
    "rank",
}
WINNER_KEYS = {"player_id", "player_name", "role", "team_id", "team_name", "value"}

DASHBOARD_BODY_KEYS = {
    "displayed_season",
    "season_mode",
    "standings_snippet",
    "next_fixture",
    "round_count_completed",
    "round_count_total",
    "leaders_points",
    "leaders_tags",
    "leaders_ratio",
    "map_config_label",
    "playoff_tournament_id",
}
# ANG-06-14 B5 -- the League dashboard (only) also says the League's mode, the
# managed team, whether owner evaluations exist, member-night progress and
# whether a fired Manager awaits reassignment.
LEAGUE_DASHBOARD_KEYS = DASHBOARD_BODY_KEYS | {
    "league",
    "mode",
    "managed_team",
    "has_owner_evaluations",
    "member_night_progress",
    "manager_awaiting_reassignment",
}
SEASON_DASHBOARD_KEYS = DASHBOARD_BODY_KEYS | {"season", "league"}
# Play-control keys owned by ANG-01d-2 -- must never leak into the 01d-1 payload.
PLAY_CONTROL_KEYS = {
    "action_button_label",
    "action_button_state",
    "playoff_phase_active",
    "playoff_completed",
    "has_following_tournament_phase",
    "following_tournament_is_final",
    "live_preview_available",
    "is_career_mode",
    "play_error",
    "play_job_id",
}

HISTORY_ROW_KEYS = {
    "season_id",
    "season_name",
    "season_url",
    "start_date",
    "teams_enrolled",
    "matches_played",
    "champion",
    "runner_up",
    "tournament_champion",
    "top_three",
    "is_in_progress",
    "season_mvp",
    "finals_mvp",
}

TEAM_SCHEDULE_KEYS = {
    "league",
    "season",
    "team",
    "upcoming",
    "completed",
    "member_night_appearances",
}
UPCOMING_ROW_KEYS = {
    "matchday",
    "round_number",
    "date",
    "red_team_id",
    "red_team_name",
    "blue_team_id",
    "blue_team_name",
}
COMPLETED_ROW_KEYS = UPCOMING_ROW_KEYS | {
    "game_round_id",
    "red_score",
    "blue_score",
    "outcome",
}

SCHEDULE_FIXTURE_KEYS = {
    "matchday",
    "round_number",
    "team_a_id",
    "team_b_id",
    "team_a",
    "team_b",
    "played",
    "game_round_id",
    "red_score",
    "blue_score",
    "date",
}

AWARDS_KEYS = {
    "season",
    "has_rounds",
    "most_points",
    "best_accuracy",
    "kd_by_role",
    "best_medic",
    "most_efficient_nuke",
    "season_mvp",
    "finals_mvp",
}
ROLE_KEYS = ("commander", "heavy", "scout", "medic", "ammo")

OWNER_EVAL_KEYS = {
    "season",
    "league",
    "team_managed",
    "wins_delta",
    "playoffs_delta",
    "money_delta",
    "wins_total",
    "playoffs_total",
    "money_total",
    "overall_mood",
    "verdict",
    "hot_seat_level",
    "fired_reason",
    "is_fired",
    "reassigned",
    "created_at",
}

CONFERENCES_STATE_KEYS = {
    "season",
    "editable",
    "map_mode",
    "teams",
    "conferences",
    "unassigned_team_ids",
}
CONFERENCE_STATE_ROW_KEYS = {
    "id",
    "name",
    "ordinal",
    "team_ids",
    "map_rotation_ids",
    "starting_team_ids",
    "starting_map_rotation_ids",
}

# ---------------------------------------------------------------------------
# Locked messages
# ---------------------------------------------------------------------------

SEASON_IN_PROGRESS_MSG = "The League already has a Season in progress."
MANAGER_FIRED_MSG = (
    "The Manager was fired. Choose a new team before starting the next Season."
)
SEASON_NOT_DRAFT_MSG = "Conferences can only be edited while the Season is in draft."
NO_COMPLETED_MSG = "No completed Season in this League."
DELETE_NOT_CAREER_MSG = (
    "Delete League is only available for career (league-mode) Leagues."
)
FIRING_NOT_CAREER_MSG = "Manager firing applies only in single-player career mode."

SHAPE_MSG = (
    'conferences must be a list of {"name", "team_ids", "map_rotation_ids"} objects.'
)
NAMES_EMPTY_MSG = "Conference names cannot be empty."
UNASSIGNED_MSG = "Every team must be assigned to a conference."
UNDER_TWO_MSG = "Each conference needs at least 2 teams."
BRACKET_FLOOR_MSG = (
    "Each conference needs at least 4 teams to hold its own playoff bracket."
)
ROTATION_INVALID_MSG = "Map rotation contains an invalid id."
ROTATION_UNKNOWN_MSG = "Map rotation contains an unknown map id."
ROTATION_GUARD_MSG = "Each conference needs at least 1 rotation map."


def duplicate_msg(team_id: int) -> str:
    return f"Team {team_id} is in more than one conference."


def not_enrolled_msg(team_id: int) -> str:
    return f"Team {team_id} is not enrolled in this Season."


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def make_teams(prefix: str, n: int, *, manager=None) -> list[Team]:
    teams: list[Team] = []
    for i in range(n):
        team, _ = make_team_with_slots(f"{prefix}{i}")
        if manager is not None:
            team.manager = manager
            team.save(update_fields=["manager"])
        teams.append(team)
    return teams


def make_league(
    name: str,
    *,
    mode: str = "league",
    manager=None,
    current_team: Team | None = None,
    state: str = "active",
) -> League:
    return League.objects.create(
        name=name,
        mode=mode,
        state=state,
        manager=manager,
        current_team=current_team,
    )


def add_win(
    season: Season,
    winner: Team,
    loser: Team,
    *,
    conference: Conference | None = None,
    win_pts: int = 100,
    lose_pts: int = 1,
) -> Match:
    """A completed Match ``winner`` takes 2-0 (rounds 1 and 2, sides swapped)."""
    match = Match.objects.create(
        team_red=winner,
        team_blue=loser,
        season=season,
        conference=conference,
        red_round1_points=win_pts,
        blue_round1_points=lose_pts,
        red_round2_points=win_pts,
        blue_round2_points=lose_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=winner,
        team_blue=loser,
        round_number=1,
        red_points=win_pts,
        blue_points=lose_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=loser,
        team_blue=winner,
        round_number=2,
        red_points=lose_pts,
        blue_points=win_pts,
        is_completed=True,
    )
    return match


def make_completed_season(
    league: League,
    teams: list[Team],
    *,
    name: str = "Season 1",
    start_date: date = date(2025, 1, 1),
    ranked: bool = True,
) -> Season:
    """A completed Season enrolling ``teams``; ``teams[i]`` beats every
    ``teams[j > i]`` when ``ranked`` so rank == index + 1."""
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format="single_round_robin",
        state="completed",
        starting_team_ids_json=sorted(t.id for t in teams),
    )
    season.teams.add(*teams)
    if ranked:
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                add_win(season, teams[i], teams[j])
    return season


def make_open_season(
    league: League,
    teams: list[Team],
    *,
    state: str = "draft",
    name: str = "Season 2",
    start_date: date = date(2026, 1, 1),
    map_mode: str = "none",
) -> Season:
    """A non-completed (draft / active) Season enrolling ``teams``."""
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format="single_round_robin",
        state=state,
        map_mode=map_mode,
        starting_team_ids_json=(
            sorted(t.id for t in teams) if state != "draft" else None
        ),
    )
    season.teams.add(*teams)
    return season


def ranked_career_league(
    prefix: str,
    n: int = 8,
    *,
    manager=None,
    mode: str = "league",
    fired: bool = False,
) -> tuple[League, Season, list[Team]]:
    """A League with one completed ranked Season; ``current_team`` is the
    STRONGEST team (``teams[0]``), so the worst-5 never contains it.
    ``fired`` (UXP-03) adds the ``fired`` evaluation new-team / reassign need."""
    teams = make_teams(prefix, n)
    league = make_league(
        f"{prefix} League", mode=mode, manager=manager, current_team=teams[0]
    )
    season = make_completed_season(league, teams)
    if fired:
        fired_evaluation(league, season, teams[0])
    return league, season, teams


def make_conference(
    season: Season,
    name: str,
    ordinal: int,
    teams: list[Team],
    *,
    rotation: list[int] | None = None,
) -> Conference:
    conf = Conference.objects.create(
        season=season, name=name, ordinal=ordinal, map_rotation_ids_json=rotation
    )
    conf.teams.set([t.id for t in teams])
    return conf


def add_tournament_phase(season: Season) -> None:
    """Give ``season`` a round_robin + tournament phase composition."""
    SeasonPhase.objects.create(
        season=season,
        ordinal=1,
        phase_type="round_robin",
        schedule_format="single_round_robin",
    )
    SeasonPhase.objects.create(season=season, ordinal=2, phase_type="tournament")


def fired_evaluation(league: League, season: Season, team: Team) -> OwnerEvaluation:
    """A hand-built ``fired`` row; the lazy writer's ``get_or_create`` leaves it."""
    return OwnerEvaluation.objects.create(
        league=league,
        season=season,
        team_managed=team,
        wins_delta=-1.0,
        playoffs_delta=-1.0,
        wins_total=-3.0,
        playoffs_total=-3.0,
        verdict="fired",
        hot_seat_level=2,
        fired_reason="owner_mood",
    )


def add_round_states(season: Season, red: Team, blue: Team) -> GameRound:
    """A regular-season Match + GameRound under ``season`` with one
    ``PlayerRoundState`` per slotted player on both sides (fixed stats)."""
    match = Match.objects.create(
        team_red=red, team_blue=blue, season=season, is_completed=True
    )
    game_round = GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=red,
        team_blue=blue,
        red_points=100,
        blue_points=80,
        is_completed=True,
    )
    slots = (
        ("slot_commander", "commander"),
        ("slot_heavy", "heavy"),
        ("slot_scout_1", "scout"),
        ("slot_scout_2", "scout"),
        ("slot_medic", "medic"),
        ("slot_ammo", "ammo"),
    )
    for team, color in ((red, "red"), (blue, "blue")):
        for idx, (slot, role) in enumerate(slots):
            PlayerRoundState.objects.create(
                game_round=game_round,
                player=getattr(team, slot),
                team_color=color,
                role=role,
                points_scored=1000 + idx * 10,
                tags_made=10 + idx,
                times_tagged=5,
                shots_missed=3,
            )
    return game_round


def _png_bytes() -> bytes:
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (10, 10), color=(0, 128, 0)).save(buf, format="PNG")
    return buf.getvalue()


def confirmed_map(name: str) -> ArenaMap:
    """An ArenaMap with a confirmed zone config (a valid rotation id).
    Call under ``core.map_test_fixtures.TempMediaMixin`` so no file leaks."""
    arena_map = ArenaMap.objects.create(
        name=name,
        image=SimpleUploadedFile(f"{name}.png", _png_bytes(), content_type="image/png"),
        img_width=10,
        img_height=10,
    )
    MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=50,
        zone_data={"zones": [[1, 1], [1, 1]]},
        confirmed=True,
    )
    # UXP-10 - a simulation-ready map also needs a red and a blue base.
    for base_type in ("red", "blue"):
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type=base_type, x_px=5, y_px=5
        )
    return arena_map


# ---------------------------------------------------------------------------
# API base case
# ---------------------------------------------------------------------------


class LeagueApiTestCase(TestCase):
    """Account A (the shared manager) drives ``self.api``; Account B owns
    foreign rows; ``self.anon`` is an unauthenticated APIClient."""

    user_b_email = "ang01d1-b@example.com"

    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email=self.user_b_email, password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.anon = APIClient()

    # --- envelope helpers ----------------------------------------------

    def ok(self, response, status: int = 200):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        return body["data"]

    def ok_page(self, response) -> tuple[list, dict]:
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        self.assertEqual(
            set(body["meta"]), {"page", "page_size", "count", "total_pages"}
        )
        return body["data"], body["meta"]

    def errors(self, response, status: int) -> list[dict]:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"errors", "meta"})
        self.assertEqual(body["meta"], {})
        self.assertTrue(body["errors"])
        for entry in body["errors"]:
            self.assertEqual(set(entry), {"code", "message", "field"})
        return body["errors"]

    def error(self, response, status: int, code: str, field: str | None = None) -> dict:
        errors = self.errors(response, status)
        matching = [e for e in errors if e["code"] == code and e["field"] == field]
        self.assertTrue(matching, errors)
        return matching[0]

    def assert_session_untouched(self) -> None:
        self.assertNotIn("last_league_id", self.api.session)

    # --- row helpers ---------------------------------------------------

    def foreign_league(self, name: str = "Foreign League") -> League:
        return make_league(name, manager=self.user_b)
