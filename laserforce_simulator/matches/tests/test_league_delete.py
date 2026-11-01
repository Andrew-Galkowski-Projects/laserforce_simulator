"""DEL-01 / ANG-06-14 B1 -- ``league_services.league_delete_summary``.

The summary counts exactly what the Delete League teardown removes. ANG-08:
the HTML confirm page and its POST went; the teardown itself (phases, Matches
and Game Rounds, owned Teams and Players, Season-scoped career rows, the
cross-context survivors, the embedded Tournament, member-night drawn Teams and
the non-career 409) is pinned through ``DELETE /api/leagues/<id>/`` in
``test_api_leagues.py::TestLeagueDetail``. Fixtures are hand-built (no
simulator run).
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from matches.league_services import league_delete_summary
from matches.models import (
    BracketNode,
    GameRound,
    League,
    Match,
    Season,
    SeasonPhase,
    SeriesMatch,
    Tournament,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team


def _make_league(name: str = "L", *, mode: str = "league") -> League:
    return League.objects.create(name=name, mode=mode, state="active")


def _make_season(
    league: League, *, name: str = "Season 1", state: str = "completed"
) -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=date(2025, 1, 1),
        state=state,
    )


def _make_rr_phase(season: Season, *, ordinal: int = 1) -> SeasonPhase:
    return SeasonPhase.objects.create(
        season=season,
        ordinal=ordinal,
        phase_type="round_robin",
        schedule_format="single_round_robin",
    )


def _make_season_match(season: Season, team_red: Team, team_blue: Team) -> Match:
    """A season-scoped completed Match + one GameRound (no simulator)."""
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=season,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        is_completed=True,
    )
    return match


class TestLeagueDeleteSummaryService(TestCase):
    """``league_delete_summary`` counts exactly what the teardown removes."""

    def _build_rich(self) -> League:
        """1 Season (1 season Match), an embedded Tournament with 1 playoff
        Match, 2 enrolled Teams (6 players each), a 2-player free-agent pool
        and ``current_team``; plus a second League whose rows never count."""
        league = _make_league("SumRich")
        season = _make_season(league)
        _make_rr_phase(season, ordinal=1)
        t1, _ = make_team_with_slots("SumA")
        t2, _ = make_team_with_slots("SumB")
        season.teams.add(t1, t2)
        _make_season_match(season, t1, t2)
        pool = Team.objects.create(name="SumPool")
        Player.objects.create(team=pool, name="SumPool1")
        Player.objects.create(team=pool, name="SumPool2")
        league.current_team = t1
        league.free_agent_pool = pool
        league.save(update_fields=["current_team", "free_agent_pool"])
        tournament = Tournament.objects.create(
            name="SumPlayoffs", format="single_elimination", state="active"
        )
        SeasonPhase.objects.create(
            season=season, ordinal=2, phase_type="tournament", tournament=tournament
        )
        node = BracketNode.objects.create(
            tournament=tournament, bracket_round=1, position=0, team_a=t1, team_b=t2
        )
        playoff = Match.objects.create(
            team_red=t1, team_blue=t2, season=None, is_completed=True
        )
        SeriesMatch.objects.create(node=node, match=playoff, game_number=1)

        other = _make_league("SumOther")
        other_season = _make_season(other)
        o1, _ = make_team_with_slots("SumOA")
        o2, _ = make_team_with_slots("SumOB")
        other_season.teams.add(o1, o2)
        _make_season_match(other_season, o1, o2)
        return league

    def test_counts_every_teardown_target(self) -> None:
        league = self._build_rich()
        self.assertEqual(
            league_delete_summary(league),
            {"seasons": 1, "matches": 2, "tournaments": 1, "teams": 3, "players": 14},
        )

    def test_empty_league_is_all_zero(self) -> None:
        league = _make_league("SumEmpty")
        self.assertEqual(
            league_delete_summary(league),
            {"seasons": 0, "matches": 0, "tournaments": 0, "teams": 0, "players": 0},
        )
