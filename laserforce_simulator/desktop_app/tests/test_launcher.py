"""DESK-01: the launcher's data folder, environment, upgrade reset and Local Account.

``migrate_or_reset`` takes the ``migrate`` callable, so these tests drive it
with a fake one over files in ``tmp_path``; nothing here touches a real
migration.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from accounts.models import User
from desktop_app import launcher

ENV_KEYS = (
    "LF_DESKTOP_HOME",
    "LF_DESKTOP_SPA",
    "SECRET_KEY",
    "DATABASE_URL",
    "DJANGO_SETTINGS_MODULE",
)


class FakeMigrate:
    """Raises ``errors`` in turn, then succeeds; counts its calls."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Restore every variable ``prepare_environment`` writes."""
    for key in ENV_KEYS:
        monkeypatch.setenv(key, "inherited")
    return monkeypatch


def _db(data: Path, content: bytes = b"old league") -> Path:
    db = data / "db.sqlite3"
    db.write_bytes(content)
    return db


def test_migrate_success_returns_no_notice_and_keeps_the_database(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    migrate = FakeMigrate()
    assert launcher.migrate_or_reset(tmp_path, migrate) is None
    assert migrate.calls == 1
    assert db.read_bytes() == b"old league"
    assert list(tmp_path.glob("*.bak")) == []


def test_a_failed_upgrade_moves_the_database_aside_and_starts_fresh(
    tmp_path: Path,
) -> None:
    _db(tmp_path)
    (tmp_path / "db.sqlite3-wal").write_bytes(b"wal")
    (tmp_path / "db.sqlite3-shm").write_bytes(b"shm")
    migrate = FakeMigrate(RuntimeError("inconsistent history"))

    notice = launcher.migrate_or_reset(tmp_path, migrate)

    assert migrate.calls == 2
    [backup] = list(tmp_path.glob("db.sqlite3.*.bak"))
    assert re.fullmatch(r"db\.sqlite3\.\d{8}-\d{6}\.bak", backup.name)
    assert backup.read_bytes() == b"old league"
    assert not (tmp_path / "db.sqlite3-wal").exists()
    assert not (tmp_path / "db.sqlite3-shm").exists()
    assert notice == (
        "Your saved data could not be upgraded (RuntimeError). "
        f"It was moved to {backup.name} and a fresh database was started."
    )


def test_a_failing_fresh_database_re_raises(tmp_path: Path) -> None:
    migrate = FakeMigrate(RuntimeError("broken build"))
    with pytest.raises(RuntimeError, match="broken build"):
        launcher.migrate_or_reset(tmp_path, migrate)
    assert migrate.calls == 1


def test_a_failing_empty_database_re_raises_and_is_left_alone(tmp_path: Path) -> None:
    db = _db(tmp_path, b"")
    migrate = FakeMigrate(RuntimeError("broken build"))
    with pytest.raises(RuntimeError):
        launcher.migrate_or_reset(tmp_path, migrate)
    assert migrate.calls == 1
    assert db.exists()
    assert list(tmp_path.glob("*.bak")) == []


def test_data_dir_honours_lf_desktop_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LF_DESKTOP_HOME", str(tmp_path))
    assert launcher.data_dir() == tmp_path


def test_data_dir_defaults_to_local_app_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LF_DESKTOP_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert launcher.data_dir() == tmp_path / "LaserforceSimulator"


def test_prepare_environment_points_django_at_the_data_folder(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    spa = tmp_path / "spa"
    launcher.prepare_environment(tmp_path, spa)

    assert (tmp_path / "logs").is_dir()
    assert os.environ["LF_DESKTOP_HOME"] == str(tmp_path)
    assert os.environ["LF_DESKTOP_SPA"] == str(spa)
    assert os.environ["DJANGO_SETTINGS_MODULE"] == "desktop_app.settings"
    assert os.environ["DATABASE_URL"] == (
        "sqlite:///" + (tmp_path / "db.sqlite3").as_posix()
    )
    assert os.environ["SECRET_KEY"] == (tmp_path / "secret_key").read_text()
    assert len(os.environ["SECRET_KEY"]) >= 50


def test_prepare_environment_keeps_the_secret_key_across_launches(
    env: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import os

    launcher.prepare_environment(tmp_path, tmp_path)
    first = os.environ["SECRET_KEY"]
    launcher.prepare_environment(tmp_path, tmp_path)
    assert os.environ["SECRET_KEY"] == first


@pytest.mark.django_db
def test_local_account_is_created_once_with_no_usable_password() -> None:
    first = launcher.local_account()
    assert first.email == "local@desktop.invalid"
    assert not first.has_usable_password()
    assert not first.is_staff
    assert not first.is_superuser
    assert launcher.local_account().pk == first.pk
    assert User.objects.filter(email="local@desktop.invalid").count() == 1
