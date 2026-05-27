"""LG-01d — Pure-unit tests for the play-orchestrator helpers in
``matches/season_dashboard.py``.

The seam contract is locked at ``.claude/worktrees/lg-01d-seam-contract.md``
(§4, §11a). Mirrors the LG-01c ``test_season_dashboard.py`` pattern —
``SimpleTestCase`` (no DB), frozen import allowlist defended by a
subprocess fresh-import + ``sys.modules`` walk, and a locally-stubbed
``@dataclass(frozen=True)`` ``ScheduleFixture`` shape so the test module
does NOT import ``matches.schedule_generator`` (which would defeat the
import guard for the pure module).

Locked test class names mirror the seam contract verbatim — do not
rename without re-syncing the contract.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

from django.test import SimpleTestCase

from matches.season_dashboard import (
    find_next_matchday,
    select_play_fixtures,
)

# ---------------------------------------------------------------------------
# Local ScheduleFixture stub — duck-types the production dataclass without
# importing ``matches.schedule_generator`` (which would defeat the import
# guard for the pure module).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _F:
    matchday: int
    round_number: int
    team_a_id: int
    team_b_id: int


# ---------------------------------------------------------------------------
# TestFindNextMatchday
# ---------------------------------------------------------------------------


class TestFindNextMatchday(SimpleTestCase):
    """``find_next_matchday`` returns the first unplayed matchday or
    ``None`` on empty / all-played input. Side-agnostic frozenset match.
    """

    def test_empty_fixtures_returns_none(self) -> None:
        self.assertIsNone(find_next_matchday([], set()))

    def test_no_played_returns_first_matchday(self) -> None:
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3),
        ]
        self.assertEqual(find_next_matchday(fixtures, set()), 1)

    def test_partial_played_returns_first_unplayed_matchday(self) -> None:
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3),
        ]
        played = {(frozenset({1, 2}), 1)}
        self.assertEqual(find_next_matchday(fixtures, played), 2)

    def test_all_played_returns_none(self) -> None:
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
        ]
        played = {
            (frozenset({1, 2}), 1),
            (frozenset({1, 3}), 1),
        }
        self.assertIsNone(find_next_matchday(fixtures, played))

    def test_side_agnostic_frozenset_match(self) -> None:
        """A played key ``(frozenset({1, 2}), 1)`` matches a fixture with
        ``team_a_id=1, team_b_id=2, round_number=1`` regardless of which
        physical side each team played.
        """
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
        ]
        # Played key carried with the pair-set reversed — should still
        # match fixture 1 via frozenset semantics.
        played = {(frozenset({2, 1}), 1)}
        self.assertEqual(find_next_matchday(fixtures, played), 2)

    def test_round_2_matchday_unplayed_while_round_1_played(self) -> None:
        """The next unplayed matchday may be a round-2 matchday — the
        round-1 mirror's matchday key differs from the round-2 mirror's
        even for the same pair.
        """
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=2, team_a_id=1, team_b_id=2),
        ]
        played = {(frozenset({1, 2}), 1)}
        # Round 1 played; the round-2 mirror is matchday 2.
        self.assertEqual(find_next_matchday(fixtures, played), 2)


# ---------------------------------------------------------------------------
# TestSelectPlayFixtures
# ---------------------------------------------------------------------------


class TestSelectPlayFixtures(SimpleTestCase):
    """``select_play_fixtures`` returns the unplayed fixtures spanning the
    next ``max_matchdays`` distinct unplayed matchdays starting at
    ``find_next_matchday``. ``max_matchdays=None`` returns ALL unplayed.
    """

    def test_empty_fixtures_returns_empty_list(self) -> None:
        self.assertEqual(select_play_fixtures([], set(), max_matchdays=1), [])
        self.assertEqual(select_play_fixtures([], set(), max_matchdays=None), [])

    def test_max_matchdays_1_returns_one_matchday_unplayed_only(self) -> None:
        """Play One Week happy path — exactly the next unplayed matchday's
        fixtures.
        """
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=1, round_number=1, team_a_id=3, team_b_id=4),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _F(matchday=2, round_number=1, team_a_id=2, team_b_id=4),
        ]
        result = select_play_fixtures(fixtures, set(), max_matchdays=1)
        self.assertEqual(len(result), 2)
        # All returned fixtures are matchday 1.
        for f in result:
            self.assertEqual(f.matchday, 1)

    def test_max_matchdays_8_returns_up_to_8_distinct_matchdays(self) -> None:
        """Play Two Months happy path — up to 8 distinct unplayed matchdays
        on a > 8-matchday Season.
        """
        fixtures = []
        for md in range(1, 13):  # 12 matchdays
            fixtures.append(_F(matchday=md, round_number=1, team_a_id=1, team_b_id=2))
        result = select_play_fixtures(fixtures, set(), max_matchdays=8)
        distinct_matchdays = {f.matchday for f in result}
        self.assertEqual(len(distinct_matchdays), 8)
        # The 8 matchdays are the FIRST 8 (1..8).
        self.assertEqual(sorted(distinct_matchdays), list(range(1, 9)))

    def test_max_matchdays_8_caps_at_actual_remaining_when_fewer(self) -> None:
        """Season with only 3 unplayed matchdays + ``max_matchdays=8``
        returns those 3.
        """
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3),
        ]
        result = select_play_fixtures(fixtures, set(), max_matchdays=8)
        # All 3 fixtures returned; only 3 distinct matchdays exist.
        self.assertEqual(len(result), 3)
        distinct = {f.matchday for f in result}
        self.assertEqual(distinct, {1, 2, 3})

    def test_max_matchdays_none_returns_all_unplayed(self) -> None:
        """Play Until End happy path — every unplayed fixture regardless of
        matchday.
        """
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3),
        ]
        played = {(frozenset({1, 2}), 1)}
        result = select_play_fixtures(fixtures, played, max_matchdays=None)
        self.assertEqual(len(result), 2)
        # The played fixture is not in result.
        for f in result:
            self.assertNotEqual(
                (frozenset({f.team_a_id, f.team_b_id}), f.round_number),
                (frozenset({1, 2}), 1),
            )

    def test_boundary_at_last_matchday_returns_that_matchdays_unplayed(
        self,
    ) -> None:
        """If only matchday K remains and ``max_matchdays >= 1``, returns
        exactly matchday K's unplayed fixtures.
        """
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _F(matchday=3, round_number=1, team_a_id=2, team_b_id=3),
            _F(matchday=3, round_number=1, team_a_id=1, team_b_id=4),
        ]
        played = {
            (frozenset({1, 2}), 1),
            (frozenset({1, 3}), 1),
        }
        result = select_play_fixtures(fixtures, played, max_matchdays=1)
        self.assertEqual(len(result), 2)
        for f in result:
            self.assertEqual(f.matchday, 3)

    def test_all_played_returns_empty_list(self) -> None:
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
        ]
        played = {
            (frozenset({1, 2}), 1),
            (frozenset({1, 3}), 1),
        }
        self.assertEqual(select_play_fixtures(fixtures, played, max_matchdays=1), [])
        self.assertEqual(select_play_fixtures(fixtures, played, max_matchdays=None), [])

    def test_preserves_generate_schedule_iteration_order(self) -> None:
        """Output list's iteration order matches the input ``fixtures``
        order (canonical iteration order is preserved).
        """
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=1, round_number=1, team_a_id=3, team_b_id=4),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _F(matchday=2, round_number=1, team_a_id=2, team_b_id=4),
        ]
        result = select_play_fixtures(fixtures, set(), max_matchdays=None)
        self.assertEqual(result, fixtures)

    def test_side_agnostic_key_matching(self) -> None:
        """A played key whose ``frozenset`` matches an unplayed fixture is
        treated as played, regardless of which physical side each team
        played.
        """
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
        ]
        # Played key carried as the reversed pair-set.
        played = {(frozenset({2, 1}), 1)}
        result = select_play_fixtures(fixtures, played, max_matchdays=None)
        # The matchday-1 fixture is treated as played; only the matchday-2
        # fixture comes back.
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].matchday, 2)

    def test_max_matchdays_1_with_zero_unplayed_matchdays_returns_empty(
        self,
    ) -> None:
        """Defensive — all-played input + ``max_matchdays=1`` ⇒ ``[]``."""
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
        ]
        played = {(frozenset({1, 2}), 1)}
        self.assertEqual(select_play_fixtures(fixtures, played, max_matchdays=1), [])

    def test_partial_matchday_played_still_returns_remaining_fixtures(
        self,
    ) -> None:
        """If 2 of 4 fixtures on matchday 3 are played and 2 are unplayed,
        ``max_matchdays=1`` starting from matchday 3 returns just those 2
        remaining fixtures.
        """
        fixtures = [
            _F(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _F(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _F(matchday=3, round_number=1, team_a_id=1, team_b_id=4),
            _F(matchday=3, round_number=1, team_a_id=2, team_b_id=5),
            _F(matchday=3, round_number=1, team_a_id=3, team_b_id=6),
            _F(matchday=3, round_number=1, team_a_id=7, team_b_id=8),
        ]
        played = {
            (frozenset({1, 2}), 1),
            (frozenset({1, 3}), 1),
            # Two of the four matchday-3 fixtures are played.
            (frozenset({1, 4}), 1),
            (frozenset({2, 5}), 1),
        }
        result = select_play_fixtures(fixtures, played, max_matchdays=1)
        self.assertEqual(len(result), 2)
        # Both remaining are matchday 3.
        for f in result:
            self.assertEqual(f.matchday, 3)
        pair_sets = {frozenset({f.team_a_id, f.team_b_id}) for f in result}
        self.assertEqual(pair_sets, {frozenset({3, 6}), frozenset({7, 8})})


# ---------------------------------------------------------------------------
# TestNoDjangoImportsLeaked — defensive frozen-allowlist subprocess check.
# (Already pinned by ``test_season_dashboard.py``; included here so the
# LG-01d additions are guarded against introducing a stray Django import
# into the pure module.)
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """``matches.season_dashboard`` must not transitively import Django.

    Mirrors the LG-01c precedent: spawn a fresh subprocess, ``import
    matches.season_dashboard``, then walk ``sys.modules`` and assert no
    entry matches the ``django`` prefix.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import pathlib
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
            import matches.season_dashboard  # noqa: F401
            leaked = sorted(
                m for m in sys.modules
                if m == "django" or m.startswith("django.")
            )
            if leaked:
                print("LEAK:" + ",".join(leaked))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Django import leaked into matches.season_dashboard.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )
