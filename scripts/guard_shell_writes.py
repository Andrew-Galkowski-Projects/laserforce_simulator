"""PreToolUse hook: send file writes to the Write / Edit tools, and refuse `cd`.

A Bash call that runs ``cd`` is refused: it moves the session's working directory
for every later call (CLAUDE.md, *Tooling*). A Bash call that writes a repo file through ``sed -i``, a heredoc, or a ``>``
redirect is refused (exit 2, the message goes back to the agent). Large heredocs
fail with ``ENAMETOOLONG`` on Windows and PowerShell adds a BOM, so the project
writes files with Write / Edit (CLAUDE.md, *Windows Shell Conventions*). Writes
under a temp or scratchpad directory, and reads such as ``sed -n``, pass.

Wired in ``.claude/settings.json``; reads the hook payload as JSON on stdin.
"""

from __future__ import annotations

import json
import re
import sys

MESSAGE = (
    "Refused: write repo files with the Edit / Write tools, not sed -i, heredocs or "
    "> redirects (CLAUDE.md, Windows Shell Conventions). Shell writes under the "
    "scratchpad or a temp directory are fine."
)

CD_MESSAGE = (
    "Refused: no `cd` (CLAUDE.md, Tooling). A Bash cd moves the session's working "
    "directory for every later call. Run from the current directory and pass paths "
    "instead: `git ...` as is, `python laserforce_simulator/manage.py ...`, "
    "`npm --prefix frontend ...`, pytest with `-c laserforce_simulator/pytest.ini "
    "--rootdir laserforce_simulator`."
)

# `cd` as a command: at the start, or after `;`, `&&`, `||`, `|` or `(`.
_CD = re.compile(r"(?:^|[;&|(])\s*cd(?:\s|$)")
_SED_IN_PLACE = re.compile(r"\bsed\s+(?:-[A-Za-z]*i\S*|--in-place)\S*\s")
_HEREDOC = re.compile(r"<<-?\s*['\"]?\w+")
# A redirect target: `> path`, `>> path`, `tee path`, skipping fd redirects (2>&1).
_TARGET = re.compile(r"(?:(?<![0-9&])>>?|\btee(?:\s+-a)?)\s*([^\s|;&<>]+)")
_SOURCE_SUFFIX = re.compile(
    r"\.(py|ts|html|scss|css|js|json|md|txt|yml|yaml|toml|ini|cfg)$", re.I
)
_SCRATCH = re.compile(r"(^/tmp/|/dev/null$|[\\/]temp[\\/]|scratchpad)", re.I)


def _is_scratch(path: str) -> bool:
    return bool(_SCRATCH.search(path.strip("'\"")))


def refusal(command: str) -> str | None:
    """The refusal message for ``command``, or ``None`` when it may run."""
    if _CD.search(command):
        return CD_MESSAGE
    if _SED_IN_PLACE.search(command):
        targets = command.split()[-1:]
        if not all(_is_scratch(t) for t in targets):
            return MESSAGE
    targets = [t for t in _TARGET.findall(command) if not _is_scratch(t)]
    if not targets:
        return None
    if _HEREDOC.search(command):
        return MESSAGE
    if any(_SOURCE_SUFFIX.search(t.strip("'\"")) for t in targets):
        return MESSAGE
    return None


def decide(payload: dict) -> tuple[int, str]:
    """``(exit code, stderr)`` for one hook payload: 2 refuses, 0 allows."""
    if payload.get("tool_name") != "Bash":
        return 0, ""
    message = refusal(payload.get("tool_input", {}).get("command", ""))
    return (2, message) if message else (0, "")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    code, message = decide(payload)
    if message:
        print(message, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
