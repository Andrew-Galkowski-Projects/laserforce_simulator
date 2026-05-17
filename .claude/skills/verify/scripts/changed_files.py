"""Compute the set of changed files and the pytest targets they imply.

Single source of truth shared by the `verify`, `code-review`, and `update-docs`
skills. These three skills all need the *same* deterministic facts:

  1. Which files changed (vs. a base ref, or just the working tree).
  2. How those files bucket by kind (python / template / migration / other).
  3. Which pytest targets cover the changed app code.

That logic used to be copy-pasted as prose into three SKILL.md files, and the
copies had drifted out of date (they referenced ``matches/tests.py`` and
``teams/tests.py``, which no longer exist). Encoding it once here keeps the
mapping correct and identical for every caller.

Run from anywhere inside the repo:

    python .claude/skills/verify/scripts/changed_files.py --base main --json
    python .claude/skills/verify/scripts/changed_files.py --mode working --print

``--json`` (default) prints a machine-readable object. ``--print`` adds a
human-readable summary on stderr.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import PurePosixPath
from typing import TypedDict


class ChangedFiles(TypedDict):
    """Deterministic facts about the working tree, shared across skills."""

    base: str
    mode: str
    repo_root: str
    django_dir: str
    changed: list[str]
    python: list[str]
    templates: list[str]
    migrations: list[str]
    other: list[str]
    pytest_targets: list[str]
    pytest_full_suite: bool
    nothing_changed: bool


# App directory -> pytest target, relative to the directory containing manage.py.
# Authoritative mapping. matches/ and teams/ keep their tests in a package
# directory; core/ keeps a single tests.py.
APP_TEST_TARGETS: dict[str, str] = {
    "matches": "matches/tests",
    "teams": "teams/tests",
    "core": "core/tests.py",
}

# Mirrors pytest.ini `python_files = test_*.py *_test.py *tests.py`.
TEST_FILE_SUFFIXES: tuple[str, ...] = ("_test.py", "tests.py")
TEST_FILE_PREFIXES: tuple[str, ...] = ("test_",)


def _git(args: list[str], cwd: str) -> list[str]:
    """Run a git command and return non-empty stdout lines."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _repo_root() -> str:
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _django_dir_rel(repo_root: str) -> str:
    """Path of the directory containing manage.py, relative to repo root."""
    matches = _git(["ls-files", "*manage.py"], cwd=repo_root)
    for path in matches:
        parent = str(PurePosixPath(path).parent)
        return "" if parent == "." else parent
    return ""


def collect_changed(repo_root: str, base: str, mode: str) -> list[str]:
    """Union of changed file paths (repo-root-relative, posix), deduplicated."""
    if mode == "working":
        # Changes since the last commit, including unstaged. Used by `verify`.
        specs = [["diff", "--name-only", "HEAD"], ["diff", "--name-only"]]
    else:
        # Everything on this branch vs. base, plus staged + unstaged.
        specs = [
            ["diff", "--name-only", f"{base}...HEAD"],
            ["diff", "--name-only", "--cached"],
            ["diff", "--name-only"],
        ]
    seen: dict[str, None] = {}
    for spec in specs:
        for path in _git(spec, cwd=repo_root):
            seen.setdefault(path, None)
    return list(seen)


def _is_test_file(name: str) -> bool:
    return name.endswith(TEST_FILE_SUFFIXES) or name.startswith(TEST_FILE_PREFIXES)


def _django_relative(path: str, django_dir_rel: str) -> str | None:
    """Strip the manage.py-dir prefix; None if the path is outside it."""
    if not django_dir_rel:
        return path
    prefix = django_dir_rel + "/"
    return path[len(prefix) :] if path.startswith(prefix) else None


def bucket(paths: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {
        "python": [],
        "templates": [],
        "migrations": [],
        "other": [],
    }
    for p in paths:
        parts = PurePosixPath(p).parts
        if "migrations" in parts and p.endswith(".py"):
            out["migrations"].append(p)
        elif p.endswith(".py"):
            out["python"].append(p)
        elif p.endswith((".html", ".htm")):
            out["templates"].append(p)
        else:
            out["other"].append(p)
    return out


def pytest_targets(paths: list[str], django_dir_rel: str) -> tuple[list[str], bool]:
    """Derive pytest targets. Returns (targets, fell_back_to_full_suite).

    A changed non-test file pulls in its whole app target; a changed test file
    is its own most precise target. If the app target is already included, the
    individual test file is dropped so pytest does not collect it twice.
    """
    app_targets: dict[str, None] = {}
    test_files: dict[str, None] = {}
    code_changed = False
    for p in paths:
        if not p.endswith((".py", ".html", ".htm")):
            continue
        rel = _django_relative(p, django_dir_rel)
        if rel is None:
            continue
        code_changed = True
        rel_parts = PurePosixPath(rel).parts
        if rel.endswith(".py") and _is_test_file(rel_parts[-1]):
            test_files.setdefault(rel, None)
            continue
        app = rel_parts[0] if rel_parts else ""
        if app in APP_TEST_TARGETS:
            app_targets.setdefault(APP_TEST_TARGETS[app], None)
    targets = list(app_targets)
    targets += [
        tf
        for tf in test_files
        if not any(tf == t or tf.startswith(t.rstrip("/") + "/") for t in app_targets)
    ]
    if code_changed and not targets:
        # Code changed but no mapping matched -> run everything.
        return [], True
    return targets, False


def compute(base: str, mode: str) -> ChangedFiles:
    repo_root = _repo_root()
    django_dir_rel = _django_dir_rel(repo_root)
    changed = collect_changed(repo_root, base, mode)
    buckets = bucket(changed)
    targets, full = pytest_targets(changed, django_dir_rel)
    return {
        "base": base,
        "mode": mode,
        "repo_root": repo_root,
        "django_dir": django_dir_rel,
        "changed": changed,
        "python": buckets["python"],
        "templates": buckets["templates"],
        "migrations": buckets["migrations"],
        "other": buckets["other"],
        "pytest_targets": targets,
        "pytest_full_suite": full,
        "nothing_changed": not changed,
    }


def _summary(data: ChangedFiles) -> str:
    lines = [
        f"changed files : {len(data['changed'])}",
        f"  python      : {len(data['python'])}",
        f"  templates   : {len(data['templates'])}",
        f"  migrations  : {len(data['migrations'])}",
        f"  other       : {len(data['other'])}",
    ]
    if data["pytest_full_suite"]:
        lines.append("pytest        : full suite (no mapping matched)")
    elif data["pytest_targets"]:
        lines.append(f"pytest        : {' '.join(data['pytest_targets'])}")
    else:
        lines.append("pytest        : nothing to run")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main", help="base ref (default: main)")
    parser.add_argument(
        "--mode",
        choices=["branch", "working"],
        default="branch",
        help="branch: vs base + staged + unstaged; working: vs HEAD + unstaged",
    )
    parser.add_argument(
        "--print", action="store_true", help="also print a summary to stderr"
    )
    args = parser.parse_args(argv)

    try:
        data = compute(args.base, args.mode)
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc.stderr or exc}", file=sys.stderr)
        return 2

    print(json.dumps(data, indent=2))
    if getattr(args, "print"):
        print(_summary(data), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
