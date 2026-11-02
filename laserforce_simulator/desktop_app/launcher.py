"""DESK-01 (ADR-0048): the Desktop App's launcher.

``desktop/main.py`` (the frozen entry point) calls ``main``. It points Django at
the app-data folder, migrates (or sets an unupgradable database aside), makes
the Local Account, starts the in-process job worker and serves through waitress
on ``127.0.0.1`` at a free port. DESK-03: it first takes the data folder's
instance lock (a second launch focuses the running App window and exits), and
shows the **App window** (``desktop_app.shell``), falling back to the browser
without WebView2. A startup failure goes to ``<data>/logs/desktop.log`` and a
message box. ``LF_DESKTOP_HOME`` (env) overrides the data folder.

Flags:

- ``--no-browser``: headless. No window, browser or message box; prints the
  launch URL (``LF_READY``), ``LF_ALREADY_RUNNING`` on a second launch and a
  startup traceback to stderr. Wins over the other two.
- ``--browser``: the default browser instead of the App window.
- ``--selftest-window``: the App window reports ``LF_WINDOW_OK <s>`` /
  ``LF_WINDOW_FAIL <text>`` once the landing page shows, then closes.

Prints ``LF_READY <seconds> <url>`` once the server is about to accept
requests; the smoke test waits for it.
"""

from __future__ import annotations

import gc
import logging
import os
import secrets
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from desktop_app.instance import acquire_instance_lock, focus_window, message_box
from desktop_app.shell import WINDOW_TITLE, choose_shell, run_window, window_available

if TYPE_CHECKING:
    from accounts.models import User

logger = logging.getLogger(__name__)

LOCAL_EMAIL = "local@desktop.invalid"
FROZEN = getattr(sys, "frozen", False)

# The job worker's context manager. Once garbage-collected, its generator's
# ``finally`` stops the worker thread and jobs sit at PENDING (ADR-0048).
_worker: Any = None

# The open ``instance.lock`` (DESK-03). Once closed or garbage-collected, the
# lock is released and a second launch would start a second server.
_instance_lock: IO[bytes] | None = None


def data_dir() -> Path:
    """``LF_DESKTOP_HOME``, else ``%LOCALAPPDATA%\\LaserforceSimulator``."""
    home = os.environ.get("LF_DESKTOP_HOME")
    if home:
        return Path(home)
    return Path(os.environ["LOCALAPPDATA"]) / "LaserforceSimulator"


def spa_dir() -> Path:
    """The built SPA: bundled beside the code when frozen, else ``ng build``'s output."""
    if FROZEN:
        return Path(sys._MEIPASS) / "spa"  # type: ignore[attr-defined]
    repo = Path(__file__).resolve().parents[2]
    return repo / "frontend" / "dist" / "frontend" / "browser"


def prepare_environment(data: Path, spa: Path) -> None:
    """Set what ``desktop_app.settings`` reads, overwriting inherited values.

    The secret key is written on first launch and reused after.
    """
    (data / "logs").mkdir(parents=True, exist_ok=True)
    key_file = data / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(50))
    os.environ["LF_DESKTOP_HOME"] = str(data)
    os.environ["LF_DESKTOP_SPA"] = str(spa)
    os.environ["SECRET_KEY"] = key_file.read_text()
    os.environ["DATABASE_URL"] = "sqlite:///" + (data / "db.sqlite3").as_posix()
    os.environ["DJANGO_SETTINGS_MODULE"] = "desktop_app.settings"
    # DESK-04: only a frozen build has a record of itself; unfrozen is dev.
    if FROZEN:
        os.environ["LF_DESKTOP_BUILD_INFO"] = str(
            Path(sys._MEIPASS) / "build_info.json"  # type: ignore[attr-defined]
        )
    else:
        os.environ.pop("LF_DESKTOP_BUILD_INFO", None)


def migrate_or_reset(data: Path, migrate: Callable[[], None]) -> str | None:
    """Run ``migrate``; if it fails on saved data, set the data aside (ADR-0004).

    Returns the notice to show, or ``None`` when nothing was reset. A missing
    or empty database that fails to migrate is a broken build, not an upgrade,
    so it re-raises. Migration history is never rewritten.
    """
    from django.db import connections

    db = data / "db.sqlite3"
    try:
        migrate()
        return None
    except Exception as exc:  # noqa: BLE001 - any failed upgrade resets
        if not db.exists() or db.stat().st_size == 0:
            raise
        failure = exc.__class__.__name__
    # Outside the except block: a connect() that failed half way leaves its
    # sqlite3 handle open in the traceback's frames, out of close_all()'s
    # reach, and Windows refuses to move an open file. Leaving the block
    # drops the traceback; the collect closes the handle.
    connections.close_all()
    gc.collect()
    backup = data / f"db.sqlite3.{time.strftime('%Y%m%d-%H%M%S')}.bak"
    shutil.move(db, backup)
    for suffix in ("-wal", "-shm"):
        Path(f"{db}{suffix}").unlink(missing_ok=True)
    migrate()
    return (
        f"Your saved data could not be upgraded ({failure}). "
        f"It was moved to {backup.name} and a fresh database was started."
    )


