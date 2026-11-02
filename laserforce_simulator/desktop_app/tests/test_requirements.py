"""DESK-02: the runtime / dev split of the requirements.

The Desktop build and the Docker image install ``requirements-runtime.txt``;
``requirements.txt`` (dev, CI) installs that file plus the test tooling.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
RUNTIME = PROJECT / "requirements-runtime.txt"
DEV = PROJECT / "requirements.txt"

DEV_ONLY = re.compile(r"^(pytest(-[\w-]+)?|coverage|selenium|trio(-[\w-]+)?)\b", re.I)


def _lines(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]


def test_requirements_txt_installs_the_runtime_file() -> None:
    assert "-r requirements-runtime.txt" in _lines(DEV)


def test_the_runtime_file_names_no_test_tooling() -> None:
    assert [line for line in _lines(RUNTIME) if DEV_ONLY.match(line)] == []
