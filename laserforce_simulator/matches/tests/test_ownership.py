"""UX-01 — ownership across the four `matches` roots, the stamping sites in
this app, the root list querysets, and the **dormant** `League.visibility`.

Seam contract: `.claude/worktrees/ux-01-seam-contract.md` §2.2 / §2.3 / §2.5
(the fields), §6.1 / §6.4 (stamping), §7 (root list querysets), §13 (the
dormant visibility surface) and §11.3 (test boundary).

The project's autouse `force_login_shared_manager` fixture logs each
`TestCase` in as the shared Account, so the view POSTs below stamp rows to it.
"""

from __future__ import annotations

import re

from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.test import TestCase
from django.urls import reverse

from conftest import get_shared_manager
from matches.forms import CreateLeagueForm
from matches.league_views import _create_league_and_season
from matches.models import (
    GameRound,
    League,
    Match,
    Season,
    SeasonPhase,
    Tournament,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

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


class TestLeagueVisibilityIsDormant(TestCase):
    """§2.5 / §13 — **nothing reads it**. This is a load-bearing guard.

    The strongest available proof of dormancy is a *same-row flip*: take ONE
    League, render it, flip only `visibility`, render it again, and require
    the two renders to be **byte-identical** — no ids, names or dates differ,
    so the only variable in the experiment is the dormant column itself. If a
    future slice starts branching on `visibility` without lifting the
    dormancy, this fails immediately and unambiguously.
    """

    def setUp(self) -> None:
        super().setUp()
        self.me = get_shared_manager()
        self.league = League.objects.create(
            name="Dormant League", visibility="closed", manager=self.me
        )
        self.season = Season.objects.create(
            league=self.league,
            name="S1",
            start_date=date(2026, 1, 1),
            state="active",
            schedule_format="single_round_robin",
            starting_team_ids_json=[],
        )

    def _flip_to_open(self) -> None:
        self.league.visibility = "open"
        self.league.save(update_fields=["visibility"])

    def test_league_detail_renders_byte_identically_across_the_flip(self) -> None:
        url = f"/leagues/{self.league.id}/"
        closed_body = self._normalise(self.client.get(url))
        self._flip_to_open()
        open_body = self._normalise(self.client.get(url))
        self.assertEqual(closed_body, open_body)

    def test_season_dashboard_renders_byte_identically_across_the_flip(self) -> None:
        url = reverse("season_dashboard", kwargs={"season_id": self.season.id})
        closed_body = self._normalise(self.client.get(url))
        self._flip_to_open()
        open_body = self._normalise(self.client.get(url))
        self.assertEqual(closed_body, open_body)

    def test_league_list_renders_byte_identically_across_the_flip(self) -> None:
        closed_body = self._normalise(self.client.get(reverse("league_list")))
        self._flip_to_open()
        open_body = self._normalise(self.client.get(reverse("league_list")))
        self.assertEqual(closed_body, open_body)

    def test_no_view_context_key_varies_with_the_visibility_value(self) -> None:
        """The context is compared key-by-key, so a leak names its own key."""
        url = f"/leagues/{self.league.id}/"
        closed_ctx = self._flat_context(self.client.get(url))
        self._flip_to_open()
        open_ctx = self._flat_context(self.client.get(url))

        self.assertEqual(set(closed_ctx), set(open_ctx))
        for key in closed_ctx:
            if key == "league":
                # The League object itself legitimately carries the column.
                continue
            with self.subTest(key=key):
                self.assertEqual(
                    repr(closed_ctx[key]),
                    repr(open_ctx[key]),
                    f"context key {key!r} varies with the dormant `visibility`",
                )

    def test_the_league_is_listed_whatever_its_visibility(self) -> None:
        """`open` must not make a League public, nor `closed` hide it."""
        self._flip_to_open()
        active = self.client.get(reverse("league_list")).context["active_leagues"]
        self.assertIn(self.league, active)

    def test_the_rendered_page_never_shows_the_word_visibility(self) -> None:
        self._flip_to_open()
        body = self.client.get(f"/leagues/{self.league.id}/").content.decode()
        self.assertNotIn("visibility", body.lower())

    @staticmethod
    def _normalise(response) -> str:
        """Rendered body with the per-request CSRF token scrubbed out.

        The token is regenerated on every request, so it is request-scoped
        noise rather than anything the dormant column could influence. The
        scrub is applied identically to both sides, and every `visibility`
        value is far too short to match, so it can only ever hide a
        difference it caused itself -- never mask a real leak.
        """
        body = response.content.decode()
        return re.sub(r'value="[A-Za-z0-9]{32,}"', 'value="[scrubbed]"', body)

    @staticmethod
    def _flat_context(response) -> dict:
        flat: dict = {}
        for context in response.context:
            flat.update(context.flatten())
        # `request`/`csrf_token` legitimately differ between two requests.
        for volatile in ("request", "csrf_token", "view", "True", "False", "None"):
            flat.pop(volatile, None)
        return flat


class TestLeagueCreateStoresVisibility(TestCase):
    """§13 — the create form authors the marker; nothing else touches it."""

    def test_create_advanced_renders_the_control(self) -> None:
        body = self.client.get(reverse("league_create_advanced")).content.decode()
        self.assertIn('id="league-create-visibility"', body)

    def test_form_field_is_optional_and_defaults_to_closed(self) -> None:
        form = CreateLeagueForm(VALID_LEAGUE_PAYLOAD)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.fields["visibility"].required)
        season = _create_league_and_season(form)
        self.assertEqual(season.league.visibility, "closed")

    def test_explicit_open_is_persisted(self) -> None:
        form = bound_league_form(visibility="open", league_name="Open Author")
        season = _create_league_and_season(form)
        self.assertEqual(season.league.visibility, "open")

    def test_blank_visibility_is_coerced_to_closed(self) -> None:
        form = bound_league_form(visibility="", league_name="Blank Author")
        season = _create_league_and_season(form)
        self.assertEqual(season.league.visibility, "closed")


