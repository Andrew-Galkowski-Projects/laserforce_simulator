"""Delete local ``worktree-agent-*`` branches whose work is already in main.

Agent worktrees leave a ``worktree-agent-<id>`` branch behind. This lists the
ones whose tip is an ancestor of ``origin/main`` (nothing on them is lost) and,
with ``--delete``, removes them with ``git branch -d``. A branch checked out in
a worktree is never touched. Branches with commits main lacks are reported, not
deleted: a squash-merged ticket branch shows up there, so check its PR merged
before ``git branch -D``.

It then lists every ``agent-*`` worktree still on disk, with whether its HEAD
is in ``origin/main`` and how many uncommitted changes it holds, so a leftover
review fix-pass worktree gets noticed. Worktrees are reported, never removed.

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


def agent_worktrees(porcelain: list[str]) -> list[tuple[str, str]]:
    """``(path, HEAD sha)`` of every ``agent-*`` worktree in a
    ``git worktree list --porcelain`` listing."""
    found: list[tuple[str, str]] = []
    path = ""
    for line in porcelain:
        if line.startswith("worktree "):
            path = line[len("worktree ") :]
        elif line.startswith("HEAD ") and Path(path).name.startswith("agent-"):
            found.append((path, line[len("HEAD ") :]))
    return found


def describe_worktree(path: str, head_merged: bool, changes: int) -> str:
    """One report line: is the worktree's HEAD in main, and is it dirty."""
    head = "head in origin/main" if head_merged else "head NOT in origin/main"
    state = "clean" if changes == 0 else f"{changes} uncommitted change(s)"
    return f"worktree {path}  {head}, {state}"


def _git(*args: str, cwd: Path = _REPO, check: bool = True) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _head_merged(sha: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=_REPO
        ).returncode
        == 0
    )


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

    # Agent worktrees still on disk -- implementers and review fix passes
    # (whose staged files outlive copy_staged.py). Reported, never removed.
    for path, sha in agent_worktrees(_git("worktree", "list", "--porcelain")):
        changes = len(_git("status", "--porcelain", cwd=Path(path), check=False))
        print(describe_worktree(path, _head_merged(sha), changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
