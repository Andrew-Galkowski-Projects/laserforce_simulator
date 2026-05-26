"""LG-00b — Pure-unit tests for ``teams/roster_importer.py``.

No DB; the only allowed Django-adjacent import is
``teams.player_generator._STAT_FIELDS`` for the
``test_stat_columns_equals_player_generator_stat_fields`` equality pin (§10.1
of the seam contract at ``.claude/worktrees/lg-00b-seam-contract.md``).

Mirrors the HX-01 / HX-02 / RES-04 / RV-03 / LG-00 pure-module precedent —
Django imports are forbidden in the module under test and a defensive
subprocess check (``TestNoDjangoImportsLeaked``) pins that
``teams.roster_importer`` does not transitively pull Django into
``sys.modules``.
"""

from __future__ import annotations

import dataclasses
import unittest

from teams.roster_importer import (
    ALL_COLUMNS,
    MAX_DATA_ROWS,
    OPTIONAL_COLUMNS,
    PROFILE_BOUNDS,
    REQUIRED_COLUMNS,
    ROLE_NAMES,
    SLOT_LIMITS,
    STAT_COLUMNS,
    STAT_DEFAULT,
    ParsedRoster,
    ParsedRow,
    RosterImportError,
    RowError,
    parse_roster_csv,
)

# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------


_REQUIRED_HEADER = ",".join(REQUIRED_COLUMNS)
_ALL_HEADER = ",".join(ALL_COLUMNS)


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
    """Render a row matching the 8-column required-only header."""
    return f"{team},{name},{role},{age},{started_playing_age},{total_games},{home_site},{height}"


def _csv_required_only(*data_rows: str) -> str:
    """CSV with just the 8 required columns + N data rows."""
    return "\n".join([_REQUIRED_HEADER, *data_rows]) + "\n"


# ---------------------------------------------------------------------------
# §10.1 — TestHeaderValidation
# ---------------------------------------------------------------------------


class TestHeaderValidation(unittest.TestCase):
    """Header-level validation raises RosterImportError immediately."""

    def test_missing_required_column_raises_with_field_named(self) -> None:
        # Omit the `role` column from the header.
        header_cols = [c for c in REQUIRED_COLUMNS if c != "role"]
        csv = (
            ",".join(header_cols)
            + "\n"
            + "Red,Alice,28,16,100,Ultrazone Chicago,5'7\"\n"
        )
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        self.assertEqual(len(ctx.exception.errors), 1)
        err = ctx.exception.errors[0]
        self.assertEqual(err.row_num, 0)
        self.assertEqual(err.field, "role")
        self.assertIn("Missing required column", err.message)

    def test_unknown_column_raises_with_column_named(self) -> None:
        csv = _REQUIRED_HEADER + ",unknown_col\n" + _valid_required_row() + ",x\n"
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        # At least one error names the unknown column.
        unknown_errors = [e for e in ctx.exception.errors if e.field == "unknown_col"]
        self.assertEqual(len(unknown_errors), 1, ctx.exception.errors)
        self.assertIn("Unknown column", unknown_errors[0].message)

    def test_duplicate_column_raises(self) -> None:
        # Inject a second `name` column.
        cols = list(REQUIRED_COLUMNS) + ["name"]
        csv = ",".join(cols) + "\n" + _valid_required_row() + ",AliceDup\n"
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        dup_errors = [e for e in ctx.exception.errors if e.field == "name"]
        self.assertGreaterEqual(len(dup_errors), 1, ctx.exception.errors)
        self.assertIn("Duplicate column", dup_errors[0].message)

    def test_bom_tolerated(self) -> None:
        csv = "﻿" + _csv_required_only(_valid_required_row())
        parsed = parse_roster_csv(csv)
        self.assertEqual(len(parsed.rows), 1)
        self.assertEqual(parsed.rows[0].team, "Red")
        self.assertEqual(parsed.rows[0].name, "Alice")

    def test_more_than_1000_rows_raises(self) -> None:
        # Build 1001 well-formed data rows on distinct (team, name) pairs that
        # do not collide on slot limits — use 1001 distinct teams so no in-file
        # slot-overflow can fire and obscure the row-cap error.
        rows = [
            _valid_required_row(team=f"Team{i}", name=f"P{i}")
            for i in range(MAX_DATA_ROWS + 1)
        ]
        csv = _csv_required_only(*rows)
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        cap_errors = [
            e
            for e in ctx.exception.errors
            if e.row_num == 0 and e.field is None and "1000" in e.message
        ]
        self.assertEqual(len(cap_errors), 1, ctx.exception.errors)


