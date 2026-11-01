"""LG-01f / LG-03 / LG-07a -- the League History row, through
``matches.league_services.history_seasons`` + ``build_history_row``.

ANG-08: the HTML History page is gone; the API (``GET
/api/leagues/<id>/history/``, ``test_api_leagues.py``) serialises these rows.
These cases pin the row logic itself: the key set, the champion fallback, the
multi-Conference rule and the member-night exclusion.

Tests hand-construct ``Match`` + ``GameRound`` rows -- no simulation.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from matches.league_services import build_history_row, history_seasons
from matches.models import (
    Conference,
    GameRound,
    League,
    Match,
    Season,
    SeasonPhase,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# ---------------------------------------------------------------------------
# Helpers -- hand-construct League / Season / Match rows.
# ---------------------------------------------------------------------------


def _make_league(name: str = "L") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _make_completed_season(
    league: League,
    *,
    name: str = "S1",
    start_date: date = date(2025, 1, 1),
    team_ids: list[int] | None = None,
    champion_team: Team | None = None,
) -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        state="completed",
        starting_team_ids_json=sorted(team_ids) if team_ids is not None else [],
        champion_team=champion_team,
    )


def _make_active_season(
    league: League,
    *,
    name: str = "Active",
    start_date: date = date(2026, 1, 1),
    teams: list[Team] | None = None,
) -> Season:
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        state="active",
        starting_team_ids_json=sorted(t.id for t in teams) if teams else [],
    )
    if teams:
        season.teams.add(*teams)
    return season


def _make_teams(prefix: str, n: int) -> list[Team]:
    return [make_team_with_slots(f"{prefix}{i}")[0] for i in range(n)]


def _make_completed_match(
    season: Season, team_a: Team, team_b: Team, *, red_pts: int = 100
) -> Match:
    match = Match.objects.create(
        team_red=team_a,
        team_blue=team_b,
        season=season,
        red_round1_points=red_pts,
        blue_round1_points=0,
        red_round2_points=0,
        blue_round2_points=red_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_a,
        team_blue=team_b,
        round_number=1,
        red_points=red_pts,
        blue_points=0,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_b,
        team_blue=team_a,
        round_number=2,
        red_points=red_pts,
        blue_points=0,
        is_completed=True,
    )
    return match


def _completed_rows(league: League) -> list[dict]:
    _in_progress, completed, teams_by_id = history_seasons(league)
    return [
        build_history_row(season, teams_by_id, is_in_progress=False)
        for season in completed
    ]


def _in_progress_row(league: League) -> dict | None:
    in_progress, _completed, teams_by_id = history_seasons(league)
    if in_progress is None:
        return None
    return build_history_row(in_progress, teams_by_id, is_in_progress=True)


# ---------------------------------------------------------------------------
# Row shape and cells
# ---------------------------------------------------------------------------


class TestHistoryRowCells(TestCase):
    def test_row_key_set(self) -> None:
        # LG-03 added ``season_mvp`` / ``finals_mvp``; ANG-08 dropped the
        # HTML-only ``season_url`` (no Season page to link to).
        league = _make_league("Row12")
        teams = _make_teams("R12", 2)
        _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        row = _completed_rows(league)[0]
        self.assertEqual(
            set(row),
            {
                "season_id",
                "season_name",
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
            },
        )

    def test_teams_enrolled_uses_starting_team_ids_json_length(self) -> None:
        league = _make_league("RowTeams")
        teams = _make_teams("RT", 4)
        _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        self.assertEqual(_completed_rows(league)[0]["teams_enrolled"], 4)

    def test_matches_played_counts_only_is_completed_matches(self) -> None:
        league = _make_league("RowMatches")
        teams = _make_teams("RM", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        _make_completed_match(season, teams[0], teams[1])
        _make_completed_match(season, teams[0], teams[1])
        for _ in range(2):
            Match.objects.create(
                team_red=teams[0], team_blue=teams[1], season=season, is_completed=False
            )
        self.assertEqual(_completed_rows(league)[0]["matches_played"], 2)

    def test_tournament_champion_is_none(self) -> None:
        league = _make_league("RowTC")
        teams = _make_teams("TC", 2)
        _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        self.assertIsNone(_completed_rows(league)[0]["tournament_champion"])

    def test_top_three_has_three_slots(self) -> None:
        league = _make_league("RowTop3")
        teams = _make_teams("T3", 3)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        _make_completed_match(season, teams[0], teams[1], red_pts=300)
        _make_completed_match(season, teams[0], teams[2], red_pts=200)
        _make_completed_match(season, teams[1], teams[2], red_pts=100)
        top = _completed_rows(league)[0]["top_three"]
        self.assertEqual(len(top), 3)
        self.assertTrue(all(t is not None for t in top))

    def test_top_three_pads_with_none_when_fewer_than_three_teams(self) -> None:
        league = _make_league("RowTop3Less")
        teams = _make_teams("T3L", 2)
        _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        top = _completed_rows(league)[0]["top_three"]
        self.assertEqual(len(top), 3)
        self.assertIsNone(top[2])

    def test_completed_seasons_newest_first_by_id(self) -> None:
        league = _make_league("RowSort")
        teams = _make_teams("RS", 2)
        ids = [
            _make_completed_season(
                league,
                name=f"S{i}",
                start_date=date(2025 - i, 1, 1),
                team_ids=[t.id for t in teams],
                champion_team=teams[0],
            ).id
            for i in range(3)
        ]
        self.assertEqual(
            [r["season_id"] for r in _completed_rows(league)], list(reversed(ids))
        )

    def test_in_progress_top_three_reads_live_standings(self) -> None:
        league = _make_league("InProgStandings")
        teams = _make_teams("IPS", 2)
        active = _make_active_season(league, teams=teams)
        _make_completed_match(active, teams[0], teams[1], red_pts=300)
        row = _in_progress_row(league)
        assert row is not None
        self.assertTrue(row["is_in_progress"])
        self.assertIsNone(row["champion"])
        self.assertEqual(row["top_three"][0], teams[0])


# ---------------------------------------------------------------------------
# Champion fallback
# ---------------------------------------------------------------------------


class TestHistoryRowChampionFallback(TestCase):
    """When ``champion_team`` is None, fall back to standings rank-1."""

    def test_champion_fk_null_falls_back_to_standings_rank_1(self) -> None:
        league = _make_league("ChampNull")
        teams = _make_teams("CN", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=None
        )
        _make_completed_match(season, teams[0], teams[1], red_pts=300)
        self.assertEqual(_completed_rows(league)[0]["champion"], teams[0])

    def test_champion_fk_present_takes_precedence_over_standings_rank_1(
        self,
    ) -> None:
        league = _make_league("ChampPresent")
        teams = _make_teams("CP", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[1]
        )
        _make_completed_match(season, teams[0], teams[1], red_pts=500)
        self.assertEqual(_completed_rows(league)[0]["champion"], teams[1])

    def test_multi_conference_null_champion_does_not_fall_back(self) -> None:
        # L-1: a >= 2-Conference Season with no Worlds crowns nobody
        # (CONF-01); whole-Season standings across disjoint Conference
        # schedules must not invent a champion, runner-up or top three.
        league = _make_league("ConfNull")
        teams = _make_teams("CF", 4)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=None
        )
        self._two_conferences(season, teams)
        _make_completed_match(season, teams[0], teams[1], red_pts=300)
        _make_completed_match(season, teams[2], teams[3], red_pts=200)
        row = _completed_rows(league)[0]
        self.assertIsNone(row["champion"])
        self.assertIsNone(row["runner_up"])
        self.assertEqual(row["top_three"], [None, None, None])

    def test_multi_conference_stamped_worlds_champion_is_shown(self) -> None:
        league = _make_league("ConfWorlds")
        teams = _make_teams("CW", 4)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[3]
        )
        self._two_conferences(season, teams)
        self.assertEqual(_completed_rows(league)[0]["champion"], teams[3])

    @staticmethod
    def _two_conferences(season: Season, teams: list[Team]) -> None:
        for ordinal, members in enumerate((teams[:2], teams[2:]), start=1):
            conf = Conference.objects.create(
                season=season,
                name=f"C{ordinal}",
                ordinal=ordinal,
                starting_team_ids_json=sorted(t.id for t in members),
            )
            conf.teams.add(*members)


# ---------------------------------------------------------------------------
# LG-07a -- member-night Matches excluded from the row (ADR-0033)
# ---------------------------------------------------------------------------


class TestLg07aMemberNightHistoryExclusion(TestCase):
    """A member-night Match does NOT inflate a completed Season's matches_played."""

    def _add_member_night_match(self, season: Season) -> None:
        mn = SeasonPhase.objects.create(
            season=season, ordinal=2, phase_type="member_night"
        )
        da = Team.objects.create(name="MN Hist Draw A", is_draw_team=True)
        db = Team.objects.create(name="MN Hist Draw B", is_draw_team=True)
        mnm = Match.objects.create(
            team_red=da,
            team_blue=db,
            season=season,
            season_phase=mn,
            red_round1_points=100,
            blue_round1_points=0,
            red_round2_points=0,
            blue_round2_points=100,
            is_completed=True,
        )
        for number, (red, blue) in enumerate(((da, db), (db, da)), start=1):
            GameRound.objects.create(
                match=mnm,
                team_red=red,
                team_blue=blue,
                round_number=number,
                red_points=100 if number == 1 else 0,
                blue_points=0 if number == 1 else 100,
                is_completed=True,
            )

    def test_member_night_match_not_counted_in_matches_played(self) -> None:
        league = _make_league("MnHistEx")
        teams = _make_teams("MnHE", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        _make_completed_match(season, teams[0], teams[1])
        self._add_member_night_match(season)
        self.assertEqual(_completed_rows(league)[0]["matches_played"], 1)

    def test_regular_match_still_counted(self) -> None:
        league = _make_league("MnHistReg")
        teams = _make_teams("MnHR", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        _make_completed_match(season, teams[0], teams[1])
        _make_completed_match(season, teams[0], teams[1])
        self._add_member_night_match(season)
        self.assertEqual(_completed_rows(league)[0]["matches_played"], 2)
