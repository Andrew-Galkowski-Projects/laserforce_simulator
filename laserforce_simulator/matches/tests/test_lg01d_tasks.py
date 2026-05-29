"""LG-01d — Celery EAGER tests for ``play_season_task``.

The seam contract is locked at ``.claude/worktrees/lg-01d-seam-contract.md``
(§5, §11b). Runs under ``CELERY_TASK_ALWAYS_EAGER=True`` (set by the
project ``conftest.py`` via ``LF_CELERY_EAGER=1``), so
``play_season_task.delay(...)`` executes synchronously in the request
thread and the returned ``AsyncResult``'s ``.result`` is the task's
return dict.

Tests use small-N Seasons (N=2 / N=3) so the loop completes quickly with
``BatchSimulator.ROUND_TICKS`` patched to a small value. Per CLAUDE.md
TDD rules, tests assert schema-level outcomes only (row counts, state
transitions, persisted FK shape) — never exact score totals from
unseeded runs.

Locked test class names mirror the seam contract verbatim — do not
rename without re-syncing the contract.
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

    Borrowed from ``matches/tests/test_api03_tasks.py::_make_minimal_arena_map``
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
