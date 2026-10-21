#!/usr/bin/env python
"""Publish PR screenshots / videos and print the markdown to paste.

An agent cannot drag an image into a GitHub PR description, so this script
commits the files to the ``pr-assets`` branch of ``origin`` under a folder
named after the feature branch, then prints one markdown line per file:
images embed (``![name](url)``), videos link (``[Video: name](url)``). The
repo is public, so the ``blob/...?raw=true`` URLs render for everyone.

``pr-assets`` is never merged and holds nothing but media, so ``main`` stays
free of binaries. The script uses git plumbing (a temporary index, then
``commit-tree`` + ``push``): the working tree, the index and the current
branch are never touched.

Usage::

    python scripts/pr_assets.py before.png after.png flow.webm
    python scripts/pr_assets.py --branch fix/uxp-07 shot.png
    python scripts/pr_assets.py --dry-run shot.png    # markdown only, no push

Capture with the Chrome DevTools MCP: ``take_screenshot`` with ``filePath``
for a still, ``screencast_start`` / ``screencast_stop`` for a flow.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ASSETS_BRANCH = "pr-assets"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov"}
_REMOTE = re.compile(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$")


def parse_remote(url: str) -> str:
    """``owner/repo`` from a GitHub https or ssh remote URL."""
    match = _REMOTE.search(url.strip())
    if not match:
        raise ValueError(f"Not a GitHub remote: {url!r}")
    return match.group(1)


def asset_path(branch: str, file: Path) -> str:
    """Where ``file`` lands on the assets branch: ``<branch-slug>/<name>``."""
    return f"{branch.replace('/', '-')}/{file.name}"


def markdown_for(repo: str, path: str) -> str:
    """The line to paste into the PR body for one published asset."""
    url = f"https://github.com/{repo}/blob/{ASSETS_BRANCH}/{path}?raw=true"
    name = Path(path).name
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return f"![{Path(path).stem}]({url})"
    if ext in VIDEO_EXTS:
        return f"[Video: {name}]({url})"
    raise ValueError(f"Unsupported media type: {name}")


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def publish(repo: Path, branch: str, files: list[Path]) -> list[str]:
    """Commit ``files`` onto ``origin/pr-assets`` and push; return their paths."""
    for file in files:
        markdown_for("o/r", asset_path(branch, file))  # rejects bad types early
    has_branch = bool(_git(repo, "ls-remote", "--heads", "origin", ASSETS_BRANCH))
    parent: str | None = None
    if has_branch:
        _git(repo, "fetch", "-q", "origin", ASSETS_BRANCH)
        parent = _git(repo, "rev-parse", "FETCH_HEAD")
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        if parent:
            _git(repo, "read-tree", parent, env=env)
        paths = []
        for file in files:
            blob = _git(repo, "hash-object", "-w", str(file.resolve()))
            path = asset_path(branch, file)
            _git(
                repo,
                "update-index",
                "--add",
                "--cacheinfo",
                f"100644,{blob},{path}",
                env=env,
            )
            paths.append(path)
        tree = _git(repo, "write-tree", env=env)
    commit_args = ["commit-tree", tree, "-m", f"PR assets for {branch}"]
    if parent:
        commit_args[2:2] = ["-p", parent]
    commit = _git(repo, *commit_args)
    _git(repo, "push", "-q", "origin", f"{commit}:refs/heads/{ASSETS_BRANCH}")
    return paths


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--branch", help="folder name (default: current branch)")
    parser.add_argument("--dry-run", action="store_true", help="print, don't push")
    args = parser.parse_args(argv)
    repo = Path.cwd()
    missing = [str(f) for f in args.files if not f.is_file()]
    if missing:
        parser.error(f"not a file: {', '.join(missing)}")
    branch = args.branch or _git(repo, "branch", "--show-current")
    owner_repo = parse_remote(_git(repo, "remote", "get-url", "origin"))
    if args.dry_run:
        paths = [asset_path(branch, f) for f in args.files]
    else:
        paths = publish(repo, branch, args.files)
    for path in paths:
        print(markdown_for(owner_repo, path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
