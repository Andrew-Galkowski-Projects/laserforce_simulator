"""ANG-01a — Team / Player API writes (seam contract §5.1–§5.10).

Every write binds an existing Django ``Form`` (ADR-0039). Covers success
shapes / status codes, form-error envelopes, PATCH merge semantics, the slots
full-replace, add-player defaults, generation (both modes), multipart roster
import, the CSV template exemption, player DELETE 204, every 405 in §5.0,
cross-Account 404 on every read and write, and Manager stamping.

The project autouse fixture logs Django's ``self.client`` in as the shared
Account; API calls here use an explicit ``APIClient`` force-logged-in as the
same Account ("A"). "B" is a second Account whose rows must be invisible.
"""

from __future__ import annotations

import random

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from matches.models import GameEvent, GameRound, Match
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team, get_free_agents_team
from teams.roster_importer import REQUIRED_COLUMNS
from teams.serializers import _PLAYER_STAT_FIELDS

User = get_user_model()

_SLOTS = (
    "slot_commander",
    "slot_heavy",
    "slot_scout_1",
    "slot_scout_2",
    "slot_medic",
    "slot_ammo",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _csv(*rows: str, header: str | None = None) -> bytes:
    head = header if header is not None else ",".join(REQUIRED_COLUMNS)
    return ("\n".join([head, *rows]) + "\n").encode("utf-8")


def _upload(body: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile("roster.csv", body, content_type="text/csv")


def _codes_fields(response) -> list[tuple[str, str | None]]:
    return [(e["code"], e["field"]) for e in response.json()["errors"]]


class _ApiTestCase(TestCase):
    """Two Accounts; the API client is logged in as A (the shared manager)."""

    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="api-writes-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.api = APIClient()
        self.api.force_login(self.user_a)

    def assert_error_envelope(self, response, status: int) -> list[dict]:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"errors", "meta"})
        self.assertEqual(body["meta"], {})
        self.assertTrue(body["errors"])
        return body["errors"]

    def assert_404(self, response) -> None:
        errors = self.assert_error_envelope(response, 404)
        self.assertEqual(errors[0]["code"], "not_found")
        self.assertIsNone(errors[0]["field"])

    def assert_405(self, response) -> None:
        errors = self.assert_error_envelope(response, 405)
        self.assertEqual(errors[0]["code"], "method_not_allowed")


# ---------------------------------------------------------------------------
# Route names (§5.0)
# ---------------------------------------------------------------------------


class TestRouteNames(TestCase):
    def test_reverse_route_names(self) -> None:
        self.assertEqual(reverse("team-list"), "/api/teams/")
        self.assertEqual(reverse("team-detail", args=[1]), "/api/teams/1/")
        self.assertEqual(reverse("team-slots", args=[1]), "/api/teams/1/slots/")
        self.assertEqual(reverse("team-players", args=[1]), "/api/teams/1/players/")
        self.assertEqual(reverse("team-generate"), "/api/teams/generate/")
        self.assertEqual(reverse("team-import"), "/api/teams/import/")
        self.assertEqual(reverse("team-import-template"), "/api/teams/import/template/")
        self.assertEqual(reverse("player-list"), "/api/players/")
        self.assertEqual(reverse("player-detail", args=[1]), "/api/players/1/")
        self.assertEqual(reverse("arenamap-list"), "/api/maps/")
        self.assertEqual(reverse("gameround-events", args=[1]), "/api/rounds/1/events/")


# ---------------------------------------------------------------------------
# POST /api/teams/ (§5.2)
# ---------------------------------------------------------------------------


