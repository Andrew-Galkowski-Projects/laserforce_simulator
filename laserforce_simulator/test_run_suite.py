"""Tests for the repo-level ``scripts/run_suite.py``.

The script lives outside the Django project, so it is loaded by path. Only the
pure output filter is tested; the pytest subprocess is a thin wrapper.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_suite.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_suite", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_suite = _load_script()

_GREEN = """\
bringing up nodes...
bringing up nodes...

........................................................................ [  0%]
.....,,,..,,s,.x........................................................ [ 50%]
..............................,,,,,,                                     [100%]
============================== warnings summary ===============================
core/test_map_services.py: 80 warnings
  C:\\x\\map_processing.py:108: DeprecationWarning: getdata is deprecated
    bw_pixels = list(cell_bw.getdata())

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
8525 passed, 3 skipped, 1 xfailed, 294 warnings, 1259 subtests passed in 84.60s
"""

_RED = """\
.....F..                                                                 [100%]
================================== FAILURES ===================================
____________________________ TestX.test_y _____________________________________
E       AssertionError: 'pbkdf2_sha256' != 'md5'
=========================== short test summary info ===========================
FAILED app/tests/test_x.py::TestX::test_y
1 failed, 7 passed in 3.45s
"""


class CondenseTests(TestCase):
    def test_green_run_keeps_only_the_summary_line(self) -> None:
        self.assertEqual(
            run_suite.condense(_GREEN),
            "8525 passed, 3 skipped, 1 xfailed, 294 warnings, 1259 subtests "
            "passed in 84.60s",
        )

    def test_red_run_keeps_failures_and_short_summary(self) -> None:
        out = run_suite.condense(_RED)
        self.assertIn("E       AssertionError: 'pbkdf2_sha256' != 'md5'", out)
        self.assertIn("FAILED app/tests/test_x.py::TestX::test_y", out)
        self.assertTrue(out.endswith("1 failed, 7 passed in 3.45s"))
        self.assertNotIn(".....F..", out)

    def test_progress_line_without_percent_is_dropped(self) -> None:
        # Classic style (-o console_output_style=classic) prints no [ nn%].
        self.assertEqual(
            run_suite.condense("....,,s.x\n3 passed in 1s\n"), "3 passed in 1s"
        )
