"""DESK-01: the desktop settings module's own values.

Imported fresh as a plain module (the suite itself runs on the Hosted
settings); it reads what the launcher puts in the environment.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def desktop_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    monkeypatch.setenv("LF_DESKTOP_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LF_DESKTOP_SPA", str(tmp_path / "spa"))
    monkeypatch.delitem(sys.modules, "desktop_app.settings", raising=False)
    module = importlib.import_module("desktop_app.settings")
    monkeypatch.delitem(sys.modules, "desktop_app.settings")
    return module


def test_jobs_run_on_the_in_process_worker_not_eagerly(
    desktop_settings: ModuleType,
) -> None:
    # Eager mode would block the request for the whole job (ADR-0048).
    assert desktop_settings.CELERY_TASK_ALWAYS_EAGER is False
    assert desktop_settings.CELERY_BROKER_URL == "memory://"
    assert desktop_settings.CELERY_RESULT_BACKEND == "cache+memory://"


def test_it_serves_this_machine_only_with_debug_off(
    desktop_settings: ModuleType,
) -> None:
    assert desktop_settings.DESKTOP_APP is True
    assert desktop_settings.DEBUG is False
    assert desktop_settings.ALLOWED_HOSTS == ["127.0.0.1", "localhost"]
    assert desktop_settings.CORS_ALLOWED_ORIGINS == []
    assert desktop_settings.CSRF_TRUSTED_ORIGINS == []
    assert desktop_settings.REFRESH_COOKIE_SECURE is False
    assert desktop_settings.ROOT_URLCONF == "desktop_app.urls"


def test_data_and_the_spa_come_from_the_launchers_folders(
    desktop_settings: ModuleType, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    assert desktop_settings.MEDIA_ROOT == home / "media"
    assert desktop_settings.SERVE_MEDIA is True
    assert desktop_settings.SPA_ROOT == tmp_path / "spa"
    assert desktop_settings.WHITENOISE_ROOT == tmp_path / "spa"
    handler = desktop_settings.LOGGING["handlers"]["file"]
    assert handler["filename"] == str(home / "logs" / "desktop.log")
