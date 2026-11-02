"""DESK-04 (spec #503): the Update check.

Once per launch the Desktop App asks GitHub whether a newer build of its own
channel exists: a ``release`` build asks for the latest numbered Release, a
``latest`` build for the ``desktop-latest`` tag's commit, and a ``dev`` build
asks nothing. The check runs on the first ``GET /api/accounts/desktop-update/``
(after the SPA is up, so launch never waits) and is cached for the process.
Any failure (offline, a timeout, a 404 because no Release exists yet, bad
JSON) is ``None`` plus one warning in the desktop log: the app works as before.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.request
from typing import Any, TypedDict

from django.conf import settings
from rest_framework import views
from rest_framework.request import Request
from rest_framework.response import Response

from .build_info import RELEASE_TAG, SHORT_SHA, BuildInfo, load_build_info

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/repos"
LATEST_TAG = "desktop-latest"


class DesktopUpdate(TypedDict):
    version: str
    url: str


def fetch_json(url: str, timeout: float) -> Any:
    """One GET of GitHub's API. The only function tests replace."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "LaserforceSimulator",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _semver(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _newer_release(info: BuildInfo, repo: str, timeout: float) -> DesktopUpdate | None:
    release = fetch_json(f"{GITHUB_API}/{repo}/releases/latest", timeout)
    tag = release["tag_name"]
    match = RELEASE_TAG.match(tag)
    if not match or info.version is None:
        return None
    if tuple(int(part) for part in match.groups()) <= _semver(info.version):
        return None
    return {"version": tag, "url": release["html_url"]}


def _newer_latest(info: BuildInfo, repo: str, timeout: float) -> DesktopUpdate | None:
    ref = fetch_json(f"{GITHUB_API}/{repo}/git/ref/tags/{LATEST_TAG}", timeout)
    sha = ref["object"]["sha"]
    if sha == info.sha:
        return None
    return {
        "version": f"latest {sha[:SHORT_SHA]}",
        "url": f"https://github.com/{repo}/releases/tag/{LATEST_TAG}",
    }


def check_for_update(info: BuildInfo) -> DesktopUpdate | None:
    """A newer build of ``info``'s channel, or ``None``; never raises."""
    if info.channel == "release":
        check = _newer_release
    elif info.channel == "latest":
        check = _newer_latest
    else:
        return None
    try:
        return check(
            info, settings.DESKTOP_RELEASES_REPO, settings.DESKTOP_UPDATE_TIMEOUT
        )
    except Exception as exc:  # noqa: BLE001 - the check must never break the app
        logger.warning("Update check failed: %s: %s", type(exc).__name__, exc)
        return None


_UNSET: Any = object()
_cached: DesktopUpdate | None = _UNSET
_lock = threading.Lock()


def cached_update() -> DesktopUpdate | None:
    """``check_for_update(load_build_info())``, run at most once per process."""
    global _cached
    with _lock:
        if _cached is _UNSET:
            _cached = check_for_update(load_build_info())
        return _cached


def reset_update_cache() -> None:
    """Forget the cached result (tests)."""
    global _cached
    with _lock:
        _cached = _UNSET


class DesktopUpdateView(views.APIView):
    """GET /api/accounts/desktop-update/ -> ``DesktopUpdate`` or ``null``.

    Routed only in ``desktop_app.urls``; authenticated like every API view.
    """

    def get(self, request: Request) -> Response:
        return Response(cached_update())
