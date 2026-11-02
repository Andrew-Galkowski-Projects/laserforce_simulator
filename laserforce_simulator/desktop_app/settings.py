"""DESK-01 (ADR-0048): the Desktop App's settings, on top of the Hosted ones.

The launcher sets ``LF_DESKTOP_HOME``, ``LF_DESKTOP_SPA``, ``SECRET_KEY`` and
``DATABASE_URL`` (SQLite in the data folder) in the environment before Django
loads, so the base settings read the last two as usual.
"""

import os
from pathlib import Path

from laserforce_simulator.settings import *  # noqa: F401,F403

DESKTOP_APP = True
LF_DESKTOP_HOME = Path(os.environ["LF_DESKTOP_HOME"])

DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CORS_ALLOWED_ORIGINS: list[str] = []
CSRF_TRUSTED_ORIGINS: list[str] = []
# Plain http://127.0.0.1: a Secure refresh cookie could be dropped.
REFRESH_COOKIE_SECURE = False

ROOT_URLCONF = "desktop_app.urls"

MEDIA_ROOT = LF_DESKTOP_HOME / "media"
SERVE_MEDIA = True

# ANG-10 (ADR-0049) serves the SPA; the base settings read SPA_ROOT once at
# import, so both names are set again here.
SPA_ROOT = Path(os.environ["LF_DESKTOP_SPA"])
WHITENOISE_ROOT = SPA_ROOT

# DESK-04: the bundled build_info.json, set by the launcher when frozen; unset
# (an unfrozen run) means no record, so a dev build with no Update check.
DESKTOP_BUILD_INFO = (
    Path(os.environ["LF_DESKTOP_BUILD_INFO"])
    if os.environ.get("LF_DESKTOP_BUILD_INFO")
    else None
)
# The Update check (desktop_app.update_check): GitHub's API, once per launch.
DESKTOP_UPDATE_TIMEOUT = 5
DESKTOP_RELEASES_REPO = "Andrew-Galkowski-Projects/laserforce_simulator"

# In-process jobs: the launcher's Celery worker thread consumes the memory
# broker; results live in the in-process cache backend.
CELERY_TASK_ALWAYS_EAGER = False
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}
    },
    "handlers": {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LF_DESKTOP_HOME / "logs" / "desktop.log"),
            "maxBytes": 2_000_000,
            "backupCount": 3,
            "formatter": "plain",
        },
    },
    "root": {"handlers": ["file"], "level": "INFO"},
}
