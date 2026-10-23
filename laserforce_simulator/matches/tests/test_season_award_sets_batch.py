"""PERF-01 T1 -- ``compute_season_award_sets``: the batched award-set read.

League History (and, later, the League player page) needs the award set of
many Seasons at once. The batch reads each completed Season's stored
``awards`` snapshot and refreshes every Player / Team name with one query
each across all the Seasons, so the cost no longer grows with the Seasons.
Every value must equal ``compute_season_award_set(season)``.

Fixtures reuse the UXP-09 snapshot tests' hand-built, ``random.seed(42)``
corpus -- no simulation.
"""

from __future__ import annotations

from dataclasses import replace

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from matches.league_services import (
    build_history_row,
    compute_season_award_set,
    compute_season_award_sets,
    history_seasons,
)
from matches.models import League, Season
from matches.season_awards import AwardSet
from matches.season_snapshot import _refresh_names
from matches.tests.test_season_summary_snapshot import _build_season, _fresh
from teams.models import Player, Team


class TestComputeSeasonAwardSets(TestCase):
    def test_empty_input_returns_empty_dict(self) -> None:
        with CaptureQueriesContext(connection) as ctx:
            result = compute_season_award_sets([])
        self.assertEqual(result, {})
        self.assertEqual(len(ctx.captured_queries), 0)

    def test_query_count_constant_in_completed_seasons(self) -> None:
        seasons = [_build_season()[0] for _ in range(3)]
        for season in seasons:
            compute_season_award_set(_fresh(season))  # warm the snapshot

        def count(n: int) -> int:
            batch = [_fresh(season) for season in seasons[:n]]
            with CaptureQueriesContext(connection) as ctx:
                compute_season_award_sets(batch)
            return len(ctx.captured_queries)

        self.assertEqual(count(3), count(1))
        self.assertLessEqual(count(3), 2)

    def test_each_value_equals_single_season_path(self) -> None:
        warm = _build_season()[0]
        compute_season_award_set(_fresh(warm))
        cold = _build_season()[0]
        active = _build_season(completed=False)[0]
        expected = {s.id: _uncached(s) for s in (warm, cold, active)}

        result = compute_season_award_sets([_fresh(s) for s in (warm, cold, active)])

        self.assertEqual(result, expected)
        self.assertEqual(list(result), [warm.id, cold.id, active.id])
        self.assertIsNotNone(result[warm.id].season_mvp)
        # The cold completed Season was written on first read, as today.
        self.assertIn("awards", _fresh(cold).summary_snapshot_json)
        self.assertIsNone(_fresh(active).summary_snapshot_json)

    def test_renamed_player_and_team_show_new_names(self) -> None:
        seasons = [_build_season()[0] for _ in range(2)]
        mvp = compute_season_award_set(_fresh(seasons[1])).season_mvp
        compute_season_award_set(_fresh(seasons[0]))
        assert mvp is not None
        Player.objects.filter(pk=mvp.player_id).update(name="Renamed Player")
        Team.objects.filter(pk=mvp.team_id).update(name="Renamed Team")

        result = compute_season_award_sets([_fresh(s) for s in seasons])

        renamed = result[seasons[1].id].season_mvp
        assert renamed is not None
        self.assertEqual(renamed.player_name, "Renamed Player")
        self.assertEqual(renamed.team_name, "Renamed Team")
        self.assertEqual(result[seasons[1].id], _uncached(seasons[1]))

    def test_deleted_player_falls_back_to_recompute(self) -> None:
        seasons = [_build_season()[0] for _ in range(2)]
        for season in seasons:
            compute_season_award_set(_fresh(season))
        mvp = compute_season_award_set(_fresh(seasons[0])).season_mvp
        assert mvp is not None
        Player.objects.filter(pk=mvp.player_id).delete()

        result = compute_season_award_sets([_fresh(s) for s in seasons])

        self.assertEqual(result[seasons[0].id], _uncached(seasons[0]))
        self.assertEqual(result[seasons[1].id], _uncached(seasons[1]))
        recomputed = result[seasons[0].id].season_mvp
        self.assertNotEqual(recomputed.player_id if recomputed else None, mvp.player_id)

    def test_unknown_snapshot_version_falls_back_to_recompute(self) -> None:
        stale, warm = [_build_season()[0] for _ in range(2)]
        compute_season_award_set(_fresh(warm))
        Season.objects.filter(pk=stale.pk).update(
            summary_snapshot_json={"v": -1, "awards": {"bogus": True}}
        )

        result = compute_season_award_sets([_fresh(stale), _fresh(warm)])

        self.assertEqual(result[stale.id], _uncached(stale))
        self.assertEqual(result[warm.id], _uncached(warm))
        # The single-Season fallback rewrote the snapshot at today's version.
        self.assertNotEqual(_fresh(stale).summary_snapshot_json["v"], -1)


