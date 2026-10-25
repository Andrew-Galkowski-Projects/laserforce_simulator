"""Tests for the repo-level ``scripts/compare_junit.py``.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "compare_junit.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("compare_junit", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare_junit = _load_script()


def _junit(*cases: tuple[str, float]) -> str:
    body = "".join(
        f'<testcase classname="{cls}" name="t{i}" time="{t}"/>'
        for i, (cls, t) in enumerate(cases)
    )
    return f"<testsuites><testsuite>{body}</testsuite></testsuites>"


class PerFileTests(TestCase):
    def test_sums_test_time_per_module(self) -> None:
        xml = _junit(
            ("accounts.tests.test_api.MeTests", 1.5),
            ("accounts.tests.test_api.TokenTests", 2.0),
            ("matches.tests.test_batch_sim.BatchTests", 4.0),
        )
        self.assertEqual(
            compare_junit.per_file(xml),
            {"accounts.tests.test_api": 3.5, "matches.tests.test_batch_sim": 4.0},
        )

    def test_module_level_test_function_keys_on_its_module(self) -> None:
        # pytest gives a plain function the module as its classname.
        xml = _junit(("core.test_maps", 0.5), ("core.test_maps.MapTests", 1.0))
        self.assertEqual(compare_junit.per_file(xml), {"core.test_maps": 1.5})


class ReportTests(TestCase):
    def test_biggest_saving_first_and_totals_line(self) -> None:
        base = {"a": 10.0, "b": 5.0, "c": 4.0}
        after = {"a": 2.0, "b": 5.5, "c": 4.0}
        lines = compare_junit.report(base, after, top=10).splitlines()
        self.assertEqual(lines[0], "TOTAL base=19.0s after=11.5s (-39%)")
        self.assertEqual(lines[1].split()[-1], "a")
        self.assertEqual(lines[-1].split()[-1], "b")

    def test_a_file_missing_from_after_counts_as_zero(self) -> None:
        out = compare_junit.report({"gone": 3.0}, {}, top=10)
        self.assertIn("gone", out)
        self.assertIn("TOTAL base=3.0s after=0.0s", out)
