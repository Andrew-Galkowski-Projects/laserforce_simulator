"""Delete local ``worktree-agent-*`` branches whose work is already in main.

Agent worktrees leave a ``worktree-agent-<id>`` branch behind. This lists the
ones whose tip is an ancestor of ``origin/main`` (nothing on them is lost) and,
with ``--delete``, removes them with ``git branch -d``. A branch checked out in
a worktree is never touched. Branches with commits main lacks are reported, not
deleted: a squash-merged ticket branch shows up there, so check its PR merged
before ``git branch -D``.

Run ``git fetch origin`` first. Usage:
    python scripts/prune_agent_branches.py [--delete]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PREFIX = "worktree-agent-"


def select(
    branches: list[str], merged: set[str], checked_out: set[str]
) -> tuple[list[str], list[str]]:
    """Split the agent branches into (safe to delete, keep)."""
    delete: list[str] = []
    keep: list[str] = []
    for branch in branches:
        if not branch.startswith(_PREFIX):
            continue
        if branch in merged and branch not in checked_out:
            delete.append(branch)
        else:
            keep.append(branch)
    return delete, keep


def _git(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=_REPO, capture_output=True, text=True, check=True
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _checked_out() -> set[str]:
    prefix = "branch refs/heads/"
    return {
        line[len(prefix) :]
        for line in _git("worktree", "list", "--porcelain")
        if line.startswith(prefix)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--delete", action="store_true", help="delete them")
    args = parser.parse_args()

    fmt = "--format=%(refname:short)"
    branches = _git("branch", fmt, "--list", f"{_PREFIX}*")
    merged = set(
        _git("branch", fmt, "--merged", "origin/main", "--list", f"{_PREFIX}*")
    )
    delete, keep = select(branches, merged, _checked_out())

    for branch in keep:
        print(f"keep    {branch}")
    for branch in delete:
        if args.delete:
            _git("branch", "-d", branch)
            print(f"deleted {branch}")
        else:
            print(f"merged  {branch}  (--delete removes it)")
    print(f"{len(delete)} merged, {len(keep)} kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
