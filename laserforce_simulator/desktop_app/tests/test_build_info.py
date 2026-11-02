"""DESK-04 (spec #503): the Desktop build's record of itself.

``desktop/write_build_info.py`` (loaded from its file, as the build runs it)
writes it; ``desktop_app.build_info`` reads it back and labels it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]
WRITER = REPO / "desktop" / "write_build_info.py"
TODAY = date(2026, 11, 2)
SHA = "abc1234def5678abc1234def5678abc1234def56"


def _load_writer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("desktop_write_build_info", WRITER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("ref", "version", "channel"),
    [
        ("refs/tags/v0.1.0", "0.1.0", "release"),
        ("refs/tags/v10.20.30", "10.20.30", "release"),
        ("refs/heads/main", None, "latest"),
        ("refs/pull/5/merge", None, "dev"),
        ("refs/tags/v0.2.0-rc1", None, "dev"),
        ("refs/tags/v1.2.3-rc1", None, "dev"),
        ("refs/tags/v1.2", None, "dev"),
        ("refs/heads/feature", None, "dev"),
        ("refs/heads/v0.1.0", None, "dev"),
        ("v0.1.0", None, "dev"),
        (None, None, "dev"),
    ],
)
def test_build_info_reads_the_channel_and_version_from_github_ref(
    ref: str | None, version: str | None, channel: str
) -> None:
    env = {"GITHUB_SHA": SHA}
    if ref is not None:
        env["GITHUB_REF"] = ref
    assert _load_writer().build_info(env, TODAY) == {
        "version": version,
        "channel": channel,
        "sha": SHA,
        "built": "2026-11-02",
    }


def test_build_info_without_github_sha_has_no_sha() -> None:
    info = _load_writer().build_info({"GITHUB_REF": "refs/heads/main"}, TODAY)
    assert info["sha"] is None
    assert info["channel"] == "latest"


def test_the_cli_writes_the_build_info_json(tmp_path: Path) -> None:
    out = tmp_path / "build_info.json"
    env = {**os.environ, "GITHUB_REF": "refs/tags/v0.1.0", "GITHUB_SHA": SHA}
    subprocess.run([sys.executable, str(WRITER), str(out)], env=env, check=True)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["version"] == "0.1.0"
    assert written["channel"] == "release"
    assert written["sha"] == SHA
    assert date.fromisoformat(written["built"])


def test_the_writer_shares_the_update_checks_release_tag_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One rule for both: the writer takes desktop_app.build_info's RELEASE_TAG
    # (the frozen app has no desktop/ folder to import from).
    from desktop_app import build_info as app_build_info

    monkeypatch.setattr(app_build_info, "RELEASE_TAG", re.compile(r"^never$"))
    writer = _load_writer()
    assert writer.RELEASE_TAG is app_build_info.RELEASE_TAG
    assert writer.build_info({"GITHUB_REF": "refs/tags/v0.1.0"}, TODAY)["channel"] == (
        "dev"
    )


def _write(tmp_path: Path, **info: str | None) -> Path:
    path = tmp_path / "build_info.json"
    path.write_text(json.dumps(info), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("version", "channel", "sha", "label"),
    [
        ("0.1.0", "release", SHA, "v0.1.0"),
        (None, "latest", SHA, "latest abc1234"),
        (None, "dev", SHA, "dev abc1234"),
        (None, "dev", None, "dev"),
    ],
)
def test_load_build_info_reads_the_file_and_labels_it(
    settings: object,
    tmp_path: Path,
    version: str | None,
    channel: str,
    sha: str | None,
    label: str,
) -> None:
    from desktop_app.build_info import BuildInfo, load_build_info

    settings.DESKTOP_BUILD_INFO = str(  # type: ignore[attr-defined]
        _write(tmp_path, version=version, channel=channel, sha=sha, built="2026-11-02")
    )
    info = load_build_info()
    assert info == BuildInfo(
        version=version, channel=channel, sha=sha, built="2026-11-02"
    )
    assert info.label == label


@pytest.mark.parametrize("missing", ["unset", "no file"])
def test_no_build_info_is_a_dev_build(
    settings: object, tmp_path: Path, missing: str
) -> None:
    from desktop_app.build_info import BuildInfo, load_build_info

    settings.DESKTOP_BUILD_INFO = (  # type: ignore[attr-defined]
        None if missing == "unset" else str(tmp_path / "absent.json")
    )
    info = load_build_info()
    assert info == BuildInfo(version=None, channel="dev", sha=None, built=None)
    assert info.label == "dev"
