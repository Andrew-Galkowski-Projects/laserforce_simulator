"""DESK-04: what ``desktop/laserforce.spec`` puts in the Desktop build.

The spec is run with stand-ins for PyInstaller's spec globals and
``PyInstaller.utils.hooks`` (neither the suite nor CI's Python jobs install
PyInstaller), so the test reads the data files it hands to ``Analysis``.
The frozen smoke in CI's ``desktop`` job checks the same files from inside
the built app.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from core.services import _DEFAULT_MAPS

REPO = Path(__file__).resolve().parents[3]
SPEC = REPO / "desktop" / "laserforce.spec"


def _run_spec(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Run the spec; the ``datas`` it passes to ``Analysis``."""
    hooks = types.ModuleType("PyInstaller.utils.hooks")
    hooks.collect_data_files = lambda *args, **kwargs: []  # type: ignore[attr-defined]
    hooks.collect_submodules = lambda *args, **kwargs: []  # type: ignore[attr-defined]
    for name in ("PyInstaller", "PyInstaller.utils"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)

    analyses: list[dict[str, Any]] = []

    class Analysis:
        def __init__(self, scripts: list[str], **kwargs: Any) -> None:
            analyses.append(kwargs)
            self.pure = self.scripts = self.binaries = self.datas = []

    def stand_in(*args: Any, **kwargs: Any) -> None:
        return None

    spec_globals = {
        "SPECPATH": str(SPEC.parent),
        "Analysis": Analysis,
        "PYZ": stand_in,
        "EXE": stand_in,
        "COLLECT": stand_in,
    }
    exec(compile(SPEC.read_text(encoding="utf-8"), str(SPEC), "exec"), spec_globals)
    [analysis] = analyses
    return [(str(Path(src)), dest) for src, dest in analysis["datas"]]


@pytest.fixture
def build_info(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    # desktop/build.ps1 writes the file and names it in LF_BUILD_INFO.
    path = tmp_path / "build_info.json"
    monkeypatch.setenv("LF_BUILD_INFO", str(path))
    return path


def test_the_build_bundles_only_the_two_default_map_images(
    monkeypatch: pytest.MonkeyPatch, build_info: Path
) -> None:
    datas = _run_spec(monkeypatch)
    maps = sorted(entry for entry in datas if entry[1] == "default_maps")
    folder = REPO / "Screenshots_and_video_examples"
    assert maps == sorted(
        (str(folder / filename), "default_maps") for _name, filename in _DEFAULT_MAPS
    )
    assert all(Path(src).is_file() for src, _dest in maps)


def test_the_build_bundles_the_build_record_at_the_root(
    monkeypatch: pytest.MonkeyPatch, build_info: Path
) -> None:
    assert (str(build_info), ".") in _run_spec(monkeypatch)


def test_the_build_refuses_to_run_without_a_build_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A PyInstaller run that skipped build.ps1 would ship no build record.
    monkeypatch.delenv("LF_BUILD_INFO", raising=False)
    with pytest.raises(KeyError, match="LF_BUILD_INFO"):
        _run_spec(monkeypatch)
