"""PERF-01 T3 -- the Team History Season scan reads draft Seasons' enrolled
Teams without one query per Season.

The detail-action guardrail (``test_detail_action_query_counts.py``) grows
active / completed Seasons only; their enrolled ids come from the
``starting_team_ids_json`` snapshot. A draft Season reads its live ``teams``
M2M instead, which this file covers.
"""

from __future__ import annotations

from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from matches.league_screen_services import enrolled_team_ids, team_history_seasons
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Team


class TestTeamHistorySeasonsDraftScan(TestCase):
    def setUp(self) -> None:
        self.team, _ = make_team_with_slots("Scan")
        self.other, _ = make_team_with_slots("ScanOther")
        self.leagues = [
            League.objects.create(name="Scan League A"),
            League.objects.create(name="Scan League B"),
        ]
        self.made = 0

    def _draft_season(self, *, enrolled: bool) -> Season:
        """One draft Season (no snapshot), alternating between the two
        Leagues, with the Team enrolled or not."""
        league = self.leagues[self.made % 2]
        self.made += 1
        season = Season.objects.create(
            league=league, name=f"Draft {self.made}", start_date=date(2026, 1, 1)
        )
        season.teams.add(self.other)
        if enrolled:
            season.teams.add(self.team)
        return season

    def _count(self) -> int:
        with CaptureQueriesContext(connection) as ctx:
            team_history_seasons(self.team)
        return len(ctx.captured_queries)

    def test_query_count_is_constant_in_draft_seasons(self) -> None:
        self._draft_season(enrolled=True)
        self._draft_season(enrolled=False)
        small = self._count()
        for _ in range(3):
            self._draft_season(enrolled=True)
            self._draft_season(enrolled=False)
        self.assertEqual(self._count(), small)

    def test_finds_enrolled_draft_seasons_across_leagues(self) -> None:
        mine = [self._draft_season(enrolled=True) for _ in range(2)]
        self._draft_season(enrolled=False)
        found = team_history_seasons(self.team)
        self.assertEqual(sorted(found), sorted(s.id for s in mine))
        self.assertEqual(
            {found[s.id].league_id for s in mine},
            {league.id for league in self.leagues},
        )


class TestTeamHistorySeasonsNarrowedScan(TestCase):
    """PERF-03 -- the scan reads only Seasons the Team has an M2M link to."""

    def setUp(self) -> None:
        self.team, _ = make_team_with_slots("Narrow")
        self.other, _ = make_team_with_slots("NarrowOther")
        self.third, _ = make_team_with_slots("NarrowThird")
        self.league = League.objects.create(name="Narrow League")

    def _active_season(self, league: League, name: str, *teams: Team) -> Season:
        season = Season.objects.create(
            league=league, name=name, start_date=date(2026, 1, 1)
        )
        season.teams.add(*teams)
        season.start_season()
        return season

    def test_never_rechecks_a_season_the_team_has_no_link_to(self) -> None:
        stranger = get_user_model().objects.create_user(
            email="perf03-stranger@example.com", password="Str0ng-Passphrase-9"
        )
        foreign_league = League.objects.create(name="Foreign", manager=stranger)
        foreign = self._active_season(foreign_league, "Foreign", self.other, self.third)
        mine = self._active_season(self.league, "Mine", self.team, self.other)
        with mock.patch(
            "matches.league_screen_services.enrolled_team_ids",
            side_effect=enrolled_team_ids,
        ) as spy:
            found = team_history_seasons(self.team)
        checked = {call.args[0].id for call in spy.call_args_list}
        self.assertNotIn(foreign.id, checked)
        self.assertEqual(list(found), [mine.id])

    def test_finds_active_season_whose_snapshot_lists_the_team(self) -> None:
        season = self._active_season(self.league, "Snap", self.team, self.other)
        self.assertIn(self.team.id, season.starting_team_ids_json)
        found = team_history_seasons(self.team)
        self.assertEqual(list(found), [season.id])
        self.assertEqual(found[season.id].league_id, self.league.id)

    def test_excludes_team_added_to_m2m_after_the_start(self) -> None:
        season = self._active_season(self.league, "Late", self.other, self.third)
        season.teams.add(self.team)
        self.assertNotIn(self.team.id, season.starting_team_ids_json)
        self.assertEqual(team_history_seasons(self.team), {})
