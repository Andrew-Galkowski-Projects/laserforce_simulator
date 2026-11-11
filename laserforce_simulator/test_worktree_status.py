"""Tests for the repo-level ``scripts/worktree_status.py`` read-only report.

CAL-03 retro: resuming after a restart needed each worktree's uncommitted files,
and the shell guard refuses ``git -C <worktree> status``. The script lives outside
the Django project, so it is loaded by path.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "worktree_status.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("worktree_status", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass looks its module up in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


worktree_status = _load_script()


class ParseWorktreesTests(TestCase):
    _PORCELAIN = [
        "worktree C:/repo",
        "HEAD 1111",
        "branch refs/heads/main",
        "",
        "worktree C:/repo/.claude/worktrees/agent-a1",
        "HEAD 2222",
        "branch refs/heads/cal-03c-visits",
        "locked",
        "",
        "worktree C:/repo/.claude/worktrees/agent-b2",
        "HEAD 3333",
        "detached",
    ]

    def test_every_worktree_with_its_head_and_branch(self) -> None:
        self.assertEqual(
            worktree_status.parse_worktrees(self._PORCELAIN),
            [
                worktree_status.Worktree("C:/repo", "1111", "main"),
                worktree_status.Worktree(
                    "C:/repo/.claude/worktrees/agent-a1", "2222", "cal-03c-visits"
                ),
                worktree_status.Worktree(
                    "C:/repo/.claude/worktrees/agent-b2", "3333", None
                ),
            ],
        )

    def test_an_empty_listing_has_no_worktrees(self) -> None:
        self.assertEqual(worktree_status.parse_worktrees([]), [])


class ReportTests(TestCase):
    _WT = worktree_status.Worktree("C:/repo/.claude/worktrees/agent-a1", "2222", "x")

    def test_a_dirty_worktree_lists_its_files(self) -> None:
        lines = worktree_status.report(
            self._WT,
            last_commit="2222abc CAL-03c: visits",
            ahead=2,
            status=[" M a.py", "?? b.py"],
        )
        self.assertEqual(
            lines,
            [
                "C:/repo/.claude/worktrees/agent-a1  [x]",
                "  last commit: 2222abc CAL-03c: visits",
                "  2 commit(s) not in origin/main",
                "  2 uncommitted change(s):",
                "     M a.py",
                "    ?? b.py",
            ],
        )

    def test_a_clean_detached_worktree_says_so(self) -> None:
        wt = worktree_status.Worktree("C:/w", "3333", None)
        lines = worktree_status.report(wt, last_commit="3333 x", ahead=0, status=[])
        self.assertEqual(
            lines,
            [
                "C:/w  [detached]",
                "  last commit: 3333 x",
                "  in origin/main",
                "  clean",
            ],
        )

    def test_a_long_status_is_cut_with_a_count(self) -> None:
        status = [f" M f{i}.py" for i in range(worktree_status.MAX_FILES + 3)]
        lines = worktree_status.report(self._WT, "c", 0, status)
        self.assertEqual(len(lines), 4 + worktree_status.MAX_FILES + 1)
        self.assertEqual(lines[-1], "    ... and 3 more")
