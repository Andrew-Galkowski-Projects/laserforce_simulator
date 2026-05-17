"""Deterministic verify pipeline: black --check, mypy, pytest on changed files.

Every step of the old `verify` skill was a fixed, repeatable operation with no
judgement involved: collect changed .py files, run three tools against them,
derive the pytest targets from a fixed mapping, and format a summary table.
That is a script, not a prompt. The agent's only real job is interpreting the
*failure* lines this script emits and deciding what to do about them.

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


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main() -> int:
    data = cf.compute(base="main", mode="working")
    repo_root = str(data["repo_root"])
    django_dir = (
        os.path.join(repo_root, str(data["django_dir"]))
        if data["django_dir"]
        else repo_root
    )

    # Changed .py files as absolute paths for black/mypy.
    py_files = [os.path.join(repo_root, p) for p in data["python"]]

    results: dict[str, tuple[str, str]] = {}  # step -> (PASS|FAIL|SKIP, note)

    # --- black ---
    if py_files:
        code, out = _run(["black", "--check", *py_files], cwd=repo_root)
        results["black"] = ("PASS", "") if code == 0 else ("FAIL", _tail(out))
    else:
        results["black"] = ("SKIP", "no changed .py files")

    # --- mypy ---
    if py_files:
        code, out = _run(["mypy", *py_files], cwd=repo_root)
        results["mypy"] = ("PASS", "") if code == 0 else ("FAIL", _tail(out))
    else:
        results["mypy"] = ("SKIP", "no changed .py files")

    # --- pytest ---
    if data["nothing_changed"]:
        results["pytest"] = ("SKIP", "nothing changed")
    else:
        targets = list(data["pytest_targets"])
        pytest_cmd = ["pytest", "-q", *targets] if targets else ["pytest", "-q"]
        code, out = _run(pytest_cmd, cwd=django_dir)
        scope = " ".join(targets) if targets else "full suite"
        results["pytest"] = (
            ("PASS", scope) if code == 0 else ("FAIL", _pytest_tail(out))
        )

    overall = "FAIL" if any(v[0] == "FAIL" for v in results.values()) else "PASS"

    print("VERIFY SUMMARY")
    print("-" * 30)
    for step in ("black", "mypy", "pytest"):
        status, note = results[step]
        print(f"{step:<8} {status:<5} {note}".rstrip())
    print("-" * 30)
    print(f"OVERALL  {overall}")

    return 0 if overall == "PASS" else 1


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
