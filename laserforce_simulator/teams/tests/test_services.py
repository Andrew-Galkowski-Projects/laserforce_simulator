"""ANG-01a — ``teams/services.py`` (seam contract §6).

``run_player_generation`` / ``GenerationResult``, ``run_roster_import`` /
``RosterImportResult``, ``resolve_import_teams`` scope, ``player_form_initial``,
``annotate_overall_rating`` and ``build_roster_template_csv`` — plus the HTML
surfaces that now delegate to them (``player_add`` GET, ``player_list``,
``import_roster_template``), whose behaviour must be unchanged.

Generation is seeded with ``random.seed(42)``; random names / profile values
are never asserted.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import random

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from conftest import get_shared_manager
from teams.models import Player, Team, get_free_agents_team
from teams.roster_importer import ALL_COLUMNS, REQUIRED_COLUMNS, RosterImportError
from teams.serializers import _PLAYER_STAT_FIELDS
from teams.services import (
    GenerationResult,
    RosterImportResult,
    annotate_overall_rating,
    build_roster_template_csv,
    player_form_initial,
    resolve_import_teams,
    run_player_generation,
    run_roster_import,
)

User = get_user_model()

_PROFILE_KEYS = {
    "name",
    "age",
    "started_playing_age",
    "total_games",
    "home_site",
    "height",
}


def _row(team: str, name: str, role: str) -> str:
    return f"{team},{name},{role},28,16,100,Ultrazone Chicago,5'7\""


def _csv_text(*rows: str) -> str:
    return "\n".join([",".join(REQUIRED_COLUMNS), *rows]) + "\n"


def _six_rows(team: str, prefix: str) -> list[str]:
    return [
        _row(team, f"{prefix}-Cdr", "commander"),
        _row(team, f"{prefix}-Hvy", "heavy"),
        _row(team, f"{prefix}-Sc1", "scout"),
        _row(team, f"{prefix}-Sc2", "scout"),
        _row(team, f"{prefix}-Med", "medic"),
        _row(team, f"{prefix}-Amm", "ammo"),
    ]


class _ServicesTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="services-b@example.com", password="Str0ng-Passphrase-9"
        )


# ---------------------------------------------------------------------------
# run_player_generation (§6.2)
# ---------------------------------------------------------------------------


class TestRunPlayerGeneration(_ServicesTestCase):
    _TEAMS = {"num_teams": "3", "players_per_team": "7", "mean": 50, "std_dev": 15}
    _POOL = {"num_teams": "0", "players_per_team": "15", "mean": 50, "std_dev": 15}

    def test_result_dataclass_fields(self) -> None:
        self.assertTrue(dataclasses.is_dataclass(GenerationResult))
        self.assertEqual(
            [f.name for f in dataclasses.fields(GenerationResult)],
            ["created_teams", "free_agent_count"],
        )

    def test_result_is_frozen(self) -> None:
        random.seed(42)
        result = run_player_generation(self._TEAMS, manager=self.user_a)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.free_agent_count = 99  # type: ignore[misc]

    def test_teams_mode_creates_and_stamps(self) -> None:
        random.seed(42)
        result = run_player_generation(self._TEAMS, manager=self.user_a)
        self.assertIsInstance(result, GenerationResult)
        self.assertEqual(result.free_agent_count, 0)
        self.assertEqual(len(result.created_teams), 3)
        for team in result.created_teams:
            self.assertIsInstance(team, Team)
            self.assertEqual(team.manager_id, self.user_a.pk)
            self.assertEqual(team.players.count(), 7)
            team.refresh_from_db()
            self.assertTrue(team.is_valid_roster, team.roster_errors)

    def test_teams_mode_with_no_manager_is_unmanaged(self) -> None:
        random.seed(42)
        result = run_player_generation(self._TEAMS, manager=None)
        for team in result.created_teams:
            self.assertIsNone(team.manager_id)

    def test_free_agent_mode(self) -> None:
        random.seed(42)
        fa = get_free_agents_team()
        teams_before = Team.objects.count()
        players_before = fa.players.count()
        result = run_player_generation(self._POOL, manager=self.user_a)
        self.assertEqual(result.created_teams, [])
        self.assertEqual(result.free_agent_count, 15)
        self.assertEqual(Team.objects.count(), teams_before)
        self.assertEqual(fa.players.count() - players_before, 15)
        fa.refresh_from_db()
        self.assertIsNone(fa.manager_id, "the global pool is never stamped")

    def test_random_markers_resolve_in_range(self) -> None:
        random.seed(42)
        result = run_player_generation(
            {
                "num_teams": "random_2_10",
                "players_per_team": "random_team",
                "mean": 50,
                "std_dev": 15,
            },
            manager=self.user_a,
        )
        self.assertTrue(2 <= len(result.created_teams) <= 10)
        for team in result.created_teams:
            self.assertTrue(6 <= team.players.count() <= 8)

    def test_same_seed_same_counts(self) -> None:
        random.seed(42)
        first = run_player_generation(
            {
                "num_teams": "random_2_10",
                "players_per_team": "random_team",
                "mean": 50,
                "std_dev": 15,
            },
            manager=self.user_a,
        )
        first_shape = [t.players.count() for t in first.created_teams]
        random.seed(42)
        second = run_player_generation(
            {
                "num_teams": "random_2_10",
                "players_per_team": "random_team",
                "mean": 50,
                "std_dev": 15,
            },
            manager=self.user_a,
        )
        self.assertEqual([t.players.count() for t in second.created_teams], first_shape)


# ---------------------------------------------------------------------------
# run_roster_import (§6.3)
# ---------------------------------------------------------------------------


class TestRunRosterImport(_ServicesTestCase):
    def test_result_dataclass_fields(self) -> None:
        self.assertTrue(dataclasses.is_dataclass(RosterImportResult))
        self.assertEqual(
            [f.name for f in dataclasses.fields(RosterImportResult)],
            ["created_teams", "appended_teams", "player_count", "row_count"],
        )

    def test_happy_path_creates_stamped_teams(self) -> None:
        text = _csv_text(*_six_rows("SvcRed", "R"), *_six_rows("SvcBlue", "B"))
        result = run_roster_import(text, manager=self.user_a)
        self.assertIsInstance(result, RosterImportResult)
        self.assertEqual([t.name for t in result.created_teams], ["SvcRed", "SvcBlue"])
        self.assertEqual(result.appended_teams, [])
        self.assertEqual(result.player_count, 12)
        self.assertEqual(result.row_count, 12)
        for team in result.created_teams:
            self.assertEqual(team.manager_id, self.user_a.pk)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.player_count = 0  # type: ignore[misc]

    def test_appends_to_own_team(self) -> None:
        mine = Team.objects.create(name="SvcMine", manager=self.user_a)
        result = run_roster_import(
            _csv_text(_row("SvcMine", "Add", "medic")), manager=self.user_a
        )
        self.assertEqual(result.created_teams, [])
        self.assertEqual([t.pk for t in result.appended_teams], [mine.pk])
        mine.refresh_from_db()
        self.assertEqual(mine.slot_medic.name, "Add")
        self.assertEqual(mine.manager_id, self.user_a.pk)

    def test_appends_to_unmanaged_team(self) -> None:
        unmanaged = Team.objects.create(name="SvcShared")
        result = run_roster_import(
            _csv_text(_row("SvcShared", "Add", "heavy")), manager=self.user_a
        )
        self.assertEqual([t.pk for t in result.appended_teams], [unmanaged.pk])
        unmanaged.refresh_from_db()
        self.assertIsNone(unmanaged.manager_id)

    def test_manager_none_creates_unmanaged(self) -> None:
        result = run_roster_import(
            _csv_text(_row("SvcNobody", "Solo", "commander")), manager=None
        )
        self.assertIsNone(result.created_teams[0].manager_id)

    def test_parse_error_raises_and_writes_nothing(self) -> None:
        teams_before, players_before = Team.objects.count(), Player.objects.count()
        text = _csv_text(*_six_rows("SvcGood", "G"), _row("SvcBad", "X", "pilot"))
        with self.assertRaises(RosterImportError) as ctx:
            run_roster_import(text, manager=self.user_a)
        self.assertTrue(ctx.exception.errors)
        self.assertEqual(Team.objects.count(), teams_before)
        self.assertEqual(Player.objects.count(), players_before)

    def test_collision_raises_and_rolls_back_earlier_teams(self) -> None:
        """A DB collision on the 2nd team leaves no trace of the 1st team."""
        full = Team.objects.create(name="SvcFull", manager=self.user_a)
        cmd = Player.objects.create(team=full, name="SvcFullCdr")
        full.slot_commander = cmd
        full.save()
        teams_before, players_before = Team.objects.count(), Player.objects.count()
        text = _csv_text(
            *_six_rows("SvcFirst", "F"), _row("SvcFull", "Two", "commander")
        )
        with self.assertRaises(RosterImportError) as ctx:
            run_roster_import(text, manager=self.user_a)
        self.assertEqual(ctx.exception.errors[0].field, "role")
        self.assertFalse(Team.objects.filter(name="SvcFirst").exists())
        self.assertEqual(Team.objects.count(), teams_before)
        self.assertEqual(Player.objects.count(), players_before)


# ---------------------------------------------------------------------------
# resolve_import_teams (§6.3)
# ---------------------------------------------------------------------------


class TestResolveImportTeams(_ServicesTestCase):
    def test_scope_is_own_plus_unmanaged(self) -> None:
        mine = Team.objects.create(name="ResMine", manager=self.user_a)
        unmanaged = Team.objects.create(name="ResShared")
        Team.objects.create(name="ResTheirs", manager=self.user_b)
        resolved = resolve_import_teams(
            ["ResMine", "ResShared", "ResTheirs", "ResMissing"], manager=self.user_a
        )
        self.assertEqual(resolved, {"ResMine": mine, "ResShared": unmanaged})

    def test_manager_none_sees_only_unmanaged(self) -> None:
        Team.objects.create(name="ResMine2", manager=self.user_a)
        unmanaged = Team.objects.create(name="ResShared2")
        resolved = resolve_import_teams(["ResMine2", "ResShared2"], manager=None)
        self.assertEqual(resolved, {"ResShared2": unmanaged})

    def test_duplicate_name_highest_id_wins(self) -> None:
        Team.objects.create(name="ResDup", manager=self.user_a)
        newer = Team.objects.create(name="ResDup")
        resolved = resolve_import_teams(["ResDup"], manager=self.user_a)
        self.assertEqual(resolved["ResDup"].pk, newer.pk)

    def test_empty_names(self) -> None:
        self.assertEqual(resolve_import_teams([], manager=self.user_a), {})


# ---------------------------------------------------------------------------
# Small helpers (§6.1)
# ---------------------------------------------------------------------------


class TestPlayerFormInitial(TestCase):
    def test_contains_stat_defaults_and_profile(self) -> None:
        random.seed(42)
        initial = player_form_initial()
        for stat in _PLAYER_STAT_FIELDS:
            self.assertEqual(initial[stat], 50, stat)
        self.assertTrue(_PROFILE_KEYS <= set(initial))
        self.assertIsInstance(initial["name"], str)
        self.assertTrue(initial["name"])

    def test_profile_values_within_form_bounds(self) -> None:
        random.seed(42)
        initial = player_form_initial()
        self.assertTrue(5 <= initial["age"] <= 100)
        self.assertTrue(3 <= initial["started_playing_age"] <= 100)
        self.assertTrue(0 <= initial["total_games"] <= 100_000)

    def test_initial_binds_a_valid_player_form(self) -> None:
        from teams.forms import PlayerForm

        random.seed(42)
        form = PlayerForm(data=player_form_initial())
        self.assertTrue(form.is_valid(), form.errors)


class TestAnnotateOverallRating(TestCase):
    def test_annotation_matches_overall_rating_property(self) -> None:
        team = Team.objects.create(name="Annotated")
        p = Player.objects.create(team=team, name="Rated", accuracy=69, stamina=12)
        row = annotate_overall_rating(Player.objects.filter(pk=p.pk)).get()
        self.assertAlmostEqual(row.overall_rating_db, p.overall_rating, places=6)
        self.assertAlmostEqual(row.overall_rating_db, (50 * 17 + 69 + 12) / 19.0)

    def test_annotation_is_orderable(self) -> None:
        team = Team.objects.create(name="Annotated 2")
        Player.objects.create(team=team, name="Weak", accuracy=0)
        Player.objects.create(team=team, name="Strong", accuracy=100)
        names = list(
            annotate_overall_rating(Player.objects.filter(team=team))
            .order_by("-overall_rating_db")
            .values_list("name", flat=True)
        )
        self.assertEqual(names, ["Strong", "Weak"])


class TestBuildRosterTemplateCsv(TestCase):
    def test_header_and_two_example_rows(self) -> None:
        rows = list(csv.reader(io.StringIO(build_roster_template_csv())))
        self.assertEqual(tuple(rows[0]), ALL_COLUMNS)
        self.assertEqual(len(rows), 3)
        for row in rows[1:]:
            self.assertEqual(len(row), len(ALL_COLUMNS))
        self.assertIn("scout,medic", rows[2])

    def test_byte_identical_to_html_download(self) -> None:
        response = self.client.get(reverse("import_roster_template"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode("utf-8"), build_roster_template_csv())

    def test_template_round_trips_through_the_importer(self) -> None:
        result = run_roster_import(build_roster_template_csv(), manager=None)
        self.assertEqual(result.row_count, 2)


# ---------------------------------------------------------------------------
# HTML surfaces delegating to services — behaviour unchanged
# ---------------------------------------------------------------------------


class TestHtmlDelegatesUnchanged(_ServicesTestCase):
    def test_player_add_get_renders_stat_defaults(self) -> None:
        team = Team.objects.create(name="Html Add", manager=self.user_a)
        random.seed(42)
        response = self.client.get(reverse("player_add", args=[team.pk]))
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        for stat in _PLAYER_STAT_FIELDS:
            self.assertEqual(form[stat].value(), 50, stat)
        self.assertTrue(form["name"].value())

    def test_player_list_overall_rating_sort_still_works(self) -> None:
        team = Team.objects.create(name="Html List", manager=self.user_a)
        Player.objects.create(team=team, name="Lo", accuracy=0)
        Player.objects.create(team=team, name="Hi", accuracy=100)
        response = self.client.get(
            reverse("player_list"), {"sort": "overall_rating", "dir": "desc"}
        )
        self.assertEqual(response.status_code, 200)
        names = [p.name for p in response.context["page_obj"]]
        self.assertEqual(names[:2], ["Hi", "Lo"])

    def test_html_generate_still_stamps(self) -> None:
        random.seed(42)
        response = self.client.post(
            reverse("generate_players"),
            {"num_teams": "2", "players_per_team": "6", "mean": "50", "std_dev": "15"},
        )
        self.assertEqual(response.status_code, 200)
        created = response.context["created_teams"]
        self.assertEqual(len(created), 2)
        self.assertEqual(response.context["free_agent_count"], 0)
        for team in created:
            self.assertEqual(team.manager_id, self.user_a.pk)

    def test_html_generate_free_agent_mode_context(self) -> None:
        random.seed(42)
        response = self.client.post(
            reverse("generate_players"),
            {"num_teams": "0", "players_per_team": "12", "mean": "50", "std_dev": "15"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["created_teams"]), [])
        self.assertEqual(response.context["free_agent_count"], 12)


# ---------------------------------------------------------------------------
# ANG-01b — career + role benchmark context builders (seam contract §3)
# ---------------------------------------------------------------------------

_CAREER_BUILDER_KEYS = {
    "total_rounds",
    "career",
    "per_role",
    "trend",
    "has_rounds",
    "min_rounds",
    "display",
    "stat_keys",
    "per_role_with_benchmarks",
}
_BENCHMARK_BUILDER_KEYS = {
    "min_rounds",
    "display",
    "roles",
    "benchmarks",
    "stat_keys",
    "benchmarks_by_role",
    "any_data",
}


class TestBuildCareerStats(TestCase):
    def setUp(self) -> None:
        from django.core.cache import cache

        cache.clear()
        self.team = Team.objects.create(name="Career Builder Team")
        self.player = Player.objects.create(team=self.team, name="Career Builder")

    def test_key_set_excludes_player(self) -> None:
        from teams.services import build_career_stats

        result = build_career_stats(
            self.player, min_rounds=5, display="mean", user=get_shared_manager()
        )
        self.assertEqual(set(result), _CAREER_BUILDER_KEYS)
        self.assertNotIn("player", result)

    def test_no_rounds_has_empty_overlay(self) -> None:
        from teams.services import build_career_stats

        result = build_career_stats(
            self.player, min_rounds=5, display="mean", user=get_shared_manager()
        )
        self.assertEqual(result["total_rounds"], 0)
        self.assertFalse(result["has_rounds"])
        self.assertEqual(result["per_role_with_benchmarks"], [])

    def test_params_passed_through(self) -> None:
        from teams.services import build_career_stats

        result = build_career_stats(
            self.player, min_rounds=2, display="median", user=get_shared_manager()
        )
        self.assertEqual(result["min_rounds"], 2)
        self.assertEqual(result["display"], "median")

    def test_with_rounds_builds_overlay(self) -> None:
        from unittest.mock import patch

        from matches.simulation import BatchSimulator
        from matches.tests.conftest import make_team_with_slots
        from teams.services import build_career_stats

        red, players = make_team_with_slots("CarBuildRed")
        blue, _ = make_team_with_slots("CarBuildBlue")
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            BatchSimulator().save_games(red, blue, [[1357, False]], 1)
        result = build_career_stats(
            players["commander"],
            min_rounds=5,
            display="mean",
            user=get_shared_manager(),
        )
        self.assertEqual(result["total_rounds"], 1)
        self.assertTrue(result["has_rounds"])
        self.assertTrue(result["per_role_with_benchmarks"])

    def test_matches_html_view_context(self) -> None:
        from teams.services import build_career_stats

        client = self.client
        client.force_login(get_shared_manager())
        response = client.get(reverse("player_career_stats", args=[self.player.pk]))
        self.assertEqual(response.status_code, 200)
        result = build_career_stats(
            self.player, min_rounds=5, display="mean", user=get_shared_manager()
        )
        for key in _CAREER_BUILDER_KEYS:
            self.assertEqual(response.context[key], result[key], key)


class TestBuildRoleBenchmarks(TestCase):
    def setUp(self) -> None:
        from django.core.cache import cache

        cache.clear()

    def test_key_set_includes_template_only_key(self) -> None:
        from teams.services import build_role_benchmarks

        result = build_role_benchmarks(
            min_rounds=5, display="mean", user=get_shared_manager()
        )
        self.assertEqual(set(result), _BENCHMARK_BUILDER_KEYS)

    def test_benchmarks_shape(self) -> None:
        from teams.role_benchmarks import ROLES, STAT_KEYS
        from teams.services import build_role_benchmarks

        result = build_role_benchmarks(
            min_rounds=5, display="mean", user=get_shared_manager()
        )
        self.assertEqual(set(result["benchmarks"]), set(ROLES))
        for role in ROLES:
            self.assertEqual(
                [row["stat"] for row in result["benchmarks"][role]], list(STAT_KEYS)
            )
        self.assertEqual(
            [role for role, _rows in result["benchmarks_by_role"]], list(ROLES)
        )

    def test_empty_db_has_no_data(self) -> None:
        from teams.services import build_role_benchmarks

        self.assertFalse(
            build_role_benchmarks(
                min_rounds=5, display="mean", user=get_shared_manager()
            )["any_data"]
        )

    def test_params_passed_through(self) -> None:
        from teams.services import build_role_benchmarks

        result = build_role_benchmarks(
            min_rounds=0, display="median", user=get_shared_manager()
        )
        self.assertEqual(result["min_rounds"], 0)
        self.assertEqual(result["display"], "median")

    def test_matches_html_view_context(self) -> None:
        from teams.services import build_role_benchmarks

        self.client.force_login(get_shared_manager())
        response = self.client.get(reverse("role_benchmarks"))
        self.assertEqual(response.status_code, 200)
        result = build_role_benchmarks(
            min_rounds=5, display="mean", user=get_shared_manager()
        )
        for key in _BENCHMARK_BUILDER_KEYS:
            self.assertEqual(response.context[key], result[key], key)
