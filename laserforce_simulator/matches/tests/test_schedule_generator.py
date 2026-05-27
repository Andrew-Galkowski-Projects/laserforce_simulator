"""LG-01 — Pure-unit tests for ``matches/schedule_generator.py``.

No DB, no Django imports in the assertion path. The seam contract is
locked at ``.claude/worktrees/lg-01-seam-contract.md`` (§2a, §6a). Uses
``SimpleTestCase`` for the no-DB guarantee; mirrors the HX-03
``test_h2h_stats.py`` precedent — pure-unit hand-crafted inputs, plus the
``TestNoDjangoImportsLeaked`` subprocess fresh-import check (HX-01 / HX-02
/ HX-03 / HX-04 / RES-04 / RV-03 / LG-00 / LG-00b precedent).
"""

from __future__ import annotations

from django.test import SimpleTestCase

from matches.schedule_generator import (
    SCHEDULE_FORMATS,
    ScheduleFixture,
    generate_schedule,
)

# ---------------------------------------------------------------------------
# §6a — Happy path
# ---------------------------------------------------------------------------


class TestGenerateScheduleHappyPath(SimpleTestCase):
    """Output sizes + pair coverage for even N."""

    # NOTE: contract §6a internal inconsistency flagged:
    # The §2a "pinned consequences" line says "N=4 -> 6 fixtures (3 matchdays
    # x 2 fixtures, round 1: matchdays 1-3; round 2: matchdays 4-6 -- 6
    # fixtures total)". That is internally inconsistent -- 3 matchdays x 2
    # fixtures = 6 fixtures for round 1, plus another 6 for round 2 = 12
    # fixtures total. The algorithm spec (single round-robin doubled to two
    # rounds) yields N*(N-1) for even N -> 12 for N=4. The §2a "for even N:
    # total = N * (N-1)" formula confirms 12. We follow the algorithm.
    def test_n4_returns_6_fixtures(self) -> None:
        # Despite the test name (locked by §6a), the correct assertion per
        # the algorithm is 12. The name is preserved verbatim from the
        # contract; the assertion follows the algorithm. Orchestrator to
        # resolve the contract typo at triage.
        fixtures = generate_schedule([1, 2, 3, 4])
        self.assertEqual(len(fixtures), 12)

    def test_n8_returns_56_fixtures(self) -> None:
        fixtures = generate_schedule([1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(len(fixtures), 56)

    def test_every_pair_appears_exactly_once_in_round_1(self) -> None:
        team_ids = [1, 2, 3, 4]
        fixtures = generate_schedule(team_ids)
        round_1 = [f for f in fixtures if f.round_number == 1]
        pairs = [frozenset({f.team_a_id, f.team_b_id}) for f in round_1]
        # 6 unordered pairs for N=4.
        expected = {
            frozenset({1, 2}),
            frozenset({1, 3}),
            frozenset({1, 4}),
            frozenset({2, 3}),
            frozenset({2, 4}),
            frozenset({3, 4}),
        }
        self.assertEqual(set(pairs), expected)
        # Each pair appears exactly once.
        self.assertEqual(len(pairs), len(expected))

    def test_every_pair_appears_exactly_once_in_round_2(self) -> None:
        team_ids = [1, 2, 3, 4]
        fixtures = generate_schedule(team_ids)
        round_2 = [f for f in fixtures if f.round_number == 2]
        pairs = [frozenset({f.team_a_id, f.team_b_id}) for f in round_2]
        expected = {
            frozenset({1, 2}),
            frozenset({1, 3}),
            frozenset({1, 4}),
            frozenset({2, 3}),
            frozenset({2, 4}),
            frozenset({3, 4}),
        }
        self.assertEqual(set(pairs), expected)
        self.assertEqual(len(pairs), len(expected))


# ---------------------------------------------------------------------------
# §6a — Order
# ---------------------------------------------------------------------------


class TestGenerateScheduleOrder(SimpleTestCase):
    """Matchday ranges, output sort, and team_a < team_b normalisation."""

    def test_round_1_matchdays_are_1_through_n_minus_1(self) -> None:
        team_ids = [1, 2, 3, 4]
        fixtures = generate_schedule(team_ids)
        r1_matchdays = {f.matchday for f in fixtures if f.round_number == 1}
        self.assertEqual(r1_matchdays, set(range(1, 4)))  # {1, 2, 3}

    def test_round_2_matchdays_are_n_through_2n_minus_2(self) -> None:
        team_ids = [1, 2, 3, 4]
        n = len(team_ids)
        fixtures = generate_schedule(team_ids)
        r2_matchdays = {f.matchday for f in fixtures if f.round_number == 2}
        # range(N, 2*(N-1) + 1) == range(4, 7) == {4, 5, 6}
        self.assertEqual(r2_matchdays, set(range(n, 2 * (n - 1) + 1)))

    def test_output_sorted_by_matchday_then_team_a_id(self) -> None:
        fixtures = generate_schedule([1, 2, 3, 4])
        keys = [(f.matchday, f.team_a_id) for f in fixtures]
        self.assertEqual(keys, sorted(keys))

    def test_team_a_id_less_than_team_b_id_per_fixture(self) -> None:
        fixtures = generate_schedule([1, 2, 3, 4, 5, 6])
        for f in fixtures:
            self.assertLess(
                f.team_a_id,
                f.team_b_id,
                msg=f"fixture {f} violates team_a_id < team_b_id",
            )


# ---------------------------------------------------------------------------
# §6a — Odd N
# ---------------------------------------------------------------------------


class TestGenerateScheduleOddN(SimpleTestCase):
    """Odd N drops bye fixtures (sentinel -1) and leaves no team double-booked."""

    def test_n5_drops_bye_fixtures_from_output(self) -> None:
        fixtures = generate_schedule([1, 2, 3, 4, 5])
        for f in fixtures:
            self.assertNotIn(-1, (f.team_a_id, f.team_b_id))

    def test_n5_no_team_appears_twice_per_matchday(self) -> None:
        fixtures = generate_schedule([1, 2, 3, 4, 5])
        by_matchday: dict[int, list[int]] = {}
        for f in fixtures:
            by_matchday.setdefault(f.matchday, []).extend((f.team_a_id, f.team_b_id))
        for md, team_list in by_matchday.items():
            self.assertEqual(
                len(team_list),
                len(set(team_list)),
                msg=f"matchday {md} has duplicate team ids: {team_list}",
            )

    def test_n5_total_played_fixtures_is_20(self) -> None:
        # Contract §2a odd-N math: 5 matchdays per round * 2 played per day
        # * 2 rounds = 20.
        fixtures = generate_schedule([1, 2, 3, 4, 5])
        self.assertEqual(len(fixtures), 20)

    def test_bye_sentinel_minus_one_never_appears_in_output(self) -> None:
        fixtures = generate_schedule([1, 2, 3, 4, 5])
        flat = [tid for f in fixtures for tid in (f.team_a_id, f.team_b_id)]
        self.assertNotIn(-1, flat)


# ---------------------------------------------------------------------------
# §6a — Determinism
# ---------------------------------------------------------------------------


class TestGenerateScheduleDeterminism(SimpleTestCase):
    """Input order does not influence output; repeated calls identical."""

    def test_input_order_does_not_affect_output(self) -> None:
        a = generate_schedule([5, 1, 3, 7])
        b = generate_schedule([1, 3, 5, 7])
        self.assertEqual(a, b)

    def test_repeated_calls_return_identical_lists(self) -> None:
        a = generate_schedule([1, 2, 3, 4])
        b = generate_schedule([1, 2, 3, 4])
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# §6a — Errors
# ---------------------------------------------------------------------------


class TestGenerateScheduleErrors(SimpleTestCase):
    """Unknown format / too-few teams raise ValueError."""

    def test_unknown_schedule_format_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            generate_schedule([1, 2], "double_round_robin")

    def test_empty_team_list_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            generate_schedule([])

    def test_single_team_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            generate_schedule([1])


# ---------------------------------------------------------------------------
# §6a — SCHEDULE_FORMATS constant
# ---------------------------------------------------------------------------


class TestScheduleFormatsConstant(SimpleTestCase):
    """The public format-list constant is the view-side validation surface."""

    def test_schedule_formats_contains_single_round_robin(self) -> None:
        self.assertIn("single_round_robin", SCHEDULE_FORMATS)


# ---------------------------------------------------------------------------
# §6a — Defensive: no Django imports leaked into the pure module
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Mirrors the HX-01 / HX-02 / HX-03 / HX-04 / RES-04 / RV-03 /
    LG-00 / LG-00b precedent.

    Importing ``matches.schedule_generator`` in a fresh subprocess must not
    pull in ``django.*`` -- the pure module's import allowlist is
    ``dataclasses`` + ``typing`` (+ optionally ``collections``).
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.schedule_generator  # noqa: F401

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