class TestTeamCreate(_ApiTestCase):
    def test_create_returns_201_team_envelope(self) -> None:
        response = self.api.post("/api/teams/", {"name": "Fresh Squad"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertEqual(body["data"]["name"], "Fresh Squad")
        self.assertEqual(body["data"]["players"], [])
        for field in ("id", "wins", "losses", "created_date", *_SLOTS):
            self.assertIn(field, body["data"])

    def test_create_stamps_manager(self) -> None:
        response = self.api.post("/api/teams/", {"name": "Stamped"}, format="json")
        team = Team.objects.get(pk=response.json()["data"]["id"])
        self.assertEqual(team.manager_id, self.user_a.pk)

    def test_create_accepts_form_encoded(self) -> None:
        response = self.api.post("/api/teams/", {"name": "Form Encoded"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(Team.objects.filter(name="Form Encoded").exists())

    def test_create_ignores_non_form_fields(self) -> None:
        response = self.api.post(
            "/api/teams/", {"name": "Sneaky", "wins": 99}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Team.objects.get(name="Sneaky").wins, 0)

    def test_missing_name_is_400_required(self) -> None:
        before = Team.objects.count()
        response = self.api.post("/api/teams/", {}, format="json")
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("required", "name")])
        self.assertEqual(Team.objects.count(), before)

    def test_overlong_name_is_400_max_length(self) -> None:
        response = self.api.post("/api/teams/", {"name": "x" * 101}, format="json")
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("max_length", "name")])

    def test_anonymous_create_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        response = APIClient().post("/api/teams/", {"name": "Anon"}, format="json")
        errors = self.assert_error_envelope(response, 401)
        self.assertEqual(errors[0]["code"], "not_authenticated")
        self.assertFalse(Team.objects.filter(name="Anon").exists())


# ---------------------------------------------------------------------------
# PATCH /api/teams/<pk>/ (§5.3)
# ---------------------------------------------------------------------------


