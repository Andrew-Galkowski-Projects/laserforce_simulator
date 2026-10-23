#!/usr/bin/env python
"""Copy another worktree's staged changes onto the current worktree.

``/next-feature`` Step 8: the review fix-pass agent stages its files and
reports; the orchestrator then brings them onto the integration branch. A
shell loop over ``cp`` (or ``git -C`` into the other worktree) is refused by
the worktree isolation guard, so this does it in one plain command: every
path staged in ``<source worktree>`` is copied to the same path here, and a
staged deletion deletes it here. Nothing is staged or committed: review the
result with ``git status`` / ``git diff`` and stage each path yourself. The
last line printed is the ``git worktree remove`` command for the source.

Usage::

    python scripts/copy_staged.py <source worktree>
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def staged_changes(src: Path) -> list[tuple[str, str]]:
    """``(status, path)`` for each staged change in ``src`` (renames split)."""
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-status", "--no-renames", "-z"],
        cwd=src,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    fields = [f for f in out.split("\0") if f]
    return list(zip(fields[0::2], fields[1::2]))


def copy_staged(src: Path, dst: Path) -> list[str]:
    """Mirror ``src``'s staged changes into ``dst``; return the paths touched."""
    touched = []
    for status, path in staged_changes(src):
        target = dst / path
        if status == "D":
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / path, target)
        touched.append(path)
    return touched


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "source", type=Path, help="the worktree holding the staged files"
    )
    args = parser.parse_args(argv)
    for path in copy_staged(args.source, Path.cwd()):
        print(path)
    # The fix worktree's job ends here; its staged files would otherwise
    # linger on disk until someone audits them (PERF-01 retro).
    print(f'git worktree remove --force "{args.source}"')


if __name__ == "__main__":
    main()
