"""CI-01: the guard against closing the DB connection inside a test transaction.

On PostgreSQL a ``close()`` inside a ``TestCase`` transaction kills the
connection every later test on that xdist worker shares. On SQLite's in-memory
test DB the close is a no-op, so the bug hides behind a green local run. The
guard (installed by the root conftest's ``pytest_configure``) makes it fail on
every backend.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase

from conftest import install_close_in_transaction_guard


class TestCloseInsideTransaction(TestCase):
    def test_close_raises_and_names_the_cause(self) -> None:
        with self.assertRaises(AssertionError) as ctx:
            connection.close()
        message = str(ctx.exception)
        self.assertIn("inside a test transaction", message)
        self.assertIn("close_old_connections()", message)
        self.assertIn("response.close()", message)

    def test_connection_still_usable_after_refused_close(self) -> None:
        with self.assertRaises(AssertionError):
            connection.close()
        # The real close never ran: the transaction and its rows survive.
        get_user_model().objects.create(email="guard@example.com")
        self.assertTrue(
            get_user_model().objects.filter(email="guard@example.com").exists()
        )

    def test_installing_twice_does_not_double_wrap(self) -> None:
        backend_class = type(connections["default"])
        wrapped = backend_class.close
        install_close_in_transaction_guard()
        self.assertIs(backend_class.close, wrapped)


class TestCloseOutsideTransaction(TransactionTestCase):
    def test_close_outside_atomic_block_does_not_raise(self) -> None:
        self.assertFalse(connection.in_atomic_block)
        connection.close()
        # Django reconnects on the next query.
        self.assertEqual(get_user_model().objects.count(), 0)