class TestTeamPatch(_ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.team, self.players = make_team_with_slots("PatchMe")
        self.team.manager = self.user_a
        self.team.save()

    def test_patch_renames_and_keeps_slots(self) -> None:
        response = self.api.patch(
            f"/api/teams/{self.team.pk}/", {"name": "Renamed"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["name"], "Renamed")
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "Renamed")
        self.assertEqual(self.team.slot_commander_id, self.players["commander"].pk)
        self.assertEqual(self.team.manager_id, self.user_a.pk)

    def test_empty_patch_is_noop_200(self) -> None:
        response = self.api.patch(f"/api/teams/{self.team.pk}/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "PatchMe Team")

    def test_patch_blank_name_is_400(self) -> None:
        response = self.api.patch(
            f"/api/teams/{self.team.pk}/", {"name": ""}, format="json"
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("required", "name")])
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, "PatchMe Team")

    def test_patch_unmanaged_team_keeps_it_unmanaged(self) -> None:
        unmanaged = Team.objects.create(name="Nobody's")
        response = self.api.patch(
            f"/api/teams/{unmanaged.pk}/", {"name": "Still Nobody's"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        unmanaged.refresh_from_db()
        self.assertEqual(unmanaged.name, "Still Nobody's")
        self.assertIsNone(unmanaged.manager_id)

    def test_patch_foreign_team_is_404_and_unchanged(self) -> None:
        theirs = Team.objects.create(name="Theirs", manager=self.user_b)
        response = self.api.patch(
            f"/api/teams/{theirs.pk}/", {"name": "Hijacked"}, format="json"
        )
        self.assert_404(response)
        theirs.refresh_from_db()
        self.assertEqual(theirs.name, "Theirs")

    def test_patch_missing_team_is_404(self) -> None:
        self.assert_404(
            self.api.patch("/api/teams/999999/", {"name": "X"}, format="json")
        )


# ---------------------------------------------------------------------------
# PUT /api/teams/<pk>/slots/ (§5.4)
# ---------------------------------------------------------------------------


class TestTeamSlotsPut(_ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.team = Team.objects.create(name="Slotty", manager=self.user_a)
        self.p1 = Player.objects.create(team=self.team, name="P1")
        self.p2 = Player.objects.create(team=self.team, name="P2")
        self.p3 = Player.objects.create(team=self.team, name="P3")
        self.team.slot_commander = self.p1
        self.team.slot_heavy = self.p2
        self.team.save()

    def _put(self, team: Team, data: dict):
        return self.api.put(f"/api/teams/{team.pk}/slots/", data, format="json")

    def test_full_replace_sets_sent_and_clears_omitted(self) -> None:
        response = self._put(self.team, {"slot_commander": self.p3.pk})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["slot_commander"], self.p3.pk)
        self.assertIsNone(data["slot_heavy"])
        self.team.refresh_from_db()
        self.assertEqual(self.team.slot_commander_id, self.p3.pk)
        self.assertIsNone(self.team.slot_heavy_id)

    def test_explicit_null_clears(self) -> None:
        response = self._put(
            self.team, {"slot_commander": self.p1.pk, "slot_heavy": None}
        )
        self.assertEqual(response.status_code, 200)
        self.team.refresh_from_db()
        self.assertEqual(self.team.slot_commander_id, self.p1.pk)
        self.assertIsNone(self.team.slot_heavy_id)

    def test_empty_body_clears_every_slot(self) -> None:
        response = self._put(self.team, {})
        self.assertEqual(response.status_code, 200)
        self.team.refresh_from_db()
        for slot in _SLOTS:
            self.assertIsNone(getattr(self.team, f"{slot}_id"), slot)

    def test_response_is_team_serializer_with_players(self) -> None:
        data = self._put(self.team, {"slot_scout_1": self.p2.pk}).json()["data"]
        self.assertEqual(data["id"], self.team.pk)
        self.assertEqual({p["name"] for p in data["players"]}, {"P1", "P2", "P3"})
        self.assertIn("accuracy", data["players"][0])

    def test_foreign_player_in_slot_is_400_invalid_choice_without_name(self) -> None:
        theirs = Team.objects.create(name="Foreign Club", manager=self.user_b)
        secret = Player.objects.create(team=theirs, name="ZzSecretForeignerZz")
        response = self._put(self.team, {"slot_commander": secret.pk})
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(
            _codes_fields(response), [("invalid_choice", "slot_commander")]
        )
        self.assertNotIn("ZzSecretForeignerZz", response.content.decode())
        self.assertNotIn("ZzSecretForeignerZz", errors[0]["message"])
        self.team.refresh_from_db()
        self.assertEqual(self.team.slot_commander_id, self.p1.pk)
        self.assertEqual(self.team.slot_heavy_id, self.p2.pk)

    def test_own_player_from_another_team_is_400_invalid_choice(self) -> None:
        other_mine = Team.objects.create(name="Other Mine", manager=self.user_a)
        stray = Player.objects.create(team=other_mine, name="Stray")
        response = self._put(self.team, {"slot_heavy": stray.pk})
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid_choice", "slot_heavy")])

    def test_foreign_team_is_404_and_unchanged(self) -> None:
        theirs, players = make_team_with_slots("TheirSlots")
        theirs.manager = self.user_b
        theirs.save()
        self.assert_404(self._put(theirs, {}))
        theirs.refresh_from_db()
        self.assertEqual(theirs.slot_commander_id, players["commander"].pk)

    def test_get_slots_is_405(self) -> None:
        self.assert_405(self.api.get(f"/api/teams/{self.team.pk}/slots/"))


# ---------------------------------------------------------------------------
# POST /api/teams/<pk>/players/ (§5.5)
# ---------------------------------------------------------------------------


class TestTeamAddPlayer(_ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.team = Team.objects.create(name="Adders", manager=self.user_a)

    def _post(self, team: Team, data: dict):
        return self.api.post(f"/api/teams/{team.pk}/players/", data, format="json")

    def test_add_named_player_201_with_default_stats(self) -> None:
        response = self._post(self.team, {"name": "Newbie"})
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["name"], "Newbie")
        self.assertEqual(data["team"], self.team.pk)
        for stat in _PLAYER_STAT_FIELDS:
            self.assertEqual(data[stat], 50, stat)
        self.assertTrue(Player.objects.filter(team=self.team, name="Newbie").exists())

    def test_omitted_name_is_filled_from_initial(self) -> None:
        random.seed(42)
        response = self._post(self.team, {})
        self.assertEqual(response.status_code, 201, response.content)
        name = response.json()["data"]["name"]
        self.assertIsInstance(name, str)
        self.assertTrue(name.strip())
        self.assertEqual(self.team.players.count(), 1)

    def test_sent_stats_and_roles_are_saved(self) -> None:
        response = self._post(
            self.team,
            {"name": "Sharp", "accuracy": 91, "preferred_roles": ["scout", "medic"]},
        )
        self.assertEqual(response.status_code, 201, response.content)
        player = Player.objects.get(team=self.team, name="Sharp")
        self.assertEqual(player.accuracy, 91)
        self.assertEqual(player.preferred_roles, ["scout", "medic"])

    def test_stat_above_100_is_400_max_value(self) -> None:
        response = self._post(self.team, {"name": "Over", "accuracy": 101})
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("max_value", "accuracy")])
        self.assertEqual(self.team.players.count(), 0)

    def test_stat_below_0_is_400_min_value(self) -> None:
        response = self._post(self.team, {"name": "Under", "stamina": -1})
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("min_value", "stamina")])

    def test_invalid_role_is_400_invalid_choice(self) -> None:
        response = self._post(
            self.team, {"name": "Roley", "preferred_roles": ["pilot"]}
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(
            _codes_fields(response), [("invalid_choice", "preferred_roles")]
        )

    def test_duplicate_name_on_team_is_400_and_creates_nothing(self) -> None:
        Player.objects.create(team=self.team, name="Twin")
        response = self._post(self.team, {"name": "Twin"})
        self.assert_error_envelope(response, 400)
        self.assertEqual(self.team.players.filter(name="Twin").count(), 1)

    def test_add_to_foreign_team_is_404_and_creates_nothing(self) -> None:
        theirs = Team.objects.create(name="Not Yours", manager=self.user_b)
        self.assert_404(self._post(theirs, {"name": "Intruder"}))
        self.assertFalse(Player.objects.filter(name="Intruder").exists())

    def test_get_players_action_is_405(self) -> None:
        self.assert_405(self.api.get(f"/api/teams/{self.team.pk}/players/"))


# ---------------------------------------------------------------------------
# POST /api/teams/generate/ (§5.6)
# ---------------------------------------------------------------------------


class TestTeamGenerate(_ApiTestCase):
    def test_teams_mode_201_and_stamped(self) -> None:
        random.seed(42)
        response = self.api.post(
            "/api/teams/generate/",
            {"num_teams": "2", "players_per_team": "6", "mean": 50, "std_dev": 15},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["meta"], {})
        self.assertEqual(set(body["data"]), {"created_teams", "free_agent_count"})
        self.assertEqual(body["data"]["free_agent_count"], 0)
        created = body["data"]["created_teams"]
        self.assertEqual(len(created), 2)
        for row in created:
            self.assertEqual(len(row["players"]), 6)
            # TeamListSerializer: slim inline players.
            self.assertEqual(set(row["players"][0]), {"id", "name", "preferred_roles"})
            team = Team.objects.get(pk=row["id"])
            self.assertEqual(team.manager_id, self.user_a.pk)
            self.assertEqual(team.players.count(), 6)

    def test_free_agent_mode_201(self) -> None:
        random.seed(42)
        fa = get_free_agents_team()
        before = fa.players.count()
        response = self.api.post(
            "/api/teams/generate/",
            {"num_teams": "0", "players_per_team": "12", "mean": 50, "std_dev": 15},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["created_teams"], [])
        self.assertEqual(data["free_agent_count"], 12)
        fa.refresh_from_db()
        self.assertEqual(fa.players.count() - before, 12)
        self.assertIsNone(fa.manager_id)

    def test_cross_field_error_has_null_field(self) -> None:
        before = Team.objects.count()
        response = self.api.post(
            "/api/teams/generate/",
            {"num_teams": "2", "players_per_team": "12", "mean": 50, "std_dev": 15},
            format="json",
        )
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(
            errors,
            [
                {
                    "code": "invalid",
                    "message": "Players per team must be 6–9 when generating teams",
                    "field": None,
                }
            ],
        )
        self.assertEqual(Team.objects.count(), before)

    def test_free_agent_cross_field_error(self) -> None:
        response = self.api.post(
            "/api/teams/generate/",
            {"num_teams": "0", "players_per_team": "6", "mean": 50, "std_dev": 15},
            format="json",
        )
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(
            errors[0]["message"],
            "Players per team must be 12–100 when generating a free-agent pool",
        )
        self.assertIsNone(errors[0]["field"])

    def test_omitted_num_teams_is_400_required_no_defaults_merged(self) -> None:
        response = self.api.post(
            "/api/teams/generate/",
            {"players_per_team": "6", "mean": 50, "std_dev": 15},
            format="json",
        )
        self.assert_error_envelope(response, 400)
        self.assertIn(("required", "num_teams"), _codes_fields(response))

    def test_mean_out_of_range_is_400(self) -> None:
        response = self.api.post(
            "/api/teams/generate/",
            {"num_teams": "2", "players_per_team": "6", "mean": 101, "std_dev": 15},
            format="json",
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("max_value", "mean")])

    def test_get_generate_is_405(self) -> None:
        self.assert_405(self.api.get("/api/teams/generate/"))


