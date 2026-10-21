"""Tests for the repo-level ``scripts/merge_into_branch.py``.

The script lives outside the Django project, so it is loaded by path. Every
test builds a throwaway repo in a temp dir and drives git there (``cwd=``), so
it never touches this checkout.
"""

import importlib.util
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "merge_into_branch.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("merge_into_branch", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


merge_into_branch = _load_script()


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


class MergeIntoBranchTests(TestCase):
    """``target`` is never checked out: the repo sits on ``main`` throughout."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "T")
        _git(self.repo, "config", "commit.gpgsign", "false")
        self._commit("base.txt", "base")
        _git(self.repo, "branch", "target")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _commit(self, name: str, content: str) -> str:
        (self.repo / name).write_text(content)
        _git(self.repo, "add", name)
        _git(self.repo, "commit", "-q", "-m", name)
        return _git(self.repo, "rev-parse", "HEAD")

    def _branch_from(self, branch: str, start: str) -> None:
        _git(self.repo, "switch", "-q", "-c", branch, start)

    def _tip(self, branch: str) -> str:
        return _git(self.repo, "rev-parse", f"refs/heads/{branch}")

    def test_fast_forwards_when_target_is_behind(self) -> None:
        self._branch_from("ticket", "target")
        ticket_tip = self._commit("a.txt", "a")
        _git(self.repo, "switch", "-q", "main")

        result = merge_into_branch.merge_into(self.repo, "target", "ticket")

        self.assertEqual(result, ("fast-forward", ticket_tip))
        self.assertEqual(self._tip("target"), ticket_tip)

    def test_up_to_date_when_source_is_already_in_target(self) -> None:
        self._branch_from("ticket", "target")
        _git(self.repo, "switch", "-q", "main")
        before = self._tip("target")

        result = merge_into_branch.merge_into(self.repo, "target", "ticket")

        self.assertEqual(result, ("up-to-date", before))
        self.assertEqual(self._tip("target"), before)

    def test_merge_commit_when_branches_diverged(self) -> None:
        self._branch_from("ticket-a", "target")
        self._commit("a.txt", "a")
        self._branch_from("ticket-b", "target")
        b_tip = self._commit("b.txt", "b")
        _git(self.repo, "switch", "-q", "main")
        merge_into_branch.merge_into(self.repo, "target", "ticket-a")
        a_merged = self._tip("target")

        kind, sha = merge_into_branch.merge_into(self.repo, "target", "ticket-b")

        self.assertEqual(kind, "merge")
        self.assertEqual(self._tip("target"), sha)
        parents = _git(self.repo, "rev-list", "--parents", "-n", "1", sha).split()[1:]
        self.assertEqual(parents, [a_merged, b_tip])
        files = _git(self.repo, "ls-tree", "--name-only", sha).split()
        self.assertEqual(sorted(files), ["a.txt", "b.txt", "base.txt"])

    def test_conflict_raises_and_leaves_target_alone(self) -> None:
        self._branch_from("ticket-a", "target")
        self._commit("same.txt", "from a")
        self._branch_from("ticket-b", "target")
        self._commit("same.txt", "from b")
        _git(self.repo, "switch", "-q", "main")
        merge_into_branch.merge_into(self.repo, "target", "ticket-a")
        before = self._tip("target")

        with self.assertRaises(merge_into_branch.MergeError) as raised:
            merge_into_branch.merge_into(self.repo, "target", "ticket-b")

        self.assertIn("same.txt", str(raised.exception))
        self.assertEqual(self._tip("target"), before)

    def test_refuses_a_target_checked_out_in_a_worktree(self) -> None:
        self._branch_from("ticket", "target")
        self._commit("a.txt", "a")
        _git(self.repo, "switch", "-q", "target")
        before = self._tip("target")

        with self.assertRaises(merge_into_branch.MergeError) as raised:
            merge_into_branch.merge_into(self.repo, "target", "ticket")

        self.assertIn("checked out", str(raised.exception))
        self.assertEqual(self._tip("target"), before)
