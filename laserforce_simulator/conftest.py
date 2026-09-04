"""Project-level conftest.

API-03 (Async batch simulation via Celery + Redis, ADR-0013): every pytest
run must set EAGER mode on the Celery app BEFORE any test calls
``.delay()`` so tasks execute synchronously in-process — no Redis broker
required for tests/CI.

Setting the env var alone (``LF_CELERY_EAGER=1``) is not sufficient: the
Celery app reads settings at construction time via ``config_from_object``,
so a later env-var flip does not propagate. ``pytest_configure`` flips the
app's runtime config directly, guaranteeing EAGER is on by the time any
test imports a task.
"""

from __future__ import annotations

import os

os.environ.setdefault("LF_CELERY_EAGER", "1")


def pytest_configure(config):
    from laserforce_simulator.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True


# ---------------------------------------------------------------------------
# UX-01 (Accounts and uniform `manager` ownership, ADR-0038) -- the autouse
# login fixture.
#
# `LoginRequiredMiddleware` gates every view in the project, so the ~1237
# pre-existing view-test calls would all 302 to the login page without this.
# Rows those tests create stay **Unmanaged** (`manager IS NULL`), which is
# readable AND writable by any authenticated Account, so no per-test manager
# stamping is needed.
#
# See `.claude/worktrees/ux-01-seam-contract.md` section 11.1.
# ---------------------------------------------------------------------------

import pytest

SHARED_MANAGER_EMAIL = "test-manager@example.com"


def get_shared_manager():
    """Return (creating on first call) the shared test **Account**.

    Idempotent within a test's transaction. Used by the autouse fixture and
    available to any test that needs the Account object itself -- notably the
    73 sites that build their own ``Client()`` / ``APIClient()`` inside the
    test body, which the autouse fixture cannot reach (contract section 11.2).

    Worker-safe under ``-n auto --dist worksteal``: ``get_or_create`` runs
    inside the calling test's own transaction, keyed on the unique
    ``SHARED_MANAGER_EMAIL``, so parallel xdist workers never collide.
    """
    from django.contrib.auth import get_user_model

    user, _created = get_user_model().objects.get_or_create(
        email=SHARED_MANAGER_EMAIL,
        defaults={"is_active": True},
    )
    return user


def _is_django_test_case(instance) -> bool:
    """True when ``instance`` is a ``django.test.SimpleTestCase`` subclass."""
    if instance is None:
        return False
    try:
        from django.test import SimpleTestCase
    except Exception:  # pragma: no cover - Django always importable here
        return False
    return isinstance(instance, SimpleTestCase)


def _test_has_db_access(request) -> bool:
    """True when the test may touch the database.

    Deliberately does NOT call ``request.getfixturevalue("db")`` -- doing so
    unconditionally would force a database onto pure unit tests that declare
    no ``django_db`` marker and subclass no ``TestCase``.
    """
    if request.node.get_closest_marker("django_db") is not None:
        return True
    instance = getattr(request, "instance", None)
    if not _is_django_test_case(instance):
        return False
    # ``SimpleTestCase.databases`` is an empty frozenset unless the test opts
    # into the database; ``TransactionTestCase`` / ``TestCase`` default it to
    # ``{"default"}``.
    return bool(getattr(instance, "databases", None))


@pytest.fixture(autouse=True)
def force_login_shared_manager(request):
    """UX-01 -- log every DB-backed test in as the shared Account.

    Tests with no database access are left completely alone: the fixture
    yields immediately without touching the ORM.

    Two delivery paths, because a Django ``TestCase`` builds ``self.client``
    inside ``_pre_setup``, which unittest runs *after* every function-scoped
    fixture has been set up:

    * ``TestCase`` subclasses -- wrap the instance's ``_pre_setup`` so the
      login lands on ``request.instance.client`` the moment Django creates it.
    * plain-pytest tests -- ``request.getfixturevalue("client")``.

    ``force_login`` (not ``client.login``) -- no password round-trip, no
    hasher cost.
    """
    if not _test_has_db_access(request):
        yield
        return

    instance = getattr(request, "instance", None)

    if _is_django_test_case(instance):
        original_pre_setup = instance._pre_setup

        def _pre_setup_then_login() -> None:
            original_pre_setup()
            client = getattr(instance, "client", None)
            if client is not None:
                client.force_login(get_shared_manager())

        # Per-instance patch: the instance is rebuilt for every test, so
        # there is nothing to restore.
        instance._pre_setup = _pre_setup_then_login
        yield
        return

    try:
        client = request.getfixturevalue("client")
    except Exception:  # pragma: no cover - no client fixture available
        client = None
    if client is not None:
        client.force_login(get_shared_manager())
    yield
