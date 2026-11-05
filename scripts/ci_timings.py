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
_SECONDS_RE = re.compile(r" in ([\d.]+)s\b")
#: How far over the shards' mean the slowest shard may run (docs/agents/ci.md).
BALANCE_LIMIT = 0.40


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


def summary_seconds(summary: str) -> float | None:
    """The seconds in a pytest result line, if it has any."""
    match = _SECONDS_RE.search(summary)
    return float(match.group(1)) if match else None


def balance_warning(seconds: list[float]) -> str | None:
    """A warning when the slowest shard's pytest is over ``BALANCE_LIMIT`` above the
    mean; docs/agents/ci.md's refresh trigger is two such runs in a row."""
    if len(seconds) < 2:
        return None
    mean = sum(seconds) / len(seconds)
    if max(seconds) <= mean * (1 + BALANCE_LIMIT):
        return None
    return (
        f"shard balance: the slowest shard is {max(seconds) / mean - 1:.0%} over the "
        "mean; two runs in a row means refresh .test_durations (docs/agents/ci.md)"
    )


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


def _is_test_shard(name: str) -> bool:
    return name == "test" or name.startswith("test (")


def run_balance_warning(run_id: str) -> str | None:
    """``balance_warning`` for a finished run, read from its test shards' logs."""
    jobs = json.loads(_gh("run", "view", run_id, "--json", "jobs"))["jobs"]
    seconds = []
    for job in jobs:
        if _is_test_shard(job["name"]):
            value = summary_seconds(pytest_summary(_job_log(run_id, job)) or "")
            if value is not None:
                seconds.append(value)
    return balance_warning(seconds)


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
    shard_seconds = []
    for job in run["jobs"]:
        name = job["name"]
        line = f"{name:<12} {elapsed(job['startedAt'], job['completedAt']):>10}"
        if _is_test_shard(name):
            summary = pytest_summary(_job_log(args.run_id, job))
            line += f"  {summary or 'no pytest summary'}"
            seconds = summary_seconds(summary or "")
            if seconds is not None:
                shard_seconds.append(seconds)
        elif name == "coverage":
            line += f"  total {coverage_total(_job_log(args.run_id, job)) or '?'}%"
        print(line)
        if name == "test-result":
            print(f"run start -> test-result: {elapsed(started, job['completedAt'])}")
    warning = balance_warning(shard_seconds)
    if warning:
        print(warning)
    if args.durations:
        from refresh_test_durations import DURATIONS_FILE, download_shards

        predicted = json.loads(DURATIONS_FILE.read_text(encoding="utf-8"))
        for line in shard_drift(predicted, download_shards(args.run_id), top=3):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
