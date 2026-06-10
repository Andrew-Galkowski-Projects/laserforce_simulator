"""Tests for playing a season (LG-01d): the pure play-orchestrator helpers
(``find_next_matchday`` / ``select_play_fixtures``) and the ``play_season_task``
Celery task under EAGER execution.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase

from matches.models import GameRound, League, Match, Season
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_season(prefix: str, n_teams: int = 2):
    """Build an ``active`` Season with ``n_teams`` enrolled. Calls
    ``start_season`` so the LG-01d task body's pure-module pipeline can
    find fixtures.
    """
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(league=league, name="S1", start_date=date.today())
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


# ---------------------------------------------------------------------------
# TestPlaySeasonTaskHappyPath
# ---------------------------------------------------------------------------


class TestPlaySeasonTaskHappyPath(TestCase):
    """Play Until End on a small Season runs all fixtures, persists
    ``GameRound`` rows, completes the Season, and returns the locked
    ``{"completed", "total"}`` shape.
    """

    def test_play_until_end_loops_n_rounds_and_persists_game_round_rows(
        self,
    ) -> None:
        from matches.tasks import play_season_task

        season, _teams = _active_season("HappyA", n_teams=2)
        # N=2 → 1 pair × 2 rounds = 2 fixtures total.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_season_task.delay(season.id, max_matchdays=None)
        self.assertEqual(result.state, "SUCCESS")
        # Two GameRound rows persisted.
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 2)

    def test_play_until_end_completes_season_via_complete_if_finished(
        self,
    ) -> None:
        from matches.tasks import play_season_task

        season, _teams = _active_season("HappyB", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(season.id, max_matchdays=None)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team)
        # Every Match in the Season is is_completed=True.
        for match in Match.objects.filter(season=season):
            self.assertTrue(match.is_completed)

    def test_task_returns_completed_and_total_keys_matching_n(self) -> None:
        from matches.tasks import play_season_task

        season, _teams = _active_season("HappyC", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            async_result = play_season_task.delay(season.id, max_matchdays=None)
        payload = async_result.result
        self.assertIsInstance(payload, dict)
        self.assertIn("completed", payload)
        self.assertIn("total", payload)
        # N=2 ⇒ 2 fixtures.
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["completed"], payload["total"])

    def test_progress_update_state_emitted_per_round(self) -> None:
        """Under EAGER, ``self.update_state`` is observable via a spy on
        the registered Task instance. Pin the locked meta shape
        ``{"completed": k+1, "total": n}``.
        """
        from laserforce_simulator.celery_app import celery_app
        from matches.tasks import play_season_task  # noqa: F401  (registers)

        season, _teams = _active_season("HappyD", n_teams=2)

        actual_task = celery_app.tasks["matches.play_season"]
        calls: list[dict] = []

        def _spy_update_state(*args, **kwargs) -> None:
            calls.append({"args": args, "kwargs": kwargs})
            return None

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(actual_task, "update_state", _spy_update_state):
                from matches.tasks import play_season_task as _pst

                _pst.delay(season.id, max_matchdays=None)

        progress_calls = [c for c in calls if c["kwargs"].get("state") == "PROGRESS"]
        # 2 fixtures ⇒ 2 PROGRESS emissions (one after each Round).
        self.assertEqual(len(progress_calls), 2)
        # Final meta carries completed==total==2.
        final_meta = progress_calls[-1]["kwargs"]["meta"]
        self.assertEqual(final_meta["completed"], 2)
        self.assertEqual(final_meta["total"], 2)
        # First meta carries completed==1, total==2.
        first_meta = progress_calls[0]["kwargs"]["meta"]
        self.assertEqual(first_meta["completed"], 1)
        self.assertEqual(first_meta["total"], 2)


# ---------------------------------------------------------------------------
# TestPlaySeasonTaskMaxMatchdays
# ---------------------------------------------------------------------------


class TestPlaySeasonTaskMaxMatchdays(TestCase):
    """``max_matchdays=1`` plays exactly one matchday;
    ``max_matchdays=8`` caps at 8 distinct matchdays;
    ``max_matchdays=None`` plays every unplayed Round.
    """

    def test_max_matchdays_1_plays_exactly_one_matchday_worth_of_rounds(
        self,
    ) -> None:
        from matches.tasks import play_season_task

        # N=4 Season ⇒ 6 fixtures per round-half ⇒ 12 fixtures total;
        # matchday 1 carries 2 pairings.
        season, _teams = _active_season("MaxOne", n_teams=4)
        before = GameRound.objects.filter(match__season=season).count()
        self.assertEqual(before, 0)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_season_task.delay(season.id, max_matchdays=1)
        self.assertEqual(result.state, "SUCCESS")
        # Exactly one matchday's fixtures should have run — N=4 ⇒ 2 fixtures
        # in matchday 1.
        after = GameRound.objects.filter(match__season=season).count()
        self.assertEqual(after, 2)
        # Season stays active (more matchdays remain).
        season.refresh_from_db()
        self.assertEqual(season.state, "active")

    def test_max_matchdays_8_caps_at_8_distinct_matchdays(self) -> None:
        from matches.tasks import play_season_task

        # N=3 ⇒ 6 fixtures total in 6 matchdays (odd-N has 1 played-pair
        # per matchday) — exhausts in fewer than 8.
        season, _teams = _active_season("MaxEight", n_teams=3)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_season_task.delay(season.id, max_matchdays=8)
        self.assertEqual(result.state, "SUCCESS")
        # All 6 fixtures should have run (Season has fewer than 8 matchdays).
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 6)
        # The result payload's total reflects 6 fixtures.
        self.assertEqual(result.result["completed"], 6)
        self.assertEqual(result.result["total"], 6)

    def test_max_matchdays_none_plays_every_unplayed_round(self) -> None:
        from matches.tasks import play_season_task

        season, _teams = _active_season("MaxNone", n_teams=3)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_season_task.delay(season.id, max_matchdays=None)
        self.assertEqual(result.state, "SUCCESS")
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 6)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")


# ---------------------------------------------------------------------------
# TestPlaySeasonTaskPerRoundCommit
# ---------------------------------------------------------------------------


class TestPlaySeasonTaskPerRoundCommit(TestCase):
    """Per-Round atomic commits — a mid-loop exception leaves prior Rounds
    persisted, and re-invoking after the failure resumes from where it
    stopped (idempotent at the Round level).
    """

    def test_mid_loop_exception_leaves_prior_rounds_committed(self) -> None:
        from matches.tasks import play_season_task

        season, _teams = _active_season("MidExc", n_teams=3)
        original = BatchSimulator.simulate_scheduled_round

        # State trick: raise on the 3rd call only.
        state = {"calls": 0}

        def _raises_on_third(self, season_, team_a, team_b, round_number, **kw):
            state["calls"] += 1
            if state["calls"] == 3:
                raise ValueError("contrived mid-loop failure")
            return original(self, season_, team_a, team_b, round_number, **kw)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(
                BatchSimulator,
                "simulate_scheduled_round",
                _raises_on_third,
            ):
                # EAGER + EAGER_PROPAGATES=True ⇒ the exception surfaces.
                with self.assertRaises(ValueError):
                    play_season_task.delay(season.id, max_matchdays=None)

        # The first 2 Rounds were committed in their own atomic blocks.
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 2)
        # Season stays active — only the final fixture's complete_if_finished
        # auto-transition would flip it.
        season.refresh_from_db()
        self.assertEqual(season.state, "active")

    def test_re_clicking_play_resumes_from_where_failure_stopped(self) -> None:
        from matches.tasks import play_season_task

        season, _teams = _active_season("Resume", n_teams=2)
        original = BatchSimulator.simulate_scheduled_round

        state = {"calls": 0}

        def _raises_first_time(self, season_, team_a, team_b, round_number, **kw):
            state["calls"] += 1
            # Fail on the 1st call only — then un-patch.
            if state["calls"] == 1:
                raise ValueError("first attempt fails")
            return original(self, season_, team_a, team_b, round_number, **kw)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(
                BatchSimulator,
                "simulate_scheduled_round",
                _raises_first_time,
            ):
                with self.assertRaises(ValueError):
                    play_season_task.delay(season.id, max_matchdays=None)
            # No GameRound persisted on the first attempt (the failure
            # happened on the 1st simulator call, before any commit).
            self.assertEqual(GameRound.objects.filter(match__season=season).count(), 0)
            # Un-patched on context exit. Re-invoke.
            result = play_season_task.delay(season.id, max_matchdays=None)
        self.assertEqual(result.state, "SUCCESS")
        # Now both fixtures (N=2 ⇒ 2) committed; Season completed.
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 2)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")


# ---------------------------------------------------------------------------
# TestPlaySeasonTaskTeamLookup
# ---------------------------------------------------------------------------


class TestPlaySeasonTaskTeamLookup(TestCase):
    """Spy on ``BatchSimulator.simulate_scheduled_round`` to assert the
    canonical id order from ``select_play_fixtures`` (``team_a_id = min``,
    ``team_b_id = max``) is preserved through to the simulator.
    """

    def test_canonical_id_order_from_select_play_fixtures_resolves_via_simulator(
        self,
    ) -> None:
        from matches.tasks import play_season_task

        season, teams = _active_season("Canon", n_teams=2)
        original_sim = BatchSimulator.simulate_scheduled_round
        calls: list[tuple[int, int]] = []

        def _spy_sim(self_, season_, team_a, team_b, round_number, **kwargs):
            calls.append((team_a.id, team_b.id))
            return original_sim(self_, season_, team_a, team_b, round_number, **kwargs)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(BatchSimulator, "simulate_scheduled_round", _spy_sim):
                play_season_task.delay(season.id, max_matchdays=None)

        # N=2 ⇒ 2 fixtures (round 1 + round 2 mirrored). Both call
        # pairs must be in canonical ascending id order per the seam
        # contract: ``team_a_id = min(pair)``, ``team_b_id = max(pair)``.
        sorted_ids = sorted(t.id for t in teams)
        self.assertGreaterEqual(len(calls), 2)
        for pair in calls:
            self.assertEqual(
                list(pair),
                sorted_ids,
                f"simulate_scheduled_round call pair {pair!r} not canonical "
                f"ascending id order {sorted_ids!r}",
            )


# ---------------------------------------------------------------------------
# TestPlaySeasonTaskMapResolution (LG-01j — appended per seam contract
# Section 9 ``play_season_task`` extension)
# ---------------------------------------------------------------------------


import io as _lg01j_io  # noqa: E402

from django.core.files.uploadedfile import (  # noqa: E402
    SimpleUploadedFile as _Lg01jSimpleUploadedFile,
)

from core.models import ArenaMap as _Lg01jArenaMap  # noqa: E402


def _lg01j_png_bytes() -> bytes:
    from PIL import Image as _PILImage

    buf = _lg01j_io.BytesIO()
    _PILImage.new("RGB", (10, 10), color=(200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


def _lg01j_make_arena_map(name: str) -> _Lg01jArenaMap:
    """Build a fully-configured 4x4 ArenaMap usable by
    ``BatchSimulator.simulate_scheduled_round``.

    Borrowed from ``matches/tests/test_batch_tasks.py::_make_minimal_arena_map``
    — confirmed ``MapZoneConfig``, red+blue ``MapBaseConfig``, computed
    ``SightLineConfig``, and red+blue ``BaseSightLineConfig`` rows.
    """
    from core.map_processing import compute_sight_lines
    from core.models import (
        BaseSightLineConfig as _LJBSL,
        MapBaseConfig as _LJBaseCfg,
        MapZoneConfig as _LJZoneCfg,
        SightLineConfig as _LJSightCfg,
    )

    zone_size = 50
    zone_data = [[1] * 4 for _ in range(4)]
    arena_map = _Lg01jArenaMap.objects.create(
        name=name, img_width=4 * zone_size, img_height=4 * zone_size
    )
    _LJZoneCfg.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        zone_data=zone_data,
        confirmed=True,
    )
    _LJBaseCfg.objects.create(
        arena_map=arena_map,
        base_type="red",
        x_px=zone_size // 2,
        y_px=zone_size // 2,
    )
    _LJBaseCfg.objects.create(
        arena_map=arena_map,
        base_type="blue",
        x_px=4 * zone_size - zone_size // 2,
        y_px=4 * zone_size - zone_size // 2,
    )
    _LJSightCfg.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        sight_data=compute_sight_lines(zone_data),
    )
    _LJBSL.objects.create(
        arena_map=arena_map, base_type="red", zone_size=zone_size, visible_cells=[]
    )
    _LJBSL.objects.create(
        arena_map=arena_map, base_type="blue", zone_size=zone_size, visible_cells=[]
    )
    return arena_map


class TestPlaySeasonTaskMapResolution(TestCase):
    """LG-01j — ``play_season_task`` calls ``_resolve_fixture_map`` once
    per fixture and passes the returned ``ArenaMap | None`` to
    ``simulate_scheduled_round`` via the ``arena_map=`` kwarg.

    Per LG-01j seam contract Section 9: ``ArenaMap.objects.in_bulk(pool_ids)``
    is called ONCE outside the per-fixture loop, not per-fixture. The
    real helper ``_resolve_fixture_map`` is exercised end-to-end (NO
    mocking of the seam — the LG-01b precedent).
    """

    def _make_active_season_with_map_config(
        self, prefix: str, *, map_mode: str, pool_ids: list[int], n_teams: int = 2
    ):
        """Build an active Season whose snapshot encodes the locked map
        config. We set the fields BEFORE ``start_season()`` so the
        snapshot taken at activation matches the fixture-test intent.
        """
        season, teams = _active_season(prefix, n_teams=n_teams)
        # Season is already active and has starting_team_ids_json
        # snapshotted. Now set the map config and re-snapshot via a
        # direct UPDATE (we don't go through clean()/start_season again
        # — see the LG-01j test_lg01e_next_season fixture pattern for
        # the precedent).
        season.map_mode = map_mode
        season.starting_map_pool_ids_json = sorted(pool_ids)
        season.save()
        season.refresh_from_db()
        return season, teams

    def test_simulate_scheduled_round_receives_arena_map_kwarg(self) -> None:
        """When the Season has map_mode='single' with a 1-map snapshot,
        every simulate_scheduled_round call's arena_map= kwarg is the
        one ArenaMap."""
        from matches.tasks import play_season_task

        the_map = _lg01j_make_arena_map("OnlyMap")
        season, _ = self._make_active_season_with_map_config(
            "MapSingle", map_mode="single", pool_ids=[the_map.id]
        )
        original_sim = BatchSimulator.simulate_scheduled_round
        captured_kwargs: list[dict] = []

        def _spy(self_, season_, team_a, team_b, round_number, **kwargs):
            captured_kwargs.append(dict(kwargs))
            return original_sim(self_, season_, team_a, team_b, round_number, **kwargs)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(BatchSimulator, "simulate_scheduled_round", _spy):
                play_season_task.delay(season.id, max_matchdays=None)

        # Every captured call carries arena_map=the_map.
        self.assertGreater(len(captured_kwargs), 0)
        for kwargs in captured_kwargs:
            self.assertIn(
                "arena_map",
                kwargs,
                "simulate_scheduled_round invoked without arena_map= kwarg",
            )
            self.assertEqual(
                kwargs["arena_map"].id if kwargs["arena_map"] else None,
                the_map.id,
            )

    def test_mode_none_passes_arena_map_kwarg_with_none_value(self) -> None:
        """``mode == 'none'`` ⇒ every simulate_scheduled_round receives
        ``arena_map=None`` — preserves the LG-01d 3-zone-fallback
        behaviour."""
        from matches.tasks import play_season_task

        season, _ = self._make_active_season_with_map_config(
            "MapNone", map_mode="none", pool_ids=[]
        )
        original_sim = BatchSimulator.simulate_scheduled_round
        captured_kwargs: list[dict] = []

        def _spy(self_, season_, team_a, team_b, round_number, **kwargs):
            captured_kwargs.append(dict(kwargs))
            return original_sim(self_, season_, team_a, team_b, round_number, **kwargs)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(BatchSimulator, "simulate_scheduled_round", _spy):
                play_season_task.delay(season.id, max_matchdays=None)

        self.assertGreater(len(captured_kwargs), 0)
        for kwargs in captured_kwargs:
            self.assertIn("arena_map", kwargs)
            self.assertIsNone(kwargs["arena_map"])

    def test_mode_random_per_round_arena_map_varies_across_fixtures(self) -> None:
        """``mode == 'random_per_round'`` with a multi-map pool ⇒ at
        least one fixture draws a different map than another (the
        per-fixture identity changes the seed)."""
        from matches.tasks import play_season_task

        ms = [_lg01j_make_arena_map(f"VaryMap{i}") for i in range(5)]
        season, _ = self._make_active_season_with_map_config(
            "MapVaryRand",
            map_mode="random_per_round",
            pool_ids=[m.id for m in ms],
            n_teams=4,  # bigger Season → more fixtures → more chances
        )
        original_sim = BatchSimulator.simulate_scheduled_round
        captured: list = []

        def _spy(self_, season_, team_a, team_b, round_number, **kwargs):
            captured.append(kwargs.get("arena_map"))
            return original_sim(self_, season_, team_a, team_b, round_number, **kwargs)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(BatchSimulator, "simulate_scheduled_round", _spy):
                play_season_task.delay(season.id, max_matchdays=None)

        # Every fixture received SOME ArenaMap (not None).
        for arena_map in captured:
            self.assertIsNotNone(
                arena_map,
                "random_per_round with non-empty pool should never yield None",
            )
        # Across N=4 ⇒ 12 fixtures + 5-map pool, at least 2 distinct
        # ArenaMap ids should appear.
        distinct = {m.id for m in captured if m is not None}
        self.assertGreater(
            len(distinct),
            1,
            "random_per_round should produce >1 distinct map across 12 fixtures",
        )

    def test_arena_map_in_bulk_called_once_not_per_fixture(self) -> None:
        """LG-01j seam contract Section 9: ``ArenaMap.objects.in_bulk(pool_ids)``
        is called ONCE outside the per-fixture loop, regardless of
        ``len(to_play)``.

        Pin this by spying on ``ArenaMap.objects.in_bulk``.
        """
        from matches.tasks import play_season_task

        ms = [_lg01j_make_arena_map(f"BulkMap{i}") for i in range(3)]
        season, _ = self._make_active_season_with_map_config(
            "MapBulk",
            map_mode="random_per_round",
            pool_ids=[m.id for m in ms],
            n_teams=4,  # 12 fixtures
        )

        from core.models import ArenaMap

        original_in_bulk = ArenaMap.objects.in_bulk
        call_count = {"n": 0}

        def _counting_in_bulk(*args, **kwargs):
            call_count["n"] += 1
            return original_in_bulk(*args, **kwargs)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(
                ArenaMap.objects,
                "in_bulk",
                _counting_in_bulk,
            ):
                play_season_task.delay(season.id, max_matchdays=None)

        # Locked: exactly ONE call to ArenaMap.objects.in_bulk for the
        # entire task body — NOT per-fixture.
        self.assertEqual(
            call_count["n"],
            1,
            f"ArenaMap.objects.in_bulk was called {call_count['n']!r} "
            "times, expected exactly 1 (single bulk fetch outside loop)",
        )

    def test_resolve_fixture_map_called_once_per_fixture(self) -> None:
        """The helper is called once per fixture in fixture order.

        Spy on ``matches.tasks._resolve_fixture_map`` (the helper lives
        in the same module — patch the module-level binding directly).
        """
        from matches import tasks as _tasks
        from matches.tasks import play_season_task

        season, _ = self._make_active_season_with_map_config(
            "ResolveOncePer",
            map_mode="none",
            pool_ids=[],
            n_teams=2,
        )
        original_resolve = _tasks._resolve_fixture_map
        captured_calls: list = []

        def _spy_resolve(season_, fixture, pool_by_id):
            captured_calls.append((fixture.matchday, fixture.round_number))
            return original_resolve(season_, fixture, pool_by_id)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(_tasks, "_resolve_fixture_map", _spy_resolve):
                play_season_task.delay(season.id, max_matchdays=None)

        # N=2 ⇒ 2 fixtures.
        self.assertEqual(len(captured_calls), 2)


# ===== Play-orchestrator pure helpers =====
import os
import subprocess
import sys
from dataclasses import dataclass

from django.test import SimpleTestCase

from matches.season_dashboard import (
    find_next_matchday,
    select_play_fixtures,
)

# ---------------------------------------------------------------------------
# Local ScheduleFixture stub — duck-types the production dataclass without
# importing ``matches.schedule_generator`` (which would defeat the import
# guard for the pure module).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _F:
    matchday: int
    round_number: int
    team_a_id: int
    team_b_id: int
    leg: int = 1  # LG-02-Part2c-3a — single-RR / leg-1 stubs default to 1


# ---------------------------------------------------------------------------
# TestFindNextMatchday
# ---------------------------------------------------------------------------


class TestFindNextMatchday(SimpleTestCase):
    """LG-02-Part2c-2 — ``find_next_matchday`` is phase-aware: ``fixtures`` is
    a list of ``(phase_id, ScheduleFixture)`` pairs and ``played_keys`` is a set
    of ``(phase_id, frozenset({team ids}), round_number, leg)`` 4-tuples
    (LG-02-Part2c-3a widened the key with ``leg``; these single-RR stubs are all
    ``leg=1``). It returns the global matchday of the first unplayed pair, or
    ``None`` on empty / all-played input. Side-agnostic frozenset match within a
    phase.
    """

    def test_empty_fixtures_returns_none(self) -> None:
        self.assertIsNone(find_next_matchday([], set()))

    def test_no_played_returns_first_matchday(self) -> None:
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
            (10, _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3)),
        ]
        self.assertEqual(find_next_matchday(fixtures, set()), 1)

    def test_partial_played_returns_first_unplayed_matchday(self) -> None:
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
            (10, _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3)),
        ]
        played = {(10, frozenset({1, 2}), 1, 1)}
        self.assertEqual(find_next_matchday(fixtures, played), 2)

    def test_all_played_returns_none(self) -> None:
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
        ]
        played = {
            (10, frozenset({1, 2}), 1, 1),
            (10, frozenset({1, 3}), 1, 1),
        }
        self.assertIsNone(find_next_matchday(fixtures, played))

    def test_side_agnostic_frozenset_match(self) -> None:
        """A played key ``(phase_id, frozenset({1, 2}), 1)`` matches a fixture
        with ``team_a_id=1, team_b_id=2, round_number=1`` (same phase)
        regardless of which physical side each team played.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
        ]
        # Played key carried with the pair-set reversed — should still
        # match fixture 1 via frozenset semantics.
        played = {(10, frozenset({2, 1}), 1, 1)}
        self.assertEqual(find_next_matchday(fixtures, played), 2)

    def test_round_2_matchday_unplayed_while_round_1_played(self) -> None:
        """The next unplayed matchday may be a round-2 matchday — the
        round-1 mirror's matchday key differs from the round-2 mirror's
        even for the same pair.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=2, team_a_id=1, team_b_id=2)),
        ]
        played = {(10, frozenset({1, 2}), 1, 1)}
        # Round 1 played; the round-2 mirror is matchday 2.
        self.assertEqual(find_next_matchday(fixtures, played), 2)

    def test_phase_discrimination_same_pair_different_phase(self) -> None:
        """An identical ``(frozenset, round_number)`` played in phase 10 does
        NOT mark the SAME pairing in phase 20 played — the ``phase_id`` is part
        of the key. The next unplayed matchday is the phase-20 fixture.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (20, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=2)),
        ]
        # Only the phase-10 pairing is played.
        played = {(10, frozenset({1, 2}), 1, 1)}
        # The phase-20 pairing (same teams, same round) is still unplayed.
        self.assertEqual(find_next_matchday(fixtures, played), 2)

    def test_first_unplayed_spans_rr1_rr2_boundary(self) -> None:
        """When every RR1 (phase 10) fixture is played, the first unplayed
        matchday is the first RR2 (phase 20) matchday — the global calendar
        crosses the phase boundary monotonically.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (20, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=2)),
        ]
        played = {(10, frozenset({1, 2}), 1, 1)}
        self.assertEqual(find_next_matchday(fixtures, played), 2)


# ---------------------------------------------------------------------------
# TestSelectPlayFixtures
# ---------------------------------------------------------------------------


class TestSelectPlayFixtures(SimpleTestCase):
    """LG-02-Part2c-2 — ``select_play_fixtures`` is phase-aware: it carries
    ``(phase_id, ScheduleFixture)`` pairs and
    ``(phase_id, frozenset, round, leg)`` 4-tuple keys (LG-02-Part2c-3a widened
    the key with ``leg``; these single-RR stubs are all ``leg=1``), and returns
    the unplayed pairs spanning the next
    ``max_matchdays`` distinct unplayed GLOBAL matchdays. ``max_matchdays=None``
    returns ALL unplayed pairs. The distinct-matchday window naturally spans the
    RR1->RR2 boundary because the play loop feeds OFFSET (global) matchdays.

    Output is the list of unplayed ``(phase_id, fixture)`` pairs in iteration
    order.
    """

    def test_empty_fixtures_returns_empty_list(self) -> None:
        self.assertEqual(select_play_fixtures([], set(), max_matchdays=1), [])
        self.assertEqual(select_play_fixtures([], set(), max_matchdays=None), [])

    def test_max_matchdays_1_returns_one_matchday_unplayed_only(self) -> None:
        """Play One Week happy path — exactly the next unplayed matchday's
        pairs.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=1, round_number=1, team_a_id=3, team_b_id=4)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
            (10, _F(matchday=2, round_number=1, team_a_id=2, team_b_id=4)),
        ]
        result = select_play_fixtures(fixtures, set(), max_matchdays=1)
        self.assertEqual(len(result), 2)
        # Each entry is a (phase_id, fixture) pair; all returned are matchday 1.
        for phase_id, f in result:
            self.assertEqual(phase_id, 10)
            self.assertEqual(f.matchday, 1)

    def test_max_matchdays_8_returns_up_to_8_distinct_matchdays(self) -> None:
        """Play Two Months happy path — up to 8 distinct unplayed matchdays
        on a > 8-matchday Season.
        """
        fixtures = []
        for md in range(1, 13):  # 12 matchdays
            fixtures.append(
                (10, _F(matchday=md, round_number=1, team_a_id=1, team_b_id=2))
            )
        result = select_play_fixtures(fixtures, set(), max_matchdays=8)
        distinct_matchdays = {f.matchday for _pid, f in result}
        self.assertEqual(len(distinct_matchdays), 8)
        # The 8 matchdays are the FIRST 8 (1..8).
        self.assertEqual(sorted(distinct_matchdays), list(range(1, 9)))

    def test_max_matchdays_8_caps_at_actual_remaining_when_fewer(self) -> None:
        """Season with only 3 unplayed matchdays + ``max_matchdays=8``
        returns those 3.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
            (10, _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3)),
        ]
        result = select_play_fixtures(fixtures, set(), max_matchdays=8)
        # All 3 pairs returned; only 3 distinct matchdays exist.
        self.assertEqual(len(result), 3)
        distinct = {f.matchday for _pid, f in result}
        self.assertEqual(distinct, {1, 2, 3})

    def test_max_matchdays_none_returns_all_unplayed(self) -> None:
        """Play Until End happy path — every unplayed pair regardless of
        matchday.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
            (10, _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3)),
        ]
        played = {(10, frozenset({1, 2}), 1, 1)}
        result = select_play_fixtures(fixtures, played, max_matchdays=None)
        self.assertEqual(len(result), 2)
        # The played fixture is not in result.
        for phase_id, f in result:
            self.assertNotEqual(
                (phase_id, frozenset({f.team_a_id, f.team_b_id}), f.round_number),
                (10, frozenset({1, 2}), 1),
            )

    def test_boundary_at_last_matchday_returns_that_matchdays_unplayed(
        self,
    ) -> None:
        """If only matchday K remains and ``max_matchdays >= 1``, returns
        exactly matchday K's unplayed pairs.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
            (10, _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3)),
            (10, _F(matchday=3, round_number=1, team_a_id=1, team_b_id=4)),
        ]
        played = {
            (10, frozenset({1, 2}), 1, 1),
            (10, frozenset({1, 3}), 1, 1),
        }
        result = select_play_fixtures(fixtures, played, max_matchdays=1)
        self.assertEqual(len(result), 2)
        for _pid, f in result:
            self.assertEqual(f.matchday, 3)

    def test_all_played_returns_empty_list(self) -> None:
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
        ]
        played = {
            (10, frozenset({1, 2}), 1, 1),
            (10, frozenset({1, 3}), 1, 1),
        }
        self.assertEqual(select_play_fixtures(fixtures, played, max_matchdays=1), [])
        self.assertEqual(select_play_fixtures(fixtures, played, max_matchdays=None), [])

    def test_preserves_generate_schedule_iteration_order(self) -> None:
        """Output list's iteration order matches the input ``fixtures``
        order (canonical iteration order is preserved); the pairs come back
        verbatim.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=1, round_number=1, team_a_id=3, team_b_id=4)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
            (10, _F(matchday=2, round_number=1, team_a_id=2, team_b_id=4)),
        ]
        result = select_play_fixtures(fixtures, set(), max_matchdays=None)
        self.assertEqual(result, fixtures)

    def test_side_agnostic_key_matching(self) -> None:
        """A played key whose ``frozenset`` matches an unplayed fixture (same
        phase) is treated as played, regardless of which physical side each
        team played.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
        ]
        # Played key carried as the reversed pair-set.
        played = {(10, frozenset({2, 1}), 1, 1)}
        result = select_play_fixtures(fixtures, played, max_matchdays=None)
        # The matchday-1 fixture is treated as played; only the matchday-2
        # fixture comes back.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1].matchday, 2)

    def test_max_matchdays_1_with_zero_unplayed_matchdays_returns_empty(
        self,
    ) -> None:
        """Defensive — all-played input + ``max_matchdays=1`` ⇒ ``[]``."""
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
        ]
        played = {(10, frozenset({1, 2}), 1, 1)}
        self.assertEqual(select_play_fixtures(fixtures, played, max_matchdays=1), [])

    def test_partial_matchday_played_still_returns_remaining_fixtures(
        self,
    ) -> None:
        """If 2 of 4 pairs on matchday 3 are played and 2 are unplayed,
        ``max_matchdays=1`` starting from matchday 3 returns just those 2
        remaining pairs.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3)),
            (10, _F(matchday=3, round_number=1, team_a_id=1, team_b_id=4)),
            (10, _F(matchday=3, round_number=1, team_a_id=2, team_b_id=5)),
            (10, _F(matchday=3, round_number=1, team_a_id=3, team_b_id=6)),
            (10, _F(matchday=3, round_number=1, team_a_id=7, team_b_id=8)),
        ]
        played = {
            (10, frozenset({1, 2}), 1, 1),
            (10, frozenset({1, 3}), 1, 1),
            # Two of the four matchday-3 fixtures are played.
            (10, frozenset({1, 4}), 1, 1),
            (10, frozenset({2, 5}), 1, 1),
        }
        result = select_play_fixtures(fixtures, played, max_matchdays=1)
        self.assertEqual(len(result), 2)
        # Both remaining are matchday 3.
        for _pid, f in result:
            self.assertEqual(f.matchday, 3)
        pair_sets = {frozenset({f.team_a_id, f.team_b_id}) for _pid, f in result}
        self.assertEqual(pair_sets, {frozenset({3, 6}), frozenset({7, 8})})

    # ---- LG-02-Part2c-2 phase-discrimination + boundary-spanning cases ----

    def test_phase_discrimination_same_pair_different_phase_not_marked_played(
        self,
    ) -> None:
        """An identical ``(frozenset, round_number)`` played in RR1 (phase 10)
        does NOT mark the RR2 (phase 20) pairing played — the ``phase_id`` is
        part of the key. The RR2 pairing comes back as unplayed.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (20, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=2)),
        ]
        played = {(10, frozenset({1, 2}), 1, 1)}
        result = select_play_fixtures(fixtures, played, max_matchdays=None)
        self.assertEqual(len(result), 1)
        phase_id, f = result[0]
        self.assertEqual(phase_id, 20)
        self.assertEqual(f.matchday, 2)

    def test_next_global_matchday_crosses_rr1_rr2_boundary(self) -> None:
        """With RR1 (phase 10, matchday 1) played, ``max_matchdays=1`` selects
        the first RR2 (phase 20) global matchday — the contiguous window spans
        the boundary.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (20, _F(matchday=2, round_number=1, team_a_id=1, team_b_id=2)),
            (20, _F(matchday=3, round_number=2, team_a_id=1, team_b_id=2)),
        ]
        played = {(10, frozenset({1, 2}), 1, 1)}
        result = select_play_fixtures(fixtures, played, max_matchdays=1)
        # Exactly the next single global matchday (matchday 2, phase 20).
        self.assertEqual(len(result), 1)
        phase_id, f = result[0]
        self.assertEqual(phase_id, 20)
        self.assertEqual(f.matchday, 2)

    def test_window_spans_boundary_when_budget_covers_both_phases(self) -> None:
        """A ``max_matchdays`` budget large enough to cover the tail of RR1 and
        the head of RR2 returns pairs from BOTH phases in one contiguous global
        window.
        """
        fixtures = [
            (10, _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2)),
            (10, _F(matchday=2, round_number=2, team_a_id=1, team_b_id=2)),
            (20, _F(matchday=3, round_number=1, team_a_id=1, team_b_id=2)),
            (20, _F(matchday=4, round_number=2, team_a_id=1, team_b_id=2)),
        ]
        result = select_play_fixtures(fixtures, set(), max_matchdays=3)
        phases = {pid for pid, _f in result}
        # The 3-matchday window covers matchdays 1,2 (phase 10) + 3 (phase 20).
        self.assertEqual(phases, {10, 20})
        self.assertEqual({f.matchday for _pid, f in result}, {1, 2, 3})


