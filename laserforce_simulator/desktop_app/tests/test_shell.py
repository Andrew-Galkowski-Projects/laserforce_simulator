"""DESK-03: the App window shell (``desktop_app/shell.py``).

CI's Python jobs run on Linux, where pywebview has no WinForms backend, so a
fake ``webview`` package in ``sys.modules`` stands in for it.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from desktop_app import shell


class WindowOk:
    """A ``window_ok`` that returns ``result`` (or raises it) and counts calls."""

    def __init__(self, result: bool | Exception) -> None:
        self.result = result
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_the_window_title_is_the_app_name() -> None:
    assert shell.WINDOW_TITLE == "Laserforce Simulator"


def test_no_browser_gives_none_without_asking_for_a_window() -> None:
    window_ok = WindowOk(True)
    assert shell.choose_shell(["main.py", "--no-browser"], window_ok) == "none"
    assert window_ok.calls == 0


def test_no_browser_beats_browser() -> None:
    window_ok = WindowOk(True)
    assert (
        shell.choose_shell(["main.py", "--browser", "--no-browser"], window_ok)
        == "none"
    )
    assert window_ok.calls == 0


def test_browser_gives_browser_without_asking_for_a_window() -> None:
    window_ok = WindowOk(True)
    assert shell.choose_shell(["main.py", "--browser"], window_ok) == "browser"
    assert window_ok.calls == 0


def test_no_flag_gives_the_window_when_it_is_available() -> None:
    window_ok = WindowOk(True)
    assert shell.choose_shell(["main.py"], window_ok) == "window"
    assert window_ok.calls == 1


def test_no_flag_falls_back_to_the_browser_when_no_window_is_available() -> None:
    assert shell.choose_shell(["main.py"], WindowOk(False)) == "browser"


def test_a_window_check_that_raises_falls_back_to_the_browser_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="desktop_app.shell"):
        result = shell.choose_shell(["main.py"], WindowOk(RuntimeError("no clr")))

    assert result == "browser"
    assert "no clr" in caplog.text


class _BrokenPlatforms(ModuleType):
    """``webview.platforms`` whose ``winforms`` fails as pythonnet can."""

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError("Failed to resolve Python.Runtime.Loader.Initialize")


def _install_webview(
    monkeypatch: pytest.MonkeyPatch, *, is_chromium: bool | None
) -> ModuleType:
    """Put a fake ``webview`` package in ``sys.modules``.

    ``is_chromium=None`` leaves ``webview.platforms.winforms`` unimportable
    through a non-``ImportError`` failure.
    """
    webview = ModuleType("webview")
    if is_chromium is None:
        platforms: ModuleType = _BrokenPlatforms("webview.platforms")
    else:
        platforms = ModuleType("webview.platforms")
        winforms = ModuleType("webview.platforms.winforms")
        winforms.is_chromium = is_chromium  # type: ignore[attr-defined]
        platforms.winforms = winforms  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "webview.platforms.winforms", winforms)
    webview.platforms = platforms  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setitem(sys.modules, "webview.platforms", platforms)
    return webview


def test_the_window_is_available_on_webview2(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_webview(monkeypatch, is_chromium=True)
    assert shell.window_available() is True


def test_the_window_is_not_available_on_mshtml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No WebView2 runtime: pywebview silently falls back to Internet Explorer,
    # which cannot run the SPA.
    _install_webview(monkeypatch, is_chromium=False)
    assert shell.window_available() is False


def test_the_window_is_not_available_without_pywebview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "webview", None)
    assert shell.window_available() is False


def test_the_window_is_not_available_when_the_backend_fails_to_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_webview(monkeypatch, is_chromium=None)
    assert shell.window_available() is False


class FakeWindow:
    """A pywebview window whose page text runs through ``texts`` in turn.

    An ``Exception`` in ``texts`` is raised by that ``evaluate_js`` call, as
    pywebview does while ``document.body`` is still null.
    """

    def __init__(self, texts: list[str | Exception]) -> None:
        self.texts = texts
        self.scripts: list[str] = []
        self.destroyed = False
        # pywebview's ``Event`` has the same ``wait`` / ``set`` / ``is_set``.
        self.events = SimpleNamespace(loaded=threading.Event())

    def evaluate_js(self, script: str) -> str:
        self.scripts.append(script)
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        if isinstance(text, Exception):
            raise text
        return text

    def destroy(self) -> None:
        self.destroyed = True


class FakeWebview(ModuleType):
    """The slice of pywebview's module API that ``run_window`` uses.

    ``start`` is how WebView2 comes up: ``"load"`` sets ``loaded`` (a healthy
    window), ``"hang"`` does nothing (BUG-WV-01's CI case), and any other string
    is logged on the ``pywebview`` logger, as ``edgechromium.on_webview_ready``
    logs an immediate failure. Then it runs ``func`` the way pywebview does.
    """

    def __init__(self, window: FakeWindow, start_as: str = "load") -> None:
        super().__init__("webview")
        self.window = window
        self.start_as = start_as
        self.settings: dict[str, Any] = {"ALLOW_DOWNLOADS": False}
        self.events: list[tuple[str, Any]] = []

    def create_window(self, title: str, url: str, **kwargs: Any) -> FakeWindow:
        self.events.append(("create_window", (title, url, kwargs)))
        return self.window

    def start(self, func: Callable[..., None] | None = None, args: Any = None) -> None:
        self.events.append(("start", dict(self.settings)))
        if self.start_as == "load":
            self.window.events.loaded.set()
        elif self.start_as != "hang":
            logging.getLogger("pywebview").error(self.start_as)
        if func is not None:
            func(*(args if isinstance(args, (tuple, list)) else (args,)))


@pytest.fixture
def fast_selftest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shell, "SELFTEST_POLL_S", 0.001)
    monkeypatch.setattr(shell, "SELFTEST_LIMIT_S", 0.05)


def _fake_webview(
    monkeypatch: pytest.MonkeyPatch, window: FakeWindow, start_as: str = "load"
) -> FakeWebview:
    webview = FakeWebview(window, start_as)
    monkeypatch.setitem(sys.modules, "webview", webview)
    return webview


def test_run_window_allows_downloads_before_it_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webview = _fake_webview(monkeypatch, FakeWindow(["Sandbox"]))

    shell.run_window("http://127.0.0.1:1234/login")

    [start] = [value for name, value in webview.events if name == "start"]
    assert start["ALLOW_DOWNLOADS"] is True


def test_run_window_opens_the_titled_window_at_1280_by_800(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webview = _fake_webview(monkeypatch, FakeWindow(["Sandbox"]))

    shell.run_window("http://127.0.0.1:1234/login")

    assert [name for name, _ in webview.events] == ["create_window", "start"]
    assert webview.events[0][1] == (
        "Laserforce Simulator",
        "http://127.0.0.1:1234/login",
        {"width": 1280, "height": 800},
    )
    assert webview.window.scripts == []  # no self-test without the flag
    assert not webview.window.destroyed


def test_the_selftest_reports_ok_once_the_landing_page_shows(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fast_selftest: None,
) -> None:
    window = FakeWindow(
        [RuntimeError("document.body is null"), "Loading", "Pick a Sandbox league"]
    )
    _fake_webview(monkeypatch, window)

    shell.run_window("http://127.0.0.1:1234/login", selftest=True)

    out = capsys.readouterr().out
    assert re.fullmatch(r"LF_WINDOW_OK \d+\.\d\d\n", out)
    assert window.scripts == ["document.body.innerText"] * 3
    assert window.destroyed


def test_the_selftest_reports_fail_with_the_page_text_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    fast_selftest: None,
) -> None:
    window = FakeWindow(["x" * 300])
    _fake_webview(monkeypatch, window)

    shell.run_window("http://127.0.0.1:1234/login", selftest=True)

    assert capsys.readouterr().out == "LF_WINDOW_FAIL " + "x" * 200 + "\n"
    assert len(window.scripts) > 1
    assert window.destroyed


# BUG-WV-01: pywebview never raises when WebView2 fails to start. It either
# logs one ERROR at once (E_UNEXPECTED) or hangs with nothing logged and no
# ``loaded`` (E_ABORT, the CI case) until the window is destroyed.

INIT_ERROR = (
    "WebView2 initialization failed with exception:\n"
    "  (0x8000FFFF): Catastrophic failure (E_UNEXPECTED)"
)


def test_an_immediate_init_failure_raises_at_once_and_closes_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell, "WINDOW_START_TIMEOUT_S", 5.0)
    window = FakeWindow(["Sandbox"])
    _fake_webview(monkeypatch, window, start_as=INIT_ERROR)
    started = time.monotonic()

    with pytest.raises(shell.WindowStartFailed) as failure:
        shell.run_window("http://127.0.0.1:1234/login")

    assert time.monotonic() - started < 2
    assert str(failure.value) == "WebView2 initialization failed with exception:"
    assert window.destroyed


def test_a_window_that_never_loads_raises_at_the_timeout_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell, "WINDOW_START_TIMEOUT_S", 0.2)
    window = FakeWindow(["Sandbox"])
    _fake_webview(monkeypatch, window, start_as="hang")
    # A load long after the timeout, so a watch with no timeout fails this
    # test instead of hanging it.
    late_load = threading.Timer(2.0, window.events.loaded.set)
    late_load.start()

    with pytest.raises(shell.WindowStartFailed) as failure:
        shell.run_window("http://127.0.0.1:1234/login", selftest=True)

    assert str(failure.value) == "the App window did not load within 0.2 s"
    assert window.destroyed
    assert window.scripts == []  # no self-test on a window that never loaded


def test_an_unrelated_pywebview_error_is_not_a_failed_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell, "WINDOW_START_TIMEOUT_S", 5.0)
    window = FakeWindow(["Sandbox"])
    webview = _fake_webview(monkeypatch, window, start_as="hang")

    def start(func: Callable[..., None] | None = None, args: Any = None) -> None:
        # The stray ERROR comes first and the load a little later, so the
        # start watch polls while the ERROR is on record.
        logging.getLogger("pywebview").error("Failed to delete user data folder")
        threading.Timer(0.3, window.events.loaded.set).start()
        FakeWebview.start(webview, func, args)

    monkeypatch.setattr(webview, "start", start)

    shell.run_window("http://127.0.0.1:1234/login")

    assert not window.destroyed


def test_the_pywebview_handler_is_removed_after_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shell, "WINDOW_START_TIMEOUT_S", 0.2)
    before = list(logging.getLogger("pywebview").handlers)
    _fake_webview(monkeypatch, FakeWindow(["Sandbox"]))
    shell.run_window("http://127.0.0.1:1234/login")
    assert logging.getLogger("pywebview").handlers == before

    _fake_webview(monkeypatch, FakeWindow(["Sandbox"]), start_as=INIT_ERROR)
    with pytest.raises(shell.WindowStartFailed):
        shell.run_window("http://127.0.0.1:1234/login")
    assert logging.getLogger("pywebview").handlers == before


def test_installed_pywebview_still_logs_the_init_failure_we_detect() -> None:
    # A pywebview upgrade that rewords this log would silently disable the
    # browser fallback. pywebview is installed in CI's Linux jobs too
    # (requirements.txt -> requirements-desktop.txt); read the source rather
    # than import the Windows backend.
    spec = importlib.util.find_spec("webview")
    assert spec is not None and spec.submodule_search_locations
    source = (
        Path(next(iter(spec.submodule_search_locations)))
        / "platforms"
        / "edgechromium.py"
    ).read_text(encoding="utf-8")

    assert f"'{shell.INIT_FAILED_PREFIX} with exception:" in source
    assert "logging.getLogger('pywebview')" in source
