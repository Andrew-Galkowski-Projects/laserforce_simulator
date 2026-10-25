#!/usr/bin/env python
"""Rewrite ``laserforce_simulator/.test_durations`` from CI-measured timings.

Every CI ``test`` shard stores its own tests' durations as the
``durations-shard-<n>`` artifact (PERF-04). This downloads them from one or
more runs and gives each test its median across the runs that saw it, so one
noisy runner doesn't skew the split. Commit the result in a normal PR.

Usage::

    python scripts/refresh_test_durations.py <run-id> [<run-id> ...]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from statistics import median
from typing import NamedTuple

DURATIONS_FILE = Path(__file__).resolve().parent.parent / (
    "laserforce_simulator/.test_durations"
)
_SHARD_JOB_RE = re.compile(r"^test \((\d+)\)$")
_ARTIFACT_RE = re.compile(r"^durations-shard-(\d+)$")


class RunDurations(NamedTuple):
    run_id: str
    shard_jobs: list[int]
    shards: dict[int, dict[str, float]]


class ShardMismatchError(ValueError):
    """A run lacks a shard's durations, or the runs split the suite differently."""


def _check_shards(runs: list[RunDurations]) -> None:
    # The shard count comes from the run's ``test (n)`` jobs, not its
    # artifacts: a run whose last shard failed would otherwise look like a
    # complete run with one shard fewer.
    for run in runs:
        if not run.shard_jobs:
            raise ShardMismatchError(f"run {run.run_id} has no test (n) shard jobs")
        missing = sorted(set(run.shard_jobs) - set(run.shards))
        if missing:
            raise ShardMismatchError(
                f"run {run.run_id} has no durations for shard(s) {missing}"
            )
    shard_sets = {tuple(sorted(run.shard_jobs)) for run in runs}
    if len(shard_sets) > 1:
        raise ShardMismatchError(
            f"runs have different shard jobs: {sorted(shard_sets)}"
        )


def merge_runs(runs: list[RunDurations]) -> dict[str, float]:
    """Per-test median across the runs that saw it; only tests seen."""
    _check_shards(runs)
    seen: dict[str, list[float]] = {}
    for run in runs:
        for durations in run.shards.values():
            for test, duration in durations.items():
                seen.setdefault(test, []).append(duration)
    return {test: median(values) for test, values in seen.items()}


class GhError(RuntimeError):
    """A ``gh`` call failed (bad run ID, not logged in, no network)."""


def _gh(*args: str, check: bool = True) -> str:
    """``gh``'s stdout, decoded as UTF-8 (the Windows default codepage is not).

    Raises ``GhError`` with ``gh``'s stderr on a non-zero exit when ``check``.
    """
    result = subprocess.run(["gh", *args], capture_output=True)
    if check and result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GhError(f"gh {' '.join(args)} failed: {stderr}")
    return result.stdout.decode("utf-8", errors="replace")


def _shard_jobs(run_id: str) -> list[int]:
    jobs = json.loads(_gh("run", "view", run_id, "--json", "jobs"))["jobs"]
    return sorted(
        int(match.group(1))
        for match in (_SHARD_JOB_RE.match(job["name"]) for job in jobs)
        if match
    )


def download_shards(run_id: str) -> dict[int, dict[str, float]]:
    """Each ``durations-shard-<n>`` artifact of the run that exists; a shard
    that failed uploaded none, which ``merge_runs`` reports. Not checked: ``gh``
    exits non-zero when no artifact matches, and ``_shard_jobs`` has already
    proved the run ID and the login."""
    with tempfile.TemporaryDirectory() as tmp:
        _gh(
            "run",
            "download",
            run_id,
            "--pattern",
            "durations-shard-*",
            "--dir",
            tmp,
            check=False,
        )
        shards = {}
        for folder in Path(tmp).iterdir():
            match = _ARTIFACT_RE.match(folder.name)
            if match:
                text = (folder / ".test_durations").read_text(encoding="utf-8")
                shards[int(match.group(1))] = json.loads(text)
        return shards


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_ids", nargs="+", metavar="run_id")
    args = parser.parse_args(argv)

    try:
        runs = [
            RunDurations(run_id, _shard_jobs(run_id), download_shards(run_id))
            for run_id in args.run_ids
        ]
        merged = merge_runs(runs)
    except (GhError, ShardMismatchError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    # pytest-split's own format, so a refresh diff stays reviewable.
    with open(DURATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, sort_keys=True, indent=4)
    print(f"wrote {len(merged)} tests to {DURATIONS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
