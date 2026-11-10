#!/usr/bin/env python
"""Copy another worktree's staged or uncommitted changes onto the current worktree.

``/next-feature`` Step 8: the review fix-pass agent stages its files and
reports; the orchestrator then brings them onto the integration branch. A
shell loop over ``cp`` (or ``git -C`` into the other worktree) is refused by
the worktree isolation guard, so this does it in one plain command: every
path staged in ``<source worktree>`` is copied to the same path here, and a
staged deletion deletes it here. Nothing is staged or committed: review the
result with ``git status`` / ``git diff`` and stage each path yourself. The
last line printed is the ``git worktree remove`` command for the source.

``--worktree`` rescues an agent whose session ended before its commit (CAL-02b):
it takes everything uncommitted in the source, staged or not, untracked files
included and ignored files left out. That source may have branched before this
worktree moved on, so a path that also changed here since the source's ``HEAD``
is left alone and printed as ``CHANGED ON BOTH SIDES``; merge those by hand. The
run then exits 1 and prints no remove command, since the source still holds work.

Usage::

    python scripts/copy_staged.py <source worktree> [--worktree]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _git_out(src: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=src, check=True, capture_output=True, text=True
    ).stdout


def _name_status(src: Path, *diff_args: str) -> list[tuple[str, str]]:
    out = _git_out(src, "diff", *diff_args, "--name-status", "--no-renames", "-z")
    fields = [f for f in out.split("\0") if f]
    return list(zip(fields[0::2], fields[1::2]))


def staged_changes(src: Path) -> list[tuple[str, str]]:
    """``(status, path)`` for each staged change in ``src`` (renames split)."""
    return _name_status(src, "--cached")


def worktree_changes(src: Path) -> list[tuple[str, str]]:
    """``(status, path)`` for everything uncommitted in ``src``: changes against
    ``HEAD``, staged or not, then the untracked files git does not ignore."""
    # Refreshes the index, so a file only touched on disk is not listed as changed.
    _git_out(src, "status", "--porcelain")
    untracked = _git_out(src, "ls-files", "--others", "--exclude-standard", "-z")
    return _name_status(src, "HEAD") + [
        ("A", path) for path in untracked.split("\0") if path
    ]


def _apply(src: Path, dst: Path, status: str, path: str) -> None:
    target = dst / path
    if status == "D":
        target.unlink(missing_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / path, target)


def copy_staged(src: Path, dst: Path) -> list[str]:
    """Mirror ``src``'s staged changes into ``dst``; return the paths touched."""
    touched = []
    for status, path in staged_changes(src):
        _apply(src, dst, status, path)
        touched.append(path)
    return touched


def _same_text(left: bytes, right: bytes) -> bool:
    """Equal but for line endings: ``core.autocrlf`` writes CRLF to a checkout."""
    return left.replace(b"\r\n", b"\n") == right.replace(b"\r\n", b"\n")


def _changed_here(src: Path, dst: Path, status: str, path: str) -> bool:
    """Whether ``dst``'s file differs from what ``src`` started from."""
    target = dst / path
    if status == "A":
        # Nothing to start from: only an identical file here is safe to replace.
        return target.exists() and not _same_text(
            target.read_bytes(), (src / path).read_bytes()
        )
    if not target.exists():
        return True
    base = subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=src, check=True, capture_output=True
    ).stdout
    return not _same_text(target.read_bytes(), base)


def copy_worktree(src: Path, dst: Path) -> tuple[list[str], list[str]]:
    """Mirror everything uncommitted in ``src`` into ``dst``; return the paths
    touched and the paths skipped because they also changed in ``dst``."""
    touched, skipped = [], []
    for status, path in worktree_changes(src):
        if _changed_here(src, dst, status, path):
            skipped.append(path)
        else:
            _apply(src, dst, status, path)
            touched.append(path)
    return touched, skipped


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "source", type=Path, help="the worktree holding the staged files"
    )
    parser.add_argument(
        "--worktree",
        action="store_true",
        help="copy everything uncommitted, not only what is staged",
    )
    args = parser.parse_args(argv)
    skipped: list[str] = []
    if args.worktree:
        touched, skipped = copy_worktree(args.source, Path.cwd())
    else:
        touched = copy_staged(args.source, Path.cwd())
    for path in touched:
        print(path)
    for path in skipped:
        print(f"CHANGED ON BOTH SIDES, not copied: {path}")
    if skipped:
        sys.exit(1)
    # The fix worktree's job ends here; its staged files would otherwise
    # linger on disk until someone audits them (PERF-01 retro). Long paths on: a worktree's
    # node_modules passes Windows' 260-character limit, and a plain remove fails half way
    # with "Filename too long" (RV-07 retro).
    print(f'git -c core.longpaths=true worktree remove --force "{args.source}"')


if __name__ == "__main__":
    main()
