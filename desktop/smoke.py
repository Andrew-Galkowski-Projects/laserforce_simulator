"""DESK-02: the Desktop build smoke, run frozen in CI and unfrozen in the suite.

    python desktop/smoke.py <exe> [--zip <zip>] [--summary <md file>]

runs ``<exe> --no-browser`` on a fresh data folder and walks it through the
steps below. Exit 0 on a pass, 1 on the first failed step. On a pass,
``--summary`` (CI passes ``$GITHUB_STEP_SUMMARY``) gets the sizes and times.
``desktop_app/tests/test_launcher_smoke.py`` calls ``run_smoke`` on
``desktop/main.py``. Stdlib only: the frozen smoke's Python has no Django.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

MAP_IMAGE = (
    Path(__file__).resolve().parent.parent
    / "Screenshots_and_video_examples"
    / "San_Marcos_Laser_Tag_Map.jpg"
)
READY_TIMEOUT_S = 90
JOB_TIMEOUT_S = 120


class SmokeFailure(Exception):
    """The first step that failed, with what went wrong and the launcher's output."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    data: bytes | None = None,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    if body is not None:
        data = json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", content_type)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with _opener.open(request, timeout=60) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def _expect(status: int, wanted: int, body: bytes) -> None:
    if status != wanted:
        raise AssertionError(f"HTTP {status}, wanted {wanted}: {body[:300]!r}")


