"""Deterministic verify pipeline over the changed files.

Steps, in order: black --check, ruff (the narrow F401/F811 set from
pyproject.toml), the mojibake / UTF-8 encoding check, mypy, and pytest on the
derived targets. It mirrors the CI lint step plus a scoped test run.

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
changed files themselves, not in every module they import.

Run from anywhere in the repo:

    python .claude/skills/verify/scripts/verify.py

Exit code is 0 only if every applicable step passed (skipped steps count as
pass). The final block is the canonical VERIFY SUMMARY table.
"""

from __future__ import annotations

import os
import subprocess
import sys

import changed_files as cf  # same directory

STEPS: tuple[str, ...] = ("black", "ruff", "encoding", "mypy", "pytest")

# Mirrors scripts/check_encoding.py TEXT_SUFFIXES.
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


def main() -> int:
    data = cf.compute(base="main", mode="working")
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

    # --- mypy (changed files only; imports followed silently) ---
    if py_files:
        code, out = _run(
            _tool("mypy", "--follow-imports=silent", *py_files), cwd=repo_root
        )
        results["mypy"] = ("PASS", "") if code == 0 else ("FAIL", _tail(out))
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

    overall = "FAIL" if any(v[0] == "FAIL" for v in results.values()) else "PASS"

    print("VERIFY SUMMARY")
    print("-" * 30)
    for step in STEPS:
        status, note = results[step]
        print(_ascii(f"{step:<9} {status:<5} {note}".rstrip()))
    print("-" * 30)
    print(f"OVERALL   {overall}")

    return 0 if overall == "PASS" else 1


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
