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


class CopyStagedTests(TestCase):
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
        self.assertEqual(lines[-1], f'git worktree remove --force "{self.src}"')
