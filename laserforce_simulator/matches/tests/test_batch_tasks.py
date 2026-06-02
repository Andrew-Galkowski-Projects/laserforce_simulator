"""API-03 — direct task-level tests for the two ``@shared_task``s.

Pinned by ``.claude/worktrees/api-03-seam-contract.md`` §4.2 (task
signatures + ``name=`` strings) and §6.4 (NEW test classes). Runs under
``CELERY_TASK_ALWAYS_EAGER = True`` (set by the project ``conftest.py``
via ``LF_CELERY_EAGER=1``) so ``task.apply(args=...)`` executes
synchronously in-process — no Redis required.

All names below are normative — do not rename, alias, or add alternatives.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# ``_make_minimal_arena_map`` is duplicated here (not imported from
# ``views_tests.py``) to keep API-03 tests independent of legacy view-test
# fixtures. Mirrors the smallest-config fixture used in test_map.py /
# views_tests.py — a fully-configured 4x4 ArenaMap usable by
# BatchSimulator.run_incremental / save_games.
# ---------------------------------------------------------------------------


def _make_minimal_arena_map(name: str = "Api03TaskMap"):
    from core.map_processing import compute_sight_lines
    from core.models import (
        ArenaMap,
        BaseSightLineConfig,
        MapBaseConfig,
        MapZoneConfig,
        SightLineConfig,
    )

    zone_size = 50
    zone_data = [[1] * 4 for _ in range(4)]
    arena_map = ArenaMap.objects.create(
        name=name, img_width=4 * zone_size, img_height=4 * zone_size
    )
    MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        zone_data=zone_data,
        confirmed=True,
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="red",
        x_px=zone_size // 2,
        y_px=zone_size // 2,
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="blue",
        x_px=4 * zone_size - zone_size // 2,
        y_px=4 * zone_size - zone_size // 2,
    )
    SightLineConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        sight_data=compute_sight_lines(zone_data),
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="red", zone_size=zone_size, visible_cells=[]
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="blue", zone_size=zone_size, visible_cells=[]
    )
    return arena_map


# ---------------------------------------------------------------------------
# simulate_batch_task
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSimulateBatchTaskHappyPath:
    """§6.4 — ``simulate_batch_task.apply(args=(red.id, blue.id, 2, None, 42))``
    under EAGER returns an ``EagerResult`` whose ``.result`` is the final
    aggregate dict and ``.state == "SUCCESS"``.
    """

    _AGGREGATE_KEYS = frozenset(
        {
            "n",
            "red_wins",
            "blue_wins",
            "ties",
            "avg_red_score",
            "avg_blue_score",
            "avg_seeds",
            "outlier_seeds",
        }
    )

    def test_apply_returns_success_with_aggregate(self) -> None:
        from matches.tasks import simulate_batch_task

        red, _ = make_team_with_slots("Api03TaskHappyR")
        blue, _ = make_team_with_slots("Api03TaskHappyB")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            result = simulate_batch_task.apply(args=(red.id, blue.id, 2, None, 42))

        assert result.state == "SUCCESS", (
            f"expected EagerResult.state=='SUCCESS', got {result.state!r}; "
            f"info={result.info!r}"
        )
        aggregate = result.result
        assert isinstance(
            aggregate, dict
        ), f"task.result must be the final aggregate dict; got {type(aggregate).__name__}"
        # Spot-check the locked aggregate key set (the same shape
        # ``_aggregate_batch`` returns and ``run()`` already pinned).
        for key in self._AGGREGATE_KEYS:
            assert (
                key in aggregate
            ), f"final aggregate missing locked key {key!r}; got keys={set(aggregate.keys())!r}"
        assert aggregate["n"] == 2, (
            f"final aggregate['n'] must equal the number of games run; "
            f"got {aggregate['n']!r}"
        )


@pytest.mark.django_db
class TestSimulateBatchTaskProgressUpdates:
    """§6.4 — under EAGER, ``simulate_batch_task`` calls
    ``self.update_state(state='PROGRESS', meta=snap)`` at least once for
    ``n=2`` and the meta payload matches the ``run_incremental`` snapshot
    shape ``{completed, total, aggregate}``.
    """

    def test_progress_emit_uses_snapshot_shape(self) -> None:
        from matches.tasks import simulate_batch_task

        red, _ = make_team_with_slots("Api03ProgressR")
        blue, _ = make_team_with_slots("Api03ProgressB")

        from laserforce_simulator.celery_app import celery_app

        calls: list[dict] = []

        # ``simulate_batch_task`` is a Celery ``Proxy`` (lazy import via
        # ``@shared_task``); patching ``type(proxy).update_state`` hits the
        # Proxy class, not the bound Task. The actual Task instance lives in
        # the app's task registry under the pinned ``name=`` string.
        actual_task = celery_app.tasks["matches.simulate_batch"]

        def _spy_update_state(*args, **kwargs) -> None:
            calls.append({"args": args, "kwargs": kwargs})
            return None

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            # Pin chunk size = 1 so n=2 yields >= 2 PROGRESS snapshots.
            with patch("matches.simulation._chunk_size_for", return_value=1):
                with patch.object(
                    actual_task,
                    "update_state",
                    _spy_update_state,
                ):
                    result = simulate_batch_task.apply(
                        args=(red.id, blue.id, 2, None, 99)
                    )
        assert result.state == "SUCCESS", (
            f"task must still succeed even with update_state spied; "
            f"state={result.state!r} info={result.info!r}"
        )

        progress_calls = [c for c in calls if c["kwargs"].get("state") == "PROGRESS"]
        assert progress_calls, (
            "simulate_batch_task did not emit any state='PROGRESS' "
            f"update_state calls; observed calls={calls!r}"
        )
        for call in progress_calls:
            meta = call["kwargs"].get("meta")
            assert isinstance(meta, dict), (
                f"PROGRESS meta must be a dict (the run_incremental snapshot); "
                f"got {type(meta).__name__}"
            )
            for key in ("completed", "total", "aggregate"):
                assert (
                    key in meta
                ), f"PROGRESS meta missing snapshot key {key!r}; got {set(meta.keys())!r}"
            assert isinstance(meta["completed"], int)
            assert isinstance(meta["total"], int)
            assert isinstance(meta["aggregate"], dict)


@pytest.mark.django_db
class TestSimulateBatchTaskWithMap:
    """§6.4 — ``arena_map_id=<real id>`` resolves to an ``ArenaMap`` and is
    threaded as ``arena_map=`` into ``run_incremental``. ``None`` passes
    ``None``. A stale id (no such row) falls back to ``None`` (preserves
    SIM-09 / SIM-10 ``_run_batch_job`` semantics).
    """

    def _capture_arena_map_via_spy(self, red, blue, arena_map_id):
        """Patch ``BatchSimulator.run_incremental`` to capture the
        ``arena_map`` kwarg the task threads in, then immediately yield a
        terminal snapshot so the task completes.
        """
        from matches.tasks import simulate_batch_task

        captured: dict = {}

        def _spy_run_incremental(
            self, team_red, team_blue, n=100, *, arena_map=None, **kwargs
        ):
            captured["arena_map"] = arena_map
            # Yield a minimal terminal snapshot — n=0 contract from
            # run_incremental: completed=0, total=0, aggregate=empty.
            yield {
                "completed": 0,
                "total": 0,
                "aggregate": {
                    "n": 0,
                    "red_wins": 0,
                    "blue_wins": 0,
                    "ties": 0,
                    "avg_red_score": 0,
                    "avg_blue_score": 0,
                    "avg_seeds": [],
                    "outlier_seeds": [],
                },
            }

        with patch.object(BatchSimulator, "run_incremental", _spy_run_incremental):
            result = simulate_batch_task.apply(
                args=(red.id, blue.id, 1, arena_map_id, 7)
            )

        return captured, result

    def test_real_arena_map_id_resolves_and_threads(self) -> None:
        red, _ = make_team_with_slots("Api03MapR")
        blue, _ = make_team_with_slots("Api03MapB")
        arena_map = _make_minimal_arena_map("Api03MapRealMap")

        captured, result = self._capture_arena_map_via_spy(red, blue, arena_map.id)

        assert (
            result.state == "SUCCESS"
        ), f"task must succeed with a real arena_map id; got state={result.state!r}"
        assert (
            "arena_map" in captured
        ), "simulate_batch_task never called run_incremental with an arena_map kwarg"
        assert captured["arena_map"] == arena_map, (
            f"simulate_batch_task did not resolve arena_map_id={arena_map.id!r}; "
            f"got {captured['arena_map']!r}"
        )

    def test_none_arena_map_id_threads_none(self) -> None:
        red, _ = make_team_with_slots("Api03NoMapR")
        blue, _ = make_team_with_slots("Api03NoMapB")

        captured, result = self._capture_arena_map_via_spy(red, blue, None)

        assert result.state == "SUCCESS"
        assert captured.get("arena_map") is None, (
            f"arena_map_id=None must thread arena_map=None; "
            f"got {captured.get('arena_map')!r}"
        )

    def test_stale_arena_map_id_falls_back_to_none(self) -> None:
        red, _ = make_team_with_slots("Api03StaleR")
        blue, _ = make_team_with_slots("Api03StaleB")

        stale_id = 9_999_999  # no ArenaMap row with this PK
        captured, result = self._capture_arena_map_via_spy(red, blue, stale_id)

        assert (
            result.state == "SUCCESS"
        ), f"stale arena_map_id must NOT crash the task; got state={result.state!r}"
        assert captured.get("arena_map") is None, (
            f"stale arena_map_id must fall back to arena_map=None; "
            f"got {captured.get('arena_map')!r}"
        )


@pytest.mark.django_db
class TestSimulateBatchTaskDeterminism:
    """§6.4 — same ``master_seed`` produces identical ``.result`` aggregates
    across two invocations (extends the SIM-07 / SIM-10 contract to the
    Celery task layer; EAGER ⇒ in-process ⇒ identical games).
    """

    def test_same_master_seed_yields_identical_aggregate(self) -> None:
        from matches.tasks import simulate_batch_task

        red, _ = make_team_with_slots("Api03DetermR")
        blue, _ = make_team_with_slots("Api03DetermB")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            result_a = simulate_batch_task.apply(args=(red.id, blue.id, 2, None, 12345))
            result_b = simulate_batch_task.apply(args=(red.id, blue.id, 2, None, 12345))

        assert result_a.state == "SUCCESS"
        assert result_b.state == "SUCCESS"
        assert result_a.result == result_b.result, (
            "simulate_batch_task with the same master_seed must produce "
            "identical aggregate dicts (extends SIM-07/SIM-10 determinism "
            "contract to the Celery task layer); diff:\n"
            f"a={result_a.result!r}\nb={result_b.result!r}"
        )


@pytest.mark.django_db
class TestSimulateBatchTaskFailFast:
    """§6.4 — patching ``_simulate_round`` to raise causes the task to surface
    the exception under ``CELERY_TASK_EAGER_PROPAGATES=True`` (the exception
    propagates as a Python exception out of ``.apply()``).
    """

    def test_simulate_round_raise_propagates(self) -> None:
        from matches.tasks import simulate_batch_task

        red, _ = make_team_with_slots("Api03FailR")
        blue, _ = make_team_with_slots("Api03FailB")

        def _raises(*args, **kwargs):
            raise ValueError("contrived task failure")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            with patch.object(BatchSimulator, "_simulate_round", _raises):
                with pytest.raises(ValueError, match="contrived task failure"):
                    simulate_batch_task.apply(args=(red.id, blue.id, 1, None, 42))


# ---------------------------------------------------------------------------
# save_games_task
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSaveGamesTaskHappyPath:
    """§6.4 — ``save_games_task.apply(args=(red.id, blue.id, [(12345, False)], 1, None))``
    returns ``.state=='SUCCESS'`` with ``.result == {"round_ids": [<int>]}``;
    ``GameRound.objects.get(id=<int>)`` exists.
    """

    def test_apply_persists_round_and_returns_round_ids(self) -> None:
        from matches.models import GameRound
        from matches.tasks import save_games_task

        red, _ = make_team_with_slots("Api03SaveHappyR")
        blue, _ = make_team_with_slots("Api03SaveHappyB")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            # Seeds payload shape (matches the SIM-07 / SIM-08 contract):
            # list of (seed, flipped) pairs serialised over JSON as
            # list[list[int|bool]] — Celery's json serialiser coerces tuples
            # to lists, so the task accepts both shapes; we use list form.
            result = save_games_task.apply(
                args=(red.id, blue.id, [[12345, False]], 1, None)
            )

        assert (
            result.state == "SUCCESS"
        ), f"expected state='SUCCESS', got {result.state!r}; info={result.info!r}"
        payload = result.result
        assert isinstance(payload, dict)
        assert (
            "round_ids" in payload
        ), f"save_games_task result must carry 'round_ids'; got {payload!r}"
        assert isinstance(payload["round_ids"], list)
        assert len(payload["round_ids"]) == 1, (
            f"expected exactly one persisted round id for one (seed, flipped) "
            f"pair; got {payload['round_ids']!r}"
        )
        rid = payload["round_ids"][0]
        # Round must actually exist in the DB.
        assert GameRound.objects.filter(id=rid).exists(), (
            f"save_games_task returned round_ids=[{rid}] but no GameRound "
            f"row with that PK exists"
        )


@pytest.mark.django_db
class TestSaveGamesTaskWithMap:
    """§6.4 — consolidates the four ``TestSim09BatchArenaMapPlumbing``
    rewrites: real ``arena_map_id`` is resolved and threaded to
    ``save_games``; ``None`` passes ``None``; a stale id falls back to
    ``None``.
    """

    def _capture_arena_map_via_spy(self, red, blue, arena_map_id):
        from matches.tasks import save_games_task

        captured: dict = {}

        def _spy_save_games(self, t_red, t_blue, seeds, n, *, arena_map=None):
            captured["arena_map"] = arena_map
            captured["team_red"] = t_red
            captured["team_blue"] = t_blue
            captured["seeds"] = seeds
            captured["n"] = n
            # Return an empty list — the task uses [gr.id for gr in ...] so
            # an empty list produces round_ids=[] (acceptable for the spy).
            return []

        with patch.object(BatchSimulator, "save_games", _spy_save_games):
            result = save_games_task.apply(
                args=(red.id, blue.id, [[12345, False]], 1, arena_map_id)
            )

        return captured, result

    def test_real_arena_map_id_resolves_and_threads(self) -> None:
        red, _ = make_team_with_slots("Api03SaveMapR")
        blue, _ = make_team_with_slots("Api03SaveMapB")
        arena_map = _make_minimal_arena_map("Api03SaveMapRealMap")

        captured, result = self._capture_arena_map_via_spy(red, blue, arena_map.id)

        assert result.state == "SUCCESS"
        assert captured.get("arena_map") == arena_map, (
            f"save_games_task did not resolve arena_map_id={arena_map.id!r}; "
            f"got {captured.get('arena_map')!r}"
        )
        # Team objects must be resolved from ids too.
        assert captured["team_red"] == red
        assert captured["team_blue"] == blue

    def test_none_arena_map_id_threads_none(self) -> None:
        red, _ = make_team_with_slots("Api03SaveNoneR")
        blue, _ = make_team_with_slots("Api03SaveNoneB")

        captured, result = self._capture_arena_map_via_spy(red, blue, None)

        assert result.state == "SUCCESS"
        assert captured.get("arena_map") is None, (
            f"arena_map_id=None must thread arena_map=None into save_games; "
            f"got {captured.get('arena_map')!r}"
        )

    def test_stale_arena_map_id_falls_back_to_none(self) -> None:
        red, _ = make_team_with_slots("Api03SaveStaleR")
        blue, _ = make_team_with_slots("Api03SaveStaleB")

        stale_id = 9_999_999
        captured, result = self._capture_arena_map_via_spy(red, blue, stale_id)

        assert result.state == "SUCCESS", (
            f"stale arena_map_id must NOT crash save_games_task; "
            f"got state={result.state!r}"
        )
        assert captured.get("arena_map") is None, (
            f"stale arena_map_id must fall back to arena_map=None; "
            f"got {captured.get('arena_map')!r}"
        )


@pytest.mark.django_db
class TestSaveGamesTaskInvalidTeam:
    """§6.4 — a bogus ``team_red_id`` raises ``Team.DoesNotExist`` from
    ``Team.objects.get(...)``. Under ``EAGER_PROPAGATES=True`` the
    exception surfaces out of ``.apply()``.
    """

    def test_invalid_team_id_raises_does_not_exist(self) -> None:
        from teams.models import Team

        from matches.tasks import save_games_task

        # Build a real blue team so the failure is unambiguously the red lookup.
        blue, _ = make_team_with_slots("Api03InvalidBlue")
        bogus_red_id = 9_999_999

        with pytest.raises(Team.DoesNotExist):
            save_games_task.apply(
                args=(bogus_red_id, blue.id, [[12345, False]], 1, None)
            )
