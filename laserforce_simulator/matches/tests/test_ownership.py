"""UX-01 — ownership across the four `matches` roots, the stamping sites in
this app, the root list querysets, and the **dormant** `League.visibility`.

Seam contract: `.claude/worktrees/ux-01-seam-contract.md` §2.2 / §2.3 / §2.5
(the fields), §6.1 / §6.4 (stamping), §7 (root list querysets), §13 (the
dormant visibility surface) and §11.3 (test boundary).

The project's autouse `force_login_shared_manager` fixture logs each
`TestCase` in as the shared Account, so the view POSTs below stamp rows to it.
"""

from __future__ import annotations

from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase

from conftest import get_shared_manager
from matches.forms import CreateLeagueForm
from matches.league_services import create_league_and_season
from matches.models import (
    GameRound,
    League,
    Match,
    Season,
    SeasonPhase,
    Tournament,
)
from matches.tests.conftest import make_team_with_slots

User = get_user_model()

#: §2.2 — the four matches-app roots and their locked `related_name`s.
ROOTS_AND_RELATED_NAMES: tuple[tuple[type[models.Model], str], ...] = (
    (League, "leagues"),
    (Tournament, "tournaments"),
    (Match, "matches"),
    (GameRound, "game_rounds"),
)

#: §6 — the reusable valid create-League payload (mirrors test_league_create).
VALID_LEAGUE_PAYLOAD: dict[str, str] = {
    "league_name": "Ownership League",
    "season_name": "Season 1",
    "start_date": "2026-06-01",
    "num_teams": "4",
    "schedule_format": "single_round_robin",
    "mean": "50",
    "std_dev": "15",
    "map_mode": "none",
}


def bound_league_form(**overrides: str) -> CreateLeagueForm:
    payload = dict(VALID_LEAGUE_PAYLOAD)
    payload.update(overrides)
    form = CreateLeagueForm(payload)
    assert form.is_valid(), form.errors
    return form


# ---------------------------------------------------------------------------
# The fields (§2.2)
# ---------------------------------------------------------------------------


class TestMatchesRootManagerFields(TestCase):
    """§2.2 — byte-identical field on all four; only `related_name` differs."""

    def test_every_root_has_a_manager_field(self) -> None:
        for model, _ in ROOTS_AND_RELATED_NAMES:
            with self.subTest(model=model.__name__):
                self.assertIsNotNone(model._meta.get_field("manager"))

    def test_every_manager_points_at_the_auth_user_model(self) -> None:
        for model, _ in ROOTS_AND_RELATED_NAMES:
            with self.subTest(model=model.__name__):
                field = model._meta.get_field("manager")
                self.assertEqual(
                    field.remote_field.model._meta.label, settings.AUTH_USER_MODEL
                )

    def test_every_manager_is_nullable_and_blank(self) -> None:
        for model, _ in ROOTS_AND_RELATED_NAMES:
            with self.subTest(model=model.__name__):
                field = model._meta.get_field("manager")
                self.assertTrue(field.null)
                self.assertTrue(field.blank)

    def test_every_manager_uses_set_null(self) -> None:
        """§2.1 — an Admin delete must demote, never cascade history away."""
        for model, _ in ROOTS_AND_RELATED_NAMES:
            with self.subTest(model=model.__name__):
                field = model._meta.get_field("manager")
                self.assertIs(field.remote_field.on_delete, models.SET_NULL)

    def test_related_names_are_the_locked_ones(self) -> None:
        user = User.objects.create_user(
            email="related@example.com", password="Str0ng-Passphrase-9"
        )
        red, _ = make_team_with_slots("RelRed")
        blue, _ = make_team_with_slots("RelBlue")
        rows = {
            "leagues": League.objects.create(name="Rel League", manager=user),
            "tournaments": Tournament.objects.create(name="Rel Cup", manager=user),
            "matches": Match.objects.create(team_red=red, team_blue=blue, manager=user),
            "game_rounds": GameRound.objects.create(round_number=1, manager=user),
        }
        for related_name, row in rows.items():
            with self.subTest(related_name=related_name):
                self.assertIn(row, getattr(user, related_name).all())

    def test_deleting_the_account_demotes_every_root_to_unmanaged(self) -> None:
        user = User.objects.create_user(
            email="demote@example.com", password="Str0ng-Passphrase-9"
        )
        red, _ = make_team_with_slots("DemRed")
        blue, _ = make_team_with_slots("DemBlue")
        league = League.objects.create(name="Dem League", manager=user)
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        tourney = Tournament.objects.create(name="Dem Cup", manager=user)
        match = Match.objects.create(team_red=red, team_blue=blue, manager=user)
        game_round = GameRound.objects.create(round_number=1, manager=user)

        user.delete()

        for row in (league, tourney, match, game_round):
            with self.subTest(row=type(row).__name__):
                row.refresh_from_db()
                self.assertIsNone(row.manager_id)
        # Derived history survives too.
        self.assertTrue(Season.objects.filter(pk=season.pk).exists())


