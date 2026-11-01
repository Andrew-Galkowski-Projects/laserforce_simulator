"""NAV-01 / PLAY-01 / LG-07a -- ``matches.league_play_services.build_play_controls``.

The helper returns the fixed play-control key dict across the Season state
matrix; the API's play controls are built from it. Moved here from the
deleted ``test_nav_play_dropdown.py`` (ANG-08), whose other classes asserted
the HTML ``Play`` dropdown.

Tests assert SCHEMA-LEVEL outcomes, never raw simulated point totals. N=4
small sims drive the playoff state under a small ``ROUND_TICKS`` patch.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase

from matches.league_play_services import build_play_controls
from matches.models import League, Match, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

_FAST_TICKS = 30


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_league(name: str = "Nav") -> League:
    return League.objects.create(name=name)


def _make_draft_season(name: str, *, n_teams: int = 4):
    """A draft Season with ``n_teams`` slotted teams enrolled (NOT started)."""
    league = _make_league(name)
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    return league, season, teams


def _make_active_season(name: str, *, n_teams: int = 4):
    league, season, teams = _make_draft_season(name, n_teams=n_teams)
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _make_completed_season(name: str) -> tuple[League, Season]:
    league = _make_league(name)
    season = Season.objects.create(
        league=league,
        name="Done",
        start_date=date(2026, 1, 1),
        state="completed",
        starting_team_ids_json=[],
    )
    return league, season


def _rr_tournament_season(name: str, *, n: int = 4):
    """Active Season: ordinal-1 round_robin + ordinal-2 tournament, ``n`` teams."""
    league, season, teams = _make_draft_season(name, n_teams=n)
    SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    SeasonPhase.objects.create(season=season, ordinal=2, phase_type="tournament")
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _play_rr(season, teams) -> None:
    """Play every RR fixture (auto-builds the tournament phase on completion)."""
    by_id = {t.id: t for t in teams}
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                sim.simulate_scheduled_round(
                    season,
                    by_id[fixture.team_a_id],
                    by_id[fixture.team_b_id],
                    fixture.round_number,
                    season_phase=phase if phase.pk is not None else None,
                )


def _drain_tournament(tournament) -> None:
    from matches.tournament_engine import play_next_node

    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for _ in range(200):
            if play_next_node(tournament) is None:
                break
    tournament.refresh_from_db()


def _member_night_active_season(name: str, *, site: str = "SiteA"):
    """An active Season whose cursor is on an ordinal-1 ``member_night`` phase
    with a single VIABLE Site (2 teams x 6 = 12 players all share ``site``)."""
    league, season, teams = _make_draft_season(name, n_teams=2)
    Player.objects.filter(team__in=teams).update(home_site=site)
    SeasonPhase.objects.create(season=season, ordinal=1, phase_type="member_night")
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _add_member_night_shell(season) -> None:
    mn = season.current_phase()
    da = Team.objects.create(name="MN Nav Draw A", is_draw_team=True)
    db = Team.objects.create(name="MN Nav Draw B", is_draw_team=True)
    Match.objects.create(
        season=season, season_phase=mn, team_red=da, team_blue=db, is_completed=False
    )


# ---------------------------------------------------------------------------
# State matrix
# ---------------------------------------------------------------------------


class TestBuildPlayControlsContext(TestCase):
    """The helper returns the correct key dict across the state matrix. PLAY-01
    added ``active_play_job_id``; LG-07a added the three ``member_night_*``
    keys -- the invariant is "the helper emits exactly this fixed key set".
    """

    _KEYS = (
        "action_button_label",
        "action_button_state",
        "playoff_phase_active",
        "playoff_tournament_id",
        "playoff_completed",
        "has_following_tournament_phase",
        "following_tournament_is_final",
        "live_preview_available",
        "is_career_mode",
        "active_play_job_id",
        "member_night_phase_active",
        "member_night_sites",
        "member_night_has_unplayed",
    )

    def test_returns_exactly_the_thirteen_keys(self) -> None:
        result = build_play_controls(_make_league("HelperKeys"), None)
        self.assertEqual(set(result.keys()), set(self._KEYS))

    def test_none_state(self) -> None:
        result = build_play_controls(_make_league("HelperNone"), None)
        self.assertEqual(result["action_button_state"], "none")
        self.assertEqual(result["action_button_label"], "No Season")
        self.assertFalse(result["playoff_phase_active"])
        self.assertIsNone(result["playoff_tournament_id"])
        self.assertFalse(result["live_preview_available"])

    def test_start_season_state(self) -> None:
        league, season, _teams = _make_draft_season("HelperDraft")
        result = build_play_controls(league, season)
        self.assertEqual(result["action_button_state"], "start_season")
        self.assertEqual(result["action_button_label"], "Start Season")

    def test_play_next_state(self) -> None:
        league, season, _teams = _make_active_season("HelperActive")
        result = build_play_controls(league, season)
        self.assertEqual(result["action_button_state"], "play_next")
        self.assertEqual(result["action_button_label"], "Play Next")

    def test_start_next_season_state(self) -> None:
        league, season = _make_completed_season("HelperCompleted")
        result = build_play_controls(league, season)
        self.assertEqual(result["action_button_state"], "start_next_season")
        self.assertEqual(result["action_button_label"], "Start Next Season")

    def test_is_career_mode_true_for_league_mode(self) -> None:
        league, season = _make_completed_season("HelperCareer")
        league.mode = "league"
        league.save(update_fields=["mode"])
        self.assertTrue(build_play_controls(league, season)["is_career_mode"])

    def test_is_career_mode_false_for_multiplayer(self) -> None:
        league, season = _make_completed_season("HelperMulti")
        league.mode = "multiplayer"
        league.save(update_fields=["mode"])
        self.assertFalse(build_play_controls(league, season)["is_career_mode"])

    def test_has_following_tournament_phase_true_for_rr_then_tournament(
        self,
    ) -> None:
        league, season, _teams = _rr_tournament_season("HelperFollowing")
        result = build_play_controls(league, season)
        self.assertTrue(result["has_following_tournament_phase"])

    def test_playoff_phase_active_true_once_tournament_built(self) -> None:
        league, season, teams = _rr_tournament_season("HelperPlayoffActive")
        _play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        result = build_play_controls(league, season)
        self.assertTrue(result["playoff_phase_active"])
        self.assertEqual(
            result["playoff_tournament_id"], tournament_phase.tournament_id
        )
        self.assertFalse(result["playoff_completed"])

    def test_playoff_completed_after_drain(self) -> None:
        league, season, teams = _rr_tournament_season("HelperPlayoffDone")
        _play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        _drain_tournament(tournament_phase.tournament)
        season.refresh_from_db()
        result = build_play_controls(league, season)
        self.assertFalse(result["playoff_phase_active"])
        self.assertTrue(result["playoff_completed"])
        self.assertEqual(
            result["playoff_tournament_id"], tournament_phase.tournament_id
        )


class TestBuildPlayControlsMemberNight(TestCase):
    """LG-07a -- ``build_play_controls`` member-night keys across the gating."""

    def test_active_with_viable_site_and_no_unplayed(self) -> None:
        league, season, _teams = _member_night_active_season("NavMnCtxA")
        result = build_play_controls(league, season)
        self.assertTrue(result["member_night_phase_active"])
        self.assertEqual(result["member_night_sites"], [{"site": "SiteA", "count": 12}])
        self.assertFalse(result["member_night_has_unplayed"])

    def test_has_unplayed_true_with_shell(self) -> None:
        league, season, _teams = _member_night_active_season("NavMnCtxB")
        _add_member_night_shell(season)
        self.assertTrue(
            build_play_controls(league, season)["member_night_has_unplayed"]
        )

    def test_keys_inactive_off_member_night(self) -> None:
        # An RR-cursor Season => the member-night keys are inactive / empty.
        _league, season, _teams = _make_active_season("NavMnCtxOff", n_teams=2)
        result = build_play_controls(season.league, season)
        self.assertFalse(result["member_night_phase_active"])
        self.assertEqual(result["member_night_sites"], [])
        self.assertFalse(result["member_night_has_unplayed"])
