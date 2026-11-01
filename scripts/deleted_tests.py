#!/usr/bin/env python
"""List every test that exists at ``<base>`` and is gone at ``<head>``.

A removal task deletes test files wholesale, and a green suite cannot show a
behaviour that lost its only test: ANG-08 deleted ``views_tests.py`` with ~1,100
lines of League play cases no port ticket had analysed, and only a manual audit
found the 11 behaviours they alone covered. Run this after each deletion
ticket; every line it prints maps to a port-or-delete verdict or a ported test.

Tests are read by AST from each test module changed between the two revisions
(``test_*`` functions, and ``test_*`` methods of any class). A test whose
``Class::name`` now lives in another file is marked ``(moved: <file>)``.

Usage::

    python scripts/deleted_tests.py <base> [<head>]   # head defaults to HEAD

Prints one ``<file>::<test>`` per removed test, then a ``N removed`` line.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import PurePosixPath

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def is_test_module(path: str) -> bool:
    """A ``test_*.py`` or ``*_tests.py`` module (``conftest.py`` holds no tests)."""
    name = PurePosixPath(path).name
    return name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_tests.py")
    )


def test_ids(source: str) -> set[str]:
    """``test_x`` and ``Class::test_x`` for each test the module defines."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    ids = set()
    for node in tree.body:
        if isinstance(node, _FUNCS) and node.name.startswith("test"):
            ids.add(node.name)
        elif isinstance(node, ast.ClassDef):
            ids.update(
                f"{node.name}::{item.name}"
                for item in node.body
                if isinstance(item, _FUNCS) and item.name.startswith("test")
            )
    return ids


def removed(base: dict[str, set[str]], head: dict[str, set[str]]) -> list[str]:
    """``<file>::<test>`` for each base test missing from its file at head."""
    home = {test: path for path, tests in head.items() for test in tests}
    lines = []
    for path in sorted(base):
        for test in sorted(base[path] - head.get(path, set())):
            moved = home.get(test)
            suffix = f"  (moved: {moved})" if moved else ""
            lines.append(f"{path}::{test}{suffix}")
    return lines


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def _tests_at(rev: str, paths: list[str]) -> dict[str, set[str]]:
    present = set(_git("ls-tree", "-r", "--name-only", rev).splitlines())
    return {
        path: test_ids(_git("show", f"{rev}:{path}"))
        for path in paths
        if path in present
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base")
    parser.add_argument("head", nargs="?", default="HEAD")
    args = parser.parse_args(argv)
    changed = [
        path
        for path in _git("diff", "--name-only", args.base, args.head).splitlines()
        if is_test_module(path)
    ]
    # A test moved into an unchanged file is still found: head reads every test module.
    head_paths = [
        path
        for path in _git("ls-tree", "-r", "--name-only", args.head).splitlines()
        if is_test_module(path)
    ]
    lines = removed(_tests_at(args.base, changed), _tests_at(args.head, head_paths))
    for line in lines:
        print(line)
    print(f"{len(lines)} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
