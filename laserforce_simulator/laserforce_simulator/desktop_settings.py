"""ANG-09 spike: settings for the Desktop App (spike/ang-09 only, never merged).

The launcher sets SECRET_KEY, DATABASE_URL and LF_DESKTOP_HOME in the
environment before Django loads, so the base settings read them as usual.
"""

import os
from pathlib import Path

from .settings import *  # noqa: F401,F403
from .settings import MIDDLEWARE

LF_DESKTOP = True
LF_DESKTOP_HOME = Path(os.environ["LF_DESKTOP_HOME"])
# The built Angular app, bundled next to the code (see desktop/laserforce.spec).
LF_DESKTOP_SPA = Path(os.environ["LF_DESKTOP_SPA"])

DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
CORS_ALLOWED_ORIGINS: list[str] = []
CSRF_TRUSTED_ORIGINS: list[str] = []

ROOT_URLCONF = "laserforce_simulator.desktop_urls"

MEDIA_ROOT = LF_DESKTOP_HOME / "media"
SERVE_MEDIA = True

# WhiteNoise serves the SPA's files at "/" (index.html for "/" itself); the
# catch-all view in desktop_urls answers every deep link with index.html.
WHITENOISE_ROOT = LF_DESKTOP_SPA
WHITENOISE_INDEX_FILE = True
WHITENOISE_AUTOREFRESH = False

# In-process jobs: a Celery worker thread (launcher) consumes the in-process
# memory broker; results live in the in-process cache backend.
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

assert "whitenoise.middleware.WhiteNoiseMiddleware" in MIDDLEWARE
