"""DESK-03: one Desktop App per data folder, and the Win32 calls it needs.

``acquire_instance_lock`` takes an OS file lock on ``<data>/instance.lock``.
The OS frees it when the holding process dies, crash included, so a stale
lock never blocks a launch. Nothing reads the file's contents.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO

from desktop_app.shell import WINDOW_TITLE

SW_RESTORE = 9
MB_OK = 0x0
MB_ICONINFORMATION = 0x40


def acquire_instance_lock(data: Path) -> IO[bytes] | None:
    """The open, locked ``instance.lock``, or ``None`` when another process holds it.

    Keep the returned file referenced for the life of the process: once it is
    closed or garbage-collected, the lock is released.
    """
    data.mkdir(parents=True, exist_ok=True)
    handle = open(data / "instance.lock", "a+b")
    try:
        _lock(handle)
    except OSError:
        handle.close()
        return None
    return handle


def _lock(handle: IO[bytes]) -> None:
    """A non-blocking exclusive lock; ``OSError`` when it is taken."""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)  # msvcrt locks from the current position
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def focus_window(title: str) -> bool:
    """Bring the top-level window titled ``title`` to the front.

    Returns whether one was found; always ``False`` off Windows.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def message_box(text: str) -> None:
    """An OK box titled ``WINDOW_TITLE`` on Windows; stderr elsewhere."""
    if sys.platform != "win32":
        print(text, file=sys.stderr)
        return
    import ctypes

    ctypes.windll.user32.MessageBoxW(
        None, text, WINDOW_TITLE, MB_OK | MB_ICONINFORMATION
    )