class TestEmbeddedTournamentCarriesManager(TestCase):
    """§2.3 — a Tournament with `season_phase` set is still its own root."""

    def test_embedded_tournament_can_be_stamped_directly(self) -> None:
        user = User.objects.create_user(
            email="embed@example.com", password="Str0ng-Passphrase-9"
        )
        league = League.objects.create(name="Embed League", manager=user)
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        phase = SeasonPhase.objects.create(season=season, ordinal=1)
        tourney = Tournament.objects.create(
            name="Embedded", season_phase=phase, manager=user
        )
        self.assertEqual(tourney.manager_id, user.pk)
        self.assertIsNotNone(tourney.season_phase_id)


# ---------------------------------------------------------------------------
# League.visibility — the dormant column (§2.5, §13)
# ---------------------------------------------------------------------------


class TestLeagueVisibilityColumn(TestCase):
    """§2.5 — the column ships, defaulted to `"closed"`, with two choices."""

    def test_default_is_closed(self) -> None:
        self.assertEqual(League.objects.create(name="Vis Default").visibility, "closed")

    def test_choices_are_closed_and_open(self) -> None:
        self.assertEqual(
            tuple(League.VISIBILITY_CHOICES),
            (("closed", "Closed"), ("open", "Open")),
        )

    def test_field_choices_match_the_class_constant(self) -> None:
        field = League._meta.get_field("visibility")
        self.assertEqual(tuple(field.choices), tuple(League.VISIBILITY_CHOICES))
        self.assertEqual(field.default, "closed")
        self.assertEqual(field.max_length, 16)

    def test_open_is_a_storable_value(self) -> None:
        league = League.objects.create(name="Vis Open", visibility="open")
        league.refresh_from_db()
        self.assertEqual(league.visibility, "open")


# ANG-08: the HTML dormancy guard (``TestLeagueVisibilityIsDormant``, a
# same-row flip over the League / Season pages) went with those pages.


class TestLeagueCreateStoresVisibility(TestCase):
    """§13 — the create form authors the marker; nothing else touches it."""

    def test_form_field_is_optional_and_defaults_to_closed(self) -> None:
        form = CreateLeagueForm(VALID_LEAGUE_PAYLOAD)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.fields["visibility"].required)
        season = create_league_and_season(form)
        self.assertEqual(season.league.visibility, "closed")

    def test_explicit_open_is_persisted(self) -> None:
        form = bound_league_form(visibility="open", league_name="Open Author")
        season = create_league_and_season(form)
        self.assertEqual(season.league.visibility, "open")

    def test_blank_visibility_is_coerced_to_closed(self) -> None:
        form = bound_league_form(visibility="", league_name="Blank Author")
        season = create_league_and_season(form)
        self.assertEqual(season.league.visibility, "closed")


# ---------------------------------------------------------------------------
# Stamping sites #3, #4 — create_league_and_season (§6.1, §6.4)
# ---------------------------------------------------------------------------


class TestCreateLeagueAndSeasonManagerKwarg(TestCase):
    """§6.1 — one keyword-only `manager` parameter, appended last, default None."""

    def test_kwarg_stamps_the_league(self) -> None:
        user = User.objects.create_user(
            email="league-author@example.com", password="Str0ng-Passphrase-9"
        )
        season = create_league_and_season(
            bound_league_form(league_name="Stamped League"), manager=user
        )
        self.assertEqual(season.league.manager_id, user.pk)

    def test_kwarg_stamps_the_generated_teams(self) -> None:
        """§6.4 #2 — the League's Teams come from `_generate_teams`."""
        user = User.objects.create_user(
            email="league-author2@example.com", password="Str0ng-Passphrase-9"
        )
        season = create_league_and_season(
            bound_league_form(league_name="Stamped Teams"), manager=user
        )
        teams = list(season.teams.all())
        self.assertTrue(teams)
        for team in teams:
            with self.subTest(team=team.name):
                self.assertEqual(team.manager_id, user.pk)

    def test_kwarg_stamps_the_free_agent_pool_team(self) -> None:
        """§6.4 #4 — the League's OWN pool Team, not the global singleton."""
        user = User.objects.create_user(
            email="league-author3@example.com", password="Str0ng-Passphrase-9"
        )
        season = create_league_and_season(
            bound_league_form(league_name="Stamped Pool"), manager=user
        )
        pool = season.league.free_agent_pool
        self.assertIsNotNone(pool, "the League should own a free-agent pool Team")
        self.assertEqual(pool.manager_id, user.pk)

    def test_omitting_the_kwarg_leaves_everything_unmanaged(self) -> None:
        season = create_league_and_season(
            bound_league_form(league_name="Unmanaged League")
        )
        self.assertIsNone(season.league.manager_id)
        for team in season.teams.all():
            with self.subTest(team=team.name):
                self.assertIsNone(team.manager_id)

    def test_the_season_itself_is_not_a_root(self) -> None:
        season = create_league_and_season(
            bound_league_form(league_name="Derived Season")
        )
        self.assertNotIn("manager", {f.name for f in Season._meta.get_fields()})


