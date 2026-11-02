"""DESK-04 (spec #503): the Desktop build's record of itself.

``desktop/write_build_info.py`` writes ``build_info.json`` at build time; the
launcher points ``LF_DESKTOP_BUILD_INFO`` (``settings.DESKTOP_BUILD_INFO``) at
the bundled copy. Stdlib only at import: the build script imports
``RELEASE_TAG`` from here before Django is set up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# A plain ``vX.Y.Z`` tag: a numbered Release. Any suffix (``-rc1``) or a
# missing part is not one. CI's "Publish a numbered Release" step is its twin.
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

SHORT_SHA = 7


@dataclass(frozen=True)
class BuildInfo:
    """Which build this is. ``channel`` is ``release``, ``latest`` or ``dev``."""

    version: str | None
    channel: str
    sha: str | None
    built: str | None

    @property
    def label(self) -> str:
        """``v0.1.0``, ``latest abc1234``, ``dev abc1234`` or ``dev``."""
        if self.channel == "release":
            return f"v{self.version}"
        if self.sha:
            return f"{self.channel} {self.sha[:SHORT_SHA]}"
        return self.channel


DEV_BUILD = BuildInfo(version=None, channel="dev", sha=None, built=None)


def load_build_info() -> BuildInfo:
    """The record at ``settings.DESKTOP_BUILD_INFO``; no path or no file is dev."""
    from django.conf import settings

    path = getattr(settings, "DESKTOP_BUILD_INFO", None)
    if not path or not Path(path).is_file():
        return DEV_BUILD
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return BuildInfo(
        version=data.get("version"),
        channel=data.get("channel") or "dev",
        sha=data.get("sha"),
        built=data.get("built"),
    )
