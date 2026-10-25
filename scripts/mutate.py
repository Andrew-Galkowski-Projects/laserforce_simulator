#!/usr/bin/env python
"""Put bugs back on purpose and check the tests catch each one.

``/next-feature`` Step 7 (and the implementers' red pairing): each mutation
replaces one exact snippet of the target file, runs pytest, and records which
tests failed. The target is restored byte for byte after every run, also when
pytest crashes. A shell loop of ``sed`` and ``cp`` is refused by the worktree
isolation guard, so this does it in one plain command.

The mutations file is JSON, a list of ``[name, old, new]``; ``old`` must occur
in the target exactly once. Exits 1 when a mutation survives or its pattern is
missing or ambiguous.

Usage::

    python scripts/mutate.py <target file> <mutations.json> -- <pytest args>
"""

from __future__ import annotations

import argparse
import json
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
    status: str  # "caught", "survived", "not found" or "ambiguous"
    failed: list[str]


def failed_tests(pytest_output: str) -> list[str]:
    """Node ids from pytest's ``FAILED <id> - <reason>`` summary lines."""
    return [
        line.removeprefix("FAILED ").split(" - ", 1)[0].replace("\\", "/")
        for line in pytest_output.splitlines()
        if line.startswith("FAILED ")
    ]


def _read(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def mutate(
    target: Path, mutations: list[Mutation], pytest_args: list[str], cwd: Path
) -> list[Outcome]:
    """Run pytest once per mutation of ``target``; return each outcome in order."""
    original = _read(target)
    outcomes = []
    try:
        for mutation in mutations:
            count = original.count(mutation.old)
            if count != 1:
                status = "not found" if count == 0 else "ambiguous"
                outcomes.append(Outcome(mutation.name, status, []))
                continue
            _write(target, original.replace(mutation.old, mutation.new))
            run = subprocess.run(
                [sys.executable, "-m", "pytest", *pytest_args],
                cwd=cwd,
                capture_output=True,
                text=True,
            )
            status = "caught" if run.returncode != 0 else "survived"
            outcomes.append(Outcome(mutation.name, status, failed_tests(run.stdout)))
    finally:
        _write(target, original)
    return outcomes


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path, help="the file to mutate")
    parser.add_argument("mutations", type=Path, help="JSON list of [name, old, new]")
    parser.add_argument("pytest_args", nargs="*", help="after --: passed to pytest")
    args = parser.parse_args(argv)
    mutations = [
        Mutation(*m) for m in json.loads(args.mutations.read_text(encoding="utf-8"))
    ]
    outcomes = mutate(args.target, mutations, args.pytest_args, cwd=Path.cwd())
    for outcome in outcomes:
        label = "caught" if outcome.status == "caught" else outcome.status.upper()
        detail = f": {', '.join(outcome.failed)}" if outcome.failed else ""
        line = f"{label:<9} {outcome.name}{detail}"
        print(line.encode("ascii", "replace").decode("ascii"))
    if any(outcome.status != "caught" for outcome in outcomes):
        sys.exit(1)


if __name__ == "__main__":
    main()
