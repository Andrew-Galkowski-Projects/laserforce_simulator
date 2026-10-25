#!/usr/bin/env python
"""Wait for a PR's CI to settle and print one summary.

``/next-feature`` watches CI after opening a PR. Finding the run id with
``$(gh ...)`` and chaining ``sleep`` is refused in a worktree session, so this
does the whole wait in one plain command: poll ``gh pr checks`` until every
check has left ``pending`` and every workflow run behind them is completed
(a later job such as ``docker`` is only listed once the job it needs passes),
then print each check and the PR's mergeable state.

Right after a close/reopen, ``gh pr checks`` still lists the previous run's
finished checks. Pass that run's id as ``--after-run`` so the watch waits for
the new runs instead of passing on the stale one.

Exit status: 0 when every check passed or was skipped, 1 on any failure or
cancellation, 2 on timeout.

Usage::

    python scripts/watch_pr_ci.py <pr> [--after-run <id>] [--interval 30] [--timeout 3600]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

_RUN_RE = re.compile(r"/actions/runs/(\d+)")
_OK_BUCKETS = {"pass", "skipping"}


def run_ids(checks: list[dict[str, str]]) -> list[str]:
    """The distinct workflow run ids behind ``checks``, in first-seen order."""
    ids: list[str] = []
    for check in checks:
        match = _RUN_RE.search(check.get("link", ""))
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def verdict(
    checks: list[dict[str, str]],
    run_statuses: dict[str, str],
    after_run: int | None = None,
) -> str:
    """``"pending"`` until settled, then ``"pass"`` or ``"fail"``.

    With ``after_run``, a check whose run id is not newer counts as pending.
    """
    if not checks or any(c["bucket"] == "pending" for c in checks):
        return "pending"
    if after_run is not None and any(int(rid) <= after_run for rid in run_ids(checks)):
        return "pending"
    if any(status != "completed" for status in run_statuses.values()):
        return "pending"
    return "pass" if all(c["bucket"] in _OK_BUCKETS for c in checks) else "fail"


def _gh_json(*args: str) -> object:
    out = subprocess.run(["gh", *args], capture_output=True, text=True).stdout
    return json.loads(out) if out.strip() else []


def _checks(pr: str) -> list[dict[str, str]]:
    fields = "name,bucket,link,workflow"
    return _gh_json("pr", "checks", pr, "--json", fields)  # type: ignore[return-value]


def _run_status(run_id: str) -> str:
    data = _gh_json("run", "view", run_id, "--json", "status")
    return data.get("status", "") if isinstance(data, dict) else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pr")
    parser.add_argument("--after-run", type=int, default=None)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()

    deadline = time.monotonic() + args.timeout
    while True:
        checks = _checks(args.pr)
        statuses = {rid: _run_status(rid) for rid in run_ids(checks)}
        result = verdict(checks, statuses, args.after_run)
        if result != "pending":
            break
        if time.monotonic() > deadline:
            print(f"timeout after {args.timeout}s; still pending")
            return 2
        time.sleep(args.interval)

    for check in checks:
        print(f"{check['bucket']:<9} {check.get('workflow', '')}/{check['name']}")
    mergeable = _gh_json("pr", "view", args.pr, "--json", "mergeable")
    if isinstance(mergeable, dict):
        print(f"mergeable: {mergeable.get('mergeable')}")
    print(f"result: {result}")
    return 0 if result == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
