"""SIM-10 — progressive batch simulation: ``run_incremental`` generator
contract.

Pinned by the seam contract at ``.claude/worktrees/sim-10-seam-contract.md``
§1 (simulation surface) and §5.1 (test boundary). Names are normative — do
not rename, alias, or add alternatives.
"""

from __future__ import annotations

import os
import time
import unittest
from typing import Any, Iterator
from unittest.mock import patch

import pytest

from matches.simulation import BatchSimulator, _chunk_size_for
from matches.sim_helpers.time_constants import SURVIVED_SENTINEL

# SIM-10 re-uses the existing aggregator (`_aggregate_batch`) without
# changing its name. It is currently a ``@staticmethod`` on
# ``BatchSimulator`` — the seam contract leaves it there ("Re-uses
# ``_aggregate_batch``" in §1.2) rather than promoting it module-level. We
# reach it via the class so the tests do not depend on a hypothetical move
# the Code agent did not actually do.
_aggregate_batch = BatchSimulator._aggregate_batch
from matches.tests.conftest import make_team_with_slots

# Locked table from §1.1 — pure-function inputs and outputs.
_CHUNK_SIZE_CASES: list[tuple[int, int]] = [
    (0, 1),
    (1, 1),
    (49, 1),
    (50, 1),
    (99, 1),
    (100, 2),
    (500, 10),
    (1000, 20),
    (1249, 24),
    (1250, 25),
    (5000, 25),
    (1_000_000, 25),
]


class TestChunkSizeFor(unittest.TestCase):
    """§5.1 — `_chunk_size_for(n)` returns the locked values for the locked
    inputs. Pure function; no fixtures needed.
    """

    def test_chunk_size_table(self) -> None:
        for n, expected in _CHUNK_SIZE_CASES:
            with self.subTest(n=n):
                self.assertEqual(
                    _chunk_size_for(n),
                    expected,
                    f"_chunk_size_for({n}) must return {expected}",
                )

    def test_chunk_size_is_in_locked_range(self) -> None:
        # Defensive: the docstring promises an int in [1, 25] for every input.
        for n in (0, 1, 49, 50, 100, 500, 1250, 10_000, 1_000_000):
            with self.subTest(n=n):
                c = _chunk_size_for(n)
                self.assertIsInstance(c, int)
                self.assertGreaterEqual(c, 1)
                self.assertLessEqual(c, 25)


# ---------------------------------------------------------------------------
# Shared roster helper. ``run_incremental`` calls ``team.active_roster`` (a
# Team ORM property) so the tests below need real Team/Player rows — hence
# ``@pytest.mark.django_db`` on the class, mirroring TestSim07RngSeed /
# TestSim08SideAlternation in ``test_batch_sim.py``.
# ---------------------------------------------------------------------------


_SNAPSHOT_KEYS: frozenset[str] = frozenset({"completed", "total", "aggregate"})


@pytest.mark.django_db
class TestRunIncrementalSnapshotShape:
    """§5.1 — every yielded snapshot has exactly the three top-level keys,
    `completed` is monotonic non-decreasing, `total == n` on every yield,
    and `aggregate["n"] == completed` (the `_aggregate_batch` partial-n
    contract).
    """

    def test_snapshot_shape_and_monotonicity(self) -> None:
        red, _ = make_team_with_slots("Sim10ShapeR")
        blue, _ = make_team_with_slots("Sim10ShapeB")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            snaps = list(BatchSimulator().run_incremental(red, blue, 4, master_seed=42))

        assert snaps, "run_incremental must yield at least once"
        last_completed = -1
        for snap in snaps:
            assert set(snap.keys()) == _SNAPSHOT_KEYS, (
                f"snapshot must have exactly {_SNAPSHOT_KEYS!r} keys, "
                f"got {set(snap.keys())!r}"
            )
            assert isinstance(snap["completed"], int)
            assert isinstance(snap["total"], int)
            assert isinstance(snap["aggregate"], dict)
            assert snap["total"] == 4
            assert (
                snap["completed"] >= last_completed
            ), "completed must be monotonic non-decreasing across yields"
            last_completed = snap["completed"]
            assert snap["aggregate"]["n"] == snap["completed"], (
                "aggregate['n'] is the _aggregate_batch partial-n contract "
                "and must equal snapshot['completed']"
            )
        # The final yielded snapshot covers all n games.
        assert snaps[-1]["completed"] == 4


