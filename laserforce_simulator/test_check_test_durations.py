"""Tests for the repo-level ``scripts/check_test_durations.py`` (MECH-23 retro).

The script lives outside the Django project, so it is loaded by path. The
pytest collection itself is a thin subprocess call and is not run here.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_test_durations.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_test_durations", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_script()

_COLLECTED = chr(0xFEFF) + """\
matches/tests/test_a.py::TestA::test_kept
matches/tests/test_a.py::TestA::test_param[1]
test_top.py::test_plain

3 tests collected in 0.10s
"""


class ParseCollectedTests(TestCase):
    def test_reads_each_id_and_skips_the_count_line_and_bom(self) -> None:
        self.assertEqual(
            check.parse_collected(_COLLECTED),
            {
                "matches/tests/test_a.py::TestA::test_kept",
                "matches/tests/test_a.py::TestA::test_param[1]",
                "test_top.py::test_plain",
            },
        )


class StaleEntriesTests(TestCase):
    def test_a_collected_id_is_not_stale(self) -> None:
        durations = {"matches/tests/test_a.py::TestA::test_kept": 0.1}
        collected = {"matches/tests/test_a.py::TestA::test_kept"}
        self.assertEqual(check.stale_entries(durations, collected), [])

    def test_a_deleted_test_is_stale(self) -> None:
        durations = {
            "matches/tests/test_a.py::TestA::test_kept": 0.1,
            "matches/tests/test_a.py::TestA::test_gone": 0.2,
        }
        collected = {"matches/tests/test_a.py::TestA::test_kept"}
        self.assertEqual(
            check.stale_entries(durations, collected),
            ["matches/tests/test_a.py::TestA::test_gone"],
        )

    def test_the_hint_says_stale_ids_wait_for_a_shard_balance_warning(self) -> None:
        # MECH-24 retro: the old hint read as "refresh now", and the refresh
        # commit landed after the PR's last push and missed the squash merge.
        self.assertIn("shard balance", check.REFRESH_HINT)
        self.assertIn("refresh_test_durations.py", check.REFRESH_HINT)

    def test_a_dropped_parameter_is_stale(self) -> None:
        durations = {"t.py::test_p[1]": 0.1, "t.py::test_p[2]": 0.1}
        self.assertEqual(
            check.stale_entries(durations, {"t.py::test_p[1]"}), ["t.py::test_p[2]"]
        )
