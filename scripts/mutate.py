#!/usr/bin/env python
"""Put bugs back on purpose and check the tests catch each one.

``/next-feature`` Step 7 (and the implementers' red pairing): each mutation
replaces one exact snippet of the target file, runs the tests, and records
which failed. The target is restored byte for byte after every run, also when
the run crashes. A shell loop of ``sed`` and ``cp`` is refused by the worktree
isolation guard, so this does it in one plain command.

The mutations file is JSON, a list of ``[name, old, new]``; ``old`` must occur
in the target exactly once. A mutation is caught when the run fails and names a
failed test; a failed run that names none (a build or collection error, e.g. a
mutation that does not compile) is ``BROKEN``: rewrite that mutation. Exits 1
when a mutation survives, is broken, or its pattern is missing or ambiguous.
Exits 2, before any mutation, when the command already
fails on the unchanged file (``BASELINE FAILED``): a red test catches every
mutation and proves nothing.

Usage::

    python scripts/mutate.py <target file> <mutations.json> -- <pytest args>
    python scripts/mutate.py <target file> <mutations.json> --run -- <any command>

``--run`` takes the words after ``--`` as the whole command (Vitest:
``npm --prefix frontend exec -- ng test --watch=false --include=<spec>``), run
with ``NO_COLOR=1``. Failed tests are read from pytest's ``FAILED`` and Vitest's
``FAIL`` lines.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class Mutation(NamedTuple):
    name: str
    old: str
    new: str


class Outcome(NamedTuple):
    name: str
    status: str  # "caught", "survived", "broken", "not found" or "ambiguous"
    failed: list[str]


# Vitest: `` FAIL  |<project>| <file> > <describe> > <test>``.
_VITEST_FAIL = re.compile(r"^\s*FAIL\s+(?:\|[^|]+\|\s+)?(.+?)\s*$")


def failed_tests(output: str) -> list[str]:
    """Failed tests from pytest's ``FAILED <id> - <reason>`` lines or Vitest's ``FAIL`` lines."""
    failed = []
    for line in output.splitlines():
        if line.startswith("FAILED "):
            failed.append(
                line.removeprefix("FAILED ").split(" - ", 1)[0].replace("\\", "/")
            )
        elif match := _VITEST_FAIL.match(line):
            failed.append(match.group(1))
    return failed


def _read(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _match_line_endings(text: str, old: str, new: str) -> tuple[str, str]:
    """``old`` / ``new`` with ``\\n`` spelled as ``\\r\\n`` when ``text`` is a CRLF file.

    JSON patterns write line breaks as ``\\n``; a Windows checkout (``core.autocrlf``)
    has CRLF, so a multi-line pattern would otherwise never match.
    """
    if "\r\n" not in text:
        return old, new
    return _crlf(old), _crlf(new)


def _crlf(pattern: str) -> str:
    return pattern.replace("\r\n", "\n").replace("\n", "\r\n")


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # No bytecode writes: a cached .pyc is checked by mtime + size, so a same-size
    # mutation written within the same second as the last run would reuse stale code.
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        shell=sys.platform == "win32" and Path(command[0]).suffix == "",
    )


def baseline_failures(command: list[str], cwd: Path) -> list[str] | None:
    """``None`` when ``command`` passes on the unchanged files, else the tests that failed.

    A test that is already red "catches" every mutation and proves nothing.
    """
    run = _run(command, cwd)
    if run.returncode == 0:
        return None
    return failed_tests(run.stdout + run.stderr)


def mutate(
    target: Path, mutations: list[Mutation], pytest_args: list[str], cwd: Path
) -> list[Outcome]:
    """Run pytest once per mutation of ``target``; return each outcome in order."""
    return mutate_with(
        target, mutations, [sys.executable, "-m", "pytest", *pytest_args], cwd
    )


def mutate_with(
    target: Path, mutations: list[Mutation], command: list[str], cwd: Path
) -> list[Outcome]:
    """Run ``command`` once per mutation of ``target``; return each outcome in order."""
    original = _read(target)
    outcomes = []
    try:
        for mutation in mutations:
            old, new = _match_line_endings(original, mutation.old, mutation.new)
            count = original.count(old)
            if count != 1:
                status = "not found" if count == 0 else "ambiguous"
                outcomes.append(Outcome(mutation.name, status, []))
                continue
            _write(target, original.replace(old, new))
            run = _run(command, cwd)
            failed = failed_tests(run.stdout + run.stderr)
            if run.returncode == 0:
                status = "survived"
            else:
                # A failed run that names no test is a build or collection error: the
                # mutation never reached a test, so nothing caught it.
                status = "caught" if failed else "broken"
            outcomes.append(Outcome(mutation.name, status, failed))
    finally:
        _write(target, original)
    return outcomes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path, help="the file to mutate")
    parser.add_argument("mutations", type=Path, help="JSON list of [name, old, new]")
    parser.add_argument(
        "--run", action="store_true", help="the words after -- are the whole command"
    )
    # Split at the first `--` by hand: argparse would eat a second one the
    # command needs (`npm exec -- ng test`).
    argv = sys.argv[1:] if argv is None else argv
    split = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:split])
    tail = argv[split + 1 :]
    mutations = [
        Mutation(*m) for m in json.loads(args.mutations.read_text(encoding="utf-8"))
    ]
    command = tail if args.run else [sys.executable, "-m", "pytest", *tail]
    failures = baseline_failures(command, cwd=Path.cwd())
    if failures is not None:
        detail = f": {', '.join(failures)}" if failures else ""
        line = f"BASELINE FAILED{detail} -- fix the tests before mutating"
        print(line.encode("ascii", "replace").decode("ascii"))
        sys.exit(2)
    outcomes = mutate_with(args.target, mutations, command, cwd=Path.cwd())
    for outcome in outcomes:
        label = "caught" if outcome.status == "caught" else outcome.status.upper()
        detail = f": {', '.join(outcome.failed)}" if outcome.failed else ""
        line = f"{label:<9} {outcome.name}{detail}"
        print(line.encode("ascii", "replace").decode("ascii"))
    if any(outcome.status != "caught" for outcome in outcomes):
        sys.exit(1)


if __name__ == "__main__":
    main()