# ---------------------------------------------------------------------------
# POST /api/teams/import/ (§5.7 / §5.9)
# ---------------------------------------------------------------------------


class TestTeamImport(_ApiTestCase):
    def _post(self, body: bytes):
        return self.api.post(
            "/api/teams/import/", {"csv_file": _upload(body)}, format="multipart"
        )

    def test_happy_path_201_and_stamped(self) -> None:
        response = self._post(_csv(*_six_rows("ImportRed", "R")))
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(
            set(data), {"created_teams", "appended_teams", "player_count", "row_count"}
        )
        self.assertEqual([t["name"] for t in data["created_teams"]], ["ImportRed"])
        self.assertEqual(data["appended_teams"], [])
        self.assertEqual(data["player_count"], 6)
        self.assertEqual(data["row_count"], 6)
        team = Team.objects.get(name="ImportRed")
        self.assertEqual(team.manager_id, self.user_a.pk)
        self.assertEqual(team.players.count(), 6)
        self.assertIsNotNone(team.slot_commander_id)

    def test_append_to_own_team(self) -> None:
        mine = Team.objects.create(name="MineToAppend", manager=self.user_a)
        response = self._post(_csv(_row("MineToAppend", "Newcomer", "commander")))
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["created_teams"], [])
        self.assertEqual([t["id"] for t in data["appended_teams"]], [mine.pk])
        mine.refresh_from_db()
        self.assertEqual(mine.slot_commander.name, "Newcomer")

    def test_missing_file_is_400_required(self) -> None:
        response = self.api.post("/api/teams/import/", {}, format="multipart")
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("required", "csv_file")])

    def test_non_utf8_file_is_400_invalid(self) -> None:
        response = self._post(b"team,name\n\xff\xfe\xfa,bad\n")
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("invalid", "csv_file")])
        self.assertEqual(errors[0]["message"], "CSV file must be UTF-8 encoded")

    def test_row_error_is_roster_invalid_with_row_and_column(self) -> None:
        before_teams, before_players = Team.objects.count(), Player.objects.count()
        response = self._post(_csv(_row("BadRole", "Bob", "pilot")))
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("roster_invalid", "row:1:role")])
        self.assertIn("pilot", errors[0]["message"])
        self.assertEqual(Team.objects.count(), before_teams)
        self.assertEqual(Player.objects.count(), before_players)

    def test_multiple_row_errors_keep_parser_order(self) -> None:
        response = self._post(
            _csv(
                _row("T", "A", "pilot"),
                _row("T", "B", "commander"),
                _row("T", "C", "x"),
            )
        )
        self.assert_error_envelope(response, 400)
        fields = [f for _c, f in _codes_fields(response)]
        self.assertEqual(fields, ["row:1:role", "row:3:role"])

    def test_header_error_is_row_0(self) -> None:
        header = ",".join(c for c in REQUIRED_COLUMNS if c != "role")
        response = self._post(_csv("Red,Alice,28,16,100,Site,5'7\"", header=header))
        self.assert_error_envelope(response, 400)
        entries = _codes_fields(response)
        self.assertTrue(entries)
        for code, field in entries:
            self.assertEqual(code, "roster_invalid")
            self.assertTrue(field.startswith("row:0"), field)
        self.assertIn(("roster_invalid", "row:0:role"), entries)

    def test_atomic_no_partial_writes_on_error(self) -> None:
        before_teams, before_players = Team.objects.count(), Player.objects.count()
        rows = _six_rows("GoodTeam", "G") + [_row("BadTeam", "X", "pilot")]
        response = self._post(_csv(*rows))
        self.assert_error_envelope(response, 400)
        self.assertFalse(Team.objects.filter(name="GoodTeam").exists())
        self.assertEqual(Team.objects.count(), before_teams)
        self.assertEqual(Player.objects.count(), before_players)

    def test_db_collision_error_uses_row_role_field(self) -> None:
        mine, _players = make_team_with_slots("Full")
        mine.manager = self.user_a
        mine.save()
        response = self._post(_csv(_row(mine.name, "Extra", "commander")))
        errors = self.assert_error_envelope(response, 400)
        self.assertEqual(
            errors,
            [
                {
                    "code": "roster_invalid",
                    "message": (
                        f"Team '{mine.name}' slot 'slot_commander' already filled "
                        "by player 'Full commander'"
                    ),
                    "field": "row:1:role",
                }
            ],
        )

    def test_json_body_is_415(self) -> None:
        response = self.api.post("/api/teams/import/", {"csv_file": "x"}, format="json")
        errors = self.assert_error_envelope(response, 415)
        self.assertEqual(errors[0]["code"], "unsupported_media_type")

    def test_get_import_is_405(self) -> None:
        self.assert_405(self.api.get("/api/teams/import/"))


