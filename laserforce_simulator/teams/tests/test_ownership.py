"""UX-01 — `Team` ownership: the `manager` FK, the stamping sites in the
`teams` app, and the root/derived list-queryset scoping.

Seam contract: `.claude/worktrees/ux-01-seam-contract.md` §2.2 (the field
definition), §6.1–§6.3 (the changed `_generate_teams` signature and the
deliberately-unstamped free-agent pool), §7 (root list querysets) and §11.3
(test boundary).

> NAMING HAZARD (contract §0.1): `Team.manager` is the FK **Team → Account**.
> It is NOT `Team.managed_in_leagues`, the pre-existing reverse accessor of
> `League.current_team` (**Team → Leagues**). `Team.manager` is set on every
> generated AI Team; it is not the career seat.

The project's autouse `force_login_shared_manager` fixture logs each
`TestCase` in as the shared Account, so view POSTs below stamp rows to it.
"""

from __future__ import annotations

import random

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase
from django.urls import reverse

from accounts.permissions import owned_queryset
from conftest import get_shared_manager
from core.models import (
    ArenaMap,
    BaseSightLineConfig,
    MapBaseConfig,
    MapZoneConfig,
    SightLineConfig,
)
from teams.models import Player, Team, get_free_agents_team
from teams.views import _generate_teams

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
# Unmanaged-row semantics (§0, ADR-0038)
# ---------------------------------------------------------------------------


