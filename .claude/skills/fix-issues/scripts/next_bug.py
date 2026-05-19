#!/usr/bin/env python
"""issues.md loop helper.

Two deterministic operations the fix-issues loop repeats every iteration:

  next_bug.py [--file issues.md]
      Print the next OPEN bug to fix: highest severity (RED > ORANGE >
      YELLOW), ties broken by table order. Skips notes (info) and any row
      already struck (contains ``~~`` or a ``(fixed`` / ``(skipped`` tag).
      Emits the bug's full detail section. Prints ``NO_OPEN_BUGS`` and exits
      0 when the open set is empty -> the loop's termination signal.

  next_bug.py --strike ID [--tag TEXT] [--file issues.md]
      Strike through that bug's Summary-table row AND its detail-section
      header in place, appending ``_(TEXT)_`` (default: ``fixed``). This is
      what removes a bug from the open set, so the loop only terminates if
      every handled bug is struck.

Selection / strike live here (not in the agent's head) because eyeballing
severity order and editing two bits of markdown per iteration is exactly the
kind of repeated, deterministic step that drifts when done by hand.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't encode severity emoji.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

# Severity emoji -> rank. info (ℹ️) is intentionally absent: notes are skipped.
SEV_RANK = {"🔴": 0, "🟠": 1, "🟡": 2}
ID_RE = re.compile(r"^[A-Z]{1,4}-\d+$")
STRUCK_MARKERS = ("~~", "(fixed", "(skipped")


def _rows(text: str):
    """Yield (line_index, id, sev, area, oneliner, raw_line) for table rows."""
    for i, line in enumerate(text.splitlines()):
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) < 4 or not ID_RE.match(cells[0]):
            continue
        yield i, cells[0], cells[1], cells[2], cells[3], line


def _is_open(raw_line: str) -> bool:
    return not any(m in raw_line for m in STRUCK_MARKERS)


def _detail(text: str, bug_id: str) -> str:
    lines = text.splitlines()
    start = None
    pat = re.compile(rf"^###\s.*(?<![A-Za-z0-9-]){re.escape(bug_id)}(?![0-9])")
    for i, line in enumerate(lines):
        if pat.search(line):
            start = i
            break
    if start is None:
        return "(no detail section found — read issues.md for context)"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ") or lines[j].startswith("### "):
            end = j
            break
    return "\n".join(lines[start:end]).strip()


def cmd_next(text: str) -> int:
    candidates = []
    for idx, bug_id, sev, area, oneliner, raw in _rows(text):
        if sev not in SEV_RANK or not _is_open(raw):
            continue
        candidates.append((SEV_RANK[sev], idx, bug_id, sev, area, oneliner))
    if not candidates:
        print("NO_OPEN_BUGS")
        return 0
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, _, bug_id, sev, area, oneliner = candidates[0]
    print(f"ID: {bug_id}")
    print(f"SEV: {sev}")
    print(f"AREA: {area}")
    print(f"ONELINER: {oneliner}")
    print(f"OPEN_REMAINING: {len(candidates)}")
    print("--- DETAIL ---")
    print(_detail(text, bug_id))
    return 0


def _strike_inline(s: str) -> str:
    s = s.strip()
    return s if (not s or s.startswith("~~")) else f"~~{s}~~"


def cmd_strike(path: Path, text: str, bug_id: str, tag: str) -> int:
    lines = text.splitlines(keepends=True)
    struck_row = struck_hdr = False
    tag_md = f" _({tag})_"

    for i, line in enumerate(lines):
        nl = "\n" if line.endswith("\n") else ""
        body = line[: -len(nl)] if nl else line

        # Summary-table row whose first cell is exactly the ID.
        if body.lstrip().startswith("|"):
            cells = body.split("|")
            inner = [c for c in cells[1:-1]]
            if inner and inner[0].strip().strip("~").strip() == bug_id:
                if any(m in body for m in STRUCK_MARKERS):
                    struck_row = True  # already done; treat as success
                else:
                    new_inner = [f" {_strike_inline(c)} " for c in inner]
                    new_inner[-1] = f" {_strike_inline(inner[-1])}{tag_md} "
                    lines[i] = "|" + "|".join(new_inner) + "|" + nl
                    struck_row = True
                continue

        # Detail-section header containing the ID.
        if body.startswith("### ") and re.search(
            rf"(?<![A-Za-z0-9-]){re.escape(bug_id)}(?![0-9])", body
        ):
            if "~~" in body or "(fixed" in body or "(skipped" in body:
                struck_hdr = True
            else:
                title = body[4:].strip()
                lines[i] = f"### ~~{title}~~{tag_md}" + nl
                struck_hdr = True

    if not struck_row:
        print(f"ERROR: no Summary-table row found for {bug_id}", file=sys.stderr)
        return 2
    path.write_text("".join(lines), encoding="utf-8")
    hdr = "header struck" if struck_hdr else "no detail header found (row only)"
    print(f"STRUCK {bug_id} ({hdr})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="issues.md fix-loop helper")
    ap.add_argument("--file", default="issues.md", help="path to issues.md")
    ap.add_argument("--strike", metavar="ID", help="strike this bug in place")
    ap.add_argument("--tag", default="fixed", help="parenthetical tag text")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")

    if args.strike:
        return cmd_strike(path, text, args.strike, args.tag)
    return cmd_next(text)


if __name__ == "__main__":
    raise SystemExit(main())
