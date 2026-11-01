"""Tests for the repo-level ``scripts/deleted_tests.py`` removed-test audit.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deleted_tests.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("deleted_tests", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deleted_tests = _load_script()


class TestIdsTests(TestCase):
    def test_lists_module_tests_and_class_test_methods(self) -> None:
        source = (
            "def test_top():\n    pass\n"
            "def helper():\n    pass\n"
            "class TestPlay(TestCase):\n"
            "    def setUp(self):\n        pass\n"
            "    def test_runs(self):\n        pass\n"
            "    async def test_async(self):\n        pass\n"
        )
        self.assertEqual(
            deleted_tests.test_ids(source),
            {"test_top", "TestPlay::test_runs", "TestPlay::test_async"},
        )

    def test_unparsable_source_has_no_tests(self) -> None:
        self.assertEqual(deleted_tests.test_ids("def broken(:\n"), set())


class IsTestModuleTests(TestCase):
    def test_test_modules_count_and_helpers_do_not(self) -> None:
        self.assertTrue(deleted_tests.is_test_module("matches/tests/test_api.py"))
        self.assertTrue(deleted_tests.is_test_module("matches/views_tests.py"))
        self.assertFalse(deleted_tests.is_test_module("matches/tests/conftest.py"))
        self.assertFalse(deleted_tests.is_test_module("matches/services.py"))


class RemovedTests(TestCase):
    def test_reports_tests_gone_from_head_with_their_file(self) -> None:
        base = {
            "m/views_tests.py": {"TestPlay::test_runs", "TestPlay::test_stops"},
            "m/tests/test_a.py": {"test_kept"},
        }
        head = {"m/tests/test_a.py": {"test_kept"}}
        self.assertEqual(
            deleted_tests.removed(base, head),
            [
                "m/views_tests.py::TestPlay::test_runs",
                "m/views_tests.py::TestPlay::test_stops",
            ],
        )

    def test_a_test_moved_to_another_file_names_its_new_home(self) -> None:
        base = {"m/old_tests.py": {"TestX::test_y"}}
        head = {"m/tests/test_new.py": {"TestX::test_y"}}
        self.assertEqual(
            deleted_tests.removed(base, head),
            ["m/old_tests.py::TestX::test_y  (moved: m/tests/test_new.py)"],
        )

    def test_nothing_removed_is_an_empty_report(self) -> None:
        base = {"m/tests/test_a.py": {"test_kept"}}
        self.assertEqual(deleted_tests.removed(base, dict(base)), [])
