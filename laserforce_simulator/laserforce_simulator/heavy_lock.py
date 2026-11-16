"""One heavy run at a time on this machine (MECH-26 retro).

The full suite, a Postgres suite and a 128-game ``round_probe`` each take most
of the CPU for 10-25 minutes. Run together they slow one another, and an edit
made during one of them reaches its workers but not its main process. A heavy
run holds a lockfile in the system temp folder, shared by every worktree; a
second one refuses and names the holder. A lock whose process is gone is taken
over.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

LOCK_PATH = Path(tempfile.gettempdir()) / "laserforce-heavy-run.lock"


class HeavyRunBusy(Exception):
    """Another heavy run holds the lock."""


def pid_alive(pid: int) -> bool:
    """Whether process ``pid`` is running. Never signals it: on Windows
    ``os.kill(pid, 0)`` terminates the process."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _holder(path: Path) -> dict | None:
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return held if isinstance(held, dict) and isinstance(held.get("pid"), int) else None


def _create(path: Path, label: str) -> bool:
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    record = {"pid": os.getpid(), "label": label, "started": datetime.now().isoformat()}
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(record, handle)
    return True


@contextmanager
def heavy_run(
    label: str,
    path: Path | None = None,
    pid_alive: Callable[[int], bool] = pid_alive,
) -> Iterator[None]:
    """Hold the heavy-run lock for the ``with`` block, or raise ``HeavyRunBusy``.

    ``path`` defaults to ``LOCK_PATH``, read at call time so a test can patch it.
    """
    path = LOCK_PATH if path is None else path
    if not _create(path, label):
        held = _holder(path)
        if held is not None and pid_alive(held["pid"]):
            raise HeavyRunBusy(
                f"Refused: another heavy run holds {path}: {held.get('label')} "
                f"(pid {held['pid']}, started {held.get('started')}). Wait for it "
                "to finish; delete the file only if that process is gone."
            )
        path.unlink(missing_ok=True)
        if not _create(path, label):
            raise HeavyRunBusy(f"Refused: another heavy run took {path} just now.")
    try:
        yield
    finally:
        held = _holder(path)
        if held is not None and held["pid"] == os.getpid():
            path.unlink(missing_ok=True)
