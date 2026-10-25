#!/usr/bin/env python
"""Run the full pytest suite and print only what a reader needs.

A full run prints about 8,500 progress dots plus a warnings section, and a
worktree session refuses ``| tail``. This runs the suite in one plain command
and prints only the failures, the short test summary and the final count line.

Usage::

    python scripts/run_suite.py [--postgres] [extra pytest args]

``--postgres`` targets the throwaway ``lf-test-pg`` container from the
``CLAUDE.md`` *Database* section (start it first with ``docker start lf-test-pg``).
The exit status is pytest's.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_POSTGRES_URL = "postgres://laserforce:laserforce@localhost:5433/laserforce_test"
_PROGRESS = re.compile(r"^[.,sxXFE]+\s*(\[\s*\d+%\])?$")
_SECTION = re.compile(r"^=+ (.+?) =+$")


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


def main(argv: list[str]) -> int:
    env = dict(os.environ)
    if "--postgres" in argv:
        argv = [a for a in argv if a != "--postgres"]
        env["DATABASE_URL"] = _POSTGRES_URL
    cmd = [sys.executable, "-m", "pytest", "laserforce_simulator", "-q"]
    cmd += ["-p", "no:cacheprovider", "--tb=short", *argv]
    proc = subprocess.run(
        cmd,
        cwd=_REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    print(condense(proc.stdout + proc.stderr))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
