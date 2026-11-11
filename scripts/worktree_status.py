"""Read-only report of every git worktree: branch, last commit, commits not in
``origin/main`` and uncommitted files.

The shell guard refuses ``git -C <worktree> ...`` (a git command aimed at another
worktree has corrupted one before), so a session resuming a cut pipeline could
not see what an implementer's worktree still held (CAL-03 retro). This script
runs only ``git worktree list``, ``git log``, ``git rev-list`` and ``git status``:
it writes nothing, so the guard's reason does not apply to it.

Run ``git fetch origin`` first for an accurate "not in origin/main" count. Usage:
    python scripts/worktree_status.py
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parent.parent
#: Uncommitted files listed per worktree; the rest are counted.
MAX_FILES = 20


@dataclass(frozen=True)
class Worktree:
    path: str
    head: str
    branch: Optional[str]  # None when detached


def parse_worktrees(porcelain: list[str]) -> list[Worktree]:
    """Every worktree in a ``git worktree list --porcelain`` listing."""
    found: list[Worktree] = []
    path = head = ""
    branch: Optional[str] = None
    for line in [*porcelain, ""]:
        if line.startswith("worktree "):
            path, head, branch = line[len("worktree ") :], "", None
        elif line.startswith("HEAD "):
            head = line[len("HEAD ") :]
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/") :]
        elif not line and path:
            found.append(Worktree(path, head, branch))
            path = ""
    return found


def report(wt: Worktree, last_commit: str, ahead: int, status: list[str]) -> list[str]:
    """The report lines for one worktree."""
    lines = [
        f"{wt.path}  [{wt.branch or 'detached'}]",
        f"  last commit: {last_commit}",
        f"  {ahead} commit(s) not in origin/main" if ahead else "  in origin/main",
    ]
    if not status:
        return [*lines, "  clean"]
    lines.append(f"  {len(status)} uncommitted change(s):")
    lines.extend(f"    {entry}" for entry in status[:MAX_FILES])
    if len(status) > MAX_FILES:
        lines.append(f"    ... and {len(status) - MAX_FILES} more")
    return lines


def _git(*args: str, cwd: Path = _REPO) -> list[str]:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    ).stdout
    return [line.rstrip() for line in out.splitlines()]


def main() -> int:
    for wt in parse_worktrees(_git("worktree", "list", "--porcelain")):
        where = Path(wt.path)
        if not where.is_dir():
            print(f"{wt.path}  [{wt.branch or 'detached'}]\n  missing on disk\n")
            continue
        last = next(iter(_git("log", "-1", "--format=%h %s", cwd=where)), "")
        count = _git("rev-list", "--count", "origin/main..HEAD", cwd=where)
        ahead = int(count[0]) if count and count[0].isdigit() else 0
        status = [s for s in _git("status", "--porcelain", cwd=where) if s]
        print("\n".join(report(wt, last, ahead, status)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