# ---------------------------------------------------------------------------
# Stamping sites #5, #7, #9, #10 — the create views (§6.2, §6.4)
# ---------------------------------------------------------------------------


# ANG-08: the League / Tournament / Match / single-Round create-view stamping
# tests went with those HTML views; the API create stamps are pinned in
# test_api_leagues.py (TestLeagueCreate), test_api_tournaments_create.py and
# test_api_match_create.py.


# ---------------------------------------------------------------------------
# Stamping site #11 — embedded-Tournament propagation (§2.3, §6.4)
# ---------------------------------------------------------------------------


class TestEmbeddedTournamentInheritsLeagueManager(TestCase):
    """§6.4 #11 — `Season._build_tournament_for_phase` stamps
    `manager=self.league.manager`, so an embedded bracket's Manager always
    equals its League's.
    """

    def _season_with_tournament_phase(self, manager) -> tuple[Season, SeasonPhase]:
        league = League.objects.create(name="Propagate League", manager=manager)
        teams = [make_team_with_slots(f"Prop{n}")[0] for n in range(4)]
        season = Season.objects.create(
            league=league,
            name="S1",
            start_date=date(2026, 1, 1),
            state="active",
            schedule_format="single_round_robin",
            starting_team_ids_json=[t.id for t in teams],
        )
        season.teams.set(teams)
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        phase = SeasonPhase.objects.create(
            season=season,
            ordinal=2,
            phase_type="tournament",
            tournament_mode="strength",
            tournament_format="single_elimination",
        )
        return season, phase

    def test_embedded_tournament_inherits_the_leagues_manager(self) -> None:
        user = User.objects.create_user(
            email="propagate@example.com", password="Str0ng-Passphrase-9"
        )
        season, phase = self._season_with_tournament_phase(user)
        tourney = season._build_tournament_for_phase(phase)
        self.assertIsNotNone(tourney, "the bracket should have been built")
        self.assertEqual(tourney.manager_id, user.pk)
        self.assertEqual(tourney.manager_id, season.league.manager_id)

    def test_unmanaged_league_yields_an_unmanaged_bracket(self) -> None:
        season, phase = self._season_with_tournament_phase(None)
        tourney = season._build_tournament_for_phase(phase)
        self.assertIsNotNone(tourney)
        self.assertIsNone(tourney.manager_id)


# ---------------------------------------------------------------------------
# §7 — the root list querysets
# ---------------------------------------------------------------------------


# ANG-08: the `league_list` / `tournament_list` / `match_list` HTML scoping
# tests went with those views; the API list scoping is pinned in
# test_api_leagues.py (TestLeagueList) and TestApiQuerysetScoping below.


# ---------------------------------------------------------------------------
# DRF viewsets (§8.2)
# ---------------------------------------------------------------------------


class TestApiQuerysetScoping(TestCase):
    """§8.2 — the four viewsets are manager-scoped, and 401 while anonymous (ADR-0041)."""

    def setUp(self) -> None:
        super().setUp()
        self.me = get_shared_manager()
        self.other = User.objects.create_user(
            email="api-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.mine, _ = make_team_with_slots("ApiMine")
        self.mine.manager = self.me
        self.mine.save(update_fields=["manager"])
        self.theirs, _ = make_team_with_slots("ApiTheirs")
        self.theirs.manager = self.other
        self.theirs.save(update_fields=["manager"])

    def test_team_list_excludes_another_accounts_team(self) -> None:
        names = {row["name"] for row in self.client.get("/api/teams/").json()["data"]}
        self.assertIn(self.mine.name, names)
        self.assertNotIn(self.theirs.name, names)

    def test_team_detail_of_another_account_is_404(self) -> None:
        self.assertEqual(
            self.client.get(f"/api/teams/{self.theirs.pk}/").status_code, 404
        )

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        self.client.force_login(self.other)
        names = {row["name"] for row in self.client.get("/api/teams/").json()["data"]}
        self.assertIn(self.theirs.name, names)
        self.assertNotIn(self.mine.name, names)

    def test_anonymous_api_access_is_401_not_302(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        self.client.logout()
        response = self.client.get("/api/teams/")
        self.assertEqual(response.status_code, 401)
