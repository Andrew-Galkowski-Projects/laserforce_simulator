"""Warn when a branch adds a GLOSSARY.md avoid-word to code or docs.

GLOSSARY.md lists, under each term, the names to avoid (``_Avoid_: "standalone
round", ...``). This check reads the quoted multi-word ones and flags added diff
lines that use them, in any case and joined by a space, ``_``, ``-`` or nothing
(``standalone_round``, ``standaloneRound``). Single words ("game") are skipped:
they collide with too many legitimate names. GLOSSARY.md, ADRs and the PLAN
files are skipped too, since they record the avoided names on purpose.

It is a warning (exit 0) unless ``--strict``. ``/verify`` runs it.

Usage: python scripts/check_glossary_avoid.py [--ref origin/main...HEAD] [--strict]
(``--ref`` is passed to ``git diff``: ``HEAD`` checks uncommitted work.)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

_REPO = Path(__file__).resolve().parent.parent
_QUOTED = re.compile(r'"([A-Za-z][A-Za-z -]*[A-Za-z])"')
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_SKIPPED = ("GLOSSARY.md", "PLAN.md", "PLAN-completed.md", "docs/adr/")


class Hit(NamedTuple):
    path: str
    line: int
    term: str
    text: str


def avoid_terms(glossary: str) -> list[str]:
    """The quoted multi-word terms on every ``_Avoid_:`` line, in file order."""
    terms: list[str] = []
    for line in glossary.splitlines():
        if not line.startswith("_Avoid_:"):
            continue
        for term in _QUOTED.findall(line):
            if len(term.split()) > 1 and term not in terms:
                terms.append(term)
    return terms


def _pattern(term: str) -> re.Pattern[str]:
    words = (re.escape(w) for w in re.split(r"[\s-]+", term))
    return re.compile(r"(?<![A-Za-z])" + r"[\s_-]*".join(words), re.IGNORECASE)


def find_hits(terms: list[str], diff: str) -> list[Hit]:
    """Added lines of a unified ``diff`` that use any of ``terms``."""
    patterns = [(t, _pattern(t)) for t in terms]
    hits: list[Hit] = []
    path = ""
    line = 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].removeprefix("b/")
            continue
        match = _HUNK.match(raw)
        if match:
            line = int(match.group(1))
            continue
        if raw.startswith("+"):
            if not path.startswith(_SKIPPED):
                text = raw[1:]
                hits.extend(
                    Hit(path, line, term, text.strip())
                    for term, pattern in patterns
                    if pattern.search(text)
                )
            line += 1
        elif not raw.startswith("-"):
            line += 1
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default="origin/main...HEAD")
    parser.add_argument("--strict", action="store_true", help="exit 1 on a hit")
    args = parser.parse_args()

    glossary = (_REPO / "GLOSSARY.md").read_text(encoding="utf-8")
    diff = subprocess.run(
        ["git", "diff", "-U0", args.ref],
        cwd=_REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    hits = find_hits(avoid_terms(glossary), diff)
    for hit in hits:
        print(f'{hit.path}:{hit.line}: avoid-word "{hit.term}": {hit.text}'[:200])
    if not hits:
        print("no GLOSSARY.md avoid-words in added lines")
    return 1 if hits and args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
