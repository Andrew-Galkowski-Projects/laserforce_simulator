"""LG-00b — View / form / DB tests for the roster-import flow.

Covers ``GET /teams/import/`` (form render), ``POST /teams/import/`` (parse,
validate, atomic DB writes with auto-create / append-to-existing Team
semantics, error rendering), and ``GET /teams/import/template.csv``
(byte-for-byte canonical template download).

Seam contract: ``.claude/worktrees/lg-00b-seam-contract.md`` §3, §4, §5, §7,
§10.2.
"""

from __future__ import annotations

import csv
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from teams.models import Player, Team
from teams.roster_importer import ALL_COLUMNS, REQUIRED_COLUMNS, STAT_COLUMNS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_REQUIRED_HEADER = ",".join(REQUIRED_COLUMNS)


def _valid_required_row(
    team: str = "Red",
    name: str = "Alice",
    role: str = "commander",
    age: int = 28,
    started_playing_age: int = 16,
    total_games: int = 100,
    home_site: str = "Ultrazone Chicago",
    height: str = "5'7\"",
) -> str:
    return f"{team},{name},{role},{age},{started_playing_age},{total_games},{home_site},{height}"


def _required_csv(*rows: str) -> bytes:
    body = "\n".join([_REQUIRED_HEADER, *rows]) + "\n"
    return body.encode("utf-8")


def _upload(body: bytes, filename: str = "roster.csv") -> SimpleUploadedFile:
    return SimpleUploadedFile(filename, body, content_type="text/csv")


def _post(client, body: bytes, filename: str = "roster.csv"):
    return client.post(
        reverse("import_roster"),
        {"csv_file": _upload(body, filename)},
    )


def _six_role_rows_for(team_name: str, name_prefix: str) -> list[str]:
    """Return 6 well-formed CSV rows covering every slot (commander, heavy,
    scout x 2, medic, ammo) for ``team_name``."""
    return [
        _valid_required_row(
            team=team_name, name=f"{name_prefix}-Cdr", role="commander"
        ),
        _valid_required_row(team=team_name, name=f"{name_prefix}-Hvy", role="heavy"),
        _valid_required_row(team=team_name, name=f"{name_prefix}-Sc1", role="scout"),
        _valid_required_row(team=team_name, name=f"{name_prefix}-Sc2", role="scout"),
        _valid_required_row(team=team_name, name=f"{name_prefix}-Med", role="medic"),
        _valid_required_row(team=team_name, name=f"{name_prefix}-Amm", role="ammo"),
    ]


# ---------------------------------------------------------------------------
# §10.2 — TestImportRosterGet
# ---------------------------------------------------------------------------


class TestImportRosterGet(TestCase):
    def test_get_200(self) -> None:
        response = self.client.get(reverse("import_roster"))
        self.assertEqual(response.status_code, 200)

    def test_form_field_present(self) -> None:
        response = self.client.get(reverse("import_roster"))
        body = response.content.decode()
        for dom_id in (
            "roster-import-form",
            "roster-import-file",
            "roster-import-submit",
            "roster-import-template-link",
        ):
            self.assertIn(dom_id, body, f"missing DOM id {dom_id!r}")


# ---------------------------------------------------------------------------
# §10.2 — TestImportRosterPostHappyPath
# ---------------------------------------------------------------------------


