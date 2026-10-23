"""PERF-01 T3 -- the Team History Season scan reads draft Seasons' enrolled
Teams without one query per Season.

The detail-action guardrail (``test_detail_action_query_counts.py``) grows
active / completed Seasons only; their enrolled ids come from the
``starting_team_ids_json`` snapshot. A draft Season reads its live ``teams``
M2M instead, which this file covers.
"""

from __future__ import annotations

from datetime import date

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from matches.league_screen_services import team_history_seasons
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots


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
