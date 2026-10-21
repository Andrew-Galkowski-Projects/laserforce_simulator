"""Migration 0004 turns each used 0003 invite into ``use_count = 1`` plus one
``InviteRedemption``, keeping who used it and when, before dropping
``used_by`` / ``used_at``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("accounts", "0003_invite")]
AFTER = [("accounts", "0004_invite_max_uses_expiry")]


class TestInviteMaxUsesMigration(TransactionTestCase):
    def setUp(self) -> None:
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        old = executor.loader.project_state(BEFORE).apps
        User = old.get_model("accounts", "User")
        Invite = old.get_model("accounts", "Invite")
        self.used_at = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        member = User.objects.create(email="member@example.com")
        self.member_pk = member.pk
        self.used_pk = Invite.objects.create(
            code="used", used_by=member, used_at=self.used_at
        ).pk
        self.unused_pk = Invite.objects.create(code="unused").pk

    def tearDown(self) -> None:
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )
        super().tearDown()

    def _migrate_forward(self):  # type: ignore[no-untyped-def]
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(AFTER)
        return executor.loader.project_state(AFTER).apps

    def test_used_invite_becomes_one_use_with_its_redemption(self) -> None:
        new = self._migrate_forward()
        Invite = new.get_model("accounts", "Invite")
        used = Invite.objects.get(pk=self.used_pk)
        self.assertEqual((used.use_count, used.max_uses), (1, 1))
        redemption = used.redemptions.get()
        self.assertEqual(redemption.user_id, self.member_pk)
        self.assertEqual(redemption.redeemed_at, self.used_at)

    def test_unused_invite_stays_unused(self) -> None:
        new = self._migrate_forward()
        Invite = new.get_model("accounts", "Invite")
        unused = Invite.objects.get(pk=self.unused_pk)
        self.assertEqual(unused.use_count, 0)
        self.assertFalse(unused.redemptions.exists())
