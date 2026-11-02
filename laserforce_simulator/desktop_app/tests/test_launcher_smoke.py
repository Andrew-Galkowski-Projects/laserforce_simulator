"""DESK-01 / DESK-02: start the real launcher and run the Desktop build smoke on it.

The launcher runs as a subprocess (``desktop/main.py --no-browser``) on a
fresh data folder, so this covers the desktop settings, the URLconf, the
Launch code, the Local Account and the in-process job worker together
(ADR-0048: a garbage-collected worker leaves jobs at PENDING forever).
DESK-02: the steps live in ``desktop/smoke.py``, which CI also runs against
the frozen exe, so the two smokes cannot drift apart.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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


def test_the_launcher_passes_every_smoke_step(tmp_path: Path) -> None:
    # Through the CLI, as CI runs it, so its pass path (the summary) is covered
    # too; one launch keeps the suite to one ~8 s smoke.
    summary = tmp_path / "summary.md"

    exit_code = smoke.main(
        [sys.executable, str(ENTRY), "--summary", str(summary)], check_spa=SPA_BUILT
    )

    assert exit_code == 0
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
