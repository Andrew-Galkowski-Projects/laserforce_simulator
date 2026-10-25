#!/usr/bin/env python
"""Put bugs back on purpose and check the tests catch each one.

``/next-feature`` Step 7 (and the implementers' red pairing): each mutation
replaces one exact snippet of the target file, runs the tests, and records
which failed. The target is restored byte for byte after every run, also when
the run crashes. A shell loop of ``sed`` and ``cp`` is refused by the worktree
isolation guard, so this does it in one plain command.

The mutations file is JSON, a list of ``[name, old, new]``; ``old`` must occur
in the target exactly once. Exits 1 when a mutation survives or its pattern is
missing or ambiguous.

Usage::

    python scripts/mutate.py <target file> <mutations.json> -- <pytest args>
    python scripts/mutate.py <target file> <mutations.json> --run -- <any command>

``--run`` takes the words after ``--`` as the whole command (Vitest:
``npm --prefix frontend exec -- ng test --watch=false --include=<spec>``), run
with ``NO_COLOR=1``. Either way a non-zero exit is a caught mutation.
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
    status: str  # "caught", "survived", "not found" or "ambiguous"
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
            run = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "NO_COLOR": "1"},
                shell=sys.platform == "win32" and Path(command[0]).suffix == "",
            )
            status = "caught" if run.returncode != 0 else "survived"
            outcomes.append(
                Outcome(mutation.name, status, failed_tests(run.stdout + run.stderr))
            )
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