# ---------------------------------------------------------------------------
# TestNoDjangoImportsLeaked — defensive frozen-allowlist subprocess check.
# (Already pinned by ``test_season_dashboard.py``; included here so the
# LG-01d additions are guarded against introducing a stray Django import
# into the pure module.)
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """``matches.season_dashboard`` must not transitively import Django.

    Mirrors the LG-01c precedent: spawn a fresh subprocess, ``import
    matches.season_dashboard``, then walk ``sys.modules`` and assert no
    entry matches the ``django`` prefix.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import pathlib
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.season_dashboard  # noqa: F401
            leaked = sorted(
                m for m in sys.modules
                if m == "django" or m.startswith("django.")
            )
            if leaked:
                print("LEAK:" + ",".join(leaked))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Django import leaked into matches.season_dashboard.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )


# ---------------------------------------------------------------------------
# LG-02-Part2a — play_season_task equivalence over a phase-less Season
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2a-seam-contract.md`` §4:
# ``play_season_task`` over a phase-less Season plays the SAME fixtures it does
# today (the ``Season.scheduled_fixtures()`` chokepoint sources the identical
# list via the implicit-fallback path). Run under the existing
# ``CELERY_TASK_ALWAYS_EAGER`` conftest. Appended as a NEW class; no existing
# class is modified.

from matches.models import SeasonPhase as _Lg02SeasonPhase  # noqa: E402


def _played_keys(season) -> set:
    """Side-agnostic ``(frozenset(team pair), round_number)`` keys of every
    persisted GameRound in ``season`` — the matchups actually simulated."""
    keys = set()
    for gr in GameRound.objects.filter(match__season=season).select_related("match"):
        m = gr.match
        keys.add((frozenset({m.team_red_id, m.team_blue_id}), gr.round_number))
    return keys


class TestLg02Part2aPlaySeasonTaskPhaseless(TestCase):
    """LG-02-Part2a — phase-less vs explicit-RR-phase play IDENTICAL fixtures."""

    def test_phaseless_plays_full_schedule(self) -> None:
        """A phase-less active Season (the LG-01d default — ``start_season``
        creates no phases) plays every fixture and completes via the
        chokepoint's implicit fallback."""
        from matches.tasks import play_season_task

        season, _teams = _active_season("Lg02Phaseless", n_teams=3)
        # No SeasonPhase rows exist for this Season (phase-less / legacy shape).
        self.assertEqual(_Lg02SeasonPhase.objects.filter(season=season).count(), 0)

        expected_fixtures = season.scheduled_fixtures()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_season_task.delay(season.id, max_matchdays=None)
        self.assertEqual(result.state, "SUCCESS")

        # Every scheduled fixture has a persisted GameRound.
        played = _played_keys(season)
        for f in expected_fixtures:
            self.assertIn(
                (frozenset({f.team_a_id, f.team_b_id}), f.round_number), played
            )
        self.assertEqual(len(played), len(expected_fixtures))
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")

    def test_phaseless_and_explicit_phase_play_same_fixtures(self) -> None:
        """Two N=3 Seasons enrolling teams with the SAME ids (so the pure
        schedule is identical) play the SAME set of matchups — one phase-less,
        one with an explicit ``round_robin`` phase."""
        from matches.tasks import play_season_task

        # Build a phase-less Season and capture its scheduled fixtures.
        phaseless, _t1 = _active_season("Lg02EqA", n_teams=3)
        self.assertEqual(_Lg02SeasonPhase.objects.filter(season=phaseless).count(), 0)
        fixtures_phaseless = phaseless.scheduled_fixtures()

        # Build a second Season and give it one explicit round_robin phase.
        with_phase, _t2 = _active_season("Lg02EqB", n_teams=3)
        _Lg02SeasonPhase.objects.create(
            season=with_phase, ordinal=1, phase_type="round_robin"
        )
        fixtures_with_phase = with_phase.scheduled_fixtures()

        # The two Seasons enroll different Team rows (different ids), so the
        # absolute fixtures differ; what must match is the STRUCTURE — same
        # fixture count and same per-fixture (matchday, round_number) shape.
        self.assertEqual(len(fixtures_phaseless), len(fixtures_with_phase))
        self.assertEqual(
            [(f.matchday, f.round_number) for f in fixtures_phaseless],
            [(f.matchday, f.round_number) for f in fixtures_with_phase],
        )

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            r1 = play_season_task.delay(phaseless.id, max_matchdays=None)
            r2 = play_season_task.delay(with_phase.id, max_matchdays=None)
        self.assertEqual(r1.state, "SUCCESS")
        self.assertEqual(r2.state, "SUCCESS")

        # Both played the same NUMBER of fixtures and both completed.
        self.assertEqual(len(_played_keys(phaseless)), len(fixtures_phaseless))
        self.assertEqual(len(_played_keys(with_phase)), len(fixtures_with_phase))
        phaseless.refresh_from_db()
        with_phase.refresh_from_db()
        self.assertEqual(phaseless.state, "completed")
        self.assertEqual(with_phase.state, "completed")


