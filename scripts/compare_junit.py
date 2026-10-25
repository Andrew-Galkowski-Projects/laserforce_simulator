#!/usr/bin/env python
"""Compare per-file test time between two pytest ``--junitxml`` reports.

A performance probe on a local machine is noisy: PERF-05's baseline full suite
ran 196 s once and 265 s the next time. Summed per-file test time compares the
two runs file by file. A file the change cannot touch (a flat **control**) shows
the gap isn't noise.

Usage::

    python scripts/compare_junit.py <base.xml> <after.xml> [--top 25]

Prints a ``TOTAL`` line, then one row per test module, ``saved base after module``,
biggest saving first.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def _module(classname: str) -> str:
    head, _, last = classname.rpartition(".")
    return head if head and last[:1].isupper() else classname


def per_file(xml_text: str) -> dict[str, float]:
    """Summed test time per test module."""
    totals: dict[str, float] = defaultdict(float)
    for case in ET.fromstring(xml_text).iter("testcase"):
        totals[_module(case.get("classname", ""))] += float(case.get("time", 0))
    return dict(totals)


def report(base: dict[str, float], after: dict[str, float], top: int) -> str:
    b_total, a_total = sum(base.values()), sum(after.values())
    change = (a_total - b_total) / b_total * 100 if b_total else 0.0
    lines = [f"TOTAL base={b_total:.1f}s after={a_total:.1f}s ({change:+.0f}%)"]
    rows = sorted(
        ((base.get(k, 0.0) - after.get(k, 0.0), k) for k in base.keys() | after.keys()),
        reverse=True,
    )

    def row(saved: float, key: str) -> str:
        b, a = base.get(key, 0.0), after.get(key, 0.0)
        return f"{saved:8.1f} {b:8.1f} {a:8.1f}  {key}"

    if len(rows) > 2 * top:
        lines += [row(*r) for r in rows[:top]] + ["..."]
        lines += [row(*r) for r in rows[-top:]]
    else:
        lines += [row(*r) for r in rows]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args()
    base = per_file(args.base.read_text(encoding="utf-8"))
    after = per_file(args.after.read_text(encoding="utf-8"))
    print(report(base, after, args.top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
