#!/usr/bin/env python
"""Print one CI run's timings: each job, each test shard's pytest summary,
the combined coverage total, and the time from run start to ``test-result``.

Measuring a CI-time change (PERF-02) took 6-10 ``gh`` calls per run, and
``$(gh ...)`` lookups are refused in a worktree session, so this does the whole
read in one plain command. Judge a CI-time claim on the slowest of at least two
runs (``docs/agents/ci.md``).

``--durations`` adds, per test shard, the committed ``.test_durations``'s
predicted test time against the shard's ``durations-shard-<n>`` artifact, with
the files that drifted most. Run it from a checkout of the commit the run split
on. PERF-06 found PERF-05's stale shard 1 this way (523 s predicted, 90 s measured).

Usage::

    python scripts/ci_timings.py <run-id> [--durations]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime

# ``gh run view --log`` lines: job, step, timestamp then the message.
_MESSAGE_RE = re.compile(r"\d{4}-\d\d-\d\dT[\d:.]+Z ?(.*)$")
_SUMMARY_RE = re.compile(r"^=+ (.+ in [\d.]+s.*?) =+$")
_TOTAL_RE = re.compile(r"^\d{1,3}(\.\d+)?$")


def _messages(log: str) -> list[str]:
    messages = []
    for line in log.splitlines():
        match = _MESSAGE_RE.search(line)
        if match:
            messages.append(match.group(1).strip())
    return messages


def elapsed(start: str, end: str) -> str:
    """``"4m39s"`` between two ISO timestamps; ``"unfinished"`` without an end."""
    if not end:
        return "unfinished"
    seconds = int(
        (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    )
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m{seconds:02d}s" if minutes else f"{seconds}s"


def pytest_summary(log: str) -> str | None:
    """The last pytest result line (``"8507 passed ... in 619.70s"``), if any."""
    summaries = [m.group(1) for m in map(_SUMMARY_RE.match, _messages(log)) if m]
    return summaries[-1] if summaries else None


def coverage_total(log: str) -> str | None:
    """The bare ``coverage report --format=total`` line after the XML report."""
    messages = _messages(log)
    for i, message in enumerate(messages[:-1]):
        if message == "Wrote XML report to coverage.xml":
            if _TOTAL_RE.match(messages[i + 1]):
                return messages[i + 1]
    return None


def shard_drift(
    predicted: dict[str, float], shards: dict[int, dict[str, float]], top: int
) -> list[str]:
    """Per shard, the committed file's predicted test time against the shard's
    measured ``durations-shard-<n>`` artifact, then its ``top`` worst-drifting files."""
    lines = []
    for shard, measured in sorted(shards.items()):
        pred = sum(predicted.get(test, 0.0) for test in measured)
        meas = sum(measured.values())
        ratio = f"{meas / pred:.2f}x" if pred else "new"
        lines.append(
            f"shard {shard}: {len(measured)} tests, predicted {pred:.0f}s, "
            f"measured {meas:.0f}s ({ratio})"
        )
        files: dict[str, list[float]] = {}
        for test, seconds in measured.items():
            entry = files.setdefault(test.split("::")[0], [0.0, 0.0])
            entry[0] += predicted.get(test, 0.0)
            entry[1] += seconds
        worst = sorted(files.items(), key=lambda kv: -abs(kv[1][1] - kv[1][0]))
        lines += [f"    pred {p:7.1f}  meas {m:7.1f}  {f}" for f, (p, m) in worst[:top]]
    return lines


def _run(command: list[str]) -> str:
    """A command's stdout, decoded as UTF-8 (CI logs are; the Windows default
    codepage is not), with any stray byte replaced."""
    result = subprocess.run(command, capture_output=True)
    return result.stdout.decode("utf-8", errors="replace")


def _gh(*args: str) -> str:
    return _run(["gh", *args])


def _job_log(run_id: str, job: dict[str, object]) -> str:
    return _gh("run", "view", run_id, "--log", "--job", str(job["databaseId"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument(
        "--durations",
        action="store_true",
        help="also compare each shard's CI durations with the committed file",
    )
    args = parser.parse_args()

    run = json.loads(_gh("run", "view", args.run_id, "--json", "createdAt,jobs"))
    started = run["createdAt"]
    for job in run["jobs"]:
        name = job["name"]
        line = f"{name:<12} {elapsed(job['startedAt'], job['completedAt']):>10}"
        if name == "test" or name.startswith("test ("):
            summary = pytest_summary(_job_log(args.run_id, job))
            line += f"  {summary or 'no pytest summary'}"
        elif name == "coverage":
            line += f"  total {coverage_total(_job_log(args.run_id, job)) or '?'}%"
        print(line)
        if name == "test-result":
            print(f"run start -> test-result: {elapsed(started, job['completedAt'])}")
    if args.durations:
        from refresh_test_durations import DURATIONS_FILE, download_shards

        predicted = json.loads(DURATIONS_FILE.read_text(encoding="utf-8"))
        for line in shard_drift(predicted, download_shards(args.run_id), top=3):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