# ---------------------------------------------------------------------------
# LG-02-Part2c-2 — play_season_task over a TWO-RR-phase Season
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-2-seam-contract.md`` §5.1 + §7:
# ``play_season_task`` iterates by phase, builds phase-aware played_keys, and
# attributes each Round's Match to the correct ``season_phase``. It plays RR1
# FULLY before any RR2 fixture (matchday order is global-continuous), and the
# Season completes only when the final RR phase finishes. Appended as NEW
# classes; no existing class is modified.

from matches.models import Match as _Lg02c2Match  # noqa: E402


def _two_rr_phase_active_season(prefix: str, n_teams: int = 2):
    """An active Season composed of two ordinal-ordered ``round_robin``
    phases (RR1 ordinal 1, RR2 ordinal 2), ``n_teams`` enrolled, started.

    Returns ``(season, teams, rr1, rr2)``.
    """
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(league=league, name="S1", start_date=date.today())
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    rr1 = _Lg02SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    rr2 = _Lg02SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="round_robin"
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr1, rr2


class TestLg02Part2cPlaySeasonTaskMultiRr(TestCase):
    """LG-02-Part2c-2 — ``play_season_task`` over a two-RR-phase Season."""

    def test_until_end_plays_both_rr_phases_and_completes(self) -> None:
        from matches.tasks import play_season_task

        season, _teams, _rr1, _rr2 = _two_rr_phase_active_season("MrrEnd", n_teams=2)
        # N=2 ⇒ 2 fixtures per RR phase ⇒ 4 fixtures total across two phases.
        expected_total = len(season.scheduled_fixtures())
        self.assertEqual(expected_total, 4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_season_task.delay(season.id, max_matchdays=None)
        self.assertEqual(result.state, "SUCCESS")
        self.assertEqual(
            GameRound.objects.filter(match__season=season).count(), expected_total
        )
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)

    def test_rounds_attributed_to_correct_season_phase(self) -> None:
        from matches.tasks import play_season_task

        season, _teams, rr1, rr2 = _two_rr_phase_active_season("MrrAttr", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(season.id, max_matchdays=None)
        # Each RR phase's Rounds are attributed to its own season_phase.
        rr1_rounds = GameRound.objects.filter(match__season_phase=rr1).count()
        rr2_rounds = GameRound.objects.filter(match__season_phase=rr2).count()
        self.assertEqual(rr1_rounds, 2)
        self.assertEqual(rr2_rounds, 2)
        # No Round is left with a NULL season_phase (every Round is by-phase).
        self.assertEqual(
            GameRound.objects.filter(
                match__season=season, match__season_phase__isnull=True
            ).count(),
            0,
        )

    def test_same_pairing_across_phases_yields_two_distinct_matches(self) -> None:
        from matches.tasks import play_season_task

        season, _teams, rr1, rr2 = _two_rr_phase_active_season("MrrDist", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(season.id, max_matchdays=None)
        # N=2 ⇒ one pairing; across two RR phases that is two distinct Matches.
        matches = _Lg02c2Match.objects.filter(season=season)
        self.assertEqual(matches.count(), 2)
        phase_ids = sorted(m.season_phase_id for m in matches)
        self.assertEqual(phase_ids, sorted([rr1.pk, rr2.pk]))

    def test_play_one_matchday_starts_in_rr1_not_rr2(self) -> None:
        from matches.tasks import play_season_task

        season, _teams, rr1, rr2 = _two_rr_phase_active_season("MrrFirst", n_teams=2)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(season.id, max_matchdays=1)
        # The first global matchday belongs to RR1 — RR2 has no Rounds yet.
        self.assertGreater(GameRound.objects.filter(match__season_phase=rr1).count(), 0)
        self.assertEqual(GameRound.objects.filter(match__season_phase=rr2).count(), 0)
        season.refresh_from_db()
        self.assertEqual(season.state, "active")

    def test_does_not_complete_after_rr1_only(self) -> None:
        from matches.tasks import play_season_task

        season, _teams, rr1, rr2 = _two_rr_phase_active_season("MrrMid", n_teams=2)
        # Play just RR1's two fixtures via a 2-matchday window (N=2 RR1 spans
        # two global matchdays: round-1 then the round-2 mirror).
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(season.id, max_matchdays=2)
        season.refresh_from_db()
        # RR1 complete but RR2 (the final phase) is not ⇒ Season stays active.
        self.assertTrue(season._phase_complete(rr1))
        self.assertFalse(season._phase_complete(rr2))
        self.assertEqual(season.state, "active")
        self.assertIsNone(season.champion_team_id)


# ===========================================================================
# LG-02-Part2c-3f — play_season_task phase-aware tail (weekly playoff pacing)
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3f-seam-contract.md`` §2.3 /
# §5 / §6.4: after the RR loop, ``play_season_task`` drains the trailing built
# tournament phase via ``play_next_bracket_round`` on the SHARED
# ``max_matchdays`` budget:
#   - ``max_matchdays=None`` ⇒ unbounded RR drain THEN unbounded bracket drain
#     to a champion (``season.champion_team`` set, ``state == "completed"``);
#   - ``max_matchdays=8`` ⇒ ``rr_weeks_played`` (distinct RR matchdays simulated
#     this run) is SUBTRACTED from the bracket budget, so a run can stop
#     mid-bracket when the shared budget is exhausted, resuming on a second call;
#   - the PROGRESS ``meta`` switches to STAGE counts (``stage_progress``) during
#     the bracket drain (``total == stage_total``).
#
# Champion id / state / Match & node counts — NEVER exact simulated point
# totals (tournament sims are non-deterministic). Runs under the existing
# ``CELERY_TASK_ALWAYS_EAGER`` conftest. Appended as NEW classes; no existing
# class is modified. These WILL fail until the Code agent lands the phase-aware
# tail + ``play_next_bracket_round`` — the TDD red state.

from matches.models import BracketNode as _Lg3fBracketNode  # noqa: E402


def _lg3f_rr_tournament_season(prefix: str, n: int = 4):
    """An active Season: ordinal-1 ``round_robin`` + ordinal-2 ``tournament``
    (season-ending ``standings``) SeasonPhase, ``n`` slotted teams enrolled,
    started. Returns ``(season, teams, rr, tournament_phase)``.
    """
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(league=league, name="S1", start_date=date.today())
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    rr = _Lg02SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    tournament_phase = _Lg02SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament"
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr, tournament_phase


class TestPlaySeasonTaskPlayoffTail(TestCase):
    """``play_season_task`` drains the RR phase THEN the bracket to a champion
    when ``max_matchdays=None``."""

    def test_until_end_drains_rr_then_bracket_to_champion(self) -> None:
        from matches.tasks import play_season_task

        season, _teams, _rr, tp = _lg3f_rr_tournament_season("TailEnd", n=4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result = play_season_task.delay(season.id, max_matchdays=None)
        self.assertEqual(result.state, "SUCCESS")

        season.refresh_from_db()
        tp.refresh_from_db()
        # The trailing tournament phase was built AND drained to a champion.
        self.assertIsNotNone(tp.tournament_id)
        self.assertEqual(tp.tournament.state, "completed")
        self.assertIsNotNone(tp.tournament.champion_id)
        # The Season is crowned with the tournament champion.
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tp.tournament.champion_id)

    def test_until_end_final_progress_meta_is_stage_counts(self) -> None:
        """During the bracket drain the PROGRESS meta switches to STAGE counts
        (``stage_progress``); the FINAL emission carries ``total ==
        stage_total`` for the fully-drained bracket."""
        from laserforce_simulator.celery_app import celery_app
        from matches.bracket import stage_progress
        from matches.models import _node_to_dict
        from matches.tasks import play_season_task  # noqa: F401  (registers)

        season, _teams, _rr, tp = _lg3f_rr_tournament_season("TailMeta", n=4)

        actual_task = celery_app.tasks["matches.play_season"]
        metas: list[dict] = []

        def _spy_update_state(*args, **kwargs) -> None:
            if kwargs.get("state") == "PROGRESS":
                metas.append(kwargs.get("meta"))
            return None

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(actual_task, "update_state", _spy_update_state):
                play_season_task.delay(season.id, max_matchdays=None)

        tp.refresh_from_db()
        nodes = [
            _node_to_dict(n)
            for n in tp.tournament.nodes.select_related(
                "advances_to", "tournament"
            ).prefetch_related("series_matches")
        ]
        _stage_completed, stage_total = stage_progress(nodes)
        # The final PROGRESS meta is stage-shaped (completed == total ==
        # stage_total) — the bracket drained fully.
        self.assertGreater(len(metas), 0)
        final_meta = metas[-1]
        self.assertEqual(final_meta["total"], stage_total)
        self.assertEqual(final_meta["completed"], stage_total)


class TestPlaySeasonTaskSharedBudget(TestCase):
    """``max_matchdays=8`` subtracts ``rr_weeks_played`` from the bracket budget,
    stopping mid-bracket when the shared budget is exhausted, and resumes on a
    second call."""

    def _resolved_node_count(self, tournament) -> int:
        return _Lg3fBracketNode.objects.filter(
            tournament=tournament, winner__isnull=False
        ).count()

    def test_shared_budget_stops_mid_bracket_then_resumes(self) -> None:
        from matches.tasks import play_season_task

        # N=4 RR spans several global matchdays; an 8-matchday budget plays the
        # RR (consuming rr_weeks_played) then a BOUNDED number of bracket stages
        # on the remainder. The 4-team bracket has 2 stages — the shared budget
        # may not crown a champion in one call.
        season, _teams, _rr, tp = _lg3f_rr_tournament_season("Shared", n=4)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            first = play_season_task.delay(season.id, max_matchdays=8)
        self.assertEqual(first.state, "SUCCESS")

        season.refresh_from_db()
        tp.refresh_from_db()
        # The RR drained and the tournament phase built (RR completion triggers
        # the auto-build), so the bracket exists by now.
        self.assertIsNotNone(tp.tournament_id)

        # Resume — a second call with a fresh budget finishes the bracket to a
        # champion (idempotent / resumable per the per-node-atomic contract).
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            second = play_season_task.delay(season.id, max_matchdays=8)
        self.assertEqual(second.state, "SUCCESS")
        season.refresh_from_db()
        tp.refresh_from_db()
        self.assertEqual(tp.tournament.state, "completed")
        self.assertIsNotNone(tp.tournament.champion_id)
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tp.tournament.champion_id)