class TestRefreshNamesWithMaps(TestCase):
    """The snapshot name refresh reads pre-fetched maps instead of querying."""

    def _payload(self) -> dict:
        return {
            "w": {"player_id": 7, "player_name": "old", "team_id": 3, "team_name": ""},
            "none": {"player_id": 8, "player_name": "x", "team_id": 0, "team_name": ""},
        }

    def test_maps_are_used_without_a_query(self) -> None:
        payload = self._payload()
        with CaptureQueriesContext(connection) as ctx:
            fresh = _refresh_names(
                payload, player_names={7: "P7", 8: "P8"}, team_names={3: "T3"}
            )
        self.assertTrue(fresh)
        self.assertEqual(len(ctx.captured_queries), 0)
        self.assertEqual(payload["w"]["player_name"], "P7")
        self.assertEqual(payload["w"]["team_name"], "T3")
        # Team id 0 is the "no Team" sentinel and keeps its empty name.
        self.assertEqual(payload["none"]["team_name"], "")

    def test_missing_id_in_maps_is_stale(self) -> None:
        self.assertFalse(
            _refresh_names(
                self._payload(), player_names={7: "P7"}, team_names={3: "T3"}
            )
        )
        self.assertFalse(
            _refresh_names(
                self._payload(), player_names={7: "P7", 8: "P8"}, team_names={}
            )
        )

    def test_without_maps_queries_as_before(self) -> None:
        with CaptureQueriesContext(connection) as ctx:
            fresh = _refresh_names(self._payload())
        self.assertFalse(fresh)  # Players 7 / 8 do not exist.
        self.assertEqual(len(ctx.captured_queries), 2)


class TestHistoryRowTakesAwardSet(TestCase):
    def test_passed_award_set_is_used(self) -> None:
        season = _build_season()[0]
        real = _uncached(season)
        assert real.season_mvp is not None
        passed = AwardSet(
            most_points=None,
            best_accuracy=None,
            kd_by_role={},
            best_medic=None,
            most_efficient_nuke=None,
            season_mvp=replace(real.season_mvp, player_name="Passed MVP"),
            finals_mvp=None,
        )
        _, _, teams_by_id = history_seasons(season.league)

        row = build_history_row(
            _fresh(season), teams_by_id, is_in_progress=False, award_set=passed
        )

        self.assertEqual(row["season_mvp"].player_name, "Passed MVP")
        self.assertIsNone(row["finals_mvp"])

    def test_without_award_set_computes_its_own(self) -> None:
        season = _build_season()[0]
        _, _, teams_by_id = history_seasons(season.league)
        row = build_history_row(_fresh(season), teams_by_id, is_in_progress=False)
        self.assertEqual(row["season_mvp"], _uncached(season).season_mvp)


class TestHtmlHistoryQueryCount(TestCase):
    """The HTML History screen batches its page's award sets too."""

    def _count(self, league: League) -> int:
        url = reverse("league_history", args=[league.id])
        self.client.get(url)  # warm every snapshot
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx.captured_queries)

    def test_constant_in_completed_seasons(self) -> None:
        small = _build_season()[0].league
        big = _build_season()[0].league
        for _ in range(2):
            extra = _build_season()[0]
            Season.objects.filter(pk=extra.pk).update(league=big)
        self.assertEqual(Season.objects.filter(league=big).count(), 3)
        self.assertEqual(self._count(big), self._count(small))


def _uncached(season: Season) -> AwardSet:
    """The award set computed straight from the corpus (an in-progress read
    is never cached)."""
    as_active = _fresh(season)
    as_active.state = "active"
    return compute_season_award_set(as_active)
