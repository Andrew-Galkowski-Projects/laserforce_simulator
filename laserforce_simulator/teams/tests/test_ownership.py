"""UX-01 — `Team` ownership: the `manager` FK and the `_generate_teams` stamp.

Seam contract: `.claude/worktrees/ux-01-seam-contract.md` §2.2 (the field
definition), §6.1–§6.3 (the changed `_generate_teams` signature and the
deliberately-unstamped free-agent pool) and §11.3 (test boundary). The HTML
list / detail / create / generate view cases went with the template views
(ANG-08 D1); the API's scoping is covered by the `test_api_*.py` files.

> NAMING HAZARD (contract §0.1): `Team.manager` is the FK **Team → Account**.
> It is NOT `Team.managed_in_leagues`, the pre-existing reverse accessor of
> `League.current_team` (**Team → Leagues**). `Team.manager` is set on every
> generated AI Team; it is not the career seat.
"""

from __future__ import annotations

import random

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase

from core.models import (
    ArenaMap,
    BaseSightLineConfig,
    MapBaseConfig,
    MapZoneConfig,
    SightLineConfig,
)
from teams.models import Player, Team, get_free_agents_team
from teams.services import _generate_teams

User = get_user_model()


# ---------------------------------------------------------------------------
# The field itself (§2.2)
# ---------------------------------------------------------------------------


class TestTeamManagerField(TestCase):
    """§2.2 — presence, nullability, `SET_NULL` and the `related_name`."""

    def test_manager_field_exists(self) -> None:
        self.assertIsNotNone(Team._meta.get_field("manager"))

    def test_manager_points_at_the_auth_user_model(self) -> None:
        field = Team._meta.get_field("manager")
        self.assertEqual(field.remote_field.model._meta.label, settings.AUTH_USER_MODEL)

    def test_manager_is_nullable_and_blank(self) -> None:
        field = Team._meta.get_field("manager")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)

    def test_manager_on_delete_is_set_null(self) -> None:
        """§2.1 — CASCADE would destroy simulation history on an Admin delete."""
        field = Team._meta.get_field("manager")
        self.assertIs(field.remote_field.on_delete, models.SET_NULL)

    def test_related_name_is_teams(self) -> None:
        user = User.objects.create_user(
            email="rn-team@example.com", password="Str0ng-Passphrase-9"
        )
        team = Team.objects.create(name="RN Team", manager=user)
        self.assertIn(team, user.teams.all())

    def test_manager_defaults_to_null(self) -> None:
        """A Team created without a Manager is an **Unmanaged row**."""
        self.assertIsNone(Team.objects.create(name="Default Null").manager_id)

    def test_deleting_the_account_demotes_the_team_to_unmanaged(self) -> None:
        user = User.objects.create_user(
            email="doomed@example.com", password="Str0ng-Passphrase-9"
        )
        team = Team.objects.create(name="Survivor", manager=user)
        player = Player.objects.create(team=team, name="Survivor Player")

        user.delete()

        team.refresh_from_db()
        self.assertIsNone(team.manager_id)
        self.assertTrue(Team.objects.filter(pk=team.pk).exists())
        self.assertTrue(Player.objects.filter(pk=player.pk).exists())


# ---------------------------------------------------------------------------
# Stamping site — _generate_teams (§6.1, §6.2)
# ---------------------------------------------------------------------------


class TestGenerateTeamsManagerKwarg(TestCase):
    """§6.1 — one keyword-only `manager` parameter, appended last, default None."""

    def _generate(self, **extra: object) -> list[Team]:
        return _generate_teams(
            2,
            6,
            rng=random.Random(42),
            mean=50,
            std_dev=15,
            team_names_pool=["Gen Alpha", "Gen Beta"],
            player_names_pool=[f"Gen Player {n}" for n in range(40)],
            **extra,
        )

    def test_kwarg_stamps_every_generated_team(self) -> None:
        user = User.objects.create_user(
            email="generator@example.com", password="Str0ng-Passphrase-9"
        )
        teams = self._generate(manager=user)
        self.assertEqual(len(teams), 2)
        for team in teams:
            with self.subTest(team=team.name):
                team.refresh_from_db()
                self.assertEqual(team.manager_id, user.pk)

    def test_omitting_the_kwarg_leaves_the_teams_unmanaged(self) -> None:
        """Default `None` keeps every pre-UX-01 caller source-compatible."""
        teams = self._generate()
        self.assertEqual(len(teams), 2)
        for team in teams:
            with self.subTest(team=team.name):
                team.refresh_from_db()
                self.assertIsNone(team.manager_id)

    def test_explicit_none_leaves_the_teams_unmanaged(self) -> None:
        for team in self._generate(manager=None):
            with self.subTest(team=team.name):
                self.assertIsNone(team.manager_id)

    def test_players_are_not_stamped_they_derive_from_their_team(self) -> None:
        user = User.objects.create_user(
            email="generator2@example.com", password="Str0ng-Passphrase-9"
        )
        teams = self._generate(manager=user)
        self.assertFalse(
            any(f.name == "manager" for f in Player._meta.get_fields()),
            "Player must not carry a `manager` FK — it derives via `team`",
        )
        self.assertTrue(Player.objects.filter(team=teams[0]).exists())


# ---------------------------------------------------------------------------
# §6.3 — the shared "Free Agents" singleton is NEVER stamped
# ---------------------------------------------------------------------------


class TestFreeAgentsSingletonStaysUnmanaged(TestCase):
    """§6.3 — stamping the global pool would let the first Account capture it."""

    def test_get_free_agents_team_returns_an_unmanaged_team(self) -> None:
        self.assertIsNone(get_free_agents_team().manager_id)


# ---------------------------------------------------------------------------
# §2.4 — the ArenaMap regression guard
# ---------------------------------------------------------------------------


class TestArenaMapIsNotOwned(TestCase):
    """§2.4 — no map model gained a `manager` column.

    `is_default`, `Season.map_mode`, the CONF-06 Map pools and
    `rotate_by_matchday` all reference maps across League boundaries, so a
    privately-owned map would silently break another Manager's rotation.
    """

    MAP_MODELS = (
        ArenaMap,
        MapZoneConfig,
        MapBaseConfig,
        SightLineConfig,
        BaseSightLineConfig,
    )

    def test_no_map_model_has_a_manager_field(self) -> None:
        for model in self.MAP_MODELS:
            with self.subTest(model=model.__name__):
                self.assertNotIn(
                    "manager",
                    {f.name for f in model._meta.get_fields()},
                    f"{model.__name__} must NOT be an Ownership root",
                )