class TestUnmanagedTeamSemantics(TestCase):
    """An Unmanaged Team is listed, readable AND writable by any Account."""

    def setUp(self) -> None:
        super().setUp()
        self.other = User.objects.create_user(
            email="unmanaged-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.open_team = Team.objects.create(name="Open Team")

    def test_unmanaged_team_is_listed_to_the_signed_in_account(self) -> None:
        response = self.client.get(reverse("team_list"))
        self.assertIn(self.open_team, response.context["teams"])

    def test_unmanaged_team_is_listed_to_a_different_account(self) -> None:
        self.client.force_login(self.other)
        response = self.client.get(reverse("team_list"))
        self.assertIn(self.open_team, response.context["teams"])

    def test_unmanaged_team_is_readable_by_any_account(self) -> None:
        for user in (get_shared_manager(), self.other):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                response = self.client.get(
                    reverse("team_detail", kwargs={"team_id": self.open_team.id})
                )
                self.assertEqual(response.status_code, 200)

    def test_unmanaged_team_is_writable_by_any_account(self) -> None:
        """NULL means *open*, not *frozen*."""
        self.client.force_login(self.other)
        response = self.client.get(
            reverse("team_detail", kwargs={"team_id": self.open_team.id})
        )
        self.assertEqual(response.status_code, 200)
        self.open_team.name = "Renamed By Other"
        self.open_team.save()
        self.open_team.refresh_from_db()
        self.assertEqual(self.open_team.name, "Renamed By Other")

    def test_another_accounts_team_is_404_not_403(self) -> None:
        owned = Team.objects.create(name="Theirs", manager=self.other)
        response = self.client.get(reverse("team_detail", kwargs={"team_id": owned.id}))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Stamping site #1 — team_create (§6.4)
# ---------------------------------------------------------------------------


class TestTeamCreateStampsTheManager(TestCase):
    """§6.4 #1 — `team_create` stamps `manager_or_none(request)`."""

    def test_post_creates_a_team_stamped_to_the_signed_in_account(self) -> None:
        response = self.client.post(reverse("team_create"), {"name": "Stamped FC"})
        self.assertEqual(response.status_code, 302)
        team = Team.objects.get(name="Stamped FC")
        self.assertEqual(team.manager_id, get_shared_manager().pk)

    def test_a_second_account_gets_its_own_stamp(self) -> None:
        other = User.objects.create_user(
            email="second-creator@example.com", password="Str0ng-Passphrase-9"
        )
        self.client.force_login(other)
        self.client.post(reverse("team_create"), {"name": "Other FC"})
        self.assertEqual(Team.objects.get(name="Other FC").manager_id, other.pk)

    def test_invalid_post_creates_nothing(self) -> None:
        before = Team.objects.count()
        response = self.client.post(reverse("team_create"), {"name": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Team.objects.count(), before)


# ---------------------------------------------------------------------------
# Stamping site #2 — _generate_teams (§6.1, §6.2)
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


class TestGeneratePlayersViewStamps(TestCase):
    """§6.2 — the `generate_players` view passes `manager_or_none(request)`."""

    PAYLOAD = {
        "num_teams": "2",
        "players_per_team": "6",
        "mean": "50",
        "std_dev": "15",
    }

    def test_generated_teams_are_stamped_to_the_signed_in_account(self) -> None:
        before = set(Team.objects.values_list("id", flat=True))
        self.client.post(reverse("generate_players"), self.PAYLOAD)
        created = Team.objects.exclude(id__in=before)
        self.assertTrue(created.exists())
        for team in created:
            with self.subTest(team=team.name):
                self.assertEqual(team.manager_id, get_shared_manager().pk)

    def test_a_second_account_gets_its_own_stamp(self) -> None:
        other = User.objects.create_user(
            email="gen-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.client.force_login(other)
        before = set(Team.objects.values_list("id", flat=True))
        self.client.post(reverse("generate_players"), self.PAYLOAD)
        created = Team.objects.exclude(id__in=before)
        self.assertTrue(created.exists())
        for team in created:
            with self.subTest(team=team.name):
                self.assertEqual(team.manager_id, other.pk)


# ---------------------------------------------------------------------------
# §6.3 — the shared "Free Agents" singleton is NEVER stamped
# ---------------------------------------------------------------------------


class TestFreeAgentsSingletonStaysUnmanaged(TestCase):
    """§6.3 — stamping the global pool would let the first Account capture it."""

    def test_get_free_agents_team_returns_an_unmanaged_team(self) -> None:
        self.assertIsNone(get_free_agents_team().manager_id)

    def test_it_stays_unmanaged_after_a_pool_generation(self) -> None:
        self.client.post(
            reverse("generate_players"),
            {
                "num_teams": "0",
                "players_per_team": "12",
                "mean": "50",
                "std_dev": "15",
            },
        )
        self.assertIsNone(get_free_agents_team().manager_id)

    def test_it_is_reachable_by_a_second_account(self) -> None:
        other = User.objects.create_user(
            email="fa-other@example.com", password="Str0ng-Passphrase-9"
        )
        pool = get_free_agents_team()
        self.client.force_login(other)
        response = self.client.get(reverse("team_detail", kwargs={"team_id": pool.id}))
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# §7 — root and derived list querysets
# ---------------------------------------------------------------------------


class TestTeamListScoping(TestCase):
    """§7 — `team_list` shows own Teams plus Unmanaged ones, and nothing else."""

    def setUp(self) -> None:
        super().setUp()
        self.me = get_shared_manager()
        self.other = User.objects.create_user(
            email="list-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.mine = Team.objects.create(name="Mine FC", manager=self.me)
        self.theirs = Team.objects.create(name="Theirs FC", manager=self.other)
        self.open_team = Team.objects.create(name="Open FC")

    def test_own_and_unmanaged_teams_are_listed(self) -> None:
        listed = set(self.client.get(reverse("team_list")).context["teams"])
        self.assertEqual(listed, {self.mine, self.open_team})

    def test_another_accounts_team_is_not_listed(self) -> None:
        listed = set(self.client.get(reverse("team_list")).context["teams"])
        self.assertNotIn(self.theirs, listed)

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        self.client.force_login(self.other)
        listed = set(self.client.get(reverse("team_list")).context["teams"])
        self.assertEqual(listed, {self.theirs, self.open_team})

    def test_free_agents_pool_is_still_excluded_by_regular(self) -> None:
        """LG-00's `Team.objects.regular()` behaviour is unchanged by UX-01."""
        pool = get_free_agents_team()
        listed = set(self.client.get(reverse("team_list")).context["teams"])
        self.assertNotIn(pool, listed)


class TestPlayerListScoping(TestCase):
    """§7 — `player_list` is scoped through `path="team"`."""

    def setUp(self) -> None:
        super().setUp()
        self.me = get_shared_manager()
        self.other = User.objects.create_user(
            email="plist-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.mine = Player.objects.create(
            team=Team.objects.create(name="PL Mine", manager=self.me), name="Mine P"
        )
        self.theirs = Player.objects.create(
            team=Team.objects.create(name="PL Theirs", manager=self.other),
            name="Theirs P",
        )
        self.open_player = Player.objects.create(
            team=Team.objects.create(name="PL Open"), name="Open P"
        )

    def test_own_and_unmanaged_players_are_listed(self) -> None:
        listed = set(
            self.client.get(reverse("player_list"), {"per_page": "100"}).context[
                "page_obj"
            ]
        )
        self.assertIn(self.mine, listed)
        self.assertIn(self.open_player, listed)

    def test_another_accounts_player_is_not_listed(self) -> None:
        listed = set(
            self.client.get(reverse("player_list"), {"per_page": "100"}).context[
                "page_obj"
            ]
        )
        self.assertNotIn(self.theirs, listed)

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        self.client.force_login(self.other)
        listed = set(
            self.client.get(reverse("player_list"), {"per_page": "100"}).context[
                "page_obj"
            ]
        )
        self.assertIn(self.theirs, listed)
        self.assertNotIn(self.mine, listed)

    def test_owned_queryset_path_agrees_with_the_view(self) -> None:
        scoped = owned_queryset(Player.objects.all(), self.me, path="team")
        self.assertIn(self.mine, scoped)
        self.assertIn(self.open_player, scoped)
        self.assertNotIn(self.theirs, scoped)


# ---------------------------------------------------------------------------
# §2.4 — the ArenaMap regression guard
# ---------------------------------------------------------------------------


class TestArenaMapIsNotOwned(TestCase):
    """§2.4 — no map model gained a `manager` column, and `/maps/` is unfiltered.

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

    def test_map_list_is_not_filtered_by_manager(self) -> None:
        arena = ArenaMap.objects.create(name="Shared Arena", image="maps/a.png")
        other = User.objects.create_user(
            email="map-other@example.com", password="Str0ng-Passphrase-9"
        )
        for user in (get_shared_manager(), other):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                response = self.client.get(reverse("map_list"))
                self.assertEqual(response.status_code, 200)
                self.assertIn(arena.name, response.content.decode())

    def test_maps_routes_stay_behind_the_login_gate(self) -> None:
        self.client.logout()
        response = self.client.get(reverse("map_list"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login")))