class _Output:
    """The launcher's output, drained on a thread so its pipe never fills."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self.lines: list[str] = []
        self.ready = threading.Event()
        self.ready_line = ""
        self._thread = threading.Thread(
            target=self._drain, args=(process,), daemon=True
        )
        self._thread.start()

    def _drain(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.lines.append(line.rstrip())
            if line.startswith("LF_READY ") and not self.ready.is_set():
                self.ready_line = line.strip()
                self.ready.set()
        self.ready.set()  # EOF: the process exited

    def text(self) -> str:
        return "\n".join(self.lines[-60:])


@contextmanager
def _step(name: str, output: _Output) -> Iterator[None]:
    print(f"smoke: {name}", flush=True)
    try:
        yield
    except SmokeFailure:
        raise
    except Exception as error:  # every failure becomes the step's SmokeFailure
        raise SmokeFailure(name, f"{error!r}\n--- launcher output ---\n{output.text()}")


def _multipart(name: str, image: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n{name}\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
        f'filename="{image.name}"\r\nContent-Type: image/jpeg\r\n\r\n'
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + image.read_bytes() + tail, f"multipart/form-data; boundary={boundary}"


def run_smoke(command: list[str], home: Path) -> dict[str, float]:
    """Launch ``command`` on data folder ``home`` and run every smoke step.

    Returns ``launcher_ready_s`` (from ``LF_READY``) and ``cold_start_s``
    (process start to the first ``GET /`` that served the SPA). Raises
    ``SmokeFailure`` at the first failed step; the process is always killed.
    """
    env = {**os.environ, "LF_DESKTOP_HOME": str(home), "PYTHONUNBUFFERED": "1"}
    started = time.perf_counter()
    process = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = _Output(process)
    try:
        return _steps(output, started)
    finally:
        process.kill()
        process.wait(timeout=30)


def _steps(output: _Output, started: float) -> dict[str, float]:
    with _step("ready", output):
        output.ready.wait(READY_TIMEOUT_S)
        if not output.ready_line:
            raise AssertionError(f"no LF_READY within {READY_TIMEOUT_S} s")
        _, ready_s, launch_url = output.ready_line.split()
    base = launch_url.split("/api/")[0]

    with _step("spa", output):
        status, _, body = _request(f"{base}/")
        _expect(status, 200, body)
        if b"<app-root" not in body:
            raise AssertionError(f"GET / is not the SPA: {body[:300]!r}")
        cold_start_s = time.perf_counter() - started

    with _step("launch_code", output):
        status, headers, body = _request(launch_url)
        _expect(status, 302, body)
        if headers.get("Location") != "/":
            raise AssertionError(f"redirects to {headers.get('Location')!r}, not /")
        refresh = SimpleCookie(headers["Set-Cookie"])["refresh_token"].value
        status, _, body = _request(launch_url)
        _expect(status, 403, body)

    with _step("refresh", output):
        status, _, body = _request(
            f"{base}/api/token/refresh/",
            method="POST",
            headers={"Cookie": f"refresh_token={refresh}"},
        )
        _expect(status, 200, body)
        auth = {"Authorization": f"Bearer {json.loads(body)['data']['access']}"}

    with _step("me", output):
        status, _, body = _request(f"{base}/api/accounts/me/", headers=auth)
        _expect(status, 200, body)
        me = json.loads(body)["data"]
        wanted = ("local@desktop.invalid", True, None)
        got = (me["email"], me["desktop"], me["desktop_notice"])
        if got != wanted:
            raise AssertionError(f"me is {got}, wanted {wanted}")

    with _step("teams", output):
        status, _, body = _request(
            f"{base}/api/teams/generate/",
            method="POST",
            body={"num_teams": "2", "players_per_team": "6", "mean": 50, "std_dev": 15},
            headers=auth,
        )
        _expect(status, 201, body)
        red, blue = (team["id"] for team in json.loads(body)["data"]["created_teams"])

    with _step("batch", output):
        status, _, body = _request(
            f"{base}/api/simulate-batch/",
            method="POST",
            body={"team_red": red, "team_blue": blue, "n": 1, "master_seed": 42},
            headers=auth,
        )
        _expect(status, 200, body)
        job_id = json.loads(body)["data"]["job_id"]
        deadline = time.monotonic() + JOB_TIMEOUT_S
        job_status = "running"
        while job_status == "running" and time.monotonic() < deadline:
            time.sleep(0.5)
            poll = _request(f"{base}/api/simulate-batch/{job_id}/", headers=auth)[2]
            job_status = json.loads(poll)["data"]["status"]
        if job_status != "complete":
            raise AssertionError(f"batch job is {job_status!r} after polling")

    with _step("report", output):
        status, _, body = _request(
            f"{base}/api/rounds/",
            method="POST",
            body={"team_red": red, "team_blue": blue},
            headers=auth,
        )
        _expect(status, 201, body)
        round_id = json.loads(body)["data"]["id"]
        status, _, body = _request(
            f"{base}/api/rounds/{round_id}/report/", headers=auth
        )
        _expect(status, 200, body)
        if not body.startswith(b"%PDF"):
            raise AssertionError(f"the report is not a PDF: {body[:40]!r}")

    with _step("map", output):
        data, content_type = _multipart("Smoke Arena", MAP_IMAGE)
        status, _, body = _request(
            f"{base}/api/maps/",
            method="POST",
            data=data,
            content_type=content_type,
            headers=auth,
        )
        _expect(status, 201, body)
        map_id = json.loads(body)["data"]["id"]
        status, _, body = _request(
            f"{base}/api/maps/{map_id}/zones/?zone_size=50", headers=auth
        )
        _expect(status, 200, body)

    return {"launcher_ready_s": float(ready_s), "cold_start_s": round(cold_start_s, 2)}


def summary_markdown(
    timings: dict[str, float], *, folder_mb: float, zip_mb: float
) -> str:
    """The job summary table: the build's sizes and the smoke's times."""
    return "\n".join(
        [
            "### Desktop build",
            "",
            "| | |",
            "|---|---|",
            f"| Zip | {zip_mb:.1f} MB |",
            f"| Unpacked | {folder_mb:.1f} MB |",
            f"| Launcher ready | {timings['launcher_ready_s']:.2f} s |",
            f"| Cold start to the SPA | {timings['cold_start_s']:.2f} s |",
            "",
        ]
    )


def _megabytes(path: Path) -> float:
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    return sum(p.stat().st_size for p in files) / (1024 * 1024)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("exe", type=Path)
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    # Windows may still hold the killed process's SQLite files for a moment.
    with tempfile.TemporaryDirectory(
        prefix="lf-smoke-", ignore_cleanup_errors=True
    ) as home:
        try:
            timings = run_smoke([str(args.exe), "--no-browser"], Path(home))
        except SmokeFailure as failure:
            print(f"smoke: FAILED at {failure.step}\n{failure.detail}", flush=True)
            return 1
    print(f"smoke: passed {timings}", flush=True)
    if args.summary:
        text = summary_markdown(
            timings,
            folder_mb=_megabytes(args.exe.parent),
            zip_mb=_megabytes(args.zip) if args.zip else 0.0,
        )
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
