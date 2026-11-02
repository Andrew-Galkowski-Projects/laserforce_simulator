"""DESK-02: the Desktop build smoke, run frozen in CI and unfrozen in the suite.

    python desktop/smoke.py <exe> [<args>...] [--window] [--zip <zip>]
        [--summary <md file>]

runs ``<exe> <args> --no-browser`` on a fresh data folder and walks it through the
steps below; the last one (DESK-03) launches it again on the same folder and
expects ``LF_ALREADY_RUNNING``. ``--window`` then runs ``<exe> <args>
--selftest-window`` on another fresh folder: the real App window must report
``LF_WINDOW_OK``. Exit 0 on a pass, 1 on the first failed step. On a pass,
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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, NamedTuple, TypeVar

MAP_IMAGE = (
    Path(__file__).resolve().parent.parent
    / "Screenshots_and_video_examples"
    / "San_Marcos_Laser_Tag_Map.jpg"
)
READY_TIMEOUT_S = 90
JOB_TIMEOUT_S = 120
INSTANCE_TIMEOUT_S = 30
WINDOW_TIMEOUT_S = 90


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


class Timings(NamedTuple):
    """What a passing smoke measured, in seconds from the process start."""

    launcher_ready_s: float  # the launcher's own LF_READY figure
    cold_start_s: float  # to the first GET / answered


class _Response(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> _Response:
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", content_type)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with _opener.open(request, timeout=60) as response:
            return _Response(response.status, dict(response.headers), response.read())
    except urllib.error.HTTPError as error:
        return _Response(error.code, dict(error.headers), error.read())


def _expect(response: _Response, wanted: int) -> None:
    if response.status != wanted:
        raise AssertionError(
            f"HTTP {response.status}, wanted {wanted}: {response.body[:300]!r}"
        )


def _api(
    url: str,
    wanted: int,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """A JSON call: POST ``body`` (GET without one), expect ``wanted``, return ``data``."""
    response = _request(
        url,
        method="GET" if body is None else "POST",
        data=None if body is None else json.dumps(body).encode(),
        headers=headers,
    )
    _expect(response, wanted)
    return json.loads(response.body)["data"]


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


def run_smoke(command: list[str], home: Path, *, check_spa: bool = True) -> Timings:
    """Launch ``command`` on data folder ``home`` and run every smoke step.

    Returns the launcher's ``LF_READY`` seconds and the cold start (process
    start to the first ``GET /`` answered). Raises
    ``SmokeFailure`` at the first failed step; the process is always killed.
    ``check_spa=False`` only times ``GET /``: an unfrozen launcher has no SPA
    until ``ng build`` has run (CI's Python jobs never run it). The frozen
    build always bundles one, so the CLI always checks it.
    """
    env = _env(home)
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
        return _steps(output, started, check_spa, command, env)
    finally:
        process.kill()
        process.wait(timeout=30)


def _check_spa(response: _Response) -> None:
    _expect(response, 200)
    if b"<app-root" not in response.body:
        raise AssertionError(f"GET / is not the SPA: {response.body[:300]!r}")


def _second_launch(
    command: list[str], env: dict[str, str], base: str, check_spa: bool
) -> None:
    """Launch ``command`` again on the same data folder while the first runs.

    It must exit 0 with ``LF_ALREADY_RUNNING`` within ``INSTANCE_TIMEOUT_S``,
    and the first launch's ``GET /`` must still answer.
    """
    second = subprocess.run(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=INSTANCE_TIMEOUT_S,
    )
    if second.returncode != 0 or "LF_ALREADY_RUNNING" not in second.stdout:
        raise AssertionError(
            f"second launch: exit code {second.returncode}, wanted 0 and "
            f"LF_ALREADY_RUNNING:\n{second.stdout[-2000:]}"
        )
    spa = _request(f"{base}/")
    if check_spa:
        _check_spa(spa)


def _steps(
    output: _Output,
    started: float,
    check_spa: bool,
    command: list[str],
    env: dict[str, str],
) -> Timings:
    with _step("ready", output):
        output.ready.wait(READY_TIMEOUT_S)
        if not output.ready_line:
            raise AssertionError(f"no LF_READY within {READY_TIMEOUT_S} s")
        _, ready_s, launch_url = output.ready_line.split()
    base = launch_url.split("/api/")[0]

    with _step("spa", output):
        spa = _request(f"{base}/")
        cold_start_s = time.perf_counter() - started
        if check_spa:
            _check_spa(spa)

    with _step("launch_code", output):
        launch = _request(launch_url)
        _expect(launch, 302)
        if launch.headers.get("Location") != "/":
            raise AssertionError(f"redirects to {launch.headers.get('Location')!r}")
        refresh = SimpleCookie(launch.headers["Set-Cookie"])["refresh_token"].value
        _expect(_request(launch_url), 403)

    with _step("refresh", output):
        tokens = _request(
            f"{base}/api/token/refresh/",
            method="POST",
            headers={"Cookie": f"refresh_token={refresh}"},
        )
        _expect(tokens, 200)
        auth = {"Authorization": f"Bearer {json.loads(tokens.body)['data']['access']}"}

    with _step("me", output):
        me = _api(f"{base}/api/accounts/me/", 200, headers=auth)
        wanted = ("local@desktop.invalid", True, None)
        got = (me["email"], me["desktop"], me["desktop_notice"])
        if got != wanted:
            raise AssertionError(f"me is {got}, wanted {wanted}")

    with _step("teams", output):
        generated = _api(
            f"{base}/api/teams/generate/",
            201,
            body={"num_teams": "2", "players_per_team": "6", "mean": 50, "std_dev": 15},
            headers=auth,
        )
        red, blue = (team["id"] for team in generated["created_teams"])
        teams = {"team_red": red, "team_blue": blue}

    with _step("batch", output):
        job = _api(
            f"{base}/api/simulate-batch/",
            200,
            body={**teams, "n": 1, "master_seed": 42},
            headers=auth,
        )
        deadline = time.monotonic() + JOB_TIMEOUT_S
        job_status = "running"
        while job_status == "running" and time.monotonic() < deadline:
            time.sleep(0.5)
            job_status = _api(
                f"{base}/api/simulate-batch/{job['job_id']}/", 200, headers=auth
            )["status"]
        if job_status != "complete":
            raise AssertionError(f"batch job is {job_status!r} after polling")

    with _step("report", output):
        game_round = _api(f"{base}/api/rounds/", 201, body=teams, headers=auth)
        report = _request(f"{base}/api/rounds/{game_round['id']}/report/", headers=auth)
        _expect(report, 200)
        if not report.body.startswith(b"%PDF"):
            raise AssertionError(f"the report is not a PDF: {report.body[:40]!r}")

    with _step("map", output):
        data, content_type = _multipart("Smoke Arena", MAP_IMAGE)
        upload = _request(
            f"{base}/api/maps/",
            method="POST",
            data=data,
            content_type=content_type,
            headers=auth,
        )
        _expect(upload, 201)
        map_id = json.loads(upload.body)["data"]["id"]
        _api(f"{base}/api/maps/{map_id}/zones/?zone_size=50", 200, headers=auth)

    # Last: the instance lock (DESK-03). Same command, same data folder.
    with _step("instance", output):
        _second_launch(command, env, base, check_spa)

    return Timings(float(ready_s), round(cold_start_s, 2))


def _env(home: Path) -> dict[str, str]:
    return {**os.environ, "LF_DESKTOP_HOME": str(home), "PYTHONUNBUFFERED": "1"}


def run_window_selftest(command: list[str], home: Path) -> float:
    """Run ``command`` (``<exe> --selftest-window``) on data folder ``home``.

    Returns the seconds from its ``LF_WINDOW_OK <s>`` line. Raises
    ``SmokeFailure("window", <output>)`` on ``LF_WINDOW_FAIL``, a non-zero exit
    or no exit within ``WINDOW_TIMEOUT_S``.
    """
    print("smoke: window", flush=True)
    try:
        result = subprocess.run(
            command,
            env=_env(home),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=WINDOW_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as expired:
        output = expired.output or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        raise SmokeFailure(
            "window",
            f"no LF_WINDOW_OK within {WINDOW_TIMEOUT_S} s"
            f"\n--- launcher output ---\n{output}",
        )
    output = result.stdout
    ready = [line for line in output.splitlines() if line.startswith("LF_WINDOW_OK ")]
    if result.returncode != 0 or not ready:
        raise SmokeFailure(
            "window",
            f"exit code {result.returncode}, wanted 0 and LF_WINDOW_OK"
            f"\n--- launcher output ---\n{output}",
        )
    return float(ready[0].split()[1])


def summary_markdown(
    timings: Timings,
    *,
    folder_mb: float,
    zip_mb: float,
    window_s: float | None = None,
) -> str:
    """The job summary table: the build's sizes and the smoke's times.

    ``window_s`` is the App window self-test's figure; its row shows only when
    ``--window`` ran.
    """
    rows = [
        "### Desktop build",
        "",
        "| | |",
        "|---|---|",
        f"| Zip | {zip_mb:.1f} MB |",
        f"| Unpacked | {folder_mb:.1f} MB |",
        f"| Launcher ready | {timings.launcher_ready_s:.2f} s |",
        f"| Cold start to the SPA | {timings.cold_start_s:.2f} s |",
    ]
    if window_s is not None:
        rows.append(f"| App window ready | {window_s:.2f} s |")
    return "\n".join([*rows, ""])


def _megabytes(path: Path) -> float:
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    return sum(p.stat().st_size for p in files) / (1024 * 1024)


_T = TypeVar("_T")


def _in_fresh_folder(run: Callable[[Path], _T]) -> _T:
    """``run`` on a new temp data folder, removed afterwards."""
    # Windows may still hold the killed process's SQLite files for a moment.
    with tempfile.TemporaryDirectory(
        prefix="lf-smoke-", ignore_cleanup_errors=True
    ) as home:
        return run(Path(home))


def main(argv: list[str] | None = None, *, check_spa: bool = True) -> int:
    """The CLI. ``check_spa`` is for the in-suite test of an unfrozen launcher,
    which runs as ``<python> desktop/main.py`` (the extra ``args``)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("exe", type=Path)
    parser.add_argument(
        "args", nargs="*", help="passed before --no-browser (and --selftest-window)"
    )
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument(
        "--window",
        action="store_true",
        help="after a pass, run <exe> <args> --selftest-window on a fresh folder",
    )
    args = parser.parse_args(argv)
    launcher = [str(args.exe), *args.args]
    window_s: float | None = None
    try:
        timings = _in_fresh_folder(
            lambda home: run_smoke(
                [*launcher, "--no-browser"], home, check_spa=check_spa
            )
        )
        if args.window:
            window_s = _in_fresh_folder(
                lambda home: run_window_selftest([*launcher, "--selftest-window"], home)
            )
    except SmokeFailure as failure:
        print(f"smoke: FAILED at {failure.step}\n{failure.detail}", flush=True)
        return 1
    print(f"smoke: passed {timings} window_s={window_s}", flush=True)
    if args.summary:
        text = summary_markdown(
            timings,
            folder_mb=_megabytes(args.exe.parent),
            zip_mb=_megabytes(args.zip) if args.zip else 0.0,
            window_s=window_s,
        )
        with args.summary.open("a", encoding="utf-8") as summary:
            summary.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