# ---------------------------------------------------------------------------
# §10.1 — TestCoercion
# ---------------------------------------------------------------------------


class TestCoercion(unittest.TestCase):
    """Per-cell coercion rules from §2d of the seam contract."""

    def test_empty_stat_cell_defaults_to_50(self) -> None:
        # Full ALL_COLUMNS header + a row where every stat cell is blank.
        prefix_values = [
            "Red",
            "Alice",
            "commander",
            "28",
            "16",
            "100",
            "Ultrazone Chicago",
            "5'7\"",
            "",  # preferred_roles
        ]
        stat_values = [""] * len(STAT_COLUMNS)
        row = ",".join(prefix_values + stat_values)
        csv = _ALL_HEADER + "\n" + row + "\n"
        parsed = parse_roster_csv(csv)
        self.assertEqual(len(parsed.rows), 1)
        for stat in STAT_COLUMNS:
            self.assertEqual(
                parsed.rows[0].stats[stat],
                STAT_DEFAULT,
                f"stat {stat!r} did not default to {STAT_DEFAULT}",
            )

    def test_omitted_stat_column_defaults_to_50(self) -> None:
        parsed = parse_roster_csv(_csv_required_only(_valid_required_row()))
        self.assertEqual(len(parsed.rows), 1)
        for stat in STAT_COLUMNS:
            self.assertEqual(parsed.rows[0].stats[stat], STAT_DEFAULT, stat)

    def test_stat_out_of_range_raises_row_error(self) -> None:
        cols = list(REQUIRED_COLUMNS) + ["player_awareness"]
        csv = ",".join(cols) + "\n" + _valid_required_row() + ",200\n"
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [
            e
            for e in ctx.exception.errors
            if e.field == "player_awareness" and e.row_num == 1
        ]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_stat_non_int_raises_row_error(self) -> None:
        cols = list(REQUIRED_COLUMNS) + ["accuracy"]
        csv = ",".join(cols) + "\n" + _valid_required_row() + ",hello\n"
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [e for e in ctx.exception.errors if e.field == "accuracy"]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_role_out_of_range_raises_row_error(self) -> None:
        csv = _csv_required_only(_valid_required_row(role="captain"))
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [e for e in ctx.exception.errors if e.field == "role"]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_role_case_normalised(self) -> None:
        csv = _csv_required_only(_valid_required_row(role="COMMANDER"))
        parsed = parse_roster_csv(csv)
        self.assertEqual(parsed.rows[0].role, "commander")

    def test_age_out_of_bounds_raises_row_error(self) -> None:
        csv = _csv_required_only(_valid_required_row(age=4))
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [e for e in ctx.exception.errors if e.field == "age"]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_started_playing_age_out_of_bounds(self) -> None:
        csv = _csv_required_only(_valid_required_row(started_playing_age=2))
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [
            e for e in ctx.exception.errors if e.field == "started_playing_age"
        ]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_total_games_out_of_bounds(self) -> None:
        csv = _csv_required_only(_valid_required_row(total_games=-1))
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [e for e in ctx.exception.errors if e.field == "total_games"]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_empty_team_cell_raises_row_error(self) -> None:
        csv = _csv_required_only(_valid_required_row(team=""))
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [e for e in ctx.exception.errors if e.field == "team"]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_empty_name_cell_raises_row_error(self) -> None:
        csv = _csv_required_only(_valid_required_row(name=""))
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [e for e in ctx.exception.errors if e.field == "name"]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_height_and_home_site_empty_allowed(self) -> None:
        csv = _csv_required_only(_valid_required_row(home_site="", height=""))
        parsed = parse_roster_csv(csv)
        self.assertEqual(parsed.rows[0].profile["home_site"], "")
        self.assertEqual(parsed.rows[0].profile["height"], "")


