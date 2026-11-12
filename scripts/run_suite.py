#!/usr/bin/env python
"""Run the full pytest suite and print only what a reader needs.

A full run prints about 8,500 progress dots plus a warnings section, and a
worktree session refuses ``| tail``. This runs the suite in one plain command
and prints only the failures, the short test summary and the final count line.

Usage::

    python scripts/run_suite.py [--postgres] [--rerun-failures] [extra pytest args]

``--postgres`` targets the throwaway ``lf-test-pg`` container from the
``CLAUDE.md`` *Database* section (start it first with ``docker start lf-test-pg``).
``--rerun-failures`` re-runs each failed test by itself (``-n0``) and lists it
as ``passes alone (likely flaky)`` or ``fails alone``.
The exit status is the full run's pytest status.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

_REPO = Path(__file__).resolve().parent.parent
_POSTGRES_URL = "postgres://laserforce:laserforce@localhost:5433/laserforce_test"
_DJANGO = _REPO / "laserforce_simulator"
_PROGRESS = re.compile(r"^[.,sxXFE]+\s*(\[\s*\d+%\])?$")
_SECTION = re.compile(r"^=+ (.+?) =+$")
_FAILED_ROW = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def condense(output: str) -> str:
    """Drop progress lines, xdist start-up and the warnings section."""
    kept: list[str] = []
    in_warnings = False
    for line in output.splitlines():
        section = _SECTION.match(line)
        if section:
            in_warnings = section.group(1) == "warnings summary"
            if in_warnings:
                continue
        elif in_warnings:
            if line.startswith("-- Docs: "):
                in_warnings = False
            continue
        if not line.strip() or _PROGRESS.match(line) or line == "bringing up nodes...":
            continue
        kept.append(line)
    return "\n".join(kept)


def failed_ids(output: str) -> list[str]:
    """The node ids on the short summary's ``FAILED`` and ``ERROR`` rows."""
    return [match.group(1) for match in _FAILED_ROW.finditer(output)]


def rerun_report(ids: list[str], run_alone: Callable[[str], int]) -> list[str]:
    """Re-run each failed test by itself and say whether it passes alone.

    ``run_alone`` runs one node id and returns pytest's exit status.
    """
    if not ids:
        return []
    lines = ["RERUN ALONE (-n0)"]
    for node in ids:
        verdict = (
            "passes alone (likely flaky)" if run_alone(node) == 0 else "fails alone"
        )
        lines.append(f"{verdict}: {node}")
    return lines


def _pytest(
    cmd: list[str], cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def main(argv: list[str]) -> int:
    env = dict(os.environ)
    if "--postgres" in argv:
        argv = [a for a in argv if a != "--postgres"]
        env["DATABASE_URL"] = _POSTGRES_URL
    rerun = "--rerun-failures" in argv
    argv = [a for a in argv if a != "--rerun-failures"]
    base = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    proc = _pytest([*base, "laserforce_simulator", "--tb=short", *argv], _REPO, env)
    output = proc.stdout + proc.stderr
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    print(condense(output))
    if rerun:
        # Node ids are relative to pytest's rootdir, the folder holding pytest.ini.
        def run_alone(node: str) -> int:
            return _pytest([*base, "-n0", "--tb=no", node], _DJANGO, env).returncode

        for line in rerun_report(failed_ids(output), run_alone):
            print(line)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
