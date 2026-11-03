"""BUG-SL-01: importing the simulation engine leaves logging configuration alone.

``entrypoints.py`` used to call ``logging.basicConfig(level=logging.DEBUG)`` at
import time, turning the root logger to DEBUG in every process that imported
it. The check runs in a fresh interpreter: this one has already imported the
module, and ``basicConfig`` is a no-op once the root logger has handlers.
"""

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]

_PROBE = (
    "import django, logging; django.setup(); "
    "import matches.simulation.entrypoints; "
    "root = logging.getLogger(); "
    "print(logging.getLevelName(root.level), len(root.handlers))"
)


def test_import_leaves_the_root_logger_alone() -> None:
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "laserforce_simulator.settings"}
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=PROJECT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["WARNING", "0"]
