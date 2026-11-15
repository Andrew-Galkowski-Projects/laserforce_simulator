"""A pytest run stops at once while ``scripts/mutate.py`` has a mutation on disk
in this checkout (MECH-25 retro: a parallel test's spawned workers imported the
mutated ``board_flip.py`` and read as serial != parallel)."""

import importlib.util
import tempfile
from pathlib import Path
from unittest import TestCase

from laserforce_simulator.testing import MUTATION_MARKER, mutation_in_progress

_MUTATE = Path(__file__).resolve().parents[3] / "scripts" / "mutate.py"


class TestMutationInProgress(TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_no_marker_lets_the_run_go(self) -> None:
        self.assertIsNone(mutation_in_progress(self.root, {}))

    def test_a_marker_stops_the_run_and_names_the_file(self) -> None:
        (self.root / MUTATION_MARKER).write_text("laserforce_simulator/a.py")
        message = mutation_in_progress(self.root, {})
        self.assertIsNotNone(message)
        self.assertIn("laserforce_simulator/a.py", message or "")
        self.assertIn("git diff", message or "")

    def test_mutate_py_own_runs_go_ahead(self) -> None:
        (self.root / MUTATION_MARKER).write_text("a.py")
        self.assertIsNone(mutation_in_progress(self.root, {"LF_MUTATE_RUN": "1"}))

    def test_the_marker_name_is_mutate_py_own(self) -> None:
        spec = importlib.util.spec_from_file_location("mutate", _MUTATE)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.MARKER, MUTATION_MARKER)
