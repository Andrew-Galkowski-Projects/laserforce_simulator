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
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The directory holding manage.py (and the matches / teams / core packages).
PROJECT_DIR: Path = Path(__file__).resolve().parent.parent


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
