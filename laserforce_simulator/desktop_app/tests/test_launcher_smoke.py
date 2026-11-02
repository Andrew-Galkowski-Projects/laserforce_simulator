"""DESK-01 / DESK-02: start the real launcher and run the Desktop build smoke on it.

The launcher runs as a subprocess (``desktop/main.py --no-browser``) on a
fresh data folder, so this covers the desktop settings, the URLconf, the
Launch code, the Local Account and the in-process job worker together
(ADR-0048: a garbage-collected worker leaves jobs at PENDING forever).
DESK-02: the steps live in ``desktop/smoke.py``, which CI also runs against
the frozen exe, so the two smokes cannot drift apart.
"""

from __future__ import annotations

import http.server
import importlib.util
import os
import socket
import subprocess
import sys
import threading
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
ENTRY = REPO / "desktop" / "main.py"
# CI's Python jobs never build the SPA, so unfrozen `/` is a 404 there; the
# frozen smoke in CI's `desktop` job always checks it (DESK-02).
SPA_BUILT = (
    REPO / "frontend" / "dist" / "frontend" / "browser" / "index.html"
).exists()


def _load_smoke() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "desktop_smoke", REPO / "desktop" / "smoke.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()


def test_the_launcher_passes_every_smoke_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Through the CLI, as CI runs it, so its pass path (the summary) is covered
    # too; one launch keeps the suite to one ~8 s smoke. DESK-03: the last
    # step launches it a second time and takes the real instance lock; the
    # wrapper only records that the real check ran.
    summary = tmp_path / "summary.md"
    second_launches: list[list[str]] = []
    real_second_launch = smoke._second_launch

    def recorded(
        command: list[str], env: dict[str, str], base: str, check_spa: bool
    ) -> None:
        second_launches.append(command)
        real_second_launch(command, env, base, check_spa)

    monkeypatch.setattr(smoke, "_second_launch", recorded)

    exit_code = smoke.main(
        [sys.executable, str(ENTRY), "--summary", str(summary)], check_spa=SPA_BUILT
    )

    assert exit_code == 0
    assert second_launches == [[sys.executable, str(ENTRY), "--no-browser"]]
    text = summary.read_text(encoding="utf-8")
    assert "| Zip | 0.0 MB |" in text  # no --zip given
    assert "| Launcher ready |" in text
    assert "| Cold start to the SPA |" in text


def test_a_process_that_exits_before_ready_fails_the_ready_step(
    tmp_path: Path,
) -> None:
    with pytest.raises(smoke.SmokeFailure) as caught:
        smoke.run_smoke([sys.executable, "-c", "print('boom')"], tmp_path)

    assert caught.value.step == "ready"
    assert "boom" in caught.value.detail


def test_the_cli_exits_1_and_writes_no_summary_on_a_failure(tmp_path: Path) -> None:
    # Python itself rejects ``--no-browser``, so it exits before LF_READY.
    summary = tmp_path / "summary.md"

    assert smoke.main([sys.executable, "--summary", str(summary)]) == 1
    assert not summary.exists()


