"""DESK-04 (spec #503): the Update check, end to end through the API.

The only fake is the network: ``update_check.fetch_json`` is replaced with a
``FakeGitHub`` that records the URLs it is asked for. ``DESKTOP_BUILD_INFO``
points at a ``tmp_path`` record, and the per-process cache is reset per test.
``fetch_json`` itself is checked against a local stand-in server, never GitHub.
"""

from __future__ import annotations

import http.server
import json
import logging
import socket
import threading
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from rest_framework.test import APIClient

from conftest import get_shared_manager
from desktop_app import update_check

URL = "/api/accounts/desktop-update/"
REPO = "Andrew-Galkowski-Projects/laserforce_simulator"
RELEASES_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
DESKTOP_LATEST_REF = f"https://api.github.com/repos/{REPO}/git/ref/tags/desktop-latest"
DESKTOP_LATEST_PAGE = f"https://github.com/{REPO}/releases/tag/desktop-latest"
SHA = "abc1234def5678abc1234def5678abc1234def56"
NEW_SHA = "fedcba9876543210fedcba9876543210fedcba98"
RELEASE_PAGE = f"https://github.com/{REPO}/releases/tag/v0.10.0"


def _http_404(url: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 404, "Not Found", None, None)  # type: ignore[arg-type]


class FakeGitHub:
    """``fetch_json`` stand-in: answers (or raises) one canned reply per URL."""

    def __init__(self, replies: dict[str, Any] | None = None) -> None:
        self.replies = replies or {}
        self.calls: list[tuple[str, float]] = []

    def __call__(self, url: str, timeout: float) -> Any:
        self.calls.append((url, timeout))
        reply = self.replies[url]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    @property
    def urls(self) -> list[str]:
        return [url for url, _ in self.calls]


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    update_check.reset_update_cache()
    yield
    update_check.reset_update_cache()


@pytest.fixture
def desktop(settings: Any, tmp_path: Path) -> Any:
    settings.ROOT_URLCONF = "desktop_app.urls"
    settings.DESKTOP_APP = True
    settings.DESKTOP_RELEASES_REPO = REPO
    settings.DESKTOP_UPDATE_TIMEOUT = 5
    settings.DESKTOP_BUILD_INFO = tmp_path / "build_info.json"
    return settings


def _build(settings: Any, channel: str, version: str | None = None) -> None:
    Path(settings.DESKTOP_BUILD_INFO).write_text(
        json.dumps(
            {"version": version, "channel": channel, "sha": SHA, "built": "2026-11-02"}
        ),
        encoding="utf-8",
    )


def _fake(monkeypatch: pytest.MonkeyPatch, replies: dict[str, Any]) -> FakeGitHub:
    fake = FakeGitHub(replies)
    monkeypatch.setattr(update_check, "fetch_json", fake)
    return fake


def _api() -> APIClient:
    api = APIClient()
    api.force_login(get_shared_manager())
    return api


def _get_update(api: APIClient | None = None) -> Any:
    response = (api or _api()).get(URL)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["meta"] == {}
    return body["data"]


NEWER = {"version": "v0.10.0", "url": RELEASE_PAGE}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("running", "reply", "expected"),
    [
        # Semver, numerically: v0.10.0 is newer than v0.9.0.
        ("0.9.0", {"tag_name": "v0.10.0", "html_url": RELEASE_PAGE}, NEWER),
        ("0.10.0", {"tag_name": "v0.10.0", "html_url": RELEASE_PAGE}, None),
        ("0.11.0", {"tag_name": "v0.10.0", "html_url": RELEASE_PAGE}, None),
        ("1.0.0", {"tag_name": "v0.10.0", "html_url": RELEASE_PAGE}, None),
        ("0.9.0", {"tag_name": "v0.10.0-rc1", "html_url": RELEASE_PAGE}, None),
        ("0.9.0", {"tag_name": "desktop-latest", "html_url": RELEASE_PAGE}, None),
        ("0.9.0", _http_404(RELEASES_LATEST), None),
        ("0.9.0", TimeoutError("timed out"), None),
        ("0.9.0", ValueError("bad JSON"), None),
        ("0.9.0", {"html_url": RELEASE_PAGE}, None),
        ("0.9.0", {"tag_name": "v0.10.0"}, None),
    ],
    ids=[
        "older",
        "equal",
        "newer-running",
        "major-newer-running",
        "rc-tag",
        "non-matching-tag",
        "404-no-release",
        "timeout",
        "bad-json",
        "missing-tag_name",
        "missing-html_url",
    ],
)
def test_a_release_build_asks_for_the_latest_release(
    desktop: Any,
    monkeypatch: pytest.MonkeyPatch,
    running: str,
    reply: Any,
    expected: Any,
) -> None:
    _build(desktop, "release", running)
    fake = _fake(monkeypatch, {RELEASES_LATEST: reply})
    assert _get_update() == expected
    assert fake.calls == [(RELEASES_LATEST, 5)]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"object": {"sha": SHA}}, None),
        (
            {"object": {"sha": NEW_SHA}},
            {"version": "latest fedcba9", "url": DESKTOP_LATEST_PAGE},
        ),
        ({"object": {}}, None),
        (_http_404(DESKTOP_LATEST_REF), None),
        (TimeoutError("timed out"), None),
    ],
    ids=["same-sha", "different-sha", "missing-sha", "404", "timeout"],
)
def test_a_latest_build_asks_for_the_desktop_latest_tag(
    desktop: Any, monkeypatch: pytest.MonkeyPatch, reply: Any, expected: Any
) -> None:
    _build(desktop, "latest")
    fake = _fake(monkeypatch, {DESKTOP_LATEST_REF: reply})
    assert _get_update() == expected
    assert fake.urls == [DESKTOP_LATEST_REF]


