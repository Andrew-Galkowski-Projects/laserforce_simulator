#!/usr/bin/env python
"""PR guardrail: a pull request that changes UI files must show it.

A PR touching templates, stylesheets, static assets or the Angular source
needs at least one screenshot or video in its body (a markdown image, an
``<img>`` / ``<video>`` tag, a GitHub attachment URL, or a link to an image /
video file). When a UI file changed but nothing visible did, the author says
so with a line ``No visual change: <reason>``. HTML comments are ignored, so
the template's placeholder never counts.

Usage (CI passes the PR body through the environment, never the shell)::

    PR_BODY="..." python scripts/check_pr_evidence.py <changed file> ...
    git diff --name-only base...head | PR_BODY="..." python scripts/check_pr_evidence.py -

Exits 1 when evidence is missing, 0 otherwise.
"""

from __future__ import annotations

import os
import re
import sys
from fnmatch import fnmatch

# Paths whose changes are visible to a user.
UI_PATTERNS = (
    "laserforce_simulator/templates/*",
    "laserforce_simulator/assets/*",
    "*/static/*",
    "frontend/src/*",
    "*.html",
    "*.css",
    "*.scss",
)
# Frontend unit tests live beside the components but change nothing visible.
NON_UI_PATTERNS = ("*.spec.ts",)

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_EVIDENCE = re.compile(
    r"!\[[^\]]*\]\([^)\s]+"  # markdown image
    r"|<img\s|<video\s"  # inline HTML media
    r"|https://github\.com/user-attachments/assets/"  # drag-and-drop upload
    r"|\S+\.(?:png|jpe?g|gif|webp|mp4|webm|mov)(?:\?\S*)?(?=[\s)\]\"'>]|$)",
    re.IGNORECASE,
)
_OPT_OUT = re.compile(r"^\s*[-*]?\s*No visual change:\s*(\S.*?)\s*$", re.MULTILINE)


def ui_files(files: list[str]) -> list[str]:
    """The changed files a reviewer would want to see rendered."""
    return [
        f
        for f in files
        if any(fnmatch(f, p) for p in UI_PATTERNS)
        and not any(fnmatch(f, p) for p in NON_UI_PATTERNS)
    ]


def _strip_comments(body: str) -> str:
    return _COMMENT.sub("", body or "")


def has_evidence(body: str) -> bool:
    """True when the body (outside HTML comments) embeds or links media."""
    return bool(_EVIDENCE.search(_strip_comments(body)))


def opt_out_reason(body: str) -> str | None:
    """The reason given on a ``No visual change: <reason>`` line, if any."""
    match = _OPT_OUT.search(_strip_comments(body))
    return match.group(1) if match else None


def check(files: list[str], body: str) -> tuple[bool, str]:
    """``(ok, message)`` for a PR changing ``files`` with description ``body``."""
    touched = ui_files(files)
    if not touched:
        return True, "No UI files changed; no screenshot needed."
    if has_evidence(body):
        return True, f"UI change ({len(touched)} file(s)) has a screenshot or video."
    reason = opt_out_reason(body)
    if reason:
        return True, f"UI files changed, marked 'No visual change: {reason}'."
    listing = "\n".join(f"  - {f}" for f in touched)
    return False, (
        "This PR changes UI files but its description has no screenshot or video:\n"
        f"{listing}\n"
        "Add one to the 'Screenshots / Demo' section (drag an image or video into the\n"
        "PR description, or link one), or add a line 'No visual change: <reason>'.\n"
        "Editing the description re-runs this check."
    )


def main(argv: list[str]) -> int:
    if argv == ["-"]:
        files = [line.strip() for line in sys.stdin if line.strip()]
    else:
        files = argv
    ok, message = check(files, os.environ.get("PR_BODY", ""))
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