# ---------------------------------------------------------------------------
# §10.1 — TestPreferredRoles
# ---------------------------------------------------------------------------


class TestPreferredRoles(unittest.TestCase):
    """``preferred_roles`` cell parsing rules."""

    def _csv_with_preferred(self, preferred: str) -> str:
        cols = list(REQUIRED_COLUMNS) + ["preferred_roles"]
        # Quote the preferred-roles cell in case it contains a comma.
        cell = f'"{preferred}"' if "," in preferred else preferred
        return ",".join(cols) + "\n" + _valid_required_row() + "," + cell + "\n"

    def test_empty_cell_yields_empty_list(self) -> None:
        parsed = parse_roster_csv(self._csv_with_preferred(""))
        self.assertEqual(parsed.rows[0].preferred_roles, [])

    def test_column_absent_yields_empty_list(self) -> None:
        parsed = parse_roster_csv(_csv_required_only(_valid_required_row()))
        self.assertEqual(parsed.rows[0].preferred_roles, [])

    def test_comma_split_parsed(self) -> None:
        parsed = parse_roster_csv(self._csv_with_preferred("commander,heavy"))
        self.assertEqual(parsed.rows[0].preferred_roles, ["commander", "heavy"])

    def test_whitespace_trimmed_and_lowercased(self) -> None:
        parsed = parse_roster_csv(self._csv_with_preferred(" Commander , Heavy "))
        self.assertEqual(parsed.rows[0].preferred_roles, ["commander", "heavy"])

    def test_invalid_role_in_cell_raises(self) -> None:
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(self._csv_with_preferred("captain"))
        offenders = [e for e in ctx.exception.errors if e.field == "preferred_roles"]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_duplicate_role_within_cell_raises(self) -> None:
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(self._csv_with_preferred("scout,scout"))
        offenders = [e for e in ctx.exception.errors if e.field == "preferred_roles"]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_empty_fragments_silently_dropped(self) -> None:
        """A stray comma (``"scout,,medic"``) yields ``["scout", "medic"]``.

        Pins the deliberate leniency of `_parse_preferred_roles`: an empty
        fragment between commas is treated as accidental punctuation rather
        than a row error. If this behaviour is ever flipped to "reject", the
        test must change in lock-step.
        """
        parsed = parse_roster_csv(self._csv_with_preferred("scout,,medic"))
        self.assertEqual(parsed.rows[0].preferred_roles, ["scout", "medic"])


# ---------------------------------------------------------------------------
# §10.1 — TestInFileCollisions
# ---------------------------------------------------------------------------


class TestInFileCollisions(unittest.TestCase):
    """Post-coercion in-file duplicate / slot-overflow pass."""

    def test_duplicate_team_name_pair_raises(self) -> None:
        csv = _csv_required_only(
            _valid_required_row(team="Red", name="Alice", role="commander"),
            _valid_required_row(team="Red", name="Alice", role="heavy"),
        )
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        # The second occurrence is row_num=2.
        offenders = [
            e
            for e in ctx.exception.errors
            if e.row_num == 2 and e.field == "name" and "Duplicate" in e.message
        ]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_two_non_scout_rows_for_same_team_role_raises(self) -> None:
        csv = _csv_required_only(
            _valid_required_row(team="Red", name="Alice", role="commander"),
            _valid_required_row(team="Red", name="Bob", role="commander"),
        )
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [
            e
            for e in ctx.exception.errors
            if e.row_num == 2
            and e.field == "role"
            and "commander" in e.message
            and "Too many" in e.message
        ]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_three_scout_rows_for_same_team_raises(self) -> None:
        csv = _csv_required_only(
            _valid_required_row(team="Red", name="Alice", role="scout"),
            _valid_required_row(team="Red", name="Bob", role="scout"),
            _valid_required_row(team="Red", name="Carol", role="scout"),
        )
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        offenders = [
            e
            for e in ctx.exception.errors
            if e.row_num == 3
            and e.field == "role"
            and "scout" in e.message
            and "Too many" in e.message
        ]
        self.assertEqual(len(offenders), 1, ctx.exception.errors)

    def test_two_scout_rows_for_same_team_allowed(self) -> None:
        csv = _csv_required_only(
            _valid_required_row(team="Red", name="Alice", role="scout"),
            _valid_required_row(team="Red", name="Bob", role="scout"),
        )
        parsed = parse_roster_csv(csv)
        self.assertEqual(len(parsed.rows), 2)
        self.assertEqual([r.role for r in parsed.rows], ["scout", "scout"])


