"""Deterministic verify pipeline over the changed files.

Steps, in order: black --check, ruff (the narrow F401/F811 set from
pyproject.toml), the mojibake / UTF-8 encoding check, mypy, pytest on the
derived targets, and -- when anything under frontend/ changed -- the Angular
lint, Prettier check and Vitest run (CI's `frontend` job minus the build). It
mirrors the CI lint step plus a scoped test run.

Every step of the old `verify` skill was a fixed, repeatable operation with no
judgement involved: collect changed files, run the tools against them, derive
the pytest targets from a fixed mapping, and format a summary table. That is a
script, not a prompt. The agent's only real job is interpreting the *failure*
lines this script emits and deciding what to do about them.

Each tool runs as ``[sys.executable, "-m", <tool>, ...]`` so the script works
from a shell that does not have the venv on PATH: invoke it with the venv's
python and every tool comes from that same environment.

mypy runs with ``--follow-imports=silent`` (plus the pyproject.toml config,
``ignore_missing_imports = true``), so it reports only errors that are in the
changed files themselves, not in every module they import. Its errors are then
filtered to the changed lines: CI does not run mypy, so an error the change did
not write is not this change's FAIL. A file with no diff hunks (new and
untracked) keeps every error.

The change set is the uncommitted work vs HEAD. On a clean tree (everything
committed, the usual state at PR time) it falls back to the branch vs main, so
the checks never skip a committed branch; the summary names which one ran.

Run from anywhere in the repo:

    python .claude/skills/verify/scripts/verify.py

Exit code is 0 only if every applicable step passed (skipped steps count as
pass). The final block is the canonical VERIFY SUMMARY table.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import changed_files as cf  # same directory

STEPS: tuple[str, ...] = (
    "black",
    "ruff",
    "encoding",
    "glossary",
    "dead-code",
    "mypy",
    "pytest",
    "ng-lint",
    "prettier",
    "vitest",
)

# CI's `frontend` job scripts, as (step, npm script). The build is left to
# /pr-ready: it is slow and rarely fails where lint and tests pass.
FRONTEND_STEPS: tuple[tuple[str, str], ...] = (
    ("ng-lint", "lint"),
    ("prettier", "format:check"),
    ("vitest", "test:ci"),
)


def _load_check_encoding() -> ModuleType:
    # scripts/ is not a package; load the CI script by path so its
    # TEXT_SUFFIXES stays the single source of truth.
    path = Path(__file__).resolve().parents[4] / "scripts" / "check_encoding.py"
    spec = importlib.util.spec_from_file_location("check_encoding", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TEXT_SUFFIXES: tuple[str, ...] = _load_check_encoding().TEXT_SUFFIXES


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _tool(name: str, *args: str) -> list[str]:
    return [sys.executable, "-m", name, *args]


def select_changes(compute: Callable[..., Any]) -> tuple[str, Any]:
    """The change set to check: uncommitted work, else the branch vs main.

    A clean tree (everything committed, the usual state at PR time) falls back
    to ``mode="branch"`` so the checks still see the branch's files instead of
    skipping every step.
    """
    data = compute(base="main", mode="working")
    if not data["nothing_changed"]:
        return "working", data
    return "branch", compute(base="main", mode="branch")


def main() -> int:
    mode, data = select_changes(cf.compute)
    repo_root = str(data["repo_root"])
    django_dir = (
        os.path.join(repo_root, str(data["django_dir"]))
        if data["django_dir"]
        else repo_root
    )

    def existing(paths: list[str]) -> list[str]:
        # Deleted files show up in `git diff --name-only`; skip them.
        absolute = (os.path.join(repo_root, p) for p in paths)
        return [p for p in absolute if os.path.isfile(p)]

    # Changed .py files (migrations excluded by the bucketing) for the linters.
    py_files = existing(list(data["python"]))
    text_files = existing(
        [p for p in data["changed"] if p.lower().endswith(TEXT_SUFFIXES)]
    )

    results: dict[str, tuple[str, str]] = {}  # step -> (PASS|FAIL|SKIP, note)

    # --- black ---
    if py_files:
        code, out = _run(_tool("black", "--check", *py_files), cwd=repo_root)
        results["black"] = ("PASS", "") if code == 0 else ("FAIL", _tail(out))
    else:
        results["black"] = ("SKIP", "no changed .py files")

    # --- ruff (rule set + excludes from pyproject.toml) ---
    if py_files:
        code, out = _run(
            _tool("ruff", "check", "--force-exclude", *py_files), cwd=repo_root
        )
        results["ruff"] = ("PASS", "") if code == 0 else ("FAIL", _tail(out))
    else:
        results["ruff"] = ("SKIP", "no changed .py files")

    # --- encoding (mojibake / non-UTF-8) ---
    if text_files:
        script = os.path.join(repo_root, "scripts", "check_encoding.py")
        code, out = _run([sys.executable, script, *text_files], cwd=repo_root)
        results["encoding"] = ("PASS", "") if code == 0 else ("FAIL", _tail(out))
    else:
        results["encoding"] = ("SKIP", "no changed text files")

    # --- glossary (GLOSSARY.md avoid-words in added lines; WARN never fails) ---
    if data["nothing_changed"]:
        results["glossary"] = ("SKIP", "nothing changed")
    else:
        script = os.path.join(repo_root, "scripts", "check_glossary_avoid.py")
        ref = "HEAD" if mode == "working" else "main...HEAD"
        code, out = _run([sys.executable, script, "--ref", ref], cwd=repo_root)
        hits = [line for line in out.splitlines() if ': avoid-word "' in line]
        if code != 0:
            results["glossary"] = ("FAIL", _tail(out))
        elif hits:
            results["glossary"] = ("WARN", " | ".join(hits[:5]))
        else:
            results["glossary"] = ("PASS", "")

    # --- dead-code (unreferenced private defs in changed files; WARN only) ---
    if py_files:
        script = os.path.join(repo_root, "scripts", "check_unused_private.py")
        code, out = _run([sys.executable, script, *py_files], cwd=repo_root)
        hits = [line for line in out.splitlines() if "has no references" in line]
        if code != 0:
            results["dead-code"] = ("FAIL", _tail(out))
        elif hits:
            results["dead-code"] = ("WARN", " | ".join(hits[:5]))
        else:
            results["dead-code"] = ("PASS", "")
    else:
        results["dead-code"] = ("SKIP", "no changed .py files")

    # --- mypy (changed files only; errors on changed lines only) ---
    if py_files:
        code, out = _run(
            _tool("mypy", "--follow-imports=silent", *py_files), cwd=repo_root
        )
        if code == 0:
            results["mypy"] = ("PASS", "")
        else:
            ref = "HEAD" if mode == "working" else "main...HEAD"
            _, diff = _run(["git", "diff", "-U0", ref, "--", *py_files], repo_root)
            errors = _errors_on_changed_lines(out, _changed_lines(diff), repo_root)
            results["mypy"] = (
                ("FAIL", " | ".join(errors[-15:]))
                if errors
                else ("PASS", "pre-existing errors only")
            )
    else:
        results["mypy"] = ("SKIP", "no changed .py files")

    # --- pytest ---
    if data["nothing_changed"]:
        results["pytest"] = ("SKIP", "nothing changed")
    else:
        targets = list(data["pytest_targets"])
        code, out = _run(_tool("pytest", "-q", *targets), cwd=django_dir)
        scope = " ".join(targets) if targets else "full suite"
        results["pytest"] = (
            ("PASS", scope) if code == 0 else ("FAIL", _pytest_tail(out))
        )

    # --- frontend (only when frontend/ changed) ---
    frontend_changed = any(p.startswith("frontend/") for p in data["changed"])
    frontend_dir = os.path.join(repo_root, "frontend")
    npm = shutil.which("npm")  # npm.cmd on Windows
    for step, script in FRONTEND_STEPS:
        if not frontend_changed:
            results[step] = ("SKIP", "no changed frontend/ files")
        elif npm is None:
            results[step] = ("FAIL", "npm not on PATH")
        elif not os.path.isdir(os.path.join(frontend_dir, "node_modules")):
            results[step] = ("FAIL", "no node_modules: run npm --prefix frontend ci")
        else:
            code, out = _run([npm, "run", script], cwd=frontend_dir)
            results[step] = ("PASS", "") if code == 0 else ("FAIL", _tail(out))

    overall = "FAIL" if any(v[0] == "FAIL" for v in results.values()) else "PASS"

    print("VERIFY SUMMARY")
    print("uncommitted changes vs HEAD" if mode == "working" else "branch vs main")
    print("-" * 30)
    for step in STEPS:
        status, note = results[step]
        print(_ascii(f"{step:<9} {status:<5} {note}".rstrip()))
    print("-" * 30)
    print(f"OVERALL   {overall}")

    return 0 if overall == "PASS" else 1


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
# Lazy path match: an absolute Windows path carries a drive colon.
_MYPY_ERROR = re.compile(r"^(?P<path>.+?\.py):(?P<line>\d+)(?::\d+)?: error:")


def _changed_lines(diff: str) -> dict[str, set[int]]:
    """``git diff -U0`` text -> {repo-relative path: new-side line numbers}."""
    changed: dict[str, set[int]] = {}
    current: set[int] | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            current = changed.setdefault(path, set())
        elif current is not None and (match := _HUNK.match(line)):
            start, count = int(match[1]), int(match[2] or "1")
            current.update(range(start, start + count))
    return changed


def _errors_on_changed_lines(
    mypy_out: str, changed: dict[str, set[int]], repo_root: str
) -> list[str]:
    """Keep the mypy errors on a changed line, or in a file with no hunks."""
    kept = []
    for line in mypy_out.splitlines():
        match = _MYPY_ERROR.match(line)
        if not match:
            continue
        absolute = os.path.join(repo_root, match["path"])  # no-op if absolute
        path = os.path.relpath(absolute, repo_root).replace(os.sep, "/")
        lines = changed.get(path)
        if lines is None or int(match["line"]) in lines:
            kept.append(line)
    return kept


def _ascii(text: str) -> str:
    # The Windows cp1252 console cannot print every tool's output; keep it safe.
    return text.encode("ascii", errors="backslashreplace").decode("ascii")


def _tail(text: str, n: int = 15) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return " | ".join(lines[-n:]) if lines else ""


def _pytest_tail(text: str) -> str:
    # Prefer the pytest summary line (e.g. "3 failed, 10 passed").
    for line in reversed(text.splitlines()):
        s = line.strip(" =")
        if ("passed" in s or "failed" in s or "error" in s) and any(
            c.isdigit() for c in s
        ):
            return s
    return _tail(text)


if __name__ == "__main__":
    raise SystemExit(main())