class TestImportRosterPostHappyPath(TestCase):
    def test_post_2_teams_12_players_creates_2_teams_12_players(self) -> None:
        rows = _six_role_rows_for("Red", "R") + _six_role_rows_for("Blue", "B")
        before_teams = Team.objects.regular().count()
        before_players = Player.objects.count()
        response = _post(self.client, _required_csv(*rows))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Team.objects.regular().count() - before_teams, 2)
        self.assertEqual(Player.objects.count() - before_players, 12)
        body = response.content.decode()
        self.assertIn("roster-import-confirm-summary", body)
        self.assertIn("roster-import-confirm-teams-list", body)

    def test_post_assigns_slot_fks_correctly(self) -> None:
        rows = _six_role_rows_for("Red", "R")
        _post(self.client, _required_csv(*rows))
        team = Team.objects.get(name="Red")
        # Each non-Scout slot should point at a Player whose CSV row had the
        # matching role.
        self.assertIsNotNone(team.slot_commander)
        self.assertIsNotNone(team.slot_heavy)
        self.assertIsNotNone(team.slot_medic)
        self.assertIsNotNone(team.slot_ammo)
        self.assertIsNotNone(team.slot_scout_1)
        self.assertIsNotNone(team.slot_scout_2)
        # The Player names map back to the CSV-encoded role: e.g. the
        # commander slot points at the player whose name ends in "-Cdr".
        self.assertTrue(team.slot_commander.name.endswith("-Cdr"))
        self.assertTrue(team.slot_heavy.name.endswith("-Hvy"))
        self.assertTrue(team.slot_medic.name.endswith("-Med"))
        self.assertTrue(team.slot_ammo.name.endswith("-Amm"))
        scout_names = {team.slot_scout_1.name, team.slot_scout_2.name}
        self.assertEqual(scout_names, {"R-Sc1", "R-Sc2"})

    def test_post_appends_to_existing_team(self) -> None:
        # Pre-create Red with slot_commander already filled.
        red = Team.objects.create(name="Red")
        existing_cmdr = Player.objects.create(team=red, name="ExistingCmdr")
        red.slot_commander = existing_cmdr
        red.save()

        rows = [
            _valid_required_row(team="Red", name="NewHeavy", role="heavy"),
            _valid_required_row(team="Red", name="NewScout", role="scout"),
        ]
        response = _post(self.client, _required_csv(*rows))
        self.assertEqual(response.status_code, 200)

        # No new Team named Red.
        self.assertEqual(Team.objects.filter(name="Red").count(), 1)

        red.refresh_from_db()
        self.assertEqual(red.slot_commander_id, existing_cmdr.id)
        self.assertIsNotNone(red.slot_heavy)
        self.assertEqual(red.slot_heavy.name, "NewHeavy")
        self.assertIsNotNone(red.slot_scout_1)
        self.assertEqual(red.slot_scout_1.name, "NewScout")

        # appended_teams context surfaces the Red team.
        appended = response.context["appended_teams"]
        self.assertIn(red, appended)

    def test_post_auto_creates_missing_team(self) -> None:
        self.assertFalse(Team.objects.filter(name="Brand New").exists())
        rows = [_valid_required_row(team="Brand New", name="X", role="commander")]
        response = _post(self.client, _required_csv(*rows))
        self.assertEqual(response.status_code, 200)
        team = Team.objects.get(name="Brand New")
        created = response.context["created_teams"]
        appended = response.context["appended_teams"]
        self.assertIn(team, created)
        self.assertNotIn(team, appended)


# ---------------------------------------------------------------------------
# §10.2 — TestImportRosterPostDbSlotCollision
# ---------------------------------------------------------------------------


class TestImportRosterPostDbSlotCollision(TestCase):
    def test_existing_team_with_slot_commander_filled_rejects_new_commander(
        self,
    ) -> None:
        red = Team.objects.create(name="Red")
        existing = Player.objects.create(team=red, name="ExistingCmdr")
        red.slot_commander = existing
        red.save()

        players_before = Player.objects.count()
        teams_before = Team.objects.count()

        rows = [_valid_required_row(team="Red", name="NewCmdr", role="commander")]
        response = _post(self.client, _required_csv(*rows))
        self.assertEqual(response.status_code, 200)

        body = response.content.decode()
        self.assertIn("roster-import-error-1-role", body)
        # No DB writes.
        self.assertEqual(Player.objects.count(), players_before)
        self.assertEqual(Team.objects.count(), teams_before)

    def test_existing_team_with_both_scout_slots_filled_rejects_new_scout(self) -> None:
        red = Team.objects.create(name="Red")
        s1 = Player.objects.create(team=red, name="S1")
        s2 = Player.objects.create(team=red, name="S2")
        red.slot_scout_1 = s1
        red.slot_scout_2 = s2
        red.save()

        players_before = Player.objects.count()
        teams_before = Team.objects.count()

        rows = [_valid_required_row(team="Red", name="NewScout", role="scout")]
        response = _post(self.client, _required_csv(*rows))
        self.assertEqual(response.status_code, 200)

        body = response.content.decode()
        self.assertIn("roster-import-error-1-role", body)
        self.assertEqual(Player.objects.count(), players_before)
        self.assertEqual(Team.objects.count(), teams_before)

    def test_partial_scout_slot_accepts_one_rejects_overflow(self) -> None:
        """Pre-existing Team with ONE Scout slot filled + TWO Scouts in CSV.

        The in-call shadow check in `_check_db_slot_collisions` should let
        the first CSV Scout claim the remaining free slot (slot_scout_2)
        and reject only the second — exactly one row error on row 2, zero
        DB writes (the all-or-nothing transaction rolls back even the
        notionally-acceptable first row).
        """
        red = Team.objects.create(name="Red")
        s1 = Player.objects.create(team=red, name="ExistingScout")
        red.slot_scout_1 = s1
        red.save()

        players_before = Player.objects.count()
        teams_before = Team.objects.count()

        rows = [
            _valid_required_row(team="Red", name="NewScoutA", role="scout"),
            _valid_required_row(team="Red", name="NewScoutB", role="scout"),
        ]
        response = _post(self.client, _required_csv(*rows))
        self.assertEqual(response.status_code, 200)

        body = response.content.decode()
        # Exactly one row-level error, pointing at row 2 (the overflow row).
        self.assertIn("roster-import-error-2-role", body)
        self.assertNotIn("roster-import-error-1-", body)
        # Atomic rollback — nothing written.
        self.assertEqual(Player.objects.count(), players_before)
        self.assertEqual(Team.objects.count(), teams_before)

    def test_existing_team_name_collision_falls_through_to_db_backstop(self) -> None:
        """A CSV row whose ``(team, name)`` matches an existing Player
        is NOT caught by `_check_db_slot_collisions` — the pre-check only
        verifies slot-FK occupancy, not name uniqueness — so the row
        reaches `_apply_roster` and triggers an `IntegrityError` from the
        `Player.unique_together = ["team", "name"]` DB constraint.

        Documented gap (issues.md LG00b-8, contract §12 punt). The test
        pins current behaviour: the `IntegrityError` propagates out of the
        view, `@transaction.atomic` rolls back, no Players persist. If
        the importer ever grows a friendly row-level name-collision check,
        flip this test to assert a 200 + `roster-import-error-1-name`.
        """
        from django.db.utils import IntegrityError

        red = Team.objects.create(name="Red")
        # Pre-existing Alice — NOT assigned to slot_commander, so the
        # slot pre-check passes.
        Player.objects.create(team=red, name="Alice")
        players_before = Player.objects.count()

        rows = [_valid_required_row(team="Red", name="Alice", role="commander")]
        with self.assertRaises(IntegrityError):
            _post(self.client, _required_csv(*rows))

        # Atomic rolled back — no extra Players created.
        self.assertEqual(Player.objects.count(), players_before)


