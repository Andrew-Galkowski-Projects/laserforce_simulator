"""Tests for the repo-level ``scripts/move_done_task.py``.

The script lives outside the Django project, so it is loaded by path. The
pure ``move_done_task`` text transform is the seam; ``main`` gets one test for
the file I/O (line endings, no BOM).
"""

import codecs
import contextlib
import datetime
import importlib.util
import io
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "move_done_task.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("move_done_task", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


move_done_task = _load_script()

_SHIPPED = datetime.date(2026, 10, 24)

_PLAN = """\
# Development Plan

---

## Found Issues

Intro.

### TEST-01 · Keep media writes out

Spec: #342

**Prio: Low.** Body.

### TOOL-01 · Script the move

Body.

---

## Sequencing Summary

- **Other:** TEST-01, TOOL-01, LG-06.

---
"""

_COMPLETED = """\
# Completed

---

## Found Issues

### PERF-06 · [DONE] Rebalance

Body.

---

## Phase 6

### X-01 · [DONE] Done thing
"""


def _move(
    plan: str = _PLAN,
    completed: str = _COMPLETED,
    task_id: str = "TEST-01",
    note: str = "It works.",
) -> Any:
    return move_done_task.move_done_task(plan, completed, task_id, note, _SHIPPED)


class MoveTests(TestCase):
    def test_section_moves_to_matching_phase_with_done_heading_and_note(
        self,
    ) -> None:
        result = _move()
        self.assertEqual(
            result.completed,
            """\
# Completed

---

## Found Issues

### PERF-06 · [DONE] Rebalance

Body.

### TEST-01 · [DONE] Keep media writes out

Spec: #342

**[DONE] Shipped (2026-10-24).** It works.

**Prio: Low.** Body.

---

## Phase 6

### X-01 · [DONE] Done thing
""",
        )

    def test_plan_keeps_a_pointer_and_the_next_task(self) -> None:
        result = _move()
        self.assertIn(
            "Intro.\n\n"
            "TEST-01 (keep media writes out) is **[DONE]**; see `PLAN-completed.md`.\n\n"
            "### TOOL-01 · Script the move\n",
            result.plan,
        )
        self.assertNotIn("**Prio: Low.** Body.", result.plan)

    def test_note_goes_straight_after_the_heading_without_a_spec_line(
        self,
    ) -> None:
        result = _move(task_id="TOOL-01")
        self.assertIn(
            "### TOOL-01 · [DONE] Script the move\n\n"
            "**[DONE] Shipped (2026-10-24).** It works.\n\n"
            "Body.\n",
            result.completed,
        )

    def test_rule_and_next_phase_heading_stay_in_plan(self) -> None:
        # PERF-02's hand move dropped the --- before the next ## heading.
        result = _move(task_id="TOOL-01")
        self.assertIn(
            "TOOL-01 (script the move) is **[DONE]**; see `PLAN-completed.md`.\n\n"
            "---\n\n## Sequencing Summary\n",
            result.plan,
        )
        self.assertNotIn("---\n\n---", result.completed.split("## Phase 6")[0])

    def test_level_four_task_stops_at_the_next_level_four_heading(self) -> None:
        plan = _PLAN.replace(
            "### TOOL-01 · Script the move\n\nBody.\n",
            "### ANG-06 · Umbrella\n\n#### ANG-06a · First\n\nA.\n\n"
            "#### ANG-06b · Second\n\nB.\n",
        )
        result = _move(plan=plan, task_id="ANG-06a")
        self.assertIn(
            "ANG-06a (first) is **[DONE]**; see `PLAN-completed.md`.\n\n"
            "#### ANG-06b · Second\n\nB.\n",
            result.plan,
        )
        self.assertIn("A.\n", result.completed)
        self.assertNotIn("B.\n", result.completed)

    def test_missing_phase_heading_becomes_a_new_block_at_the_end(self) -> None:
        plan = _PLAN.replace("## Found Issues", "## Phase 9 — New")
        result = _move(plan=plan)
        self.assertTrue(
            result.completed.endswith(
                "### X-01 · [DONE] Done thing\n\n---\n\n## Phase 9 — New\n\n"
                "### TEST-01 · [DONE] Keep media writes out\n\nSpec: #342\n\n"
                "**[DONE] Shipped (2026-10-24).** It works.\n\n"
                "**Prio: Low.** Body.\n\n---\n"
            ),
            result.completed,
        )


def _summary(*bullets: str) -> str:
    """``_PLAN`` with its Sequencing Summary bullets replaced."""
    return _PLAN.replace(
        "- **Other:** TEST-01, TOOL-01, LG-06.\n", "".join(f"{b}\n" for b in bullets)
    )