# ---------------------------------------------------------------------------
# §10.1 — TestMultiErrorAccumulation
# ---------------------------------------------------------------------------


class TestMultiErrorAccumulation(unittest.TestCase):
    """Errors accumulate into a single raise; header errors short-circuit."""

    def test_multiple_row_errors_accumulate_in_single_raise(self) -> None:
        # Five distinct teams to avoid in-file slot-overflow noise.
        # Errors injected on data rows 2, 3, 5 (1-based data index).
        csv = _csv_required_only(
            _valid_required_row(team="A", name="P1"),  # row 1 — valid
            _valid_required_row(team="B", name="P2", age=4),  # row 2 — bad age
            _valid_required_row(
                team="C", name="P3", role="captain"
            ),  # row 3 — bad role
            _valid_required_row(team="D", name="P4"),  # row 4 — valid
            _valid_required_row(team="E", name="P5", total_games=-1),  # row 5 — bad
        )
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        errs = ctx.exception.errors
        self.assertGreaterEqual(len(errs), 3, errs)
        row_nums = {e.row_num for e in errs}
        self.assertTrue(
            {2, 3, 5}.issubset(row_nums),
            f"expected {{2,3,5}} ⊆ {row_nums}",
        )

    def test_header_error_short_circuits_per_row_parsing(self) -> None:
        # Missing `role` column AND a stat OOB on the only data row.
        cols = [c for c in REQUIRED_COLUMNS if c != "role"] + ["player_awareness"]
        csv = (
            ",".join(cols) + "\n" + "Red,Alice,28,16,100,Ultrazone Chicago,5'7\",200\n"
        )
        with self.assertRaises(RosterImportError) as ctx:
            parse_roster_csv(csv)
        # No per-row errors (row_num >= 1) should be present — parser
        # short-circuits on the header-level fault.
        per_row_errors = [e for e in ctx.exception.errors if e.row_num >= 1]
        self.assertEqual(per_row_errors, [], per_row_errors)


# ---------------------------------------------------------------------------
# §10.1 — TestRowErrorShape
# ---------------------------------------------------------------------------


class TestRowErrorShape(unittest.TestCase):
    """``RowError`` dataclass shape pins."""

    def test_row_error_is_frozen_dataclass(self) -> None:
        self.assertTrue(dataclasses.is_dataclass(RowError))
        err = RowError(row_num=1, field="age", message="bad")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            err.row_num = 99  # type: ignore[misc]

    def test_row_error_is_hashable(self) -> None:
        s = {RowError(row_num=1, field="age", message="x")}
        self.assertEqual(len(s), 1)


# ---------------------------------------------------------------------------
# §10.1 — TestParsedRosterShape
# ---------------------------------------------------------------------------


