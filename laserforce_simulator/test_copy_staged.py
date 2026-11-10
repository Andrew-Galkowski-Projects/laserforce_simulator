"""Tests for the repo-level ``scripts/copy_staged.py`` script.

The script lives outside the Django project, so it is loaded by path.
"""

import contextlib
import importlib.util
import io
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "copy_staged.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("copy_staged", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


copy_staged = _load_script()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


class _TwoWorktrees(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.src = Path(self._tmp.name, "src")
        self.dst = Path(self._tmp.name, "dst")
        self.src.mkdir()
        self.dst.mkdir()
        _git(self.src, "init", "-q")
        _git(self.src, "config", "user.email", "t@example.com")
        _git(self.src, "config", "user.name", "t")
        Path(self.src, "kept.txt").write_text("old\n")
        Path(self.src, "gone.txt").write_text("bye\n")
        _git(self.src, "add", "kept.txt", "gone.txt")
        _git(self.src, "commit", "-q", "-m", "base")
        Path(self.dst, "gone.txt").write_text("bye\n")


class CopyStagedTests(_TwoWorktrees):
    def test_copies_staged_files_and_deletes_staged_deletions(self) -> None:
        Path(self.src, "kept.txt").write_text("new\n")
        Path(self.src, "sub").mkdir()
        Path(self.src, "sub", "added.txt").write_text("added\n")
        Path(self.src, "unstaged.txt").write_text("skip\n")
        _git(self.src, "add", "kept.txt", "sub/added.txt")
        _git(self.src, "rm", "-q", "gone.txt")

        changed = copy_staged.copy_staged(self.src, self.dst)

        self.assertEqual(sorted(changed), ["gone.txt", "kept.txt", "sub/added.txt"])
        self.assertEqual(Path(self.dst, "kept.txt").read_text(), "new\n")
        self.assertEqual(Path(self.dst, "sub", "added.txt").read_text(), "added\n")
        self.assertFalse(Path(self.dst, "gone.txt").exists())
        self.assertFalse(Path(self.dst, "unstaged.txt").exists())

    def test_nothing_staged_copies_nothing(self) -> None:
        self.assertEqual(copy_staged.copy_staged(self.src, self.dst), [])

    def test_main_ends_with_the_command_that_removes_the_source(self) -> None:
        Path(self.src, "kept.txt").write_text("new\n")
        _git(self.src, "add", "kept.txt")
        out = io.StringIO()

        with contextlib.chdir(self.dst), contextlib.redirect_stdout(out):
            copy_staged.main([str(self.src)])

        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "kept.txt")
        self.assertEqual(
            lines[-1],
            f'git -c core.longpaths=true worktree remove --force "{self.src}"',
        )


class CopyWorktreeTests(_TwoWorktrees):
    """``--worktree``: a session that ended before its commit left unstaged work
    (CAL-02b). The destination may have moved since, so a path changed on both
    sides is reported, never overwritten."""

    def setUp(self) -> None:
        super().setUp()
        Path(self.dst, "kept.txt").write_text("old\n")

    def _main(self, *extra: str) -> tuple[list[str], int]:
        out = io.StringIO()
        code = 0
        with contextlib.chdir(self.dst), contextlib.redirect_stdout(out):
            try:
                copy_staged.main([str(self.src), "--worktree", *extra])
            except SystemExit as stop:
                code = int(stop.code or 0)
        return out.getvalue().splitlines(), code

    def test_copies_modified_untracked_and_staged_and_deletes_deleted(self) -> None:
        Path(self.src, "kept.txt").write_text("new\n")
        Path(self.src, "sub").mkdir()
        Path(self.src, "sub", "untracked.txt").write_text("added\n")
        Path(self.src, "gone.txt").unlink()

        changed, skipped = copy_staged.copy_worktree(self.src, self.dst)

        self.assertEqual(sorted(changed), ["gone.txt", "kept.txt", "sub/untracked.txt"])
        self.assertEqual(skipped, [])
        self.assertEqual(Path(self.dst, "kept.txt").read_text(), "new\n")
        self.assertEqual(Path(self.dst, "sub", "untracked.txt").read_text(), "added\n")
        self.assertFalse(Path(self.dst, "gone.txt").exists())

    def test_an_ignored_file_stays_behind(self) -> None:
        Path(self.src, ".gitignore").write_text(".env\n")
        Path(self.src, ".env").write_text("SECRET_KEY=x\n")

        changed, _ = copy_staged.copy_worktree(self.src, self.dst)

        self.assertEqual(changed, [".gitignore"])
        self.assertFalse(Path(self.dst, ".env").exists())

    def test_a_path_changed_on_both_sides_is_skipped_not_overwritten(self) -> None:
        Path(self.src, "kept.txt").write_text("source\n")
        Path(self.dst, "kept.txt").write_text("moved on\n")

        changed, skipped = copy_staged.copy_worktree(self.src, self.dst)

        self.assertEqual(changed, [])
        self.assertEqual(skipped, ["kept.txt"])
        self.assertEqual(Path(self.dst, "kept.txt").read_text(), "moved on\n")

    def test_a_new_file_that_exists_differently_here_is_skipped(self) -> None:
        Path(self.src, "new.txt").write_text("source\n")
        Path(self.dst, "new.txt").write_text("other\n")

        changed, skipped = copy_staged.copy_worktree(self.src, self.dst)

        self.assertEqual((changed, skipped), ([], ["new.txt"]))
        self.assertEqual(Path(self.dst, "new.txt").read_text(), "other\n")

    def test_line_endings_alone_are_not_a_change_here(self) -> None:
        Path(self.src, "kept.txt").write_text("new\n")
        Path(self.dst, "kept.txt").write_bytes(b"old\r\n")

        changed, skipped = copy_staged.copy_worktree(self.src, self.dst)

        self.assertEqual((changed, skipped), (["kept.txt"], []))

    def test_main_ends_with_the_remove_command_when_nothing_was_skipped(self) -> None:
        Path(self.src, "kept.txt").write_text("new\n")

        lines, code = self._main()

        self.assertEqual(code, 0)
        self.assertEqual(lines[0], "kept.txt")
        self.assertEqual(
            lines[-1],
            f'git -c core.longpaths=true worktree remove --force "{self.src}"',
        )

    def test_main_keeps_the_source_and_exits_1_when_a_path_was_skipped(self) -> None:
        Path(self.src, "kept.txt").write_text("source\n")
        Path(self.dst, "kept.txt").write_text("moved on\n")

        lines, code = self._main()

        self.assertEqual(code, 1)
        self.assertEqual(lines, ["CHANGED ON BOTH SIDES, not copied: kept.txt"])
