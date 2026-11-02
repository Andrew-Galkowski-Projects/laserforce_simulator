"""DESK-04 (spec #503): write the Desktop build's record of itself.

``python desktop/write_build_info.py <out file>`` writes
``build_info(os.environ, <UTC today>)`` as JSON. ``build.ps1`` runs it before
PyInstaller, which bundles the file as ``build_info.json``; the app reads it
back through ``desktop_app.build_info.load_build_info``.

The channel comes from CI's ``GITHUB_REF``: a plain ``vX.Y.Z`` tag is a
``release``, ``main`` is ``latest``, anything else (a pull request, an ``-rc``
tag, a local build) is ``dev``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "laserforce_simulator"))

from desktop_app.build_info import RELEASE_TAG  # noqa: E402

__all__ = ["RELEASE_TAG", "build_info", "main"]

TAG_PREFIX = "refs/tags/"
MAIN_REF = "refs/heads/main"


def build_info(env: Mapping[str, str], today: date) -> dict[str, str | None]:
    """``{version, channel, sha, built}`` for the build running under ``env``."""
    ref = env.get("GITHUB_REF", "")
    version: str | None = None
    channel = "dev"
    match = RELEASE_TAG.match(ref.removeprefix(TAG_PREFIX))
    if ref.startswith(TAG_PREFIX) and match:
        version = ".".join(match.groups())
        channel = "release"
    elif ref == MAIN_REF:
        channel = "latest"
    return {
        "version": version,
        "channel": channel,
        "sha": env.get("GITHUB_SHA") or None,
        "built": today.isoformat(),
    }


def main(argv: list[str]) -> None:
    import os

    out = Path(argv[1])
    today = datetime.now(timezone.utc).date()
    out.write_text(json.dumps(build_info(os.environ, today)), encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
