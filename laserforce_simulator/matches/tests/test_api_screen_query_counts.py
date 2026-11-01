"""ANG-08e -- query-count ports of the HTML snapshot / batching checks.

The UXP-09 summary snapshot and the PERF-01 batched award-set read were only
checked end-to-end through the HTML screens (``test_season_summary_snapshot``
``TestViewsUseSnapshot`` and ``test_season_award_sets_batch``
``TestHtmlHistoryQueryCount``). These assert the same through the API:

- a warm second GET of the awards, Season dashboard, League Leaders and League
  player reads issues no whole-Season ``PlayerRoundState`` corpus query;
- the League History query count is constant in the number of completed
  Seasons.

Fixtures reuse the UXP-09 snapshot tests' hand-built ``random.seed(42)``
corpus -- no simulation.
"""

from __future__ import annotations

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import League, Season
from matches.tests.test_season_summary_snapshot import _build_season, _prs_queries


class _QueryCountTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = get_shared_manager()
        self.api = APIClient()
        self.api.force_login(self.user)

    def own(self, league: League) -> League:
        League.objects.filter(pk=league.pk).update(manager=self.user)
        return league

    def cold_and_warm_prs(self, url: str) -> tuple[list[str], list[str]]:
        with CaptureQueriesContext(connection) as cold:
            response = self.api.get(url)
            self.assertEqual(response.status_code, 200, response.content)
        with CaptureQueriesContext(connection) as warm:
            response = self.api.get(url)
            self.assertEqual(response.status_code, 200, response.content)
        return _prs_queries(cold), _prs_queries(warm)


class TestWarmReadsSkipTheCorpus(_QueryCountTestCase):
    """Port of ``TestViewsUseSnapshot``: the second GET of each whole-Season
    read is served from the completed Season's summary snapshot."""

    def setUp(self) -> None:
        super().setUp()
        self.season, players_by_team = _build_season()
        self.league = self.own(self.season.league)
        self.player = next(iter(players_by_team.values()))["scout"]

    def test_season_awards(self) -> None:
        cold, warm = self.cold_and_warm_prs(f"/api/seasons/{self.season.id}/awards/")
        self.assertTrue(cold)
        # Only the cheap ``has_rounds`` EXISTS probe remains.
        self.assertLessEqual(len(warm), 1)

    def test_season_dashboard(self) -> None:
        cold, warm = self.cold_and_warm_prs(f"/api/seasons/{self.season.id}/dashboard/")
        self.assertTrue(cold)
        self.assertEqual(warm, [])

    def test_league_leaders(self) -> None:
        cold, warm = self.cold_and_warm_prs(
            f"/api/leagues/{self.league.id}/leaders/?season={self.season.id}"
        )
        self.assertTrue(cold)
        self.assertEqual(warm, [])

    def test_league_player_skips_the_award_corpus(self) -> None:
        cold, warm = self.cold_and_warm_prs(
            f"/api/leagues/{self.league.id}/players/{self.player.id}/"
        )
        # Every warm PRS query is scoped to this one Player.
        self.assertLess(len(warm), len(cold))
        for sql in warm:
            self.assertIn('."player_id" = ', sql)


class TestHistoryQueryCount(_QueryCountTestCase):
    """Port of ``TestHtmlHistoryQueryCount``: League History batches its
    award sets, so its query count does not grow with completed Seasons."""

    def _count(self, league: League) -> int:
        url = f"/api/leagues/{league.id}/history/"
        self.assertEqual(self.api.get(url).status_code, 200)  # warm snapshots
        with CaptureQueriesContext(connection) as ctx:
            response = self.api.get(url)
            self.assertEqual(response.status_code, 200, response.content)
        return len(ctx.captured_queries)

    def test_constant_in_completed_seasons(self) -> None:
        small = self.own(_build_season()[0].league)
        big = self.own(_build_season()[0].league)
        for _ in range(2):
            extra = _build_season()[0]
            Season.objects.filter(pk=extra.pk).update(league=big)
        self.assertEqual(Season.objects.filter(league=big).count(), 3)
        self.assertEqual(self._count(big), self._count(small))