# ---------------------------------------------------------------------------
# GET /api/teams/import/template/ (§5.8)
# ---------------------------------------------------------------------------


class TestImportTemplate(_ApiTestCase):
    def test_template_byte_identical_to_html(self) -> None:
        api_resp = self.api.get("/api/teams/import/template/")
        html_resp = self.client.get(reverse("import_roster_template"))
        self.assertEqual(api_resp.status_code, 200)
        self.assertEqual(api_resp.content, html_resp.content)
        self.assertEqual(api_resp["Content-Type"], html_resp["Content-Type"])
        self.assertEqual(
            api_resp["Content-Disposition"], html_resp["Content-Disposition"]
        )
        self.assertEqual(
            api_resp["Content-Disposition"],
            'attachment; filename="roster_template.csv"',
        )

    def test_template_is_not_enveloped(self) -> None:
        content = self.api.get("/api/teams/import/template/").content.decode()
        self.assertFalse(content.lstrip().startswith("{"))
        self.assertTrue(content.startswith(",".join(REQUIRED_COLUMNS[:2])))

    def test_template_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        errors = self.assert_error_envelope(
            APIClient().get("/api/teams/import/template/"), 401
        )
        self.assertEqual(errors[0]["code"], "not_authenticated")

    def test_post_template_is_405(self) -> None:
        self.assert_405(self.api.post("/api/teams/import/template/", {}))


