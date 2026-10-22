"""Tests for the repo-level ``scripts/watch_pr_ci.py``.

The script lives outside the Django project, so it is loaded by path. Only the
pure decision functions are tested; the ``gh`` calls are thin wrappers.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "watch_pr_ci.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("watch_pr_ci", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch_pr_ci = _load_script()

_RUNS = "https://github.com/o/r/actions/runs"


def _check(name: str, bucket: str, run: str = "111") -> dict[str, str]:
    return {"name": name, "bucket": bucket, "link": f"{_RUNS}/{run}/job/9"}


class RunIdsTests(TestCase):
    def test_distinct_run_ids_in_first_seen_order(self) -> None:
        checks = [
            _check("test", "pass", "111"),
            _check("screenshots", "pass", "222"),
            _check("frontend", "pass", "111"),
        ]
        self.assertEqual(watch_pr_ci.run_ids(checks), ["111", "222"])

    def test_check_without_a_run_link_is_skipped(self) -> None:
        checks = [{"name": "external", "bucket": "pass", "link": "https://x.test/"}]
        self.assertEqual(watch_pr_ci.run_ids(checks), [])


class VerdictTests(TestCase):
    def test_no_checks_yet_is_pending(self) -> None:
        self.assertEqual(watch_pr_ci.verdict([], {}), "pending")

    def test_pending_check_is_pending(self) -> None:
        checks = [_check("test", "pending"), _check("frontend", "pass")]
        self.assertEqual(watch_pr_ci.verdict(checks, {"111": "in_progress"}), "pending")

    def test_all_pass_but_run_still_open_is_pending(self) -> None:
        # A later job (docker waits on test) may not be listed yet.
        checks = [_check("test", "pass")]
        self.assertEqual(watch_pr_ci.verdict(checks, {"111": "in_progress"}), "pending")

    def test_all_pass_and_runs_completed_is_pass(self) -> None:
        checks = [_check("test", "pass"), _check("docs", "skipping")]
        self.assertEqual(watch_pr_ci.verdict(checks, {"111": "completed"}), "pass")

    def test_any_failure_once_settled_is_fail(self) -> None:
        checks = [_check("test", "fail"), _check("frontend", "pass")]
        self.assertEqual(watch_pr_ci.verdict(checks, {"111": "completed"}), "fail")

    def test_cancelled_check_is_fail(self) -> None:
        checks = [_check("test", "cancel")]
        self.assertEqual(watch_pr_ci.verdict(checks, {"111": "completed"}), "fail")
