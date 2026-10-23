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
