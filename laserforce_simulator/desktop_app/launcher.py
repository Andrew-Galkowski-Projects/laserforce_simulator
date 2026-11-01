"""DESK-01 (ADR-0048): the Desktop App's launcher.

``desktop/main.py`` (the frozen entry point) calls ``main``. It points Django at
the app-data folder, migrates (or sets an unupgradable database aside), makes
the Local Account, starts the in-process job worker and serves through waitress
on ``127.0.0.1`` at a free port. Flags: ``--no-browser`` prints the launch URL
instead of opening a browser. ``LF_DESKTOP_HOME`` (env) overrides the data
folder.

Prints ``LF_READY <seconds> <url>`` once the server is about to accept
requests; the smoke test waits for it.
"""

from __future__ import annotations

import os
import secrets
import shutil
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from accounts.models import User

LOCAL_EMAIL = "local@desktop.invalid"
FROZEN = getattr(sys, "frozen", False)

# The job worker's context manager. Once garbage-collected, its generator's
# ``finally`` stops the worker thread and jobs sit at PENDING (ADR-0048).
_worker: Any = None


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
        connections.close_all()
        backup = data / f"db.sqlite3.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.move(db, backup)
        for suffix in ("-wal", "-shm"):
            Path(f"{db}{suffix}").unlink(missing_ok=True)
        migrate()
        return (
            f"Your saved data could not be upgraded ({exc.__class__.__name__}). "
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


def _migrate() -> None:
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=0)


def main() -> None:
    started = time.perf_counter()
    data = data_dir()
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
    print("Laserforce Simulator is running. Close this window to quit.", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    server.run()
