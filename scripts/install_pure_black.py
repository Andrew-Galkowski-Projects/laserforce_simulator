#!/usr/bin/env python
"""Install a pure-Python black into the main checkout's ``.tools/black`` (gitignored).

Run from a worktree, it still installs into the main checkout, which every
worktree's ``/verify`` then uses.

On a machine whose Application Control policy blocks the venv's compiled black
(``ImportError: DLL load failed while importing black: An Application Control
policy has blocked this file``), this installs black and its dependencies from
their pure-Python wheels, so no compiled module is loaded. ``/verify``'s black step falls back to this copy
on its own. To run black by hand, put the folder on ``PYTHONPATH``; the script
prints the line to paste.

The version is the one CI's lint step pins in ``.github/workflows/ci.yml``.

Usage::

    .venv\\Scripts\\python.exe scripts/install_pure_black.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CI = _REPO / ".github" / "workflows" / "ci.yml"


def target_dir(checkout: Path) -> Path:
    """``.tools/black`` in the main checkout that owns ``checkout``.

    Every worktree shares the main checkout's ``.git``, so one install serves
    them all: a per-worktree copy failed black in each new worktree (MECH-23 retro).
    """
    common = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=checkout,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(common).parent / ".tools" / "black"


def ci_black_version(ci_text: str) -> str:
    """The ``black==<version>`` pin in CI's lint step."""
    match = re.search(r"\bblack==([\w.]+)", ci_text)
    if match is None:
        raise ValueError("no black==<version> pin in ci.yml")
    return match.group(1)


def main() -> int:
    version = ci_black_version(_CI.read_text(encoding="utf-8"))
    target = target_dir(_REPO)
    # Pure py3-none-any wheels only: black, pytokens and every dependency ship one.
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--target",
        str(target),
    ]
    cmd += ["--only-binary", ":all:", "--platform", "any", "--implementation", "py"]
    cmd += [f"black=={version}"]
    code = subprocess.run(cmd).returncode
    if code == 0:
        print(f"black {version} installed in {target}")
        print(f'PowerShell: $env:PYTHONPATH = "{target}"')
    return code


if __name__ == "__main__":
    sys.exit(main())
