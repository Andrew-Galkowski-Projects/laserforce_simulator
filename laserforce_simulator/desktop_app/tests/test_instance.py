"""DESK-03: one Desktop App per data folder (``desktop_app/instance.py``).

The lock tests run a second, real process on the same folder. On Linux (CI's
Python jobs) they cover the ``fcntl`` branch; on Windows, ``msvcrt``. CI's
frozen smoke ``instance`` step covers ``msvcrt`` in the build.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from desktop_app import instance

PROJECT = Path(__file__).resolve().parents[2]

# argv: <project dir> <data dir>. Prints whether it got the lock, then, on
# stdin "crash", dies without closing anything.
CHILD = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from desktop_app.instance import acquire_instance_lock
lock = acquire_instance_lock(Path(sys.argv[2]))
print("LOCKED" if lock is not None else "BUSY", flush=True)
if sys.stdin.readline().strip() == "crash":
    os._exit(1)
"""


def _child(data: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(PROJECT), str(data)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )


def _try_in_child(data: Path) -> str:
    child = _child(data)
    out, _ = child.communicate("exit\n", timeout=30)
    return out.strip()


def test_the_lock_is_a_file_in_the_data_folder(tmp_path: Path) -> None:
    lock = instance.acquire_instance_lock(tmp_path)
    try:
        assert lock is not None
        assert (tmp_path / "instance.lock").is_file()
    finally:
        assert lock is not None
        lock.close()


def test_a_second_process_is_refused_while_the_lock_is_held(tmp_path: Path) -> None:
    lock = instance.acquire_instance_lock(tmp_path)
    assert lock is not None
    try:
        assert _try_in_child(tmp_path) == "BUSY"
    finally:
        lock.close()

    assert _try_in_child(tmp_path) == "LOCKED"


def test_a_second_acquire_in_this_process_is_refused_and_closes_its_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = []
    real_open = open

    def recording_open(*args: object, **kwargs: object) -> object:
        handle = real_open(*args, **kwargs)  # type: ignore[call-overload]
        opened.append(handle)
        return handle

    held = instance.acquire_instance_lock(tmp_path)
    assert held is not None
    try:
        monkeypatch.setattr("builtins.open", recording_open)
        assert instance.acquire_instance_lock(tmp_path) is None
        monkeypatch.undo()
        [refused] = opened
        assert refused.closed
    finally:
        held.close()


def test_a_crashed_holder_frees_the_lock(tmp_path: Path) -> None:
    child = _child(tmp_path)
    assert child.stdout is not None and child.stdin is not None
    assert child.stdout.readline().strip() == "LOCKED"
    assert instance.acquire_instance_lock(tmp_path) is None

    child.stdin.write("crash\n")
    child.stdin.flush()
    assert child.wait(timeout=30) == 1

    lock = instance.acquire_instance_lock(tmp_path)
    assert lock is not None
    lock.close()


def test_focus_window_finds_nothing_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert instance.focus_window("Laserforce Simulator") is False


def test_message_box_prints_to_stderr_off_windows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    instance.message_box("Laserforce Simulator is already running.")
    assert capsys.readouterr().err == "Laserforce Simulator is already running.\n"
