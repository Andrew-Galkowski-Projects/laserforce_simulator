"""Tests for the repo-level ``scripts/check_unused_private.py`` dead-code check.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_unused_private.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_unused_private", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_unused_private = _load_script()


class PrivateDefsTests(TestCase):
    def test_lists_module_level_private_functions_and_classes(self) -> None:
        source = (
            "def _helper():\n    pass\n"
            "async def _fetch():\n    pass\n"
            "class _Row:\n    def _method(self):\n        pass\n"
            "def public():\n    def _inner():\n        pass\n"
            "def __dunder__():\n    pass\n"
        )
        self.assertEqual(
            check_unused_private.private_defs(source),
            [("_helper", 1), ("_fetch", 3), ("_Row", 5)],
        )


class UsedNamesTests(TestCase):
    def test_counts_calls_attributes_and_string_targets(self) -> None:
        source = (
            "x = _called()\n"
            "y = module._attr\n"
            "patch('matches.league_services._patched')\n"
        )
        used = check_unused_private.used_names(source)
        self.assertTrue({"_called", "_attr", "_patched"} <= used)

    def test_an_import_alone_is_not_a_use(self) -> None:
        source = (
            "from matches.league_services import (  # noqa: F401\n    _reexported,\n)\n"
        )
        self.assertNotIn("_reexported", check_unused_private.used_names(source))


class IsTestFileTests(TestCase):
    def test_recognises_test_modules_and_test_packages(self) -> None:
        for path in (
            "teams/tests/test_services.py",
            "matches/tests/conftest.py",
            "matches/tests/league_api_fixtures.py",
            "laserforce_simulator/test_mutate.py",
            "matches/views_tests.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(check_unused_private.is_test_file(path))

    def test_production_modules_are_not_tests(self) -> None:
        for path in ("teams/services.py", "matches/job_status.py", "scripts/mutate.py"):
            with self.subTest(path=path):
                self.assertFalse(check_unused_private.is_test_file(path))


class UnusedTests(TestCase):
    def test_reports_a_private_def_nothing_uses(self) -> None:
        defs = {"a.py": [("_dead", 3), ("_live", 9)]}
        self.assertEqual(
            check_unused_private.unused(defs, used={"_live"}, used_by_code={"_live"}),
            ["a.py:3: _dead has no references (dead code?)"],
        )

    def test_reports_nothing_when_every_def_is_used(self) -> None:
        defs = {"a.py": [("_live", 9)]}
        self.assertEqual(
            check_unused_private.unused(defs, used={"_live"}, used_by_code={"_live"}),
            [],
        )

    def test_a_production_def_only_tests_reference_is_dead(self) -> None:
        """ANG-08: ``_coerce_sort`` moved to ``teams.services`` with its tests
        and no caller, and the check stayed quiet."""
        defs = {"teams/services.py": [("_coerce_sort", 139)]}
        self.assertEqual(
            check_unused_private.unused(
                defs, used={"_coerce_sort"}, used_by_code=set()
            ),
            [
                "teams/services.py:139: _coerce_sort is referenced only by tests (dead code?)"
            ],
        )

    def test_a_test_helper_its_tests_use_is_live(self) -> None:
        defs = {"matches/tests/test_x.py": [("_make_row", 5)]}
        self.assertEqual(
            check_unused_private.unused(defs, used={"_make_row"}, used_by_code=set()),
            [],
        )
