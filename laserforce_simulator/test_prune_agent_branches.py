"""Tests for the repo-level ``scripts/prune_agent_branches.py`` cleanup script.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prune_agent_branches.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prune_agent_branches", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prune_agent_branches = _load_script()


class SelectTests(TestCase):
    def test_deletes_merged_agent_branches_only(self) -> None:
        delete, keep = prune_agent_branches.select(
            branches=["worktree-agent-a1", "worktree-agent-b2", "feature-x"],
            merged={"worktree-agent-a1", "feature-x"},
            checked_out=set(),
        )
        self.assertEqual(delete, ["worktree-agent-a1"])
        self.assertEqual(keep, ["worktree-agent-b2"])

    def test_keeps_a_branch_checked_out_in_a_worktree(self) -> None:
        delete, keep = prune_agent_branches.select(
            branches=["worktree-agent-a1"],
            merged={"worktree-agent-a1"},
            checked_out={"worktree-agent-a1"},
        )
        self.assertEqual(delete, [])
        self.assertEqual(keep, ["worktree-agent-a1"])

    def test_ignores_non_agent_branches_even_when_merged(self) -> None:
        delete, keep = prune_agent_branches.select(
            branches=["main", "worktree-ang-06c-x"],
            merged={"main", "worktree-ang-06c-x"},
            checked_out=set(),
        )
        self.assertEqual((delete, keep), ([], []))


class AgentWorktreeTests(TestCase):
    """PERF-01 retro: fix-pass worktrees outlived their copy; list every one."""

    _PORCELAIN = [
        "worktree C:/repo",
        "HEAD 1111",
        "branch refs/heads/main",
        "worktree C:/repo/.claude/worktrees/agent-a1",
        "HEAD 2222",
        "branch refs/heads/worktree-agent-a1",
        "worktree C:/repo/.claude/worktrees/feature-x",
        "HEAD 3333",
        "branch refs/heads/feature-x",
        "worktree C:/repo/.claude/worktrees/agent-b2",
        "HEAD 4444",
        "detached",
    ]

    def test_lists_only_agent_worktrees_with_their_head(self) -> None:
        self.assertEqual(
            prune_agent_branches.agent_worktrees(self._PORCELAIN),
            [
                ("C:/repo/.claude/worktrees/agent-a1", "2222"),
                ("C:/repo/.claude/worktrees/agent-b2", "4444"),
            ],
        )

    def test_no_agent_worktrees_lists_nothing(self) -> None:
        self.assertEqual(prune_agent_branches.agent_worktrees(self._PORCELAIN[:3]), [])

    def test_describes_a_merged_clean_worktree(self) -> None:
        self.assertEqual(
            prune_agent_branches.describe_worktree("C:/w/agent-a1", True, 0),
            "worktree C:/w/agent-a1  head in origin/main, clean",
        )

    def test_describes_a_dirty_unmerged_worktree(self) -> None:
        self.assertEqual(
            prune_agent_branches.describe_worktree("C:/w/agent-b2", False, 3),
            "worktree C:/w/agent-b2  head NOT in origin/main, 3 uncommitted change(s)",
        )