# ---------------------------------------------------------------------------
# §10.2 — TestImportRosterPostFormErrors
# ---------------------------------------------------------------------------


class TestImportRosterPostFormErrors(TestCase):
    def test_post_file_too_large_renders_form_error(self) -> None:
        from teams.forms import RosterImportForm

        # Body slightly larger than the cap.
        body = b"x" * (RosterImportForm.MAX_UPLOAD_BYTES + 1)
        before_teams = Team.objects.count()
        before_players = Player.objects.count()
        response = _post(self.client, body)
        self.assertEqual(response.status_code, 200)
        self.assertIn("too large", response.content.decode())
        self.assertEqual(Team.objects.count(), before_teams)
        self.assertEqual(Player.objects.count(), before_players)

    def test_post_non_utf8_file_renders_form_error(self) -> None:
        # Body containing a byte sequence that is not valid UTF-8 (a lone
        # 0xff is not a legal UTF-8 start byte).
        body = b"\xff\xfe\xfd\xfc not utf-8"
        before_teams = Team.objects.count()
        before_players = Player.objects.count()
        response = _post(self.client, body)
        self.assertEqual(response.status_code, 200)
        self.assertIn("must be UTF-8", response.content.decode())
        self.assertEqual(Team.objects.count(), before_teams)
        self.assertEqual(Player.objects.count(), before_players)


# ---------------------------------------------------------------------------
# §10.2 — TestImportRosterPostParseErrors
# ---------------------------------------------------------------------------


class TestImportRosterPostParseErrors(TestCase):
    def test_post_unknown_column_renders_row_errors_list(self) -> None:
        # Build CSV with an extra unknown column.
        header = _REQUIRED_HEADER + ",unknown_col"
        row = _valid_required_row() + ",x"
        body = (header + "\n" + row + "\n").encode("utf-8")
        response = _post(self.client, body)
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn("roster-import-errors", text)
        self.assertIn("Unknown column", text)

    def test_post_invalid_row_renders_per_row_error_with_dom_id(self) -> None:
        # Add an `accuracy` column with an out-of-range value on row 1.
        header = _REQUIRED_HEADER + ",accuracy"
        row = _valid_required_row() + ",999"
        body = (header + "\n" + row + "\n").encode("utf-8")
        response = _post(self.client, body)
        self.assertEqual(response.status_code, 200)
        self.assertIn("roster-import-error-1-accuracy", response.content.decode())

    def test_post_multiple_errors_all_rendered(self) -> None:
        # Three rows on three distinct teams each with a different field bad.
        rows = [
            _valid_required_row(team="A", name="P1", age=4),  # row 1 — bad age
            _valid_required_row(
                team="B", name="P2", role="captain"
            ),  # row 2 — bad role
            _valid_required_row(team="C", name="P3", total_games=-1),  # row 3 — bad
        ]
        body = _required_csv(*rows)
        response = _post(self.client, body)
        self.assertEqual(response.status_code, 200)
        text = response.content.decode()
        self.assertIn("roster-import-error-1-", text)
        self.assertIn("roster-import-error-2-", text)
        self.assertIn("roster-import-error-3-", text)


