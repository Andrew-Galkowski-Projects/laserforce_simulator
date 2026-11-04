"""Tests for the repo-level ``scripts/cosmetic_diff.py`` script.

The script lives outside the Django project, so it is loaded by path.
"""

import contextlib
import importlib.util
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "cosmetic_diff.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cosmetic_diff", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cosmetic_diff = _load_script()

_OLD = 'def f(x):\n    """Old words."""\n    # old comment\n    return x + 1\n'


class IsCosmeticTests(TestCase):
    def test_a_docstring_and_comment_rewrite_is_cosmetic(self) -> None:
        new = 'def f(x):\n    """New, longer\n    words."""\n    return x + 1  # new\n'
        self.assertTrue(cosmetic_diff.is_cosmetic("a.py", _OLD, new))

    def test_a_code_change_is_not_cosmetic(self) -> None:
        new = _OLD.replace("x + 1", "x + 2")
        self.assertFalse(cosmetic_diff.is_cosmetic("a.py", _OLD, new))

    def test_a_reformat_is_cosmetic(self) -> None:
        new = 'def f( x ):\n    """Old words."""\n    return (x\n            + 1)\n'
        self.assertTrue(cosmetic_diff.is_cosmetic("a.py", _OLD, new))

    def test_a_module_and_class_docstring_change_is_cosmetic(self) -> None:
        old = '"""Mod."""\n\n\nclass C:\n    """Old."""\n\n    x = 1\n'
        new = '"""Module."""\n\n\nclass C:\n    """New."""\n\n    x = 1\n'
        self.assertTrue(cosmetic_diff.is_cosmetic("a.py", old, new))

    def test_a_docstring_only_body_changed_to_code_is_not_cosmetic(self) -> None:
        old = 'def f():\n    """Doc."""\n'
        new = 'def f():\n    """Doc."""\n    return 1\n'
        self.assertFalse(cosmetic_diff.is_cosmetic("a.py", old, new))

    def test_a_markdown_change_is_cosmetic(self) -> None:
        self.assertTrue(cosmetic_diff.is_cosmetic("docs/x.md", "a\n", "b\n"))

    def test_an_added_or_deleted_python_file_is_not_cosmetic(self) -> None:
        self.assertFalse(cosmetic_diff.is_cosmetic("a.py", None, _OLD))
        self.assertFalse(cosmetic_diff.is_cosmetic("a.py", _OLD, None))

    def test_any_other_file_is_not_cosmetic(self) -> None:
        self.assertFalse(cosmetic_diff.is_cosmetic("x.html", "<p>a</p>", "<p>b</p>"))
        self.assertFalse(cosmetic_diff.is_cosmetic("x.yml", "a: 1\n", "a: 2\n"))

    def test_python_that_does_not_parse_is_not_cosmetic(self) -> None:
        self.assertFalse(cosmetic_diff.is_cosmetic("a.py", _OLD, "def f(:\n"))


class NonCosmeticPathsTests(TestCase):
    """Real git in a throwaway repo: tracked edits and untracked files both count."""

    def _git(self, *args: str) -> None:
        subprocess.run(["git", *args], check=True, capture_output=True)

    def test_lists_only_the_paths_that_change_behaviour(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            self._git("init", "-q")
            self._git("config", "user.email", "t@example.com")
            self._git("config", "user.name", "t")
            Path("a.py").write_text(_OLD, encoding="utf-8")
            Path("b.py").write_text(_OLD, encoding="utf-8")
            Path("notes.md").write_text("old\n", encoding="utf-8")
            self._git("add", "a.py", "b.py", "notes.md")
            self._git("commit", "-q", "-m", "base")

            Path("a.py").write_text(_OLD.replace("Old words", "New"), encoding="utf-8")
            Path("notes.md").write_text("new\n", encoding="utf-8")
            self.assertEqual(cosmetic_diff.non_cosmetic_paths("HEAD"), [])

            Path("b.py").write_text(_OLD.replace("x + 1", "x"), encoding="utf-8")
            Path("c.py").write_text(_OLD, encoding="utf-8")
            self.assertEqual(cosmetic_diff.non_cosmetic_paths("HEAD"), ["b.py", "c.py"])
