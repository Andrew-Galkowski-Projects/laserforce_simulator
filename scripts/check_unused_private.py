"""Warn when a changed file defines a private function or class nothing uses.

A module-level ``_name`` def or class in one of the given files is dead when no
Python file in the repo references it. A reference is a name, an attribute, or
an identifier inside a string (``mock.patch("pkg.mod._name")`` targets count).
An ``import`` alone does not: a re-export with no caller is still dead (the
PERF-01 review found ``_team_winp_for_season`` kept alive only by one). ruff's
F401 / F811 set cannot see this, and CODING_STANDARDS.md bans dead code.

It is a warning (exit 0): a name reached only through ``getattr`` is a false
positive. ``/verify`` runs it on the changed ``.py`` files.

Usage: python scripts/check_unused_private.py <file.py> [...]
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SKIP_DIRS = {".venv", "node_modules", ".git", "worktrees", "__pycache__"}


def private_defs(source: str) -> list[tuple[str, int]]:
    """``(name, line)`` of each module-level private def or class (no dunders)."""
    nodes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return [
        (node.name, node.lineno)
        for node in ast.parse(source).body
        if isinstance(node, nodes)
        and node.name.startswith("_")
        and not node.name.startswith("__")
    ]


def used_names(source: str) -> set[str]:
    """Every name, attribute and string identifier the source references."""
    used: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            used.update(_IDENT.findall(node.value))
    return used


def unused(defs: dict[str, list[tuple[str, int]]], used: set[str]) -> list[str]:
    """One report line per private def in ``defs`` that ``used`` lacks."""
    return [
        f"{path}:{line}: {name} has no references (dead code?)"
        for path, found in defs.items()
        for name, line in found
        if name not in used
    ]


def _repo_sources() -> list[Path]:
    return [
        path
        for path in _REPO.rglob("*.py")
        if not _SKIP_DIRS.intersection(path.relative_to(_REPO).parts)
    ]


def _read(path: Path) -> str | None:
    try:
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None
    return source


def main(argv: list[str]) -> int:
    defs: dict[str, list[tuple[str, int]]] = {}
    for arg in argv:
        source = _read(Path(arg))
        if source is not None:
            defs[arg] = private_defs(source)
    if not any(defs.values()):
        return 0
    used: set[str] = set()
    for path in _repo_sources():
        source = _read(path)
        if source is not None:
            used |= used_names(source)
    for line in unused(defs, used):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
