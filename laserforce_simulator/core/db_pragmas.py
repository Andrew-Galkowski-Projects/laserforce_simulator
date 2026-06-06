"""SQLite per-connection PRAGMA hook.

Enables WAL journal mode and ``synchronous=NORMAL`` on every new SQLite
connection. WAL allows a single writer and concurrent readers on the same
database file, which prevents the long-running Celery "Play …" write loops
from colliding with the dashboard's ``play_status`` polling and raising
``OperationalError: database is locked``.

These cannot live in ``DATABASES["default"]["OPTIONS"]["init_command"]``:
Django runs ``init_command`` through a single ``cursor.execute()``, and
Python's sqlite3 driver rejects more than one statement per ``execute()``.
A ``connection_created`` receiver runs each PRAGMA on its own ``execute()``.

WAL is a sticky, file-level setting, so re-issuing it per connection is a
cheap no-op after the first time. On an in-memory test database the PRAGMA
silently stays ``memory`` (WAL is unsupported there) — harmless.
"""

from django.db.backends.signals import connection_created
from django.dispatch import receiver


@receiver(connection_created)
def set_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
