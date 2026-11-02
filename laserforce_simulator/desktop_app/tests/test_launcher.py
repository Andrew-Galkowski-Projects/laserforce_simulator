"""DESK-01: the launcher's data folder, environment, upgrade reset and Local Account.

``migrate_or_reset`` takes the ``migrate`` callable, so these tests drive it
with a fake one over files in ``tmp_path``; nothing here touches a real
migration.
"""

from __future__ import annotations

import io
import os
import re
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from accounts.models import User
from desktop_app import instance, launcher

ENTRY = Path(__file__).resolve().parents[3] / "desktop" / "main.py"

ENV_KEYS = (
    "LF_DESKTOP_HOME",
    "LF_DESKTOP_SPA",
    "SECRET_KEY",
    "DATABASE_URL",
    "DJANGO_SETTINGS_MODULE",
    "LF_DESKTOP_BUILD_INFO",
)


class FakeMigrate:
    """Raises ``errors`` in turn, then succeeds; counts its calls."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Restore every variable ``prepare_environment`` writes."""
    for key in ENV_KEYS:
        monkeypatch.setenv(key, "inherited")
    return monkeypatch


def _db(data: Path, content: bytes = b"old league") -> Path:
    db = data / "db.sqlite3"
    db.write_bytes(content)
    return db