class TestParsedRosterShape(unittest.TestCase):
    """``ParsedRoster`` shape: by-team grouping order, rows order, and the
    ``STAT_COLUMNS == player_generator._STAT_FIELDS`` equality pin."""

    def test_by_team_grouping_in_csv_encounter_order(self) -> None:
        csv = _csv_required_only(
            _valid_required_row(team="A", name="P1", role="commander"),
            _valid_required_row(team="B", name="P2", role="commander"),
            _valid_required_row(team="A", name="P3", role="heavy"),
        )
        parsed = parse_roster_csv(csv)
        self.assertEqual(list(parsed.by_team.keys()), ["A", "B"])
        self.assertEqual(len(parsed.by_team["A"]), 2)
        self.assertEqual(len(parsed.by_team["B"]), 1)

    def test_rows_list_matches_csv_order(self) -> None:
        csv = _csv_required_only(
            _valid_required_row(team="A", name="P1", role="commander"),
            _valid_required_row(team="B", name="P2", role="commander"),
            _valid_required_row(team="C", name="P3", role="commander"),
        )
        parsed = parse_roster_csv(csv)
        self.assertEqual([r.row_num for r in parsed.rows], [1, 2, 3])

    def test_stat_columns_equals_player_generator_stat_fields(self) -> None:
        # This is the ONE allowed teams.player_generator import in this file
        # (§10.1 of the seam contract).
        from teams.player_generator import _STAT_FIELDS

        self.assertEqual(STAT_COLUMNS, _STAT_FIELDS)


# ---------------------------------------------------------------------------
# §10.1 — TestNoDjangoImportsLeaked
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """The pure module must import cleanly without pulling in Django.

    Mirrors ``teams/tests/test_player_generator.py::TestNoDjangoImportsLeaked``.
    """

    def test_no_django_imports_leaked(self) -> None:
        import subprocess
        import sys

        # In-process surface check — catches a trivial module-level Django
        # import without spinning up a subprocess.
        import teams.roster_importer as m

        self.assertNotIn("django", dir(m))
        self.assertNotIn("models", dir(m))
        self.assertNotIn("forms", dir(m))

        # Subprocess check — a pristine Python interpreter imports
        # ``teams.roster_importer`` and reports any ``django.*`` modules that
        # ended up in ``sys.modules``.
        script = (
            "import teams.roster_importer; "
            "import sys; "
            "print(','.join(m for m in sys.modules if m.startswith('django')))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        leaked = proc.stdout.strip()
        self.assertEqual(
            leaked,
            "",
            f"django modules leaked into teams.roster_importer: {leaked!r}\n"
            f"stderr: {proc.stderr!r}",
        )


# ---------------------------------------------------------------------------
# Sanity asserts for the pinned module constants (defensive — these are
# imported above so import-time mismatches surface immediately, but the
# explicit equality checks make a contract-drift bug easier to read).
# ---------------------------------------------------------------------------


class TestModuleConstantsPinned(unittest.TestCase):
    def test_required_columns_pinned(self) -> None:
        self.assertEqual(
            REQUIRED_COLUMNS,
            (
                "team",
                "name",
                "role",
                "age",
                "started_playing_age",
                "total_games",
                "home_site",
                "height",
            ),
        )

    def test_optional_columns_starts_with_preferred_roles(self) -> None:
        self.assertEqual(OPTIONAL_COLUMNS[0], "preferred_roles")
        self.assertEqual(OPTIONAL_COLUMNS[1:], STAT_COLUMNS)

    def test_all_columns_has_28_entries(self) -> None:
        self.assertEqual(len(ALL_COLUMNS), 28)
        self.assertEqual(ALL_COLUMNS, (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS))

    def test_role_names_pinned(self) -> None:
        self.assertEqual(
            ROLE_NAMES,
            ("commander", "heavy", "scout", "medic", "ammo"),
        )

    def test_slot_limits_pinned(self) -> None:
        self.assertEqual(
            SLOT_LIMITS,
            {"commander": 1, "heavy": 1, "scout": 2, "medic": 1, "ammo": 1},
        )

    def test_profile_bounds_pinned(self) -> None:
        self.assertEqual(
            PROFILE_BOUNDS,
            {
                "age": (5, 100),
                "started_playing_age": (3, 100),
                "total_games": (0, 100_000),
            },
        )

    def test_stat_default_and_max_rows_pinned(self) -> None:
        self.assertEqual(STAT_DEFAULT, 50)
        self.assertEqual(MAX_DATA_ROWS, 1000)


if __name__ == "__main__":
    unittest.main()