# ---------------------------------------------------------------------------
# §10.2 — TestImportRosterPostAtomic
# ---------------------------------------------------------------------------


class TestImportRosterPostAtomic(TestCase):
    def test_db_slot_collision_rolls_back_all_writes(self) -> None:
        # Pre-create Red with slot_commander filled.
        red = Team.objects.create(name="Red")
        existing = Player.objects.create(team=red, name="ExistingCmdr")
        red.slot_commander = existing
        red.save()

        players_before = Player.objects.count()  # 1
        teams_before = Team.objects.count()  # 1

        # CSV: 5 valid rows for brand-new team "Blue" AND 1 colliding row on
        # Red. The Blue team must NOT be persisted after the rollback.
        blue_rows = [
            _valid_required_row(team="Blue", name="B-Cdr", role="commander"),
            _valid_required_row(team="Blue", name="B-Hvy", role="heavy"),
            _valid_required_row(team="Blue", name="B-Sc1", role="scout"),
            _valid_required_row(team="Blue", name="B-Sc2", role="scout"),
            _valid_required_row(team="Blue", name="B-Med", role="medic"),
        ]
        bad_red_row = _valid_required_row(team="Red", name="NewCmdr", role="commander")
        body = _required_csv(*blue_rows, bad_red_row)
        response = _post(self.client, body)
        self.assertEqual(response.status_code, 200)

        # Blue must not exist; player count + team count unchanged.
        self.assertEqual(Team.objects.filter(name="Blue").count(), 0)
        self.assertEqual(Player.objects.count(), players_before)
        self.assertEqual(Team.objects.count(), teams_before)

    def test_parser_raise_writes_nothing(self) -> None:
        # role=captain → parser-level RowError, no DB writes.
        rows = [_valid_required_row(team="Red", name="X", role="captain")]
        body = _required_csv(*rows)
        before_teams = Team.objects.count()
        before_players = Player.objects.count()
        response = _post(self.client, body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Team.objects.count(), before_teams)
        self.assertEqual(Player.objects.count(), before_players)


# ---------------------------------------------------------------------------
# §10.2 — TestImportRosterTemplate
# ---------------------------------------------------------------------------


def _expected_template_body() -> bytes:
    """Reconstruct the expected template CSV body via csv.writer (mirroring
    the view) so the assertion is resilient to platform-specific newline
    handling while still byte-exact."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(list(ALL_COLUMNS))
    # Row 1 — Red Phoenix commander, plain (no comma in any cell).
    writer.writerow(
        [
            "Red Phoenix",
            "Alice",
            "commander",
            "28",
            "16",
            "120",
            "Ultrazone Chicago",
            "5'7\"",
            "commander",
            "75",
            "70",
            "65",
            "80",
            "72",
            "68",
            "74",
            "66",
            "71",
            "78",
            "80",
            "82",
            "55",
            "60",
            "50",
            "60",
            "65",
            "72",
            "55",
        ]
    )
    # Row 2 — Red Phoenix scout with comma-split preferred_roles.
    writer.writerow(
        [
            "Red Phoenix",
            "Bob",
            "scout",
            "24",
            "18",
            "85",
            "Ultrazone Chicago",
            "5'10\"",
            "scout,medic",
            "60",
            "62",
            "58",
            "70",
            "65",
            "80",
            "85",
            "78",
            "72",
            "65",
            "68",
            "55",
            "62",
            "60",
            "58",
            "55",
            "82",
            "75",
            "60",
        ]
    )
    return buf.getvalue().encode("utf-8")


class TestImportRosterTemplate(TestCase):
    def test_template_csv_200_with_content_disposition(self) -> None:
        response = self.client.get(reverse("import_roster_template"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="roster_template.csv"',
        )
        self.assertTrue(response["Content-Type"].startswith("text/csv"))

    def test_template_csv_body_byte_for_byte(self) -> None:
        response = self.client.get(reverse("import_roster_template"))
        self.assertEqual(response.content, _expected_template_body())

    def test_template_header_lists_all_28_columns_in_ALL_COLUMNS_order(self) -> None:
        response = self.client.get(reverse("import_roster_template"))
        body = response.content.decode("utf-8")
        first_line = body.splitlines()[0]
        self.assertEqual(first_line.split(","), list(ALL_COLUMNS))
        # Sanity: 8 required + 1 preferred + 19 stat = 28.
        self.assertEqual(len(list(ALL_COLUMNS)), 28)
        self.assertEqual(len(STAT_COLUMNS), 19)


# ---------------------------------------------------------------------------
# §10.2 — TestEntryPointLink
# ---------------------------------------------------------------------------


class TestEntryPointLink(TestCase):
    def test_team_list_contains_roster_import_link(self) -> None:
        response = self.client.get(reverse("team_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("roster-import-link", body)
        self.assertIn("Import Roster", body)
