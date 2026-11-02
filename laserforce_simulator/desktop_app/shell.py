"""DESK-03: the Desktop App's shell, the **App window** (GLOSSARY).

The launcher asks ``choose_shell`` whether to open the App window (pywebview
over WebView2), the default browser, or nothing (``--no-browser``, headless).
``webview`` is imported inside the functions only: the Hosted deploy and CI's
Linux jobs never need its WinForms backend.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Literal

WINDOW_TITLE = "Laserforce Simulator"

# ``--selftest-window``: the landing card's text
# (``frontend/src/app/landing/landing.html``), polled every 0.5 s for 60 s.
LANDING_TEXT = "Sandbox"
SELFTEST_POLL_S = 0.5
SELFTEST_LIMIT_S = 60.0
# The ``LF_WINDOW_OK`` figure counts from here: the launcher imports this
# module first thing, so it stands in for the process start.
PROCESS_START = time.monotonic()

# BUG-WV-01: pywebview never raises when WebView2 fails to start. It logs this
# ERROR on the ``pywebview`` logger at once (``edgechromium.on_webview_ready``),
# or hangs with no ``loaded`` until the window is destroyed. A healthy window
# loads in about 0.9 s.
INIT_FAILED_PREFIX = "WebView2 initialization failed"
WINDOW_START_TIMEOUT_S = 20.0
_START_POLL_S = 0.1

logger = logging.getLogger(__name__)


class WindowStartFailed(RuntimeError):
    """The App window could not start; the message says why."""


class _InitFailureWatch(logging.Handler):
    """Notes pywebview's WebView2 initialisation ERROR.

    It runs on the GUI thread, so it only records: the start watch closes the
    window.
    """

    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.failed = threading.Event()
        self.reason = ""

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if message.startswith(INIT_FAILED_PREFIX) and not self.failed.is_set():
            self.reason = message.splitlines()[0]
            self.failed.set()


def choose_shell(
    argv: list[str], window_ok: Callable[[], bool]
) -> Literal["window", "browser", "none"]:
    """``--no-browser`` beats ``--browser``; else the window when ``window_ok()``.

    ``window_ok`` is asked only when no flag decides. If it raises, the
    browser is the fallback and the exception is logged.
    """
    if "--no-browser" in argv:
        return "none"
    if "--browser" in argv:
        return "browser"
    try:
        ok = window_ok()
    except Exception:  # noqa: BLE001 - any failed check falls back
        logger.exception("The App window check failed; opening the browser.")
        return "browser"
    return "window" if ok else "browser"


def window_available() -> bool:
    """Whether pywebview loads **and** renders with WebView2.

    With no WebView2 runtime, pywebview silently falls back to MSHTML
    (Internet Explorer), which cannot run the SPA, so ``is_chromium`` must be
    true. Any failure to import (no pywebview, no pythonnet) is ``False``.
    """
    try:
        import webview  # noqa: F401
        from webview.platforms import winforms
    except Exception:  # noqa: BLE001 - pythonnet fails with non-ImportErrors
        logger.info("pywebview is unavailable.", exc_info=True)
        return False
    return bool(getattr(winforms, "is_chromium", False))


def run_window(url: str, *, selftest: bool = False) -> None:
    """Open the App window on ``url`` and block until it closes.

    Downloads are allowed (a Save dialog; pywebview cancels them by default).
    With ``selftest``, the window reports ``LF_WINDOW_OK <s>`` once the landing
    page shows, or ``LF_WINDOW_FAIL <text>`` on timeout, then closes itself.

    Raises ``WindowStartFailed`` when WebView2 fails to start (BUG-WV-01): its
    initialisation ERROR, or no ``loaded`` within ``WINDOW_START_TIMEOUT_S``.
    The empty window is closed first.
    """
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(WINDOW_TITLE, url, width=1280, height=800)
    watch = _InitFailureWatch()
    failure: list[str] = []
    pywebview_logger = logging.getLogger("pywebview")
    pywebview_logger.addHandler(watch)
    try:
        webview.start(_watch_start, (window, watch, failure, selftest))
    finally:
        pywebview_logger.removeHandler(watch)
    if failure:
        raise WindowStartFailed(failure[0])


def _watch_start(
    window: Any, watch: _InitFailureWatch, failure: list[str], selftest: bool
) -> None:
    """Wait for the first load; on a failed start record why and close."""
    deadline = time.monotonic() + WINDOW_START_TIMEOUT_S
    while not window.events.loaded.wait(_START_POLL_S):
        if watch.failed.is_set():
            failure.append(watch.reason)
            break
        if time.monotonic() >= deadline:
            failure.append(
                f"the App window did not load within {WINDOW_START_TIMEOUT_S:g} s"
            )
            break
    if failure:
        window.destroy()
    elif selftest:
        _selftest(window)


def _selftest(window: Any) -> None:
    """Poll the page text until the landing card shows; always destroy."""
    deadline = time.monotonic() + SELFTEST_LIMIT_S
    try:
        while True:
            try:
                text = window.evaluate_js("document.body.innerText") or ""
            except Exception:  # noqa: BLE001 - null body while loading
                text = ""
            if LANDING_TEXT in text:
                print(
                    f"LF_WINDOW_OK {time.monotonic() - PROCESS_START:.2f}", flush=True
                )
                return
            if time.monotonic() >= deadline:
                print(f"LF_WINDOW_FAIL {text[:200]}", flush=True)
                return
            time.sleep(SELFTEST_POLL_S)
    finally:
        window.destroy()
