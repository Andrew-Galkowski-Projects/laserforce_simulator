"""DESK-01: the Desktop App's entry point (frozen by DESK-02's PyInstaller spec).

Unfrozen: ``python desktop/main.py [--no-browser]`` with the main ``.venv``
Python. Everything else lives in ``desktop_app.launcher``.
"""

import multiprocessing
import sys
from pathlib import Path

if __name__ == "__main__":
    multiprocessing.freeze_support()
    if not getattr(sys, "frozen", False):
        sys.path.insert(
            0, str(Path(__file__).resolve().parent.parent / "laserforce_simulator")
        )

    from desktop_app.launcher import main

    main()
