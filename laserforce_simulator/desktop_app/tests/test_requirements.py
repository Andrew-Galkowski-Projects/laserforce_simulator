"""DESK-02: the runtime / dev split of the requirements.

The Desktop build and the Docker image install ``requirements-runtime.txt``;
``requirements.txt`` (dev, CI) installs that file plus the test tooling.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
RUNTIME = PROJECT / "requirements-runtime.txt"
DESKTOP = PROJECT / "requirements-desktop.txt"
DEV = PROJECT / "requirements.txt"

DEV_ONLY = re.compile(r"^(pytest(-[\w-]+)?|coverage|selenium|trio(-[\w-]+)?)\b", re.I)
PYWEBVIEW = re.compile(r"^pywebview\b", re.I)


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]


def test_requirements_txt_installs_the_runtime_file() -> None:
    # DESK-03: dev -> desktop -> runtime, so dev and CI get pywebview too.
    assert "-r requirements-desktop.txt" in _lines(DEV)
    assert "-r requirements-runtime.txt" not in _lines(DEV)
    assert "-r requirements-runtime.txt" in _lines(DESKTOP)


def test_the_desktop_file_pins_pywebview_exactly() -> None:
    assert [line for line in _lines(DESKTOP) if PYWEBVIEW.match(line)] == [
        "pywebview==6.2.1"
    ]


def test_the_runtime_file_names_no_pywebview() -> None:
    # The Hosted image installs only the runtime file.
    assert [line for line in _lines(RUNTIME) if PYWEBVIEW.match(line)] == []


def test_the_desktop_file_names_no_test_tooling() -> None:
    assert [line for line in _lines(DESKTOP) if DEV_ONLY.match(line)] == []


def test_the_runtime_file_names_no_test_tooling() -> None:
    assert [line for line in _lines(RUNTIME) if DEV_ONLY.match(line)] == []