def test_migrate_success_returns_no_notice_and_keeps_the_database(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    migrate = FakeMigrate()
    assert launcher.migrate_or_reset(tmp_path, migrate) is None
    assert migrate.calls == 1
    assert db.read_bytes() == b"old league"
    assert list(tmp_path.glob("*.bak")) == []


def test_a_failed_upgrade_moves_the_database_aside_and_starts_fresh(
    tmp_path: Path,
) -> None:
    _db(tmp_path)
    (tmp_path / "db.sqlite3-wal").write_bytes(b"wal")
    (tmp_path / "db.sqlite3-shm").write_bytes(b"shm")
    migrate = FakeMigrate(RuntimeError("inconsistent history"))

    notice = launcher.migrate_or_reset(tmp_path, migrate)

    assert migrate.calls == 2
    [backup] = list(tmp_path.glob("db.sqlite3.*.bak"))
    assert re.fullmatch(r"db\.sqlite3\.\d{8}-\d{6}\.bak", backup.name)
    assert backup.read_bytes() == b"old league"
    assert not (tmp_path / "db.sqlite3-wal").exists()
    assert not (tmp_path / "db.sqlite3-shm").exists()
    assert notice == (
        "Your saved data could not be upgraded (RuntimeError). "
        f"It was moved to {backup.name} and a fresh database was started."
    )


def test_a_corrupt_database_left_open_by_the_failure_is_still_moved_aside(
    tmp_path: Path,
) -> None:
    # Found in the DESK-01 browser check: Django's SQLite connect() fails on a
    # file that is not a database after sqlite3 has opened it, and the open
    # handle lives on in the exception's frames. Windows refuses to move an
    # open file, so the reset must drop the failure before moving.
    db = _db(tmp_path, b"not a sqlite database" * 100)
    calls = []

    def migrate() -> None:
        calls.append(1)
        if len(calls) == 1:
            leaked = sqlite3.connect(db)
            leaked.execute("select 1 from sqlite_master")  # file is not a database

    notice = launcher.migrate_or_reset(tmp_path, migrate)

    assert len(calls) == 2
    assert notice is not None and "(DatabaseError)" in notice
    [backup] = list(tmp_path.glob("db.sqlite3.*.bak"))
    assert backup.read_bytes() == b"not a sqlite database" * 100


def test_a_failing_fresh_database_re_raises(tmp_path: Path) -> None:
    migrate = FakeMigrate(RuntimeError("broken build"))
    with pytest.raises(RuntimeError, match="broken build"):
        launcher.migrate_or_reset(tmp_path, migrate)
    assert migrate.calls == 1


def test_a_failing_empty_database_re_raises_and_is_left_alone(tmp_path: Path) -> None:
    db = _db(tmp_path, b"")
    migrate = FakeMigrate(RuntimeError("broken build"))
    with pytest.raises(RuntimeError):
        launcher.migrate_or_reset(tmp_path, migrate)
    assert migrate.calls == 1
    assert db.exists()
    assert list(tmp_path.glob("*.bak")) == []


def test_data_dir_honours_lf_desktop_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LF_DESKTOP_HOME", str(tmp_path))
    assert launcher.data_dir() == tmp_path


def test_data_dir_defaults_to_local_app_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LF_DESKTOP_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert launcher.data_dir() == tmp_path / "LaserforceSimulator"


def test_prepare_environment_points_django_at_the_data_folder(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    spa = tmp_path / "spa"
    launcher.prepare_environment(tmp_path, spa)

    assert (tmp_path / "logs").is_dir()
    assert os.environ["LF_DESKTOP_HOME"] == str(tmp_path)
    assert os.environ["LF_DESKTOP_SPA"] == str(spa)
    assert os.environ["DJANGO_SETTINGS_MODULE"] == "desktop_app.settings"
    assert os.environ["DATABASE_URL"] == (
        "sqlite:///" + (tmp_path / "db.sqlite3").as_posix()
    )
    assert os.environ["SECRET_KEY"] == (tmp_path / "secret_key").read_text()
    assert len(os.environ["SECRET_KEY"]) >= 50


def test_frozen_prepare_environment_points_at_the_bundled_build_info(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    env.setattr(launcher, "FROZEN", True)
    env.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    launcher.prepare_environment(tmp_path, tmp_path)
    assert os.environ["LF_DESKTOP_BUILD_INFO"] == str(
        tmp_path / "bundle" / "build_info.json"
    )


def test_unfrozen_prepare_environment_leaves_build_info_unset(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    # An inherited value must not survive: unfrozen is always a dev build.
    env.setattr(launcher, "FROZEN", False)
    launcher.prepare_environment(tmp_path, tmp_path)
    assert "LF_DESKTOP_BUILD_INFO" not in os.environ


def test_prepare_environment_keeps_the_secret_key_across_launches(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    launcher.prepare_environment(tmp_path, tmp_path)
    first = os.environ["SECRET_KEY"]
    launcher.prepare_environment(tmp_path, tmp_path)
    assert os.environ["SECRET_KEY"] == first


def test_quiet_std_streams_replaces_missing_streams_with_devnull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # PyInstaller --noconsole with no pipe (a double-click): both are None,
    # and Django's OutputWrapper or Celery would raise on .write.
    import os

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    launcher.quiet_std_streams()

    for stream in (sys.stdout, sys.stderr):
        assert stream is not None
        assert stream.name == os.devnull
        stream.write("ignored")  # does not raise
    sys.stdout.close()
    sys.stderr.close()


def test_quiet_std_streams_keeps_streams_that_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    launcher.quiet_std_streams()

    assert sys.stdout is out
    assert sys.stderr is err


def _raise_and_report(data: Path, *, headless: bool) -> None:
    try:
        raise RuntimeError("no such table: accounts_user")
    except RuntimeError:
        launcher.report_startup_failure(data, headless=headless)


@pytest.fixture
def boxes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every ``message_box`` text the launcher shows."""
    shown: list[str] = []
    monkeypatch.setattr(launcher, "message_box", shown.append)
    return shown


def test_a_startup_failure_is_appended_to_the_log_and_shown_in_a_box(
    tmp_path: Path, boxes: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "logs" / "desktop.log"
    log.parent.mkdir()
    log.write_text("earlier line\n", encoding="utf-8")

    _raise_and_report(tmp_path, headless=False)

    text = log.read_text(encoding="utf-8")
    assert text.startswith("earlier line\n")
    assert re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", text)
    assert "Traceback (most recent call last)" in text
    assert "RuntimeError: no such table: accounts_user" in text
    assert boxes == [f"Laserforce Simulator could not start. Details: {log}"]
    assert capsys.readouterr().err == ""


def test_a_headless_startup_failure_prints_the_traceback_and_shows_no_box(
    tmp_path: Path, boxes: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    _raise_and_report(tmp_path, headless=True)

    assert boxes == []
    err = capsys.readouterr().err
    assert "RuntimeError: no such table: accounts_user" in err
    log = tmp_path / "logs" / "desktop.log"
    assert "RuntimeError: no such table: accounts_user" in log.read_text(
        encoding="utf-8"
    )


@pytest.fixture
def home(
    env: pytest.MonkeyPatch, tmp_path: Path, boxes: list[str]
) -> Iterator[FakeMigrate]:
    """``main()`` on ``tmp_path`` with no Django setup and a counting ``migrate``.

    Yields the fake ``migrate``; closes any lock ``main()`` kept.
    """
    env.setenv("LF_DESKTOP_HOME", str(tmp_path))
    env.setattr("django.setup", lambda: None)
    migrate = FakeMigrate()
    env.setattr(launcher, "_migrate", migrate)
    env.setattr(launcher, "_instance_lock", None)
    yield migrate
    if launcher._instance_lock is not None:
        launcher._instance_lock.close()


def _main_exit_code(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setattr(sys, "argv", ["main.py", *argv])
    with pytest.raises(SystemExit) as caught:
        launcher.main()
    return caught.value.code


@pytest.fixture
def held_lock(tmp_path: Path) -> Iterator[None]:
    lock = instance.acquire_instance_lock(tmp_path)
    assert lock is not None
    yield
    lock.close()


@pytest.fixture
def focused(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Titles ``main()`` tried to focus; the fake finds a window."""
    titles: list[str] = []

    def focus(title: str) -> bool:
        titles.append(title)
        return True

    monkeypatch.setattr(launcher, "focus_window", focus)
    return titles


def test_a_headless_second_launch_prints_already_running_and_exits_0(
    home: FakeMigrate,
    held_lock: None,
    focused: list[str],
    boxes: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _main_exit_code(["--no-browser"], monkeypatch) == 0

    assert "LF_ALREADY_RUNNING" in capsys.readouterr().out
    assert home.calls == 0
    assert focused == []
    assert boxes == []


def test_a_second_launch_focuses_the_running_window_and_shows_no_box(
    home: FakeMigrate,
    held_lock: None,
    focused: list[str],
    boxes: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _main_exit_code([], monkeypatch) == 0

    assert focused == ["Laserforce Simulator"]
    assert boxes == []
    assert home.calls == 0


def test_a_second_launch_with_no_window_to_focus_shows_a_box(
    home: FakeMigrate,
    held_lock: None,
    boxes: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launcher, "focus_window", lambda title: False)

    assert _main_exit_code(["--browser"], monkeypatch) == 0

    assert boxes == ["Laserforce Simulator is already running."]
    assert home.calls == 0


def test_main_quiets_missing_std_streams_first(
    home: FakeMigrate,
    held_lock: None,
    focused: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    assert _main_exit_code([], monkeypatch) == 0

    assert sys.stdout is not None and sys.stdout.name == os.devnull
    assert sys.stderr is not None and sys.stderr.name == os.devnull
    sys.stdout.close()
    sys.stderr.close()


def test_main_exits_1_when_migrate_fails_on_an_empty_folder(
    home: FakeMigrate,
    tmp_path: Path,
    boxes: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home.errors.append(RuntimeError("broken build"))

    assert _main_exit_code([], monkeypatch) == 1

    assert home.calls == 1
    log = tmp_path / "logs" / "desktop.log"
    assert "RuntimeError: broken build" in log.read_text(encoding="utf-8")
    assert boxes == [f"Laserforce Simulator could not start. Details: {log}"]


def test_main_holds_the_instance_lock_while_it_runs(
    home: FakeMigrate, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Checked at migrate time: the lock is taken before migrate, and kept in
    # the module global (a dropped handle would release it).
    seen = []

    def migrate() -> None:
        seen.append(instance.acquire_instance_lock(tmp_path))
        raise RuntimeError("stop here")

    monkeypatch.setattr(launcher, "_migrate", migrate)

    assert _main_exit_code(["--no-browser"], monkeypatch) == 1

    assert seen == [None]
    assert launcher._instance_lock is not None


def test_a_real_startup_failure_exits_1_with_the_traceback(tmp_path: Path) -> None:
    # Not a fake: the data folder's secret_key is a directory, so reading it
    # fails inside the startup ``try``, before Django is set up.
    (tmp_path / "secret_key").mkdir()

    result = subprocess.run(
        [sys.executable, str(ENTRY), "--no-browser"],
        env={**os.environ, "LF_DESKTOP_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 1, result.stderr
    assert "Traceback (most recent call last)" in result.stderr
    assert "secret_key" in result.stderr
    log = (tmp_path / "logs" / "desktop.log").read_text(encoding="utf-8")
    assert "secret_key" in log
    assert "LF_READY" not in result.stdout


class FakeServer:
    """A waitress server whose ``run`` records the thread it ran on."""

    def __init__(self) -> None:
        self.ran = threading.Event()
        self.thread: threading.Thread | None = None

    def run(self) -> None:
        self.thread = threading.current_thread()
        self.ran.set()


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool, bool]]:
    """``run_window`` calls: (url, selftest, on the main thread)."""
    calls: list[tuple[str, bool, bool]] = []

    def run_window(url: str, *, selftest: bool = False) -> None:
        calls.append(
            (url, selftest, threading.current_thread() is threading.main_thread())
        )

    monkeypatch.setattr(launcher, "run_window", run_window)
    monkeypatch.setattr(launcher, "window_available", lambda: True)
    return calls


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """URLs the browser opened; the 0.2 s timer fires at once."""
    urls: list[str] = []

    class Timer:
        def __init__(self, delay: float, func: Callable[..., Any], args: Any) -> None:
            self.call = (func, args)

        def start(self) -> None:
            func, args = self.call
            func(*args)

    monkeypatch.setattr(launcher.threading, "Timer", Timer)
    monkeypatch.setattr(launcher.webbrowser, "open", urls.append)
    return urls


def test_the_window_shell_serves_on_a_daemon_thread_and_opens_the_window(
    windows: list[tuple[str, bool, bool]], opened: list[str]
) -> None:
    server = FakeServer()

    launcher._open_shell(server, "http://127.0.0.1:1/x", ["main.py"])

    assert server.ran.wait(5)
    assert server.thread is not None
    assert server.thread is not threading.main_thread()
    assert server.thread.daemon
    assert windows == [("http://127.0.0.1:1/x", False, True)]
    assert opened == []


def test_selftest_window_reaches_the_window(
    windows: list[tuple[str, bool, bool]], opened: list[str]
) -> None:
    launcher._open_shell(
        FakeServer(), "http://127.0.0.1:1/x", ["main.py", "--selftest-window"]
    )
    assert windows == [("http://127.0.0.1:1/x", True, True)]


def test_a_window_that_raises_falls_back_to_the_browser_and_keeps_serving(
    monkeypatch: pytest.MonkeyPatch,
    opened: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Grill decision 2: WebView2 failing at start-up (not only missing) still
    # leaves the person a working app in the default browser.
    def run_window(url: str, *, selftest: bool = False) -> None:
        raise OSError("WebView2 failed to start")

    monkeypatch.setattr(launcher, "run_window", run_window)
    monkeypatch.setattr(launcher, "window_available", lambda: True)
    server = FakeServer()

    launcher._open_shell(server, "http://127.0.0.1:1/x", ["main.py"])

    assert opened == ["http://127.0.0.1:1/x"]
    assert server.ran.is_set()
    assert "WebView2 failed to start" in caplog.text


def test_a_selftest_window_that_raises_fails_at_once_with_no_browser(
    monkeypatch: pytest.MonkeyPatch,
    opened: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The self-test must fail CI straight away, not open a browser and block
    # until the smoke's 90 s timeout.
    def run_window(url: str, *, selftest: bool = False) -> None:
        raise OSError("WebView2 failed to start")

    monkeypatch.setattr(launcher, "run_window", run_window)
    monkeypatch.setattr(launcher, "window_available", lambda: True)

    launcher._open_shell(
        FakeServer(), "http://127.0.0.1:1/x", ["main.py", "--selftest-window"]
    )

    assert opened == []
    assert "LF_WINDOW_FAIL OSError: WebView2 failed to start" in capsys.readouterr().out


def test_the_browser_shell_opens_the_browser_and_serves_on_the_main_thread(
    windows: list[tuple[str, bool, bool]], opened: list[str]
) -> None:
    server = FakeServer()

    launcher._open_shell(server, "http://127.0.0.1:1/x", ["main.py", "--browser"])

    assert opened == ["http://127.0.0.1:1/x"]
    assert server.thread is threading.main_thread()
    assert windows == []


def test_no_browser_only_serves(
    windows: list[tuple[str, bool, bool]], opened: list[str]
) -> None:
    server = FakeServer()

    launcher._open_shell(server, "http://127.0.0.1:1/x", ["main.py", "--no-browser"])

    assert server.thread is threading.main_thread()
    assert opened == []
    assert windows == []


@pytest.mark.django_db
def test_local_account_is_created_once_with_no_usable_password() -> None:
    first = launcher.local_account()
    assert first.email == "local@desktop.invalid"
    assert not first.has_usable_password()
    assert not first.is_staff
    assert not first.is_superuser
    assert launcher.local_account().pk == first.pk
    assert User.objects.filter(email="local@desktop.invalid").count() == 1
