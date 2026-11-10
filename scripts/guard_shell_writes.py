"""PreToolUse hook: send file writes to the Write / Edit tools, and refuse `cd`.

A Bash or PowerShell call that runs ``cd`` (PowerShell: also ``Set-Location``,
``Push-Location``, ``sl``, ``pushd``) or ``git -C`` is refused: a ``cd`` moves the
session's working directory for every later call, and ``-C`` points git at a
worktree that shares this ``.git`` (CLAUDE.md, *Tooling*). A Bash call that writes a repo file through ``sed -i``, a heredoc, or a ``>``
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
    "Refused: no `cd` (CLAUDE.md, Tooling). A shell cd moves the session's working "
    "directory for every later call. Run from the current directory and pass paths "
    "instead: `git ...` as is, `python laserforce_simulator/manage.py ...`, "
    "`npm --prefix frontend ...`, pytest with `-c laserforce_simulator/pytest.ini "
    "--rootdir laserforce_simulator`."
)

GIT_C_MESSAGE = (
    "Refused: no `git -C` (CLAUDE.md, Tooling). Every worktree shares this .git, "
    "and a git command aimed at another one has corrupted a working tree before. "
    "Run `git ...` from the current directory; for another worktree's state use "
    "`git worktree list` or that worktree's own session."
)

# `cd` as a command: at the start, or after `;`, `&&`, `||`, `|` or `(`.
_CD = re.compile(r"(?:^|[;&|(])\s*cd(?:\s|$)")
# PowerShell's directory changers, also after `{` (`if ($?) { ... }`).
_PS_CD = re.compile(
    r"(?:^|[;&|({\n])\s*(?:cd|sl|pushd|Set-Location|Push-Location)(?:\s|$)", re.I
)
# `git -C <path>`, also after `-c key=value` options (the capital C only).
_GIT_C = re.compile(r"\bgit(?:\s+-c\s+\S+)*\s+-C\s")
_SED_IN_PLACE = re.compile(r"\bsed\s+(?:-[A-Za-z]*i\S*|--in-place)\S*\s")
# Command separators: `;`, `&&`, `||`, `|`, newline.
_SEPARATOR = re.compile(r"\s*(?:;|&&|\|\||\||\n)\s*")
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
    if _GIT_C.search(command):
        return GIT_C_MESSAGE
    # Each `sed -i` segment's target is that segment's last token (BUG-SL-01
    # retro: the whole command's last token was a later pipe's `head`).
    for segment in _SEPARATOR.split(command):
        if _SED_IN_PLACE.search(segment + " "):
            if not _is_scratch(segment.split()[-1]):
                return MESSAGE
    targets = [t for t in _TARGET.findall(command) if not _is_scratch(t)]
    if not targets:
        return None
    if _HEREDOC.search(command):
        return MESSAGE
    if any(_SOURCE_SUFFIX.search(t.strip("'\"")) for t in targets):
        return MESSAGE
    return None


def powershell_refusal(command: str) -> str | None:
    """The refusal for a PowerShell ``command``: only ``cd`` and ``git -C``.

    Its file writes are left alone; the BOM rule is CLAUDE.md's to state.
    """
    if _PS_CD.search(command):
        return CD_MESSAGE
    if _GIT_C.search(command):
        return GIT_C_MESSAGE
    return None


def decide(payload: dict) -> tuple[int, str]:
    """``(exit code, stderr)`` for one hook payload: 2 refuses, 0 allows."""
    command = payload.get("tool_input", {}).get("command", "")
    tool = payload.get("tool_name")
    if tool == "Bash":
        message = refusal(command)
    elif tool == "PowerShell":
        message = powershell_refusal(command)
    else:
        return 0, ""
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
