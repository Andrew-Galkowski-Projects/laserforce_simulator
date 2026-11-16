"""Tests for ``laserforce_simulator.heavy_lock`` (MECH-26 retro).

The Postgres suite, ``/verify`` and a 128-game probe overlapped and slowed one
another; the lock makes a second heavy run refuse while the first one holds it.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase

from laserforce_simulator.heavy_lock import HeavyRunBusy, heavy_run


class HeavyRunTests(TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "heavy.lock"

    def tearDown(self) -> None:
        self.dir.cleanup()

    def _hold(self, pid: int, label: str = "suite [sqlite @abc]") -> None:
        self.path.write_text(json.dumps({"pid": pid, "label": label, "started": "t"}))

    def test_holds_the_lock_and_releases_it(self) -> None:
        with heavy_run("suite", path=self.path):
            self.assertTrue(self.path.exists())
            held = json.loads(self.path.read_text())
            self.assertEqual(held["pid"], os.getpid())
            self.assertEqual(held["label"], "suite")
        self.assertFalse(self.path.exists())

    def test_releases_the_lock_when_the_run_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            with heavy_run("suite", path=self.path):
                raise RuntimeError("boom")
        self.assertFalse(self.path.exists())

    def test_a_live_holder_refuses_with_its_label(self) -> None:
        self._hold(pid=4242, label="round_probe --lineups test")
        with self.assertRaises(HeavyRunBusy) as caught:
            with heavy_run("suite", path=self.path, pid_alive=lambda pid: True):
                self.fail("ran while the lock was held")
        self.assertIn("round_probe --lineups test", str(caught.exception))
        self.assertIn("4242", str(caught.exception))
        self.assertTrue(self.path.exists())

    def test_a_dead_holders_lock_is_taken_over(self) -> None:
        self._hold(pid=4242)
        with heavy_run("suite", path=self.path, pid_alive=lambda pid: False):
            held = json.loads(self.path.read_text())
            self.assertEqual(held["pid"], os.getpid())
        self.assertFalse(self.path.exists())

    def test_an_unreadable_lock_is_taken_over(self) -> None:
        self.path.write_text("not json")
        with heavy_run("suite", path=self.path, pid_alive=lambda pid: True):
            self.assertEqual(json.loads(self.path.read_text())["label"], "suite")

    def test_the_real_pid_check_sees_this_process_alive(self) -> None:
        from laserforce_simulator.heavy_lock import pid_alive

        self.assertTrue(pid_alive(os.getpid()))
