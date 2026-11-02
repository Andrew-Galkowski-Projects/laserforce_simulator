"""DESK-01: the Desktop App's entry point (frozen by DESK-02's PyInstaller spec).

Unfrozen: ``python desktop/main.py [--no-browser | --browser]
[--selftest-window]`` with the main ``.venv`` Python. Everything else lives in
``desktop_app.launcher``.

Flags (DESK-03):

- ``--no-browser``: headless, no App window, browser or message box (the
  smoke test). Wins over the other two.
- ``--browser``: the default browser instead of the App window.
- ``--selftest-window``: the App window prints ``LF_WINDOW_OK <s>`` /
  ``LF_WINDOW_FAIL <text>`` once the landing page shows, then closes
  (``desktop/smoke.py --window``).
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
