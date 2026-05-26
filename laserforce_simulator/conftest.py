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
