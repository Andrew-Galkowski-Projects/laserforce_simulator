#!/usr/bin/env python
"""List ``.test_durations`` entries that name no collected test.

pytest-split ignores an unknown name, so nothing fails when a PR deletes or
renames tests, and the file drifts until a refresh (MECH-23 deleted the
hiding-miss tests and left them listed). ``/verify`` runs this as its
``durations`` row, a WARN, when a branch changes a test module. The WARN is
information: a refresh waits for a shard balance warning (``REFRESH_HINT``).

The ids come from ``pytest --collect-only``, so tests a class inherits from a
mixin in another module count, as do parametrised ids.

Usage::

    python scripts/check_test_durations.py

Prints one stale id per line, then ``N stale``; exit 0 either way.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
DURATIONS_FILE = _REPO / "laserforce_simulator" / ".test_durations"
# Stale ids only cost the shards some balance, so the refresh waits for a
# `shard balance:` warning (docs/agents/ci.md). MECH-24 refreshed on this WARN
# alone, after the PR's last push, and the commit missed the squash merge.
REFRESH_HINT = (
    "harmless until watch_pr_ci.py prints a shard balance warning on two runs; then "
    "python scripts/refresh_test_durations.py <run-id> [<run-id> ...] (docs/agents/ci.md)"
)


def parse_collected(output: str) -> set[str]:
    """The test ids in ``pytest --collect-only -q`` output (its first line may carry a BOM)."""
    bom = chr(0xFEFF)
    return {line.strip().lstrip(bom) for line in output.splitlines() if "::" in line}


def stale_entries(durations: dict[str, float], collected: set[str]) -> list[str]:
    """Each id in ``durations`` that pytest no longer collects, sorted."""
    return sorted(set(durations) - collected)


def collect_ids() -> set[str]:
    """Every test id the suite collects, spelled as ``.test_durations`` spells it."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "laserforce_simulator/pytest.ini",
            "--rootdir",
            "laserforce_simulator",
            "--collect-only",
            "-q",
            "-n0",
            "-p",
            "no:cacheprovider",
            "laserforce_simulator",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pytest --collect-only failed:\n{proc.stdout[-2000:]}")
    return parse_collected(proc.stdout)


def find_stale() -> list[str]:
    durations = json.loads(DURATIONS_FILE.read_text(encoding="utf-8"))
    return stale_entries(durations, collect_ids())


def main() -> int:
    stale = find_stale()
    for test_id in stale:
        print(test_id)
    print(f"{len(stale)} stale")
    if stale:
        print(f"refresh: {REFRESH_HINT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
