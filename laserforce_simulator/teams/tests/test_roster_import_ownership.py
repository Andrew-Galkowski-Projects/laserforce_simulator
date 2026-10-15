"""ANG-01a — roster-import ownership leak regression (seam contract §6.3).

Before ANG-01a, ``_check_db_slot_collisions`` / ``_apply_roster`` looked Teams
up by bare name, ignoring ownership: importing a CSV that named another
Account's Team appended Players to it, and a slot collision there echoed that
Account's player name in the error.

Scenario: Account B owns Team "Shared" whose commander slot is filled by
"SecretPlayer". Account A imports a CSV with a "Shared" / commander row. The
import must succeed, create a NEW "Shared" Team managed by A, leave B's Team
untouched, and never surface "SecretPlayer" anywhere — through the HTML view,
the API, the service, and the Tournament participant import.

Collision against A's own Team or an Unmanaged Team still errors with the
existing message; duplicate same-name owned Teams must not raise
``MultipleObjectsReturned``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import Tournament
from teams.models import Player, Team
from teams.roster_importer import REQUIRED_COLUMNS

User = get_user_model()

SECRET = "SecretPlayer"


def _row(team: str, name: str, role: str) -> str:
    return f"{team},{name},{role},28,16,100,Ultrazone Chicago,5'7\""


def _six_rows(team: str, prefix: str) -> list[str]:
    return [
        _row(team, f"{prefix}-Cdr", "commander"),
        _row(team, f"{prefix}-Hvy", "heavy"),
        _row(team, f"{prefix}-Sc1", "scout"),
        _row(team, f"{prefix}-Sc2", "scout"),
        _row(team, f"{prefix}-Med", "medic"),
        _row(team, f"{prefix}-Amm", "ammo"),
    ]


def _csv_text(*rows: str) -> str:
    return "\n".join([",".join(REQUIRED_COLUMNS), *rows]) + "\n"


def _upload(text: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        "roster.csv", text.encode("utf-8"), content_type="text/csv"
    )


class _LeakScenario(TestCase):
    """B owns "Shared" with commander "SecretPlayer"; A is logged in."""

    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="leak-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.b_team = Team.objects.create(name="Shared", manager=self.user_b)
        self.secret = Player.objects.create(team=self.b_team, name=SECRET)
        self.b_team.slot_commander = self.secret
        self.b_team.save()
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.csv = _csv_text(_row("Shared", "MyCommander", "commander"))

    def assert_b_team_untouched(self) -> None:
        self.b_team.refresh_from_db()
        self.assertEqual(self.b_team.manager_id, self.user_b.pk)
        self.assertEqual(self.b_team.slot_commander_id, self.secret.pk)
        self.assertEqual(
            list(self.b_team.players.values_list("name", flat=True)), [SECRET]
        )

    def assert_new_shared_team_for_a(self) -> Team:
        new_team = Team.objects.get(name="Shared", manager=self.user_a)
        self.assertNotEqual(new_team.pk, self.b_team.pk)
        self.assertEqual(new_team.slot_commander.name, "MyCommander")
        self.assertEqual(Team.objects.filter(name="Shared").count(), 2)
        return new_team


# ---------------------------------------------------------------------------
# The leak regression, per surface
# ---------------------------------------------------------------------------


class TestLeakRegressionHtml(_LeakScenario):
    def test_html_import_creates_new_team_and_never_leaks(self) -> None:
        response = self.client.post(
            reverse("import_roster"), {"csv_file": _upload(self.csv)}
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn(SECRET, body)
        self.assertIn("roster-import-confirm-summary", body)
        self.assertNotIn("roster-import-errors", body)
        self.assert_new_shared_team_for_a()
        self.assert_b_team_untouched()

    def test_html_context_reports_created_not_appended(self) -> None:
        response = self.client.post(
            reverse("import_roster"), {"csv_file": _upload(self.csv)}
        )
        self.assertEqual(
            [t.name for t in response.context["created_teams"]], ["Shared"]
        )
        self.assertEqual(list(response.context["appended_teams"]), [])


class TestLeakRegressionApi(_LeakScenario):
    def test_api_import_creates_new_team_and_never_leaks(self) -> None:
        response = self.api.post(
            "/api/teams/import/", {"csv_file": _upload(self.csv)}, format="multipart"
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn(SECRET, response.content.decode())
        data = response.json()["data"]
        self.assertEqual([t["name"] for t in data["created_teams"]], ["Shared"])
        self.assertEqual(data["appended_teams"], [])
        new_team = self.assert_new_shared_team_for_a()
        self.assertEqual(data["created_teams"][0]["id"], new_team.pk)
        self.assert_b_team_untouched()

    def test_api_full_foreign_roster_still_not_leaked(self) -> None:
        """Even a CSV hitting every one of B's slots never names B's players."""
        for slot, name in (
            ("slot_heavy", "SecretHeavy"),
            ("slot_scout_1", "SecretScout1"),
            ("slot_scout_2", "SecretScout2"),
            ("slot_medic", "SecretMedic"),
            ("slot_ammo", "SecretAmmo"),
        ):
            setattr(
                self.b_team, slot, Player.objects.create(team=self.b_team, name=name)
            )
        self.b_team.save()
        response = self.api.post(
            "/api/teams/import/",
            {"csv_file": _upload(_csv_text(*_six_rows("Shared", "A")))},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("Secret", response.content.decode())
        self.assertEqual(
            Team.objects.get(name="Shared", manager=self.user_a).players.count(), 6
        )
        self.assertEqual(self.b_team.players.count(), 6)


class TestLeakRegressionService(_LeakScenario):
    def test_service_import_creates_new_stamped_team(self) -> None:
        from teams.services import run_roster_import

        result = run_roster_import(self.csv, manager=self.user_a)
        self.assertEqual([t.name for t in result.created_teams], ["Shared"])
        self.assertEqual(result.created_teams[0].manager_id, self.user_a.pk)
        self.assertEqual(result.appended_teams, [])
        self.assert_b_team_untouched()

    def test_collision_check_ignores_foreign_team(self) -> None:
        from teams.roster_importer import parse_roster_csv
        from teams.services import _check_db_slot_collisions

        # Must not raise — B's filled slot is invisible to A.
        _check_db_slot_collisions(parse_roster_csv(self.csv), manager=self.user_a)

    def test_collision_check_still_fires_for_the_owner(self) -> None:
        from teams.roster_importer import RosterImportError, parse_roster_csv
        from teams.services import _check_db_slot_collisions

        with self.assertRaises(RosterImportError):
            _check_db_slot_collisions(parse_roster_csv(self.csv), manager=self.user_b)

    def test_manager_none_also_cannot_see_foreign_team(self) -> None:
        from teams.services import run_roster_import

        result = run_roster_import(self.csv, manager=None)
        self.assertEqual(len(result.created_teams), 1)
        self.assertIsNone(result.created_teams[0].manager_id)
        self.assert_b_team_untouched()

    def test_manager_is_required_keyword_on_private_helpers(self) -> None:
        """No caller can silently keep the leak by omitting ``manager``."""
        from teams.roster_importer import parse_roster_csv
        from teams.services import _apply_roster, _check_db_slot_collisions

        parsed = parse_roster_csv(self.csv)
        with self.assertRaises(TypeError):
            _check_db_slot_collisions(parsed)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            _apply_roster(parsed)  # type: ignore[call-arg]

    def test_views_re_export_is_the_service_function(self) -> None:
        import teams.services as services
        import teams.views as views

        self.assertIs(views._apply_roster, services._apply_roster)
        self.assertIs(
            views._check_db_slot_collisions, services._check_db_slot_collisions
        )


class TestLeakRegressionTournamentImport(_LeakScenario):
    def test_tournament_participant_import_never_touches_foreign_team(self) -> None:
        tournament = Tournament.objects.create(name="LeakCup", manager=self.user_a)
        rows = _six_rows("Shared", "T")
        for i in range(3):
            rows.extend(_six_rows(f"LeakFresh{i}", f"L{i}"))
        response = self.client.post(
            reverse("tournament_import_participants", args=[tournament.pk]),
            {"csv_file": _upload(_csv_text(*rows))},
        )
        self.assertEqual(response.status_code, 302, response.content[:500])
        self.assertNotIn(SECRET, response.content.decode())
        new_shared = Team.objects.get(name="Shared", manager=self.user_a)
        self.assertEqual(new_shared.players.count(), 6)
        participant_team_ids = set(
            tournament.participants.values_list("team_id", flat=True)
        )
        self.assertIn(new_shared.pk, participant_team_ids)
        self.assertNotIn(self.b_team.pk, participant_team_ids)
        self.assert_b_team_untouched()

    def test_tournament_created_teams_are_stamped(self) -> None:
        tournament = Tournament.objects.create(name="StampCup", manager=self.user_a)
        rows: list[str] = []
        for i in range(4):
            rows.extend(_six_rows(f"StampTeam{i}", f"S{i}"))
        self.client.post(
            reverse("tournament_import_participants", args=[tournament.pk]),
            {"csv_file": _upload(_csv_text(*rows))},
        )
        for i in range(4):
            team = Team.objects.get(name=f"StampTeam{i}")
            self.assertEqual(team.manager_id, self.user_a.pk)


# ---------------------------------------------------------------------------
# Collisions that MUST still fire (own / Unmanaged Teams)
# ---------------------------------------------------------------------------


class TestCollisionsStillFire(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.api = APIClient()
        self.api.force_login(self.user_a)

    def _filled_team(self, name: str, manager) -> Team:
        team = Team.objects.create(name=name, manager=manager)
        cmd = Player.objects.create(team=team, name=f"{name}Cdr")
        team.slot_commander = cmd
        team.save()
        return team

    def _expected_message(self, team_name: str) -> str:
        return (
            f"Team '{team_name}' slot 'slot_commander' already filled by "
            f"player '{team_name}Cdr'"
        )

    def test_own_team_collision_api_400_with_existing_message(self) -> None:
        self._filled_team("Mine", self.user_a)
        response = self.api.post(
            "/api/teams/import/",
            {"csv_file": _upload(_csv_text(_row("Mine", "Extra", "commander")))},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            response.json()["errors"],
            [
                {
                    "code": "roster_invalid",
                    "message": self._expected_message("Mine"),
                    "field": "row:1:role",
                }
            ],
        )
        self.assertFalse(Player.objects.filter(name="Extra").exists())

    def test_unmanaged_team_collision_api_400(self) -> None:
        self._filled_team("Commons", None)
        response = self.api.post(
            "/api/teams/import/",
            {"csv_file": _upload(_csv_text(_row("Commons", "Extra", "commander")))},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            response.json()["errors"][0]["message"], self._expected_message("Commons")
        )

    def test_own_team_collision_html_still_renders_error(self) -> None:
        self._filled_team("MineHtml", self.user_a)
        response = self.client.post(
            reverse("import_roster"),
            {"csv_file": _upload(_csv_text(_row("MineHtml", "Extra", "commander")))},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("roster-import-errors", response.content.decode())
        self.assertIn("MineHtmlCdr", response.content.decode())
        self.assertFalse(Player.objects.filter(name="Extra").exists())

    def test_duplicate_same_name_owned_teams_do_not_raise(self) -> None:
        older = Team.objects.create(name="Dup", manager=self.user_a)
        newer = Team.objects.create(name="Dup", manager=self.user_a)
        response = self.api.post(
            "/api/teams/import/",
            {"csv_file": _upload(_csv_text(_row("Dup", "DupCdr", "commander")))},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["created_teams"], [])
        self.assertEqual([t["id"] for t in data["appended_teams"]], [newer.pk])
        newer.refresh_from_db()
        older.refresh_from_db()
        self.assertEqual(newer.slot_commander.name, "DupCdr")
        self.assertIsNone(older.slot_commander_id)

    def test_duplicate_same_name_html_does_not_raise(self) -> None:
        Team.objects.create(name="DupHtml", manager=self.user_a)
        Team.objects.create(name="DupHtml")
        response = self.client.post(
            reverse("import_roster"),
            {"csv_file": _upload(_csv_text(_row("DupHtml", "X", "heavy")))},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("roster-import-confirm-summary", response.content.decode())
