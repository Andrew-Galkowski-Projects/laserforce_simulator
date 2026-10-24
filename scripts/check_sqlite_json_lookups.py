"""Fail on JSONField ``__contains`` / ``__contained_by`` lookups.

Django raises ``NotSupportedError`` for both on SQLite, the dev database
(ADR-0025). CI runs on Postgres, where they work, so such code goes green in CI
and breaks ``runserver`` and the local suite. PERF-03 found it with a probe.
Filter on a relation instead, or check the JSON in Python.

The JSONField names come from every ``models.py`` in the repo (``x =
models.JSONField(...)``); a lookup counts when the segment before
``__contains`` / ``__contained_by`` is one of them. A line that must hold one on
purpose (a test fixture for this check) opts out with the marker
``check-sqlite-json: ignore``.

Usage:

    python scripts/check_sqlite_json_lookups.py              # every tracked .py file
    python scripts/check_sqlite_json_lookups.py a.py b.py    # just these files

Exit status is 1 (with one ``path:line: reason`` per hit) on any hit, else 0.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SKIP_DIRS = {".venv", "node_modules", ".git", "worktrees", "__pycache__"}
IGNORE_MARKER = "check-sqlite-json: ignore"
_LOOKUP = re.compile(r"([A-Za-z0-9_]+?)__(contains|contained_by)\b")


def json_field_names(source: str) -> set[str]:
    """Names assigned a ``JSONField(...)`` / ``models.JSONField(...)`` call."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)):
            continue
        func = node.value.func
        called = (
            func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        )
        if called == "JSONField":
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def hits(source: str, names: set[str]) -> list[tuple[int, str]]:
    """``(line, lookup)`` for each unsupported lookup on a field in ``names``."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(source.splitlines(), start=1):
        if IGNORE_MARKER in line:
            continue
        for match in _LOOKUP.finditer(line):
            if match.group(1).split("__")[-1] in names:
                found.append((number, match.group(0)))
    return found


def _model_sources() -> list[Path]:
    return [
        path
        for path in _REPO.rglob("models.py")
        if not _SKIP_DIRS.intersection(path.relative_to(_REPO).parts)
    ]


def _tracked_python() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=_REPO, capture_output=True, text=True
    ).stdout
    return [str(_REPO / line) for line in out.splitlines() if line]


def main(argv: list[str]) -> int:
    names: set[str] = set()
    for path in _model_sources():
        names |= json_field_names(path.read_text(encoding="utf-8"))
    status = 0
    for arg in argv or _tracked_python():
        try:
            source = Path(arg).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line, lookup in hits(source, names):
            print(f"{arg}:{line}: {lookup} is unsupported on SQLite (ADR-0025)")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
