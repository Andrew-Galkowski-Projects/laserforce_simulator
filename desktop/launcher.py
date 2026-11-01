"""ANG-09 spike: the Desktop App's entry point (spike/ang-09 only, never merged).

Run unfrozen with the main .venv Python (``python desktop/launcher.py``) or
frozen through ``desktop/laserforce.spec``. Flags:

- ``--no-browser``: print the launch URL instead of opening a browser.
- ``LF_DESKTOP_HOME`` (env): the data folder; defaults to
  ``%LOCALAPPDATA%\\LaserforceSimulator``.

Prints ``LF_READY <seconds> <url>`` once the server accepts requests, which is
what ``desktop/measure.py`` waits for.
"""

from __future__ import annotations

import multiprocessing
import os
import secrets
import shutil
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

T0 = time.perf_counter()
FROZEN = getattr(sys, "frozen", False)
LOCAL_EMAIL = "local@desktop.invalid"


def bundle_dir() -> Path:
    return Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    home = os.environ.get("LF_DESKTOP_HOME")
    if home:
        return Path(home)
    return Path(os.environ["LOCALAPPDATA"]) / "LaserforceSimulator"


def prepare_environment(data: Path) -> None:
    (data / "logs").mkdir(parents=True, exist_ok=True)
    key_file = data / "secret_key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(50))
    root = bundle_dir()
    if not FROZEN:
        sys.path.insert(0, str(root / "laserforce_simulator"))
    spa = (
        root / "spa" if FROZEN else root / "frontend" / "dist" / "frontend" / "browser"
    )
    os.environ["LF_DESKTOP_HOME"] = str(data)
    os.environ["LF_DESKTOP_SPA"] = str(spa)
    os.environ["SECRET_KEY"] = key_file.read_text()
    os.environ["DATABASE_URL"] = "sqlite:///" + (data / "db.sqlite3").as_posix()
    os.environ["DJANGO_SETTINGS_MODULE"] = "laserforce_simulator.desktop_settings"


def migrate_or_reset(data: Path) -> str | None:
    """``migrate``; on failure, set the database aside and start fresh (ADR-0004)."""
    from django.core.management import call_command
    from django.db import connections

    try:
        call_command("migrate", interactive=False, verbosity=0)
        return None
    except Exception as exc:  # noqa: BLE001 - any failed upgrade resets
        connections.close_all()
        db = data / "db.sqlite3"
        if not db.exists() or db.stat().st_size == 0:
            raise  # a fresh database failed: a broken build, not an upgrade
        backup = data / f"db.sqlite3.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.move(db, backup)
        for suffix in ("-wal", "-shm"):
            Path(f"{db}{suffix}").unlink(missing_ok=True)
        call_command("migrate", interactive=False, verbosity=0)
        return f"Your saved data could not be upgraded ({exc.__class__.__name__}). It was moved to {backup.name} and a fresh database was started."


def local_account():
    from accounts.models import User

    user = User.objects.filter(email=LOCAL_EMAIL).first()
    if user is None:
        user = User.objects.create_user(LOCAL_EMAIL, password=secrets.token_urlsafe(32))
    return user


def start_job_worker() -> None:
    """A Celery worker thread on the in-process memory broker."""
    from celery.contrib.testing.worker import start_worker

    from laserforce_simulator.celery_app import celery_app

    global _worker
    # Keep the context manager referenced: once garbage-collected, its
    # generator's ``finally`` stops the worker thread.
    _worker = start_worker(
        celery_app,
        pool="solo",
        concurrency=1,
        perform_ping_check=False,
        loglevel="WARNING",
    )
    _worker.__enter__()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    data = data_dir()
    prepare_environment(data)

    import django

    django.setup()
    notice = migrate_or_reset(data)
    if notice:
        print(f"NOTICE: {notice}", flush=True)
    user = local_account()
    start_job_worker()

    from waitress import create_server

    from accounts.desktop import mint_launch_code
    from laserforce_simulator.wsgi import application

    port = free_port()
    server = create_server(application, host="127.0.0.1", port=port, threads=8)
    url = f"http://127.0.0.1:{port}/api/accounts/desktop-login/?code={mint_launch_code(user)}"
    print(f"LF_READY {time.perf_counter() - T0:.2f} {url}", flush=True)
    print("Laserforce Simulator is running. Close this window to quit.", flush=True)
    if "--webview" in sys.argv:
        run_in_webview(server, url)
        return
    if "--no-browser" not in sys.argv:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    server.run()


def run_in_webview(server, url: str) -> None:  # noqa: ANN001
    """Stretch: a native window (pywebview / WebView2); closing it quits.

    With ``--webview-selftest`` the window prints the page text once the SPA
    has signed in, then closes itself.
    """
    import webview

    threading.Thread(target=server.run, daemon=True).start()
    window = webview.create_window("Laserforce Simulator", url, width=1280, height=800)

    def selftest() -> None:
        for _ in range(60):
            time.sleep(0.5)
            text = window.evaluate_js("document.body.innerText") or ""
            if "Sandbox" in text:
                print(
                    f"LF_WEBVIEW_OK {time.perf_counter() - T0:.2f} {text[:80]!r}",
                    flush=True,
                )
                break
        else:
            print("LF_WEBVIEW_FAIL", flush=True)
        window.destroy()

    webview.start(selftest if "--webview-selftest" in sys.argv else None)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