class SequencingSummaryTests(TestCase):
    def test_first_item_is_dropped(self) -> None:
        result = _move()
        self.assertIn("- **Other:** TOOL-01, LG-06.\n", result.plan)
        self.assertEqual(result.warnings, [])

    def test_last_item_is_dropped_keeping_the_full_stop(self) -> None:
        plan = _summary("- **Other:** LG-06, TEST-01.")
        self.assertIn("- **Other:** LG-06.\n", _move(plan=plan).plan)

    def test_middle_item_is_dropped(self) -> None:
        plan = _summary("- **Other:** LG-06, TEST-01, SIM-04")
        self.assertIn("- **Other:** LG-06, SIM-04\n", _move(plan=plan).plan)

    def test_emptied_bullet_is_removed(self) -> None:
        plan = _summary("- **Phase 4:** RV-04.", "- **Other:** TEST-01.")
        result = _move(plan=plan)
        self.assertIn("- **Phase 4:** RV-04.\n\n---", result.plan)
        self.assertNotIn("**Other:**", result.plan)

    def test_longer_id_with_same_prefix_is_kept(self) -> None:
        plan = _summary("- **Other:** TEST-01b, TEST-01, LG-06.")
        self.assertIn("- **Other:** TEST-01b, LG-06.\n", _move(plan=plan).plan)

    def test_longer_id_earlier_in_the_list_is_kept(self) -> None:
        plan = _summary("- **Other:** LG-06, TEST-01b, TEST-01.")
        self.assertIn("- **Other:** LG-06, TEST-01b.\n", _move(plan=plan).plan)

    def test_other_mention_is_left_alone_with_a_warning(self) -> None:
        line = "- **Mechanics:** TEST-01..TEST-05, then MECH-07"
        result = _move(plan=_summary(line))
        self.assertIn(line + "\n", result.plan)
        self.assertEqual(len(result.warnings), 1)
        self.assertIn(line, result.warnings[0])

    def test_mentions_outside_the_summary_are_untouched(self) -> None:
        plan = _PLAN.replace("Intro.", "Intro: TEST-01, LG-06.")
        result = _move(plan=plan)
        self.assertIn("Intro: TEST-01, LG-06.", result.plan)
        self.assertEqual(result.warnings, [])


class FailureTests(TestCase):
    def assert_refused(self, plan: str, task_id: str, reason: str) -> None:
        with self.assertRaisesRegex(move_done_task.MoveError, reason):
            _move(plan=plan, task_id=task_id)

    def test_unknown_id(self) -> None:
        self.assert_refused(_PLAN, "NOPE-01", "not found")

    def test_id_with_a_longer_sibling_only_is_unknown(self) -> None:
        self.assert_refused(_PLAN, "TEST-0", "not found")

    def test_id_matching_two_headings(self) -> None:
        plan = _PLAN.replace("### TOOL-01 · Script", "### TEST-01 · Script")
        self.assert_refused(plan, "TEST-01", "2 headings")

    def test_already_done(self) -> None:
        plan = _PLAN.replace("TEST-01 · Keep", "TEST-01 · [DONE] Keep")
        self.assert_refused(plan, "TEST-01", "already")

    def test_no_enclosing_phase_heading(self) -> None:
        plan = _PLAN.replace("## Found Issues\n", "")
        self.assert_refused(plan, "TEST-01", "no enclosing")


class MainTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.plan = self.root / "PLAN.md"
        self.completed = self.root / "PLAN-completed.md"
        self.plan.write_bytes(_PLAN.replace("\n", "\r\n").encode("utf-8"))
        self.completed.write_bytes(_COMPLETED.encode("utf-8"))

    def _main(self, *argv: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            move_done_task.main(list(argv), root=self.root)
        return out.getvalue()

    def test_writes_both_files_keeping_line_endings_without_a_bom(self) -> None:
        printed = self._main("TEST-01", "It works.", "--date", "2026-10-24")
        plan = self.plan.read_bytes()
        completed = self.completed.read_bytes()
        self.assertFalse(plan.startswith(codecs.BOM_UTF8))
        self.assertFalse(completed.startswith(codecs.BOM_UTF8))
        self.assertEqual(plan.count(b"\n"), plan.count(b"\r\n"))
        self.assertNotIn(b"\r\n", completed)
        self.assertIn("**[DONE] Shipped (2026-10-24).** It works.", completed.decode())
        self.assertIn(b"TEST-01 (keep media writes out) is **[DONE]**", plan)
        self.assertTrue(printed.isascii())
        self.assertIn("TEST-01", printed)

    def test_refused_move_exits_1_and_writes_nothing(self) -> None:
        before = (self.plan.read_bytes(), self.completed.read_bytes())
        with self.assertRaises(SystemExit) as caught:
            self._main("NOPE-01", "It works.")
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual((self.plan.read_bytes(), self.completed.read_bytes()), before)