def local_account() -> User:
    """The one Local Account, created on first launch with no usable password."""
    from accounts.models import User

    user = User.objects.filter(email=LOCAL_EMAIL).first()
    if user is None:
        user = User.objects.create_user(LOCAL_EMAIL)
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def start_job_worker() -> None:
    """A Celery worker thread on the in-process ``memory://`` broker."""
    from celery.contrib.testing.worker import start_worker

    from laserforce_simulator.celery_app import celery_app

    global _worker
    _worker = start_worker(
        celery_app,
        pool="solo",
        concurrency=1,
        perform_ping_check=False,
        loglevel="WARNING",
    )
    _worker.__enter__()


def quiet_std_streams() -> None:
    """Point a ``None`` ``sys.stdout`` / ``sys.stderr`` at ``os.devnull``.

    A ``--noconsole`` build started with no pipe (a double-click) has neither;
    Django's ``OutputWrapper`` and Celery would raise on ``.write``. Streams
    that are set (a pipe, a console) stay as they are.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")


def report_startup_failure(data: Path, *, headless: bool) -> None:
    """Append the current exception to ``<data>/logs/desktop.log`` and tell the person.

    Called from an ``except`` block. A plain file append: Django's logging may
    not be configured yet. Headless prints the traceback to stderr; otherwise a
    message box names the log.
    """
    log = data / "logs" / "desktop.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    details = traceback.format_exc()
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} startup failed\n")
        handle.write(details)
    if headless:
        print(details, file=sys.stderr, flush=True)
    else:
        message_box(f"Laserforce Simulator could not start. Details: {log}")


def _migrate() -> None:
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=0)


def _already_running(headless: bool) -> None:
    """A second launch on the same data folder: point the person at the first."""
    if headless:
        print("LF_ALREADY_RUNNING", flush=True)
    elif not focus_window(WINDOW_TITLE):
        message_box("Laserforce Simulator is already running.")


def _start_server(data: Path, started: float) -> tuple[Any, str]:
    """Set up Django, migrate, start the job worker; the server and launch URL."""
    prepare_environment(data, spa_dir())

    import django

    django.setup()

    from desktop_app.notice import set_notice

    set_notice(migrate_or_reset(data, _migrate))
    user = local_account()
    start_job_worker()

    from waitress import create_server

    from desktop_app.launch_code import mint_launch_code
    from laserforce_simulator.wsgi import application

    # Port 0: the OS picks a free one, read back as effective_port.
    server = create_server(application, host="127.0.0.1", port=0, threads=8)
    url = (
        f"http://127.0.0.1:{server.effective_port}/api/accounts/desktop-login/"
        f"?code={mint_launch_code(user)}"
    )
    print(f"LF_READY {time.perf_counter() - started:.2f} {url}", flush=True)
    return server, url


def _open_shell(server: Any, url: str, argv: list[str]) -> None:
    """Serve, and show the shell ``choose_shell`` picks; returns when it closes.

    The App window must own the main thread, so waitress runs on a daemon
    thread then and the process ends when the window closes. A window that
    raises falls back to the browser, served by that same thread; under
    ``--selftest-window`` it prints ``LF_WINDOW_FAIL`` and returns instead, so
    the smoke fails at once rather than at its timeout.
    """
    shell = choose_shell(argv, window_available)
    if shell == "window":
        selftest = "--selftest-window" in argv
        serving = threading.Thread(target=server.run, name="waitress", daemon=True)
        serving.start()
        try:
            run_window(url, selftest=selftest)
            return
        except Exception as exc:  # noqa: BLE001 - any window failure falls back
            logger.exception("The App window failed; opening the browser.")
            if selftest:
                print(f"LF_WINDOW_FAIL {type(exc).__name__}: {exc}", flush=True)
                return
        webbrowser.open(url)
        serving.join()
        return
    if shell == "browser":
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    server.run()


def main() -> None:
    global _instance_lock
    started = time.perf_counter()
    quiet_std_streams()
    data = data_dir()
    (data / "logs").mkdir(parents=True, exist_ok=True)
    headless = "--no-browser" in sys.argv

    # Before migrate, so two launches never migrate at once.
    _instance_lock = acquire_instance_lock(data)
    if _instance_lock is None:
        _already_running(headless)
        sys.exit(0)

    try:
        server, url = _start_server(data, started)
        _open_shell(server, url, sys.argv)
    except Exception:  # noqa: BLE001 - every startup failure is reported
        report_startup_failure(data, headless=headless)
        sys.exit(1)
