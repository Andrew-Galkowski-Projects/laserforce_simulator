"""Report whether the changes since a commit are cosmetic: Markdown, comments, docstrings, formatting.

A suite run at ``<base>`` still holds for a working tree whose changes since
``<base>`` are all cosmetic, so ``/pr-ready`` reuses that run's counts instead of
running the suite again (SM5-01's review fix was a docstring, and the suite ran
another 90 s). A Python file is cosmetic when its AST, with every docstring
removed, is unchanged: comments and formatting never reach the AST. A Markdown
file is always cosmetic. Every other change, an added or deleted Python file
included, is not.

Usage:
    python scripts/cosmetic_diff.py <base>

Prints ``cosmetic`` and exits 0, or prints each non-cosmetic path and exits 1.
"""

from __future__ import annotations

import ast
import subprocess
import sys

_DOC_SUFFIXES = (".md",)


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:]
    return tree


def _code_shape(source: str) -> str:
    return ast.dump(_strip_docstrings(ast.parse(source)))


def is_cosmetic(path: str, old: str | None, new: str | None) -> bool:
    """True when the change from ``old`` to ``new`` (``None``: absent) cannot change behaviour."""
    if path.endswith(_DOC_SUFFIXES):
        return True
    if not path.endswith(".py") or old is None or new is None:
        return False
    try:
        return _code_shape(old) == _code_shape(new)
    except SyntaxError:
        return False


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8"
    )


def _at_base(base: str, path: str) -> str | None:
    shown = _git("show", f"{base}:{path}")
    return shown.stdout if shown.returncode == 0 else None


def _on_disk(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def non_cosmetic_paths(base: str) -> list[str]:
    """Paths changed since ``base`` (working tree and untracked files) that are not cosmetic."""
    tracked = _git("diff", "--name-only", base).stdout.splitlines()
    untracked = _git("ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return [
        path
        for path in dict.fromkeys(tracked + untracked)
        if not is_cosmetic(path, _at_base(base, path), _on_disk(path))
    ]


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        sys.exit(__doc__)
    paths = non_cosmetic_paths(argv[0])
    print("\n".join(paths) if paths else "cosmetic")
    sys.exit(1 if paths else 0)


if __name__ == "__main__":
    main()