@pytest.mark.django_db
class TestRunIncrementalFinalEqualsRun:
    """§5.1 — for a pinned ``master_seed``, the last yielded snapshot's
    ``aggregate`` dict equals what ``run()`` returns for the same args.
    Full-dict equality (not subset).
    """

    def test_last_snapshot_aggregate_matches_run(self) -> None:
        red, _ = make_team_with_slots("Sim10FinalR")
        blue, _ = make_team_with_slots("Sim10FinalB")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            snaps = list(
                BatchSimulator().run_incremental(red, blue, 10, master_seed=42)
            )
            run_result = BatchSimulator().run(red, blue, 10, master_seed=42)

        assert snaps[-1]["aggregate"] == run_result, (
            "last snapshot's aggregate must equal run() output (full dict "
            "equality) for the same master_seed"
        )


@pytest.mark.django_db
class TestRunIncrementalSerialEqualsParallelAtEveryBoundary:
    """§5.1 — submission-indexed ordering: serial and parallel must produce
    identical snapshots at every chunk boundary, not just the last.
    """

    @unittest.skipUnless(
        (os.cpu_count() or 1) > 1,
        "parallel path requires more than one CPU",
    )
    def test_serial_equals_parallel_every_boundary(self) -> None:
        red, _ = make_team_with_slots("Sim10ParR")
        blue, _ = make_team_with_slots("Sim10ParB")

        # NOTE: no ``ROUND_TICKS`` patch here. ``patch.object`` mutates the
        # parent-process class attribute, but ``ProcessPoolExecutor`` workers
        # spawn fresh subprocesses that re-import ``matches.simulation`` and
        # therefore see the production ``TICKS_PER_ROUND`` regardless. Running
        # serial under a small ROUND_TICKS while parallel runs full rounds
        # produced divergent seeded games — same seed, different mechanics.
        # The test pins the most critical SIM-10 contract (serial == parallel
        # at every chunk boundary) so we eat the ~3 s real-round cost on the
        # 3-zone fallback to keep both modes apples-to-apples.
        serial_snaps = list(
            BatchSimulator().run_incremental(red, blue, 8, workers=None, master_seed=42)
        )
        try:
            parallel_snaps = list(
                BatchSimulator().run_incremental(
                    red, blue, 8, workers=2, master_seed=42
                )
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(
                f"Parallel worker pool unavailable in this environment: {exc!r}"
            )

        assert len(serial_snaps) == len(parallel_snaps), (
            "serial and parallel must yield the same number of snapshots "
            f"(serial={len(serial_snaps)}, parallel={len(parallel_snaps)})"
        )
        for i, (s, p) in enumerate(zip(serial_snaps, parallel_snaps)):
            assert s == p, (
                f"snapshot {i} differs between serial and parallel:\n"
                f"  serial:   {s}\n  parallel: {p}"
            )


@pytest.mark.django_db
class TestRunIncrementalNZero:
    """§5.1 / §1.6 — `n == 0` yields exactly one terminal snapshot whose
    aggregate equals ``_aggregate_batch([], 0)``.
    """

    def test_n_zero_yields_single_terminal_snapshot(self) -> None:
        red, _ = make_team_with_slots("Sim10ZeroR")
        blue, _ = make_team_with_slots("Sim10ZeroB")

        snaps = list(BatchSimulator().run_incremental(red, blue, 0, master_seed=42))

        assert (
            len(snaps) == 1
        ), f"n==0 must yield exactly one snapshot, got {len(snaps)}"
        snap = snaps[0]
        assert snap["completed"] == 0
        assert snap["total"] == 0
        assert snap["aggregate"] == _aggregate_batch([], 0)


@pytest.mark.django_db
class TestRunIncrementalFailFast:
    """§5.1 / §1.7 — the generator surfaces an exception from
    ``_simulate_round`` and emits no half-snapshot containing the failed
    game. The parallel pool shuts down within a wall-clock budget.
    """

    def _seeded_failure(self, fail_on_call: int):
        """Build a side-effect that succeeds for the first ``fail_on_call - 1``
        calls and raises ``RuntimeError`` on the ``fail_on_call``-th call.

        We let the real ``_simulate_round`` run for the successful calls so
        the partial aggregate has plausible content; the failure only kicks
        in on the chosen game index.
        """
        real_simulate = BatchSimulator._simulate_round
        state: dict[str, int] = {"calls": 0}

        def wrapped(self_, *args, **kwargs):
            state["calls"] += 1
            if state["calls"] == fail_on_call:
                raise RuntimeError("contrived sim10 failure")
            return real_simulate(self_, *args, **kwargs)

        return wrapped

    def test_serial_fail_fast_propagates_and_no_half_snapshot(self) -> None:
        red, _ = make_team_with_slots("Sim10FailSerR")
        blue, _ = make_team_with_slots("Sim10FailSerB")

        emitted: list[dict[str, Any]] = []

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            with patch.object(
                BatchSimulator,
                "_simulate_round",
                self._seeded_failure(fail_on_call=3),
            ):
                gen: Iterator[dict] = BatchSimulator().run_incremental(
                    red, blue, 10, workers=None, master_seed=42
                )
                with pytest.raises(RuntimeError, match="contrived sim10 failure"):
                    for snap in gen:
                        emitted.append(snap)

        # No emitted snapshot may include the failed game (index 2 → games
        # `[0..3)`). Chunk size for n=10 is 1, so we expect at most two
        # snapshots before the raise.
        for snap in emitted:
            assert snap["completed"] < 3, (
                "fail-fast: no snapshot may include the failed game "
                f"(completed={snap['completed']})"
            )

    def test_parallel_fail_fast_cancels_pending_and_reraises(self) -> None:
        """Parallel fail-fast — mocked-executor unit test of the cancel loop.

        Patching a seam in the parent process does NOT propagate to
        ``ProcessPoolExecutor`` worker subprocesses (they re-import
        ``matches.simulation`` from disk), so a real-subprocess version of
        this test cannot reliably inject a contrived failure into game N.
        Instead we mock ``ProcessPoolExecutor`` and ``as_completed`` at the
        seam ``run_incremental`` imports them from, then verify the parallel
        branch's cancel-then-raise contract directly: when one future raises
        on ``.result()``, every future in ``future_to_index`` has ``.cancel()``
        called and the original exception propagates out of the generator.
        """
        red, _ = make_team_with_slots("Sim10FailParR")
        blue, _ = make_team_with_slots("Sim10FailParB")

        class _FakeFuture:
            def __init__(self, idx: int, raises: bool) -> None:
                self.idx = idx
                self._raises = raises
                self.cancel_called = False

            def result(self):
                if self._raises:
                    raise RuntimeError("contrived sim10 parallel failure")
                return {
                    "red_points": 0,
                    "blue_points": 0,
                    "red_survivors": 0,
                    "blue_survivors": 0,
                    "red_eliminated": False,
                    "blue_eliminated": False,
                    "eliminated_at": SURVIVED_SENTINEL,
                }

            def cancel(self) -> bool:
                self.cancel_called = True
                return True

        futures: list[_FakeFuture] = []

        class _FakeExecutor:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def __enter__(self) -> "_FakeExecutor":
                return self

            def __exit__(self, *exc) -> None:
                return None

            def submit(self, _fn, _args):
                fut = _FakeFuture(idx=len(futures), raises=(len(futures) == 2))
                futures.append(fut)
                return fut

        def _fake_as_completed(mapping):
            # Drain in submission order. The seam contract only requires
            # cancel-on-first-raise; the iteration order does not change the
            # invariant being tested.
            for fut in list(mapping):
                yield fut

        # run_incremental does a function-local
        # ``from concurrent.futures import ProcessPoolExecutor, as_completed``,
        # so the patch must target ``concurrent.futures`` (the source module
        # the bare names are bound from), not ``matches.simulation``.
        import concurrent.futures as cf

        with patch.object(cf, "ProcessPoolExecutor", _FakeExecutor):
            with patch.object(cf, "as_completed", _fake_as_completed):
                gen = BatchSimulator().run_incremental(
                    red, blue, 10, workers=2, master_seed=42
                )
                with pytest.raises(RuntimeError, match="contrived sim10 parallel"):
                    for _ in gen:
                        pass

        # Every submitted future had cancel() called — the cancel loop visits
        # all of `future_to_index` (the already-raised one is a no-op cancel,
        # but the seam contract says it is called all the same).
        assert len(futures) == 10, f"expected 10 submitted futures, got {len(futures)}"
        for i, fut in enumerate(futures):
            assert fut.cancel_called, (
                f"future {i} was not cancelled on fail-fast — the cancel loop "
                "skipped it (or ran in the wrong order)"
            )


@pytest.mark.django_db
class TestRunIncrementalDriveRun:
    """§5.1 / §1.5 — ``run()`` is implemented as the consumer of
    ``run_incremental``. A recording wrapper around ``run_incremental``
    must see ``run()`` drain the generator to exhaustion and return the
    last snapshot's ``aggregate``.
    """

    def test_run_consumes_run_incremental_to_exhaustion(self) -> None:
        red, _ = make_team_with_slots("Sim10DriveR")
        blue, _ = make_team_with_slots("Sim10DriveB")

        seen: list[dict[str, Any]] = []
        real_run_incremental = BatchSimulator.run_incremental

        def recording_wrapper(self_, *args, **kwargs):
            for snap in real_run_incremental(self_, *args, **kwargs):
                seen.append(snap)
                yield snap

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            with patch.object(BatchSimulator, "run_incremental", recording_wrapper):
                result = BatchSimulator().run(red, blue, 5, master_seed=42)

        assert seen, "run() must consume run_incremental at least once"
        assert (
            result == seen[-1]["aggregate"]
        ), "run() must return the last snapshot's aggregate dict"
        # The recorded snapshots' completed values must reach n.
        assert seen[-1]["completed"] == 5