class _Spa(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<html><app-root></app-root></html>")

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def running_base() -> Iterator[str]:
    """A stand-in for the first launch: a local server whose ``GET /`` is the SPA."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Spa)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _second(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_a_second_launch_that_reports_already_running_passes(
    running_base: str,
) -> None:
    smoke._second_launch(
        _second("print('LF_ALREADY_RUNNING')"),
        dict(os.environ),
        running_base,
        check_spa=True,
    )


def test_a_second_launch_must_print_lf_already_running(running_base: str) -> None:
    # Exit 0 alone is not enough: a launcher with no lock might exit 0 too.
    with pytest.raises(AssertionError, match="LF_ALREADY_RUNNING"):
        smoke._second_launch(
            _second("print('LF_READY 1.0 http://x')"),
            dict(os.environ),
            running_base,
            check_spa=False,
        )


def test_a_second_launch_must_exit_0(running_base: str) -> None:
    with pytest.raises(AssertionError, match="exit code 2"):
        smoke._second_launch(
            _second("import sys; print('LF_ALREADY_RUNNING'); sys.exit(2)"),
            dict(os.environ),
            running_base,
            check_spa=False,
        )


def test_a_second_launch_must_end_within_the_instance_timeout(
    running_base: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "INSTANCE_TIMEOUT_S", 1)

    with pytest.raises(subprocess.TimeoutExpired):
        smoke._second_launch(
            _second("import time; time.sleep(30)"),
            dict(os.environ),
            running_base,
            check_spa=False,
        )


def test_the_first_launch_must_still_answer_after_the_second(tmp_path: Path) -> None:
    # A second launch that took the first one down: nothing listens any more.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_base = f"http://127.0.0.1:{probe.getsockname()[1]}"

    with pytest.raises(urllib.error.URLError):
        smoke._second_launch(
            _second("print('LF_ALREADY_RUNNING')"),
            dict(os.environ),
            dead_base,
            check_spa=False,
        )


def _fake_launcher(tmp_path: Path, body: str) -> Path:
    """A script standing in for the exe: it records its argv and data folder."""
    script = tmp_path / "fake_launcher.py"
    script.write_text(
        "import os, sys\n"
        f"open({str(tmp_path / 'argv.txt')!r}, 'w').write("
        "' '.join(sys.argv[1:]) + '|' + os.environ['LF_DESKTOP_HOME'])\n" + body,
        encoding="utf-8",
    )
    return script


def _passing_smoke(command: list[str], home: Path, *, check_spa: bool = True) -> Any:
    return smoke.Timings(launcher_ready_s=4.6, cold_start_s=4.71)


def _window_body(tmp_path: Path, outcomes: list[str]) -> str:
    """A fake launcher body for ``--window``, as the real one behaves (BUG-WV-01).

    With ``WEBVIEW2_USER_DATA_FOLDER`` under a regular file (the
    ``window_failure`` step) it fails to start; anything else exits 3. Without
    it, the Nth self-test run prints ``outcomes[N]`` (the last one repeats) and
    counts itself in ``runs.txt``.
    """
    runs = tmp_path / "runs.txt"
    return (
        "from pathlib import Path\n"
        "data = os.environ.get('WEBVIEW2_USER_DATA_FOLDER')\n"
        "if data:\n"
        "    if not Path(data).parent.is_file(): sys.exit(3)\n"
        "    print('LF_WINDOW_FAIL WindowStartFailed: the App window did not load"
        " within 20 s')\n"
        "    sys.exit(0)\n"
        f"runs = Path({str(runs)!r})\n"
        "n = int(runs.read_text()) if runs.exists() else 0\n"
        "runs.write_text(str(n + 1))\n"
        f"outcomes = {outcomes!r}\n"
        "print(outcomes[min(n, len(outcomes) - 1)])\n"
    )


def _runs(tmp_path: Path) -> int:
    return int((tmp_path / "runs.txt").read_text())


def test_the_cli_window_flag_runs_the_selftest_on_a_fresh_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The launcher smoke itself is the test above; here only --window's wiring.
    smoke_homes: list[Path] = []

    def passing_smoke(command: list[str], home: Path, *, check_spa: bool = True) -> Any:
        smoke_homes.append(home)
        return _passing_smoke(command, home)

    monkeypatch.setattr(smoke, "run_smoke", passing_smoke)
    script = _fake_launcher(tmp_path, _window_body(tmp_path, ["LF_WINDOW_OK 2.5"]))
    summary = tmp_path / "summary.md"

    exit_code = smoke.main(
        [sys.executable, str(script), "--window", "--summary", str(summary)]
    )

    assert exit_code == 0
    text = summary.read_text(encoding="utf-8")
    assert "| App window ready | 2.50 s |" in text
    # BUG-WV-01: --window then runs the window_failure step (the last launch).
    assert "| App window failure fallback |" in text
    args, home = (tmp_path / "argv.txt").read_text().split("|")
    assert args == "--selftest-window"
    assert Path(home) != smoke_homes[0]  # a fresh data folder, not the smoke's
    assert _runs(tmp_path) == 1


def test_the_cli_exits_1_when_the_window_selftest_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "run_smoke", _passing_smoke)
    script = _fake_launcher(tmp_path, "print('LF_WINDOW_FAIL Loading...')\n")
    summary = tmp_path / "summary.md"

    exit_code = smoke.main(
        [sys.executable, str(script), "--window", "--summary", str(summary)]
    )

    assert exit_code == 1
    assert not summary.exists()


def test_the_window_selftest_returns_the_lf_window_ok_seconds(tmp_path: Path) -> None:
    command = [
        sys.executable,
        "-c",
        "import os; print('loading'); print('LF_WINDOW_OK 1.5');"
        " print('home', os.environ['LF_DESKTOP_HOME'])",
    ]

    assert smoke.run_window_selftest(command, tmp_path) == 1.5


def test_the_window_selftest_fails_on_lf_window_fail(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "print('LF_WINDOW_FAIL Loading...')"]

    with pytest.raises(smoke.SmokeFailure) as caught:
        smoke.run_window_selftest(command, tmp_path)

    assert caught.value.step == "window"
    assert "LF_WINDOW_FAIL Loading..." in caught.value.detail


def test_the_window_selftest_fails_on_a_non_zero_exit(tmp_path: Path) -> None:
    # Even after an LF_WINDOW_OK: a crash on close is still a failure.
    command = [
        sys.executable,
        "-c",
        "import sys; print('LF_WINDOW_OK 1.5', flush=True); sys.exit(3)",
    ]

    with pytest.raises(smoke.SmokeFailure) as caught:
        smoke.run_window_selftest(command, tmp_path)

    assert caught.value.step == "window"
    assert "exit code 3" in caught.value.detail


def test_the_window_selftest_fails_on_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "WINDOW_TIMEOUT_S", 1)
    command = [
        sys.executable,
        "-c",
        "import time; print('waiting', flush=True); time.sleep(30)",
    ]

    with pytest.raises(smoke.SmokeFailure) as caught:
        smoke.run_window_selftest(command, tmp_path)

    assert caught.value.step == "window"
    assert "no LF_WINDOW_OK within 1 s" in caught.value.detail


def test_the_summary_lists_sizes_and_times() -> None:
    text = smoke.summary_markdown(
        smoke.Timings(launcher_ready_s=4.6, cold_start_s=4.71),
        folder_mb=234.04,
        zip_mb=99.8,
    )

    assert "| Zip | 99.8 MB |" in text
    assert "| Unpacked | 234.0 MB |" in text
    assert "| Launcher ready | 4.60 s |" in text
    assert "| Cold start to the SPA | 4.71 s |" in text
    assert "App window ready" not in text  # no window_s: --window did not run


def test_the_summary_shows_the_app_window_row_when_window_s_is_given() -> None:
    text = smoke.summary_markdown(
        smoke.Timings(launcher_ready_s=4.6, cold_start_s=4.71),
        folder_mb=234.04,
        zip_mb=99.8,
        window_s=6.234,
    )

    assert "| App window ready | 6.23 s |" in text


# BUG-WV-01: the window_failure step, and one retry of a failed WebView2 start.

START_FAILED = "LF_WINDOW_FAIL WindowStartFailed: the App window did not load in 20 s"


def test_the_retried_line_names_the_launchers_start_failure() -> None:
    # smoke.py stays stdlib-only (the frozen smoke's Python has no Django), so
    # it spells the line out; the launcher prints f"LF_WINDOW_FAIL {type}: ..."
    # (launcher._open_shell). Renaming the exception must turn this red.
    from desktop_app.shell import WindowStartFailed

    assert smoke.WINDOW_START_FAILED == f"LF_WINDOW_FAIL {WindowStartFailed.__name__}"


def test_the_cli_retries_the_window_once_after_a_failed_webview2_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "run_smoke", _passing_smoke)
    body = _window_body(tmp_path, [START_FAILED, "LF_WINDOW_OK 3.0"])
    script = _fake_launcher(tmp_path, body)
    summary = tmp_path / "summary.md"

    exit_code = smoke.main(
        [sys.executable, str(script), "--window", "--summary", str(summary)]
    )

    assert exit_code == 0
    assert _runs(tmp_path) == 2
    text = summary.read_text(encoding="utf-8")
    assert "| App window ready | 3.00 s (after 1 retry) |" in text


def test_the_cli_does_not_retry_any_other_window_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "run_smoke", _passing_smoke)
    body = _window_body(tmp_path, ["LF_WINDOW_FAIL Loading...", "LF_WINDOW_OK 3.0"])
    script = _fake_launcher(tmp_path, body)

    assert smoke.main([sys.executable, str(script), "--window"]) == 1
    assert _runs(tmp_path) == 1


def test_the_cli_fails_when_the_retry_fails_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "run_smoke", _passing_smoke)
    script = _fake_launcher(tmp_path, _window_body(tmp_path, [START_FAILED]))

    assert smoke.main([sys.executable, str(script), "--window"]) == 1
    assert _runs(tmp_path) == 2


def test_the_window_failure_step_passes_on_a_failed_start(tmp_path: Path) -> None:
    # The fake checks the env as the real step relies on it: the WebView2 data
    # folder sits under a regular file, which no user (admin or not) can create.
    command = [
        sys.executable,
        "-c",
        "import os, sys; from pathlib import Path;"
        " data = Path(os.environ['WEBVIEW2_USER_DATA_FOLDER']);"
        " sys.exit(3) if not data.parent.is_file() else None;"
        f" print({START_FAILED!r})",
    ]

    seconds = smoke.run_window_failure_selftest(command, tmp_path)

    assert 0 <= seconds < smoke.WINDOW_FAILURE_TIMEOUT_S


def test_the_window_failure_step_fails_when_the_window_starts(tmp_path: Path) -> None:
    command = [sys.executable, "-c", "print('LF_WINDOW_OK 1.5')"]

    with pytest.raises(smoke.SmokeFailure) as caught:
        smoke.run_window_failure_selftest(command, tmp_path)

    assert caught.value.step == "window_failure"
    assert "LF_WINDOW_OK 1.5" in caught.value.detail


def test_the_window_failure_step_fails_on_a_non_zero_exit(tmp_path: Path) -> None:
    command = [
        sys.executable,
        "-c",
        f"import sys; print({START_FAILED!r}, flush=True); sys.exit(3)",
    ]

    with pytest.raises(smoke.SmokeFailure) as caught:
        smoke.run_window_failure_selftest(command, tmp_path)

    assert caught.value.step == "window_failure"
    assert "exit code 3" in caught.value.detail


def test_the_window_failure_step_fails_on_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smoke, "WINDOW_FAILURE_TIMEOUT_S", 1)
    command = [sys.executable, "-c", "import time; time.sleep(30)"]

    with pytest.raises(smoke.SmokeFailure) as caught:
        smoke.run_window_failure_selftest(command, tmp_path)

    assert caught.value.step == "window_failure"
    assert "within 1 s" in caught.value.detail


def test_the_summary_shows_the_retry_and_the_failure_fallback_rows() -> None:
    text = smoke.summary_markdown(
        smoke.Timings(launcher_ready_s=4.6, cold_start_s=4.71),
        folder_mb=234.04,
        zip_mb=99.8,
        window_s=5.05,
        window_retried=True,
        window_failure_s=21.3,
    )

    assert "| App window ready | 5.05 s (after 1 retry) |" in text
    assert "| App window failure fallback | 21.30 s |" in text
