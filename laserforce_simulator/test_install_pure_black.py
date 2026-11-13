"""Tests for the repo-level ``scripts/install_pure_black.py`` (CAL-04 retro).

The script lives outside the Django project, so it is loaded by path. Only the
version lookup is tested; the pip call is a thin wrapper.
"""

import importlib.util
import subprocess
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "install_pure_black.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_pure_black", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install = _load_script()


class CiBlackVersionTests(TestCase):
    def test_reads_the_pin_from_the_pip_line(self) -> None:
        line = "python -m pip install ruff==0.17.1 black==26.10.1 actionlint-py==1.7"
        self.assertEqual(install.ci_black_version(line), "26.10.1")

    def test_the_committed_ci_file_has_a_pin(self) -> None:
        ci = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertRegex(install.ci_black_version(ci), r"^\d+\.\d+")

    def test_no_pin_is_an_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "black=="):
            install.ci_black_version("python -m pip install ruff==0.17.1")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class TargetDirTests(TestCase):
    """MECH-23 retro: a worktree has no ``.tools``, so black failed in each new one."""

    def test_a_worktree_installs_into_the_main_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main = Path(tmp, "main")
            main.mkdir()
            _git(main, "init", "-q")
            _git(
                main,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            )
            tree = Path(tmp, "tree")
            _git(main, "worktree", "add", "-q", str(tree))
            self.assertEqual(
                install.target_dir(tree).resolve(),
                (main / ".tools" / "black").resolve(),
            )
            self.assertEqual(
                install.target_dir(main).resolve(),
                (main / ".tools" / "black").resolve(),
            )
