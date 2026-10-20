"""Fail on mojibake and non-UTF-8 bytes in text files.

Windows tooling (cp1252 consoles, PowerShell ``Set-Content`` defaults) has
repeatedly round-tripped UTF-8 text through the wrong codec, turning an em-dash
into ``\\u00e2\\u20ac\\u201d`` and an arrow into ``\\u00e2\\u2020\\u2019``. This
check catches that class of damage before it lands.

A file fails when it contains:

* bytes that are not valid UTF-8, or
* the mojibake lead-in ``\\u00e2\\u20ac`` (a mangled U+2010..U+206F character),
* the mojibake lead-in ``\\u00e2\\u2020`` (a mangled U+2190..U+21FF arrow), or
* the replacement character ``\\ufffd``.

A line that must contain one of these on purpose (e.g. a test fixture) can opt
out with the marker ``check-encoding: ignore`` anywhere on that line.

Usage:

    python scripts/check_encoding.py              # every tracked text file
    python scripts/check_encoding.py a.py b.md    # just these files

Exit status is 1 (with one ``path:line: reason`` per hit) on any failure, else 0.
Stdlib only; output is ASCII so it is safe on a cp1252 console.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

TEXT_SUFFIXES: tuple[str, ...] = (
    ".py",
    ".md",
    ".html",
    ".txt",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".js",
    ".css",
)

# Written as escapes so this file does not trip its own check.
BAD_SEQUENCES: dict[str, str] = {
    "â€": "mojibake sequence U+00E2 U+20AC",
    "â†": "mojibake sequence U+00E2 U+2020",
    "�": "replacement character U+FFFD",
}

IGNORE_MARKER = "check-encoding: ignore"


def tracked_text_files() -> list[str]:
    """Tracked files (repo-root-relative) whose suffix is a checked text type."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode("utf-8", errors="replace")
    return [p for p in out.split("\0") if p and p.lower().endswith(TEXT_SUFFIXES)]


def check_file(path: str) -> list[str]:
    """Return ``path:line: reason`` strings for every problem in ``path``."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        return [f"{path}:0: unreadable ({exc.strerror})"]

    problems: list[str] = []
    for lineno, raw in enumerate(data.splitlines(), start=1):
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{path}:{lineno}: not valid UTF-8")
            continue
        if IGNORE_MARKER in line:
            continue
        for seq, reason in BAD_SEQUENCES.items():
            if seq in line:
                problems.append(f"{path}:{lineno}: {reason}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail on mojibake and non-UTF-8 bytes in text files."
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="files to check (default: every tracked text file)",
    )
    args = parser.parse_args(argv)

    files = args.files or tracked_text_files()
    problems: list[str] = []
    for path in files:
        if not path.lower().endswith(TEXT_SUFFIXES):
            continue
        problems.extend(check_file(path))

    for line in problems:
        print(line.encode("ascii", errors="backslashreplace").decode("ascii"))
    if problems:
        print(f"check_encoding: {len(problems)} problem(s) found")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
