"""DESK-01: start the real launcher, sign in through it and run one real job.

The launcher runs as a subprocess (``desktop/main.py --no-browser``) on a
fresh data folder, so this covers the desktop settings, the URLconf, the
Launch code, the Local Account and the in-process job worker together
(ADR-0048: a garbage-collected worker leaves jobs at PENDING forever).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
ENTRY = REPO / "desktop" / "main.py"
READY_TIMEOUT_S = 90
JOB_TIMEOUT_S = 120


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with _opener.open(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _wait_for_ready(process: subprocess.Popen[str]) -> str:
    deadline = time.monotonic() + READY_TIMEOUT_S
    assert process.stdout is not None
    while time.monotonic() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        if line.startswith("LF_READY "):
            return line.split()[2]
    process.kill()
    raise AssertionError(f"no LF_READY; output: {process.communicate()[0]}")


@pytest.fixture
def launched(tmp_path: Path) -> Any:
    env = {
        **os.environ,
        "LF_DESKTOP_HOME": str(tmp_path),
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [sys.executable, str(ENTRY), "--no-browser"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        yield _wait_for_ready(process)
    finally:
        process.kill()
        process.wait(timeout=30)


def test_the_launcher_signs_in_once_and_runs_a_job(launched: str) -> None:
    launch_url = launched
    base = launch_url.split("/api/")[0]

    status, headers, _ = _request(launch_url)
    assert status == 302
    assert headers["Location"] == "/"
    cookie = SimpleCookie(headers["Set-Cookie"])["refresh_token"]
    assert _request(launch_url)[0] == 403

    status, _, body = _request(
        f"{base}/api/token/refresh/",
        method="POST",
        headers={"Cookie": f"refresh_token={cookie.value}"},
    )
    assert status == 200, body
    auth = {"Authorization": f"Bearer {json.loads(body)['data']['access']}"}

    me = json.loads(_request(f"{base}/api/accounts/me/", headers=auth)[2])["data"]
    assert me["email"] == "local@desktop.invalid"
    assert me["desktop"] is True
    assert me["desktop_notice"] is None

    status, _, body = _request(
        f"{base}/api/teams/generate/",
        method="POST",
        body={"num_teams": "2", "players_per_team": "6", "mean": 50, "std_dev": 15},
        headers=auth,
    )
    assert status == 201, body
    red, blue = (team["id"] for team in json.loads(body)["data"]["created_teams"])

    status, _, body = _request(
        f"{base}/api/simulate-batch/",
        method="POST",
        body={"team_red": red, "team_blue": blue, "n": 1, "master_seed": 42},
        headers=auth,
    )
    assert status == 200, body
    job_id = json.loads(body)["data"]["job_id"]

    deadline = time.monotonic() + JOB_TIMEOUT_S
    job_status = "running"
    while time.monotonic() < deadline:
        poll = _request(f"{base}/api/simulate-batch/{job_id}/", headers=auth)[2]
        job_status = json.loads(poll)["data"]["status"]
        if job_status != "running":
            break
        time.sleep(0.5)
    assert job_status == "complete"
