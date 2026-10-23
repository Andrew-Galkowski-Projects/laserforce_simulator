"""``assert_owner_only``: the cross-Account 404 check that cannot pass vacuously.

A bare "the other Account gets 404" test also passes when the endpoint 404s
for every Account, so it proves nothing about ownership. The helper first
asserts the owner gets 200 on the same URL.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from conftest import get_shared_manager
from laserforce_simulator.testing import assert_owner_only
from matches.models import GameRound
from matches.tests.conftest import make_team_with_slots

User = get_user_model()


class TestAssertOwnerOnly(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner = get_shared_manager()
        self.other = User.objects.create_user(
            email="owner-only-other@example.com", password="Str0ng-Passphrase-9"
        )
        red, _ = make_team_with_slots("OoRed")
        blue, _ = make_team_with_slots("OoBlue")
        self.game_round = GameRound.objects.create(
            round_number=1, team_red=red, team_blue=blue, manager=self.owner
        )

    def test_passes_on_an_owned_route(self) -> None:
        assert_owner_only(
            f"/api/rounds/{self.game_round.pk}/", owner=self.owner, other=self.other
        )

    def test_fails_when_the_owner_also_gets_404(self) -> None:
        with self.assertRaises(AssertionError):
            assert_owner_only(
                f"/api/rounds/{self.game_round.pk}/no-such-action/",
                owner=self.owner,
                other=self.other,
            )

    def test_fails_when_the_other_account_can_read_it(self) -> None:
        with self.assertRaises(AssertionError):
            assert_owner_only(
                f"/api/rounds/{self.game_round.pk}/", owner=self.owner, other=self.owner
            )
