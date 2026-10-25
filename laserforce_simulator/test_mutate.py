"""Tests for the repo-level ``scripts/mutate.py`` script.

The script lives outside the Django project, so it is loaded by path. Each
test runs real pytest against a tiny throwaway project in a temp dir.
"""

import contextlib
import importlib.util
import io
import json
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mutate.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mutate", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mutate = _load_script()

_CALC = b"def add(a, b):\r\n    return a + b\r\n"
_TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
_ARGS = ["-p", "no:cacheprovider", "-p", "no:django", "-q", "test_calc.py"]


class MutateTests(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.target = self.root / "calc.py"
        self.target.write_bytes(_CALC)
        (self.root / "test_calc.py").write_text(_TEST)

    def _mutate(self, *mutations: tuple[str, str, str]) -> list:
        return mutate.mutate(
            self.target,
            [mutate.Mutation(*m) for m in mutations],
            _ARGS,
            cwd=self.root,
        )

    def test_a_mutation_the_tests_catch_names_the_failing_test(self) -> None:
        [outcome] = self._mutate(("plus to minus", "a + b", "a - b"))
        self.assertEqual(outcome.status, "caught")
        self.assertEqual(outcome.failed, ["test_calc.py::test_add"])

    def test_a_mutation_the_tests_miss_survives(self) -> None:
        [outcome] = self._mutate(("no-op", "return a + b", "return (a + b)"))
        self.assertEqual(outcome.status, "survived")
        self.assertEqual(outcome.failed, [])

    def test_missing_and_ambiguous_patterns_are_reported_without_a_run(
        self,
    ) -> None:
        missing, ambiguous = self._mutate(("gone", "zzz", "y"), ("twice", "a", "c"))
        self.assertEqual(missing.status, "not found")
        self.assertEqual(ambiguous.status, "ambiguous")

    def test_target_is_restored_byte_for_byte(self) -> None:
        self._mutate(("plus to minus", "a + b", "a - b"), ("no-op", "b\r\n", "b \r\n"))
        self.assertEqual(self.target.read_bytes(), _CALC)

    def test_main_prints_each_outcome_and_exits_1_on_a_survivor(self) -> None:
        mutations = self.root / "mutations.json"
        mutations.write_text(
            json.dumps(
                [
                    ["plus to minus", "a + b", "a - b"],
                    ["no-op", "return a + b", "return (a + b)"],
                ]
            )
        )
        out = io.StringIO()
        with contextlib.chdir(self.root), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as caught:
                mutate.main([str(self.target), str(mutations), "--", *_ARGS])
        self.assertEqual(caught.exception.code, 1)
        printed = out.getvalue()
        self.assertTrue(printed.isascii())
        self.assertIn("caught    plus to minus: test_calc.py::test_add", printed)
        self.assertIn("SURVIVED  no-op", printed)

    def test_main_exits_0_when_every_mutation_is_caught(self) -> None:
        mutations = self.root / "mutations.json"
        mutations.write_text(json.dumps([["plus to minus", "a + b", "a - b"]]))
        with contextlib.chdir(self.root), contextlib.redirect_stdout(io.StringIO()):
            mutate.main([str(self.target), str(mutations), "--", *_ARGS])


class FailedTestsTests(TestCase):
    def test_reads_node_ids_from_the_summary_with_forward_slashes(self) -> None:
        output = (
            "..F\n"
            r"FAILED app\tests\test_x.py::T::test_a - AssertionError: 1 != 2"
            "\n"
            "FAILED app/test_y.py::test_b\n"
            "1 failed, 2 passed in 0.1s\n"
        )
        self.assertEqual(
            mutate.failed_tests(output),
            ["app/tests/test_x.py::T::test_a", "app/test_y.py::test_b"],
        )
