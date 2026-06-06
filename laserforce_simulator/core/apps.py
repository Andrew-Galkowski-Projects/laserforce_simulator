from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # Register the SQLite PRAGMA hook (WAL + synchronous=NORMAL) on every
        # new connection. WAL lets a reader and a writer coexist, which is what
        # keeps the dashboard polling from colliding with the long "Play …"
        # write loop and raising "database is locked". Importing here (inside
        # ready()) keeps the signal wiring out of module import time.
        from . import db_pragmas  # noqa: F401
