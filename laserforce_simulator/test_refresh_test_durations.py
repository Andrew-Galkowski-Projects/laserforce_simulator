"""Tests for the repo-level ``scripts/refresh_test_durations.py``.

The script lives outside the Django project, so it is loaded by path. Only the
pure merge is tested; the ``gh`` calls are thin wrappers.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "refresh_test_durations.py"
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("refresh_test_durations", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


refresh = _load_script()


def _run(run_id: str, shards: dict[int, dict[str, float]], jobs=None):
    shard_jobs = sorted(shards) if jobs is None else jobs
    return refresh.RunDurations(run_id=run_id, shard_jobs=shard_jobs, shards=shards)


class MergeRunsTests(TestCase):
    def test_one_run_unions_its_shards(self) -> None:
        run = _run("1", {1: {"a.py::t1": 1.0}, 2: {"b.py::t2": 2.0, "b.py::t3": 3.0}})

        self.assertEqual(
            refresh.merge_runs([run]),
            {"a.py::t1": 1.0, "b.py::t2": 2.0, "b.py::t3": 3.0},
        )

    def test_each_test_gets_its_median_across_runs(self) -> None:
        runs = [
            _run("1", {1: {"a.py::t1": 1.0}}),
            _run("2", {1: {"a.py::t1": 2.0}}),
            _run("3", {1: {"a.py::t1": 9.0}}),
        ]

        self.assertEqual(refresh.merge_runs(runs), {"a.py::t1": 2.0})

    def test_a_test_some_runs_missed_gets_the_median_of_its_values(self) -> None:
        runs = [
            _run("1", {1: {"a.py::t1": 1.0, "a.py::new": 4.0}}),
            _run("2", {1: {"a.py::t1": 1.0, "a.py::new": 6.0}}),
            _run("3", {1: {"a.py::t1": 1.0, "a.py::gone": 8.0}}),
        ]

        self.assertEqual(
            refresh.merge_runs(runs),
            {"a.py::t1": 1.0, "a.py::new": 5.0, "a.py::gone": 8.0},
        )


class MissingShardTests(TestCase):
    def test_a_run_missing_a_shard_artifact_names_the_run_and_shard(self) -> None:
        run = _run("777", {1: {"a.py::t1": 1.0}, 2: {"b.py::t2": 2.0}}, [1, 2, 3])

        with self.assertRaises(refresh.MissingShardError) as caught:
            refresh.merge_runs([run])

        self.assertIn("777", str(caught.exception))
        self.assertIn("3", str(caught.exception))

    def test_a_run_without_test_shards_is_refused(self) -> None:
        # e.g. a run of another workflow: merging it would write an empty file.
        with self.assertRaises(refresh.MissingShardError) as caught:
            refresh.merge_runs([_run("888", {}, [])])

        self.assertIn("888", str(caught.exception))

    def test_runs_with_different_shard_jobs_are_refused(self) -> None:
        runs = [
            _run("1", {1: {"a.py::t1": 1.0}, 2: {"b.py::t2": 2.0}}),
            _run("2", {n: {"a.py::t1": 1.0} for n in (1, 2, 3)}),
        ]

        with self.assertRaises(refresh.MissingShardError):
            refresh.merge_runs(runs)