# ---------------------------------------------------------------------------
# Stamping sites #3, #4 — _create_league_and_season (§6.1, §6.4)
# ---------------------------------------------------------------------------


class TestCreateLeagueAndSeasonManagerKwarg(TestCase):
    """§6.1 — one keyword-only `manager` parameter, appended last, default None."""

    def test_kwarg_stamps_the_league(self) -> None:
        user = User.objects.create_user(
            email="league-author@example.com", password="Str0ng-Passphrase-9"
        )
        season = _create_league_and_season(
            bound_league_form(league_name="Stamped League"), manager=user
        )
        self.assertEqual(season.league.manager_id, user.pk)

    def test_kwarg_stamps_the_generated_teams(self) -> None:
        """§6.4 #2 — the League's Teams come from `_generate_teams`."""
        user = User.objects.create_user(
            email="league-author2@example.com", password="Str0ng-Passphrase-9"
        )
        season = _create_league_and_season(
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
        season = _create_league_and_season(
            bound_league_form(league_name="Stamped Pool"), manager=user
        )
        pool = season.league.free_agent_pool
        self.assertIsNotNone(pool, "the League should own a free-agent pool Team")
        self.assertEqual(pool.manager_id, user.pk)

    def test_omitting_the_kwarg_leaves_everything_unmanaged(self) -> None:
        season = _create_league_and_season(
            bound_league_form(league_name="Unmanaged League")
        )
        self.assertIsNone(season.league.manager_id)
        for team in season.teams.all():
            with self.subTest(team=team.name):
                self.assertIsNone(team.manager_id)

    def test_the_season_itself_is_not_a_root(self) -> None:
        season = _create_league_and_season(
            bound_league_form(league_name="Derived Season")
        )
        self.assertNotIn("manager", {f.name for f in Season._meta.get_fields()})


class TestLeagueCreateViewStamps(TestCase):
    """§6.2 — the create view threads `manager_or_none(request)` through."""

    def test_post_stamps_the_league_to_the_signed_in_account(self) -> None:
        payload = dict(VALID_LEAGUE_PAYLOAD, league_name="View Authored")
        response = self.client.post(reverse("league_create_advanced"), payload)
        self.assertEqual(response.status_code, 302)
        league = League.objects.get(name="View Authored")
        self.assertEqual(league.manager_id, get_shared_manager().pk)

    def test_a_second_account_gets_its_own_stamp(self) -> None:
        other = User.objects.create_user(
            email="league-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.client.force_login(other)
        payload = dict(VALID_LEAGUE_PAYLOAD, league_name="Other Authored")
        self.client.post(reverse("league_create_advanced"), payload)
        self.assertEqual(League.objects.get(name="Other Authored").manager_id, other.pk)


# ---------------------------------------------------------------------------
# Stamping sites #7, #9, #10 — the create views (§6.4)
# ---------------------------------------------------------------------------


class TestTournamentCreateStamps(TestCase):
    """§6.4 #7 — `tournament_create` stamps the Tournament and its Teams."""

    def setUp(self) -> None:
        super().setUp()
        self.teams = [make_team_with_slots(f"TC{n}")[0] for n in range(4)]

    def test_post_stamps_the_tournament(self) -> None:
        response = self.client.post(
            reverse("tournament_create"),
            {"name": "Stamped Cup", "teams": [t.id for t in self.teams]},
        )
        self.assertIn(response.status_code, (200, 302))
        tourney = Tournament.objects.get(name="Stamped Cup")
        self.assertEqual(tourney.manager_id, get_shared_manager().pk)

    def test_a_second_account_gets_its_own_stamp(self) -> None:
        other = User.objects.create_user(
            email="tourney-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.client.force_login(other)
        self.client.post(
            reverse("tournament_create"),
            {"name": "Other Cup", "teams": [t.id for t in self.teams]},
        )
        self.assertEqual(Tournament.objects.get(name="Other Cup").manager_id, other.pk)

    def test_generated_teams_are_stamped_too(self) -> None:
        """§6.2 — `tournament_create` passes `manager=manager_or_none(request)`."""
        before = set(Team.objects.values_list("id", flat=True))
        self.client.post(
            reverse("tournament_create"),
            {"name": "Generated Cup", "generate_count": "4"},
        )
        created = Team.objects.exclude(id__in=before)
        self.assertTrue(created.exists())
        for team in created:
            with self.subTest(team=team.name):
                self.assertEqual(team.manager_id, get_shared_manager().pk)


class TestSandboxCreateViewsStamp(TestCase):
    """§6.4 #9 / #10 — post-hoc `stamp_manager` on the `BatchSimulator` output.

    The sandbox create views never construct the row themselves; they delegate
    to `BatchSimulator` and stamp what comes back. `manager` is deliberately
    NOT threaded through the simulator (§6.5).
    """

    def setUp(self) -> None:
        super().setUp()
        self.red, _ = make_team_with_slots("SandRed")
        self.blue, _ = make_team_with_slots("SandBlue")

    def test_create_match_stamps_the_match(self) -> None:
        from unittest.mock import patch

        from matches.simulation import BatchSimulator

        before = set(Match.objects.values_list("id", flat=True))
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            self.client.post(
                reverse("create_match"),
                {
                    "team_red": self.red.id,
                    "team_blue": self.blue.id,
                    "match_type": "friendly",
                },
            )
        created = Match.objects.exclude(id__in=before)
        self.assertTrue(created.exists())
        for match in created:
            with self.subTest(match=match.pk):
                self.assertEqual(match.manager_id, get_shared_manager().pk)

    def test_create_single_round_stamps_the_game_round(self) -> None:
        from unittest.mock import patch

        from matches.simulation import BatchSimulator

        before = set(GameRound.objects.values_list("id", flat=True))
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            self.client.post(
                reverse("create_single_round"),
                {"team_red": self.red.id, "team_blue": self.blue.id},
            )
        created = GameRound.objects.exclude(id__in=before)
        self.assertTrue(created.exists())
        for game_round in created:
            with self.subTest(round=game_round.pk):
                self.assertEqual(game_round.manager_id, get_shared_manager().pk)


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


class TestLeagueListScoping(TestCase):
    """§7 — `league_list` filters both the active and archived lists."""

    def setUp(self) -> None:
        super().setUp()
        self.me = get_shared_manager()
        self.other = User.objects.create_user(
            email="ll-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.mine = League.objects.create(
            name="LL Mine", state="active", manager=self.me
        )
        self.theirs = League.objects.create(
            name="LL Theirs", state="active", manager=self.other
        )
        self.open_league = League.objects.create(name="LL Open", state="active")
        self.mine_archived = League.objects.create(
            name="LL Mine Arch", state="archived", manager=self.me
        )
        self.theirs_archived = League.objects.create(
            name="LL Theirs Arch", state="archived", manager=self.other
        )

    def test_active_list_shows_own_and_unmanaged_only(self) -> None:
        active = set(self.client.get(reverse("league_list")).context["active_leagues"])
        self.assertEqual(active, {self.mine, self.open_league})

    def test_archived_list_shows_own_and_unmanaged_only(self) -> None:
        archived = set(
            self.client.get(reverse("league_list")).context["archived_leagues"]
        )
        self.assertEqual(archived, {self.mine_archived})

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        self.client.force_login(self.other)
        context = self.client.get(reverse("league_list")).context
        self.assertEqual(
            set(context["active_leagues"]), {self.theirs, self.open_league}
        )
        self.assertEqual(set(context["archived_leagues"]), {self.theirs_archived})


class TestTournamentListScoping(TestCase):
    """§7 — `tournament_list`."""

    def setUp(self) -> None:
        super().setUp()
        self.me = get_shared_manager()
        self.other = User.objects.create_user(
            email="tl-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.mine = Tournament.objects.create(name="TL Mine", manager=self.me)
        self.theirs = Tournament.objects.create(name="TL Theirs", manager=self.other)
        self.open_cup = Tournament.objects.create(name="TL Open")

    def test_own_and_unmanaged_tournaments_are_listed(self) -> None:
        listed = set(self.client.get(reverse("tournament_list")).context["tournaments"])
        self.assertEqual(listed, {self.mine, self.open_cup})

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        self.client.force_login(self.other)
        listed = set(self.client.get(reverse("tournament_list")).context["tournaments"])
        self.assertEqual(listed, {self.theirs, self.open_cup})


class TestMatchListScoping(TestCase):
    """§7 — `match_list` uses the two conditional-root predicates."""

    def setUp(self) -> None:
        super().setUp()
        self.me = get_shared_manager()
        self.other = User.objects.create_user(
            email="ml-other@example.com", password="Str0ng-Passphrase-9"
        )
        self.red, _ = make_team_with_slots("MLRed")
        self.blue, _ = make_team_with_slots("MLBlue")

        self.mine = Match.objects.create(
            team_red=self.red, team_blue=self.blue, manager=self.me
        )
        self.theirs = Match.objects.create(
            team_red=self.red, team_blue=self.blue, manager=self.other
        )
        self.open_match = Match.objects.create(team_red=self.red, team_blue=self.blue)

        self.my_round = GameRound.objects.create(round_number=1, manager=self.me)
        self.their_round = GameRound.objects.create(round_number=1, manager=self.other)
        self.open_round = GameRound.objects.create(round_number=1)

    def test_own_and_unmanaged_matches_are_listed(self) -> None:
        listed = set(self.client.get(reverse("match_list")).context["matches"])
        self.assertEqual(listed, {self.mine, self.open_match})

    def test_another_accounts_match_is_not_listed(self) -> None:
        listed = set(self.client.get(reverse("match_list")).context["matches"])
        self.assertNotIn(self.theirs, listed)

    def test_own_and_unmanaged_standalone_rounds_are_listed(self) -> None:
        listed = set(self.client.get(reverse("match_list")).context["detailed_rounds"])
        self.assertEqual(listed, {self.my_round, self.open_round})

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        self.client.force_login(self.other)
        context = self.client.get(reverse("match_list")).context
        self.assertEqual(set(context["matches"]), {self.theirs, self.open_match})
        self.assertEqual(
            set(context["detailed_rounds"]), {self.their_round, self.open_round}
        )

    def test_season_match_is_scoped_through_its_league(self) -> None:
        league = League.objects.create(name="ML League", manager=self.other)
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        their_season_match = Match.objects.create(
            team_red=self.red, team_blue=self.blue, season=season
        )
        listed = set(self.client.get(reverse("match_list")).context["matches"])
        self.assertNotIn(their_season_match, listed)


# ---------------------------------------------------------------------------
# DRF viewsets (§8.2)
# ---------------------------------------------------------------------------


class TestApiQuerysetScoping(TestCase):
    """§8.2 — the four viewsets are manager-scoped, and 403 while anonymous."""

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
        names = {
            row["name"] for row in self.client.get("/api/teams/").json()["results"]
        }
        self.assertIn(self.mine.name, names)
        self.assertNotIn(self.theirs.name, names)

    def test_team_detail_of_another_account_is_404(self) -> None:
        self.assertEqual(
            self.client.get(f"/api/teams/{self.theirs.pk}/").status_code, 404
        )

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        self.client.force_login(self.other)
        names = {
            row["name"] for row in self.client.get("/api/teams/").json()["results"]
        }
        self.assertIn(self.theirs.name, names)
        self.assertNotIn(self.mine.name, names)

    def test_anonymous_api_access_is_403_not_302(self) -> None:
        self.client.logout()
        response = self.client.get("/api/teams/")
        self.assertEqual(response.status_code, 403)
