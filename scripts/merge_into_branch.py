#!/usr/bin/env python
"""Merge one branch into another without checking either out.

``/next-feature`` merges each ticket branch into an integration (or prototype)
branch that is not checked out in the session's own worktree. This moves the
target ref with git plumbing only: a fast-forward when the target is behind,
nothing when the source is already in it, and otherwise a two-parent merge
commit built by ``git merge-tree --write-tree`` + ``commit-tree``. A conflict
leaves the target untouched and names the conflicting paths.

A target checked out in any worktree is refused: moving its ref would leave
that worktree's files out of step with its branch. Merge there with
``git merge`` instead.

Usage::

    python scripts/merge_into_branch.py <target> <source>
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


class MergeError(RuntimeError):
    """The merge was refused or conflicted; the target ref did not move."""


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, check=check, capture_output=True, text=True
    )


def _sha(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        _git(
            repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False
        ).returncode
        == 0
    )


def _checked_out(repo: Path, branch: str) -> bool:
    listing = _git(repo, "worktree", "list", "--porcelain").stdout
    return f"branch refs/heads/{branch}" in listing.splitlines()


def merge_into(repo: Path, target: str, source: str) -> tuple[str, str]:
    """Merge ``source`` into ``target``; return ``(kind, new target sha)``.

    ``kind`` is ``"fast-forward"``, ``"up-to-date"`` or ``"merge"``.
    """
    if _checked_out(repo, target):
        raise MergeError(
            f"{target} is checked out in a worktree; run `git merge {source}` there"
        )
    old = _sha(repo, f"refs/heads/{target}")
    new_source = _sha(repo, source)
    if _is_ancestor(repo, new_source, old):
        return "up-to-date", old
    if _is_ancestor(repo, old, new_source):
        _git(repo, "update-ref", f"refs/heads/{target}", new_source, old)
        return "fast-forward", new_source
    merged = _git(
        repo, "merge-tree", "--write-tree", "--name-only", old, new_source, check=False
    )
    if merged.returncode != 0:
        lines = merged.stdout.strip().splitlines()
        paths = [line for line in lines[1:] if line and " " not in line]
        raise MergeError(
            f"merge conflict in: {', '.join(paths) or merged.stdout.strip()}"
        )
    tree = merged.stdout.strip().splitlines()[0]
    commit = _git(
        repo,
        "commit-tree",
        tree,
        "-p",
        old,
        "-p",
        new_source,
        "-m",
        f"Merge {source} into {target}",
    ).stdout.strip()
    _git(repo, "update-ref", f"refs/heads/{target}", commit, old)
    return "merge", commit


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", help="branch to move (not checked out anywhere)")
    parser.add_argument("source", help="branch or commit to merge in")
    args = parser.parse_args(argv)
    try:
        kind, sha = merge_into(Path.cwd(), args.target, args.source)
    except MergeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{kind}: {args.target} -> {sha[:7]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