# ---------------------------------------------------------------------------
# PATCH / DELETE /api/players/<pk>/ (§5.10)
# ---------------------------------------------------------------------------


class TestPlayerWrites(_ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.team = Team.objects.create(name="Player Home", manager=self.user_a)
        self.player = Player.objects.create(
            team=self.team,
            name="Patchable",
            accuracy=40,
            stamina=60,
            preferred_roles=["heavy"],
        )
        self.team.slot_heavy = self.player
        self.team.save()
        self.theirs = Team.objects.create(name="Their Home", manager=self.user_b)
        self.their_player = Player.objects.create(
            team=self.theirs, name="Untouchable", accuracy=33
        )

    def test_patch_changes_only_sent_fields(self) -> None:
        response = self.api.patch(
            f"/api/players/{self.player.pk}/", {"accuracy": 88}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["accuracy"], 88)
        self.player.refresh_from_db()
        self.assertEqual(self.player.accuracy, 88)
        self.assertEqual(self.player.stamina, 60)
        self.assertEqual(self.player.name, "Patchable")
        self.assertEqual(self.player.preferred_roles, ["heavy"])

    def test_patch_cannot_move_team(self) -> None:
        other_mine = Team.objects.create(name="Elsewhere", manager=self.user_a)
        response = self.api.patch(
            f"/api/players/{self.player.pk}/", {"team": other_mine.pk}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.player.refresh_from_db()
        self.assertEqual(self.player.team_id, self.team.pk)

    def test_patch_invalid_stat_is_400_and_unchanged(self) -> None:
        response = self.api.patch(
            f"/api/players/{self.player.pk}/", {"accuracy": 150}, format="json"
        )
        self.assert_error_envelope(response, 400)
        self.assertEqual(_codes_fields(response), [("max_value", "accuracy")])
        self.player.refresh_from_db()
        self.assertEqual(self.player.accuracy, 40)

    def test_patch_foreign_player_is_404_and_unchanged(self) -> None:
        self.assert_404(
            self.api.patch(
                f"/api/players/{self.their_player.pk}/", {"accuracy": 99}, format="json"
            )
        )
        self.their_player.refresh_from_db()
        self.assertEqual(self.their_player.accuracy, 33)

    def test_delete_is_204_empty_and_clears_slot(self) -> None:
        response = self.api.delete(f"/api/players/{self.player.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertFalse(Player.objects.filter(pk=self.player.pk).exists())
        self.team.refresh_from_db()
        self.assertIsNone(self.team.slot_heavy_id)

    def test_delete_foreign_player_is_404_and_kept(self) -> None:
        self.assert_404(self.api.delete(f"/api/players/{self.their_player.pk}/"))
        self.assertTrue(Player.objects.filter(pk=self.their_player.pk).exists())

    def test_delete_missing_player_is_404(self) -> None:
        self.assert_404(self.api.delete("/api/players/999999/"))

    def test_foreign_player_read_is_404(self) -> None:
        self.assert_404(self.api.get(f"/api/players/{self.their_player.pk}/"))

    def test_foreign_team_read_is_404(self) -> None:
        self.assert_404(self.api.get(f"/api/teams/{self.theirs.pk}/"))


# ---------------------------------------------------------------------------
# 405s that must hold (§5.0)
# ---------------------------------------------------------------------------


class TestMethodsNotAllowed(_ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.team, players = make_team_with_slots("NotAllowed")
        self.player = players["commander"]
        blue, _ = make_team_with_slots("NotAllowedBlue")
        self.match = Match.objects.create(team_red=self.team, team_blue=blue)
        self.round = GameRound.objects.create(
            match=self.match, round_number=1, team_red=self.team, team_blue=blue
        )
        GameEvent.objects.create(
            game_round=self.round, timestamp=1, event_type="tag", actor=self.player
        )

    def test_team_detail_put_and_delete(self) -> None:
        self.assert_405(
            self.api.put(f"/api/teams/{self.team.pk}/", {"name": "X"}, format="json")
        )
        self.assert_405(self.api.delete(f"/api/teams/{self.team.pk}/"))
        self.assertTrue(Team.objects.filter(pk=self.team.pk).exists())

    def test_player_list_post_and_put(self) -> None:
        self.assert_405(self.api.post("/api/players/", {"name": "X"}, format="json"))
        self.assert_405(self.api.put("/api/players/", {"name": "X"}, format="json"))

    def test_player_detail_put(self) -> None:
        self.assert_405(
            self.api.put(
                f"/api/players/{self.player.pk}/", {"name": "X"}, format="json"
            )
        )

    def test_match_writes(self) -> None:
        # ANG-01b: POST /api/matches/ is now a create (400 on an empty body);
        # every other write on a Match stays 405.
        self.assertEqual(
            self.api.post("/api/matches/", {}, format="json").status_code, 400
        )
        self.assert_405(
            self.api.patch(f"/api/matches/{self.match.pk}/", {}, format="json")
        )
        self.assert_405(
            self.api.put(f"/api/matches/{self.match.pk}/", {}, format="json")
        )
        self.assert_405(self.api.delete(f"/api/matches/{self.match.pk}/"))

    def test_round_and_event_writes(self) -> None:
        # ANG-01b: POST /api/rounds/ is now a create (400 on an empty body).
        self.assertEqual(
            self.api.post("/api/rounds/", {}, format="json").status_code, 400
        )
        self.assert_405(
            self.api.put(f"/api/rounds/{self.round.pk}/", {}, format="json")
        )
        self.assert_405(
            self.api.patch(f"/api/rounds/{self.round.pk}/", {}, format="json")
        )
        self.assert_405(self.api.delete(f"/api/rounds/{self.round.pk}/"))
        self.assert_405(
            self.api.post(f"/api/rounds/{self.round.pk}/events/", {}, format="json")
        )

    def test_map_writes(self) -> None:
        # ANG-01c: POST /api/maps/ is now a multipart upload, so a JSON body is
        # 415; the full map write surface lives in core/test_maps_api_writes.py.
        self.assertEqual(
            self.api.post("/api/maps/", {}, format="json").status_code, 415
        )
