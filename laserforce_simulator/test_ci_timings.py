"""Tests for the repo-level ``scripts/ci_timings.py``.

The script lives outside the Django project, so it is loaded by path. Only the
pure parsing functions are tested; the ``gh`` calls are thin wrappers.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci_timings.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_timings", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_timings = _load_script()


def _log(*messages: str, step: str = "Run tests") -> str:
    """``gh run view --log`` lines: job, step, timestamp and message, tab-separated."""
    return "\n".join(
        f"test (1)\t{step}\t2026-10-24T16:33:13.8555422Z {m}" for m in messages
    )


class RunTests(TestCase):
    def test_output_is_utf8_whatever_the_console_codepage(self) -> None:
        # CI logs carry UTF-8 (and stray non-UTF-8) bytes; the Windows default
        # cp1252 decode crashed on byte 0x81 in run 40120433549.
        child = "import sys; sys.stdout.buffer.write(b'\\xe2\\x9c\\x93 ok \\x81')"

        out = ci_timings._run([sys.executable, "-c", child])

        self.assertTrue(out.startswith("✓ ok "))


class ElapsedTests(TestCase):
    def test_minutes_and_seconds(self) -> None:
        self.assertEqual(
            ci_timings.elapsed("2026-10-24T16:28:54Z", "2026-10-24T16:33:33Z"),
            "4m39s",
        )

    def test_under_a_minute(self) -> None:
        self.assertEqual(
            ci_timings.elapsed("2026-10-24T16:28:54Z", "2026-10-24T16:29:31Z"), "37s"
        )

    def test_missing_end_is_unfinished(self) -> None:
        self.assertEqual(ci_timings.elapsed("2026-10-24T16:28:54Z", ""), "unfinished")


class PytestSummaryTests(TestCase):
    def test_last_summary_line_without_the_rule(self) -> None:
        log = _log(
            "============ slowest 25 durations ============",
            "= 1396 passed, 1 xfailed, 1347 warnings, 351 subtests passed in 188.37s (0:03:08) =",
        )
        self.assertEqual(
            ci_timings.pytest_summary(log),
            "1396 passed, 1 xfailed, 1347 warnings, 351 subtests passed in 188.37s (0:03:08)",
        )

    def test_failed_run_summary(self) -> None:
        log = _log("==== 1 failed, 4387 passed, 2 skipped in 114.87s (0:01:54) ====")
        self.assertEqual(
            ci_timings.pytest_summary(log),
            "1 failed, 4387 passed, 2 skipped in 114.87s (0:01:54)",
        )

    def test_no_summary_is_none(self) -> None:
        self.assertIsNone(ci_timings.pytest_summary(_log("collecting ...")))


class CoverageTotalTests(TestCase):
    def test_bare_number_after_the_xml_report(self) -> None:
        log = _log(
            "Combined data file .coverage.shard4",
            "Wrote XML report to coverage.xml",
            "99",
            "##[group]Run codecov/codecov-action@v5",
            step="Combine coverage",
        )
        self.assertEqual(ci_timings.coverage_total(log), "99")

    def test_codecov_log_lines_are_not_the_total(self) -> None:
        # The Codecov uploader also logs "Wrote XML report"; only a bare
        # number line is the total.
        log = _log(
            "info - 2026-10-24 16:33:26,473 -- Wrote XML report to coverage.xml",
            "info - 2026-10-24 16:33:26,504 -- Found 1 coverage files to report",
        )
        self.assertIsNone(ci_timings.coverage_total(log))


class ShardDriftTests(TestCase):
    """PERF-06 retro: predicted (committed file) vs measured (CI artifact) per shard
    found PERF-05's stale shard 1 (523 s predicted, 90 s measured) in one read."""

    _PREDICTED = {
        "accounts/tests/test_a.py::T::one": 80.0,
        "accounts/tests/test_a.py::T::two": 20.0,
        "accounts/tests/test_b.py::T::one": 5.0,
        "matches/tests/test_c.py::T::one": 50.0,
    }

    def test_one_line_per_shard_with_predicted_and_measured(self) -> None:
        shards = {
            2: {"matches/tests/test_c.py::T::one": 55.0},
            1: {
                "accounts/tests/test_a.py::T::one": 2.0,
                "accounts/tests/test_a.py::T::two": 1.0,
                "accounts/tests/test_b.py::T::one": 5.0,
            },
        }
        lines = ci_timings.shard_drift(self._PREDICTED, shards, top=1)
        self.assertEqual(
            [line for line in lines if line.startswith("shard")],
            [
                "shard 1: 3 tests, predicted 105s, measured 8s (0.08x)",
                "shard 2: 1 tests, predicted 50s, measured 55s (1.10x)",
            ],
        )

    def test_worst_drifting_file_listed_under_its_shard(self) -> None:
        shards = {
            1: {
                "accounts/tests/test_a.py::T::one": 2.0,
                "accounts/tests/test_a.py::T::two": 1.0,
                "accounts/tests/test_b.py::T::one": 5.0,
            }
        }
        lines = ci_timings.shard_drift(self._PREDICTED, shards, top=1)
        self.assertEqual(
            lines[1].split(),
            ["pred", "100.0", "meas", "3.0", "accounts/tests/test_a.py"],
        )
        self.assertEqual(len(lines), 2)

    def test_test_missing_from_the_file_counts_as_zero_predicted(self) -> None:
        shards = {1: {"core/test_new.py::test_x": 4.0}}
        lines = ci_timings.shard_drift({}, shards, top=1)
        self.assertEqual(lines[0], "shard 1: 1 tests, predicted 0s, measured 4s (new)")
