"""Tests for the repo-level ``scripts/install_pure_black.py`` (CAL-04 retro).

The script lives outside the Django project, so it is loaded by path. Only the
version lookup is tested; the pip call is a thin wrapper.
"""

import importlib.util
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
