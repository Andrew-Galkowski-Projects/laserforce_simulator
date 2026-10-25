#!/usr/bin/env python
"""Move a shipped task's section from PLAN.md into PLAN-completed.md.

``/update-docs`` Step 3: marks the task heading ``[DONE]``, inserts the
shipped note, appends the section under the same ``##`` heading in
``PLAN-completed.md`` (adding it if missing), leaves a one-line pointer in
``PLAN.md`` and drops the ID from the Sequencing Summary. Every ``---``
separator stays where it was.

Usage::

    python scripts/move_done_task.py <ID> "<shipped note>" [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime
import re
import sys
import textwrap
from pathlib import Path
from typing import NamedTuple


class MoveError(ValueError):
    """The move can't be applied; neither file is written."""


class MoveResult(NamedTuple):
    plan: str
    completed: str
    warnings: list[str]


def _find_task(lines: list[str], task_id: str) -> tuple[int, int, str]:
    """``(line index, heading level, title)`` of ``task_id``'s one open heading."""
    pattern = re.compile(rf"^(#{{3,4}}) {re.escape(task_id)} · (.*)$")
    found = [(i, m) for i, line in enumerate(lines) if (m := pattern.match(line))]
    if not found:
        raise MoveError(f"{task_id}: task heading not found in PLAN.md")
    if len(found) > 1:
        raise MoveError(f"{task_id}: matches {len(found)} headings in PLAN.md")
    start, match = found[0]
    title = match.group(2)
    if title.startswith("[DONE]"):
        raise MoveError(f"{task_id}: heading is already [DONE]")
    return start, len(match.group(1)), title


def _block_end(lines: list[str], start: int) -> int:
    """Index of the next ``## `` heading after ``start``, or the end of ``lines``."""
    end = start + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    return end


def _section_end(lines: list[str], start: int, level: int) -> int:
    """Index one past the section's last non-blank line."""
    end = start + 1
    while end < len(lines):
        line = lines[end]
        heading = re.match(r"^(#+) ", line)
        if line.strip() == "---" or (heading and len(heading.group(1)) <= level):
            break
        end += 1
    while lines[end - 1].strip() == "":
        end -= 1
    return end


def _phase_heading(lines: list[str], start: int) -> str:
    for line in reversed(lines[:start]):
        if line.startswith("## "):
            return line
    raise MoveError("no enclosing ## heading")


def _done_section(section: list[str], note_lines: list[str]) -> list[str]:
    heading = re.sub(r" · ", " · [DONE] ", section[0], count=1)
    first = next((i for i in range(1, len(section)) if section[i].strip()), 0)
    has_spec_line = first > 0 and section[first].startswith("Spec: #")
    insert_at = first + 1 if has_spec_line else 1
    return [heading, *section[1:insert_at], "", *note_lines, *section[insert_at:]]


def _append_to_phase(lines: list[str], phase: str, section: list[str]) -> list[str]:
    if phase not in lines:
        while lines and lines[-1].strip() == "":
            lines = lines[:-1]
        return [*lines, "", "---", "", phase, "", *section, "", "---", ""]
    end = _block_end(lines, lines.index(phase))
    while lines[end - 1].strip() in ("", "---"):
        end -= 1
    return [*lines[:end], "", *section, *lines[end:]]


def _drop_from_summary(lines: list[str], task_id: str) -> tuple[list[str], list[str]]:
    """Drop ``task_id`` as a whole comma-list item inside ``## Sequencing Summary``.

    Returns the new lines and a warning for each other mention left in place.
    """
    if "## Sequencing Summary" not in lines:
        return lines, []
    start = lines.index("## Sequencing Summary")
    end = _block_end(lines, start)
    item = re.escape(task_id)
    mention = re.compile(rf"(?<![\w-]){item}(?![\w-])")
    only_item = re.compile(rf"^\s*- \*\*[^*]+:\*\* {item}\.?$")
    first_item = re.compile(rf"(?<=:\*\* ){item}, ")
    later_item = re.compile(rf", {item}(?=,|\.?$)")
    kept: list[str] = []
    warnings: list[str] = []
    for line in lines[start:end]:
        if only_item.match(line):
            continue
        line = later_item.sub("", first_item.sub("", line, count=1), count=1)
        if mention.search(line):
            warnings.append(f"Sequencing Summary still mentions {task_id}: {line}")
        kept.append(line)
    return [*lines[:start], *kept, *lines[end:]], warnings


def move_done_task(
    plan: str, completed: str, task_id: str, note: str, shipped: datetime.date
) -> MoveResult:
    """Return the new PLAN.md and PLAN-completed.md text after moving ``task_id``."""
    plan_lines = plan.split("\n")
    start, level, title = _find_task(plan_lines, task_id)
    end = _section_end(plan_lines, start, level)
    phase = _phase_heading(plan_lines, start)

    note_lines = textwrap.wrap(
        f"**[DONE] Shipped ({shipped.isoformat()}).** {note}",
        width=100,
        break_long_words=False,
        break_on_hyphens=False,
    )
    section = _done_section(plan_lines[start:end], note_lines)
    short_title = re.sub(r" \([^()]*\)$", "", title)
    pointer = (
        f"{task_id} ({short_title[:1].lower()}{short_title[1:]}) is **[DONE]**; "
        "see `PLAN-completed.md`."
    )
    new_plan, warnings = _drop_from_summary(
        [*plan_lines[:start], pointer, *plan_lines[end:]], task_id
    )
    new_completed = _append_to_phase(completed.split("\n"), phase, section)
    return MoveResult("\n".join(new_plan), "\n".join(new_completed), warnings)


def _read(path: Path) -> tuple[str, str]:
    """``(text with \n line endings, the file's own newline)``."""
    with path.open(encoding="utf-8", newline="") as handle:
        raw = handle.read()
    newline = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), newline


def _write(path: Path, text: str, newline: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace("\n", newline))


def main(argv: list[str] | None = None, root: Path | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task_id", help="the task ID, e.g. TOOL-01")
    parser.add_argument("note", help="the shipped note, a few sentences")
    parser.add_argument(
        "--date",
        type=datetime.date.fromisoformat,
        default=datetime.date.today(),
        help="the shipped date, YYYY-MM-DD (default: today)",
    )
    args = parser.parse_args(argv)
    root = root or Path(__file__).resolve().parent.parent
    plan_path, completed_path = root / "PLAN.md", root / "PLAN-completed.md"
    plan, plan_newline = _read(plan_path)
    completed, completed_newline = _read(completed_path)
    try:
        result = move_done_task(plan, completed, args.task_id, args.note, args.date)
    except MoveError as error:
        print(f"move_done_task: {error}")
        sys.exit(1)
    _write(plan_path, result.plan, plan_newline)
    _write(completed_path, result.completed, completed_newline)
    print(f"Moved {args.task_id} to PLAN-completed.md.")
    for warning in result.warnings:
        print(f"WARNING: {warning}".encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    main()
