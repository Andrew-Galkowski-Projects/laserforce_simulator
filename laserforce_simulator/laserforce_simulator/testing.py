"""Test-only helpers shared by every app's test suite.

``run_project_python`` is the one seam for the ``TestNoDjangoImportsLeaked``
family of tests: each spawns a fresh interpreter, imports a pure module (e.g.
``matches.team_history_logic``) and walks ``sys.modules`` for leaked Django
imports. A bare ``subprocess.run([sys.executable, "-c", code])`` inherits the
caller's cwd, so it could only import ``matches`` when pytest happened to run
from the Django project directory; launched from the repo root it failed with
``ModuleNotFoundError``. Running the child with ``cwd`` set to the project
directory *and* that directory on ``PYTHONPATH`` makes the check independent of
where pytest was started.

``install_close_in_transaction_guard`` (CI-01) makes closing a DB connection
inside a test transaction raise on every backend. The root conftest's
``pytest_configure`` installs it once per pytest run.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from django.db.backends.base.base import BaseDatabaseWrapper

# The directory holding manage.py (and the matches / teams / core packages).
PROJECT_DIR: Path = Path(__file__).resolve().parent.parent

# CI-01: closing the DB connection inside a test transaction is harmless on
# SQLite's in-memory test DB but, on PostgreSQL, kills the connection every
# later test on that xdist worker shares (``InterfaceError: connection already
# closed``). The guard makes it fail loudly on every backend.
CLOSE_IN_TRANSACTION_MESSAGE = (
    "DB connection closed inside a test transaction. On PostgreSQL this kills "
    "the connection for every later test on this worker (InterfaceError: "
    "connection already closed). Usual culprits: close_old_connections() "
    "called from code under test (e.g. a Celery task's finally block -- leave "
    "connection cleanup to Celery's worker fixup), or request_finished fired "
    "by a manual response.close() in a test (read streaming_content to "
    "exhaustion instead; the test client closes the response)."
)


def install_close_in_transaction_guard() -> None:
    """Make ``close()`` raise while a connection is inside an atomic block.

    Wraps ``close`` on the concrete backend class of every configured
    connection: SQLite's wrapper overrides ``close()`` without calling the
    base, so patching ``BaseDatabaseWrapper`` alone never fires there. Outside
    an atomic block the real ``close`` runs unchanged, so Django's own
    ``tearDownClass`` close (after rollback) and test DB teardown are
    unaffected. Idempotent.
    """
    from django.db import connections

    for alias in connections:
        backend_class = type(connections[alias])
        original_close = backend_class.close
        if getattr(original_close, "_close_in_transaction_guard", False):
            continue

        @functools.wraps(original_close)
        def guarded_close(
            self: BaseDatabaseWrapper,
            _original: Callable[[BaseDatabaseWrapper], None] = original_close,
        ) -> None:
            if self.in_atomic_block:
                raise AssertionError(CLOSE_IN_TRANSACTION_MESSAGE)
            return _original(self)

        guarded_close._close_in_transaction_guard = True  # type: ignore[attr-defined]
        backend_class.close = guarded_close


def run_project_python(
    code: str, *, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter rooted at the Django project dir.

    The child gets ``cwd=PROJECT_DIR``, ``PROJECT_DIR`` prepended to
    ``PYTHONPATH`` and ``PYTHONDONTWRITEBYTECODE=1``. Output is captured as
    UTF-8 text; the caller asserts on ``returncode`` / ``stdout`` / ``stderr``.
    """
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{PROJECT_DIR}{os.pathsep}{existing}" if existing else str(PROJECT_DIR)
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_DIR,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