@pytest.mark.django_db
def test_a_dev_build_makes_no_call(
    desktop: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build(desktop, "dev")
    fake = _fake(monkeypatch, {})
    assert _get_update() is None
    assert fake.calls == []


@pytest.mark.django_db
def test_no_build_record_is_dev_and_makes_no_call(
    desktop: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop.DESKTOP_BUILD_INFO = None
    fake = _fake(monkeypatch, {})
    assert _get_update() is None
    assert fake.calls == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "reply",
    [{"tag_name": "v0.10.0", "html_url": RELEASE_PAGE}, _http_404(RELEASES_LATEST)],
    ids=["update", "failure"],
)
def test_a_second_request_makes_no_second_call(
    desktop: Any, monkeypatch: pytest.MonkeyPatch, reply: Any
) -> None:
    _build(desktop, "release", "0.9.0")
    fake = _fake(monkeypatch, {RELEASES_LATEST: reply})
    api = _api()
    first = _get_update(api)
    assert _get_update(api) == first
    assert len(fake.calls) == 1


@pytest.mark.django_db
def test_a_failed_check_logs_one_warning(
    desktop: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _build(desktop, "release", "0.9.0")
    _fake(monkeypatch, {RELEASES_LATEST: TimeoutError("timed out")})
    with caplog.at_level(logging.WARNING, logger=update_check.logger.name):
        assert _get_update() is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert warnings[0].name == update_check.logger.name


def test_two_concurrent_first_calls_check_once(
    desktop: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build(desktop, "release", "0.9.0")
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow_fetch(url: str, timeout: float) -> Any:
        calls.append(url)
        started.set()
        release.wait(5)
        return {"tag_name": "v0.10.0", "html_url": RELEASE_PAGE}

    monkeypatch.setattr(update_check, "fetch_json", slow_fetch)
    results: list[Any] = []
    first = threading.Thread(
        target=lambda: results.append(update_check.cached_update())
    )
    first.start()
    assert started.wait(5)
    second = threading.Thread(
        target=lambda: results.append(update_check.cached_update())
    )
    second.start()
    second.join(0.2)
    release.set()
    first.join(5)
    second.join(5)
    assert calls == [RELEASES_LATEST]
    assert results == [NEWER, NEWER]


@pytest.mark.django_db
def test_an_anonymous_request_is_401(desktop: Any) -> None:
    assert APIClient().get(URL).status_code == 401


@pytest.mark.django_db
def test_post_is_not_allowed(desktop: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _build(desktop, "dev")
    assert _api().post(URL).status_code == 405


@pytest.mark.django_db
def test_the_hosted_deploy_has_no_update_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _fake(monkeypatch, {})
    assert _api().get(URL).status_code == 404
    assert fake.calls == []


class _Recorder(http.server.BaseHTTPRequestHandler):
    seen: list[dict[str, str]] = []

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        type(self).seen.append({"path": self.path, **dict(self.headers.items())})
        body = json.dumps({"tag_name": "v0.1.0"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        pass


def test_fetch_json_sends_githubs_headers_and_parses_the_body() -> None:
    _Recorder.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/repos/x/releases/latest"
        assert update_check.fetch_json(url, 5) == {"tag_name": "v0.1.0"}
    finally:
        server.shutdown()
        server.server_close()
    [request] = _Recorder.seen
    assert request["path"] == "/repos/x/releases/latest"
    assert request["Accept"] == "application/vnd.github+json"
    assert request["User-Agent"] == "LaserforceSimulator"


@pytest.mark.django_db
def test_an_unreachable_github_is_no_update(
    desktop: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real failure behind the fake's raises: the real fetch_json against a
    # closed port, so the endpoint still answers null.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(update_check, "GITHUB_API", f"http://127.0.0.1:{port}/repos")
    _build(desktop, "release", "0.1.0")
    assert _get_update() is None
