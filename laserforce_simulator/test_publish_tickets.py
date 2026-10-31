"""Tests for the repo-level ``scripts/publish_tickets.py`` ticket publisher.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "publish_tickets.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("publish_tickets", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass looks its module up in sys.modules while building the class.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


publish_tickets = _load_script()
Ticket = publish_tickets.Ticket


def _tickets() -> list:
    return [
        Ticket(key="A", title="X-1a: Foundation", body_file="a.md", blocked_by=()),
        Ticket(key="B", title="X-1b: Screen", body_file="b.md", blocked_by=("A",)),
    ]


class PlanTests(TestCase):
    def test_fresh_run_creates_links_and_blocks_in_order(self) -> None:
        actions = publish_tickets.plan(
            _tickets(), existing={}, sub_issues=set(), edges={}
        )
        self.assertEqual(
            actions,
            [
                ("create", "A"),
                ("link", "A"),
                ("create", "B"),
                ("link", "B"),
                ("block", "B", "A"),
            ],
        )

    def test_rerun_after_a_partial_failure_only_does_what_is_missing(self) -> None:
        """ANG-06-18 retro: the first run created #445 and linked it, then died."""
        actions = publish_tickets.plan(
            _tickets(),
            existing={"X-1a: Foundation": 445},
            sub_issues={445},
            edges={},
        )
        self.assertEqual(actions, [("create", "B"), ("link", "B"), ("block", "B", "A")])

    def test_a_complete_run_plans_nothing(self) -> None:
        actions = publish_tickets.plan(
            _tickets(),
            existing={"X-1a: Foundation": 445, "X-1b: Screen": 446},
            sub_issues={445, 446},
            edges={446: {445}},
        )
        self.assertEqual(actions, [])

    def test_an_existing_unlinked_issue_is_linked_not_recreated(self) -> None:
        actions = publish_tickets.plan(
            _tickets()[:1],
            existing={"X-1a: Foundation": 445},
            sub_issues=set(),
            edges={},
        )
        self.assertEqual(actions, [("link", "A")])


class ValidateTests(TestCase):
    def test_a_blocker_listed_later_is_refused(self) -> None:
        tickets = list(reversed(_tickets()))
        with self.assertRaisesRegex(ValueError, "B is blocked by A"):
            publish_tickets.validate(tickets)

    def test_an_unknown_blocker_is_refused(self) -> None:
        tickets = [Ticket(key="A", title="t", body_file="a.md", blocked_by=("Z",))]
        with self.assertRaisesRegex(ValueError, "A is blocked by Z"):
            publish_tickets.validate(tickets)

    def test_a_duplicate_key_is_refused(self) -> None:
        ticket = _tickets()[0]
        with self.assertRaisesRegex(ValueError, "duplicate key A"):
            publish_tickets.validate([ticket, ticket])

    def test_dependency_order_passes(self) -> None:
        publish_tickets.validate(_tickets())


class LoadTests(TestCase):
    def test_reads_the_json_with_body_files_relative_to_it(self) -> None:
        tickets = publish_tickets.load_tickets(
            [
                {"key": "A", "title": "t", "body_file": "a.md"},
                {"key": "B", "title": "u", "body_file": "b.md", "blocked_by": ["A"]},
            ],
            base=Path("C:/scratch"),
        )
        self.assertEqual(tickets[0].blocked_by, ())
        self.assertEqual(tickets[1].blocked_by, ("A",))
        self.assertEqual(Path(tickets[1].body_file), Path("C:/scratch/b.md"))
