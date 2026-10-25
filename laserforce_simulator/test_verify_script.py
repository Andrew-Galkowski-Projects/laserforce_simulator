"""Tests for the ``/verify`` skill's ``verify.py`` change-set selection.

The script lives outside the Django project, so it is loaded by path, with its
own directory on ``sys.path`` for its sibling ``changed_files`` import.
"""

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPTS = (
    Path(__file__).resolve().parent.parent / ".claude" / "skills" / "verify" / "scripts"
)


def _load_script() -> ModuleType:
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "verify_script", _SCRIPTS / "verify.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = _load_script()
changed_files = verify.cf


def _git(repo: str, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


class CollectChangedUntrackedTests(TestCase):
    def test_working_mode_includes_a_new_untracked_file(self) -> None:
        # CI-01 retro: a brand-new file was never linted, because `git diff`
        # does not list untracked files.
        with tempfile.TemporaryDirectory() as repo:
            _git(repo, "init", "-q", "-b", "main")
            _git(repo, "config", "user.email", "t@example.com")
            _git(repo, "config", "user.name", "t")
            Path(repo, "kept.py").write_text("x = 1\n", encoding="utf-8")
            _git(repo, "add", "kept.py")
            _git(repo, "commit", "-q", "-m", "init")
            Path(repo, "new_file.py").write_text("y = 2\n", encoding="utf-8")

            changed = changed_files.collect_changed(repo, "main", "working")

        self.assertEqual(changed, ["new_file.py"])


def _changes(*paths: str) -> dict:
    return {"changed": list(paths), "nothing_changed": not paths}


class SelectChangesTests(TestCase):
    def test_uncommitted_work_is_checked_against_head(self) -> None:
        calls: list[str] = []

        def compute(base: str, mode: str) -> dict:
            calls.append(mode)
            return _changes("a.py") if mode == "working" else _changes("b.py")

        mode, data = verify.select_changes(compute)
        self.assertEqual(mode, "working")
        self.assertEqual(data["changed"], ["a.py"])
        self.assertEqual(calls, ["working"])

    def test_clean_tree_falls_back_to_the_branch_vs_main(self) -> None:
        # CI-01 retro: once everything was committed, /verify skipped every
        # check and still printed OVERALL PASS.
        def compute(base: str, mode: str) -> dict:
            self.assertEqual(base, "main")
            return _changes() if mode == "working" else _changes("b.py")

        mode, data = verify.select_changes(compute)
        self.assertEqual(mode, "branch")
        self.assertEqual(data["changed"], ["b.py"])


def _scope(targets: list[str], full_suite: bool) -> dict:
    return {
        "nothing_changed": False,
        "pytest_targets": targets,
        "pytest_full_suite": full_suite,
    }


class PytestScopeTests(TestCase):
    def test_no_python_change_skips_pytest(self) -> None:
        # ANG-07 retro: a Docker / docs-only branch ran a bare `pytest -q`,
        # which is the full suite, because empty targets were never told
        # apart from the no-mapping fallback.
        self.assertIsNone(verify.pytest_scope(_scope([], False)))

    def test_unmapped_code_runs_the_full_suite(self) -> None:
        self.assertEqual(verify.pytest_scope(_scope([], True)), [])

    def test_mapped_code_runs_its_targets(self) -> None:
        self.assertEqual(
            verify.pytest_scope(_scope(["teams/tests/"], False)), ["teams/tests/"]
        )


class BucketTests(TestCase):
    def test_a_django_template_is_a_template(self) -> None:
        buckets = changed_files.bucket(
            ["laserforce_simulator/templates/matches/x.html"]
        )
        self.assertEqual(
            buckets["templates"], ["laserforce_simulator/templates/matches/x.html"]
        )

    def test_an_angular_template_is_other(self) -> None:
        # An Angular component's .html is no Django template: it must not
        # trigger the full pytest suite on a frontend-only branch (ANG-06g retro).
        path = "frontend/src/app/matches/round-charts/round-charts.html"
        buckets = changed_files.bucket([path])
        self.assertEqual(buckets["templates"], [])
        self.assertEqual(buckets["other"], [path])
