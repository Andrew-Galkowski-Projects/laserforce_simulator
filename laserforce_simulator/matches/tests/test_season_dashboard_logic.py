"""LG-01c — Pure-unit tests for ``matches/season_dashboard.py``.

No DB, no Django imports in the assertion path. The seam contract is
locked at ``.claude/worktrees/lg-01c-seam-contract.md`` (§5, §8a). Mirrors
the HX-03 ``test_h2h_stats.py`` / LG-01 ``test_schedule_generator.py``
precedent — hand-crafted dict literals through ``_player_round_dict``
keyword helper. ``ScheduleFixture`` is duck-typed via a local minimal
frozen dataclass stub so this module loads with the *frozen* import
allowlist (no transit through ``matches.schedule_generator``).

Locked test class names mirror the seam contract verbatim — do not
rename without re-syncing the contract.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from dataclasses import dataclass

from matches.season_dashboard import (
    LeaderRow,
    compute_leaders,
    find_next_fixture,
    round_progress,
)

# ---------------------------------------------------------------------------
# Local ScheduleFixture stub — duck-types the production dataclass without
# importing ``matches.schedule_generator`` (which would defeat the import
# guard for the pure module).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LocalScheduleFixture:
    matchday: int
    round_number: int
    team_a_id: int
    team_b_id: int


# ---------------------------------------------------------------------------
# Pure-unit fixture helpers
# ---------------------------------------------------------------------------


def _player_round_dict(
    *,
    player_id: int = 1,
    player_name: str = "Player",
    role: str = "commander",
    team_id: int = 100,
    team_name: str = "Team",
    tags_made: int = 0,
    times_tagged: int = 0,
    points_scored: int = 0,
) -> dict:
    """Build one ``player_rounds`` entry with every key populated."""
    return {
        "player_id": player_id,
        "player_name": player_name,
        "role": role,
        "team_id": team_id,
        "team_name": team_name,
        "tags_made": tags_made,
        "times_tagged": times_tagged,
        "points_scored": points_scored,
    }


# ---------------------------------------------------------------------------
# TestComputeLeadersEmpty
# ---------------------------------------------------------------------------


class TestComputeLeadersEmpty(unittest.TestCase):
    """Empty ``player_rounds`` returns ``[]`` for every stat."""

    def test_empty_player_rounds_returns_empty_list(self) -> None:
        for stat in ("points_per_game", "tags_per_game", "tag_ratio"):
            self.assertEqual(compute_leaders([], stat), [])


# ---------------------------------------------------------------------------
# TestComputeLeadersSinglePlayer
# ---------------------------------------------------------------------------


class TestComputeLeadersSinglePlayer(unittest.TestCase):
    """Single-row aggregation per stat."""

    def test_single_row_points_per_game(self) -> None:
        rows = [_player_round_dict(player_id=1, player_name="Alice", points_scored=100)]
        result = compute_leaders(rows, "points_per_game")
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertEqual(row.player_id, 1)
        self.assertEqual(row.player_name, "Alice")
        self.assertEqual(row.value, 100.0)
        self.assertEqual(row.games_played, 1)
        self.assertEqual(row.rank, 1)

    def test_single_row_tags_per_game(self) -> None:
        rows = [_player_round_dict(player_id=1, tags_made=10)]
        result = compute_leaders(rows, "tags_per_game")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].value, 10.0)
        self.assertEqual(result[0].games_played, 1)
        self.assertEqual(result[0].rank, 1)

    def test_single_row_tag_ratio_uses_max_one_denominator(self) -> None:
        # times_tagged == 0 → clamp denominator to 1.
        rows = [_player_round_dict(player_id=1, tags_made=5, times_tagged=0)]
        result = compute_leaders(rows, "tag_ratio")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].value, 5.0)


# ---------------------------------------------------------------------------
# TestComputeLeadersTiebreak
# ---------------------------------------------------------------------------


class TestComputeLeadersTiebreak(unittest.TestCase):
    """Tied value resolves by ``games_played`` desc then ``player_id`` asc."""

    def test_tied_value_resolved_by_games_played_desc(self) -> None:
        rows = [
            # Player 1: 1 game, 50 points (value=50)
            _player_round_dict(player_id=1, points_scored=50),
            # Player 2: 2 games, 50 each → mean 50, but games_played=2 wins
            _player_round_dict(player_id=2, points_scored=50),
            _player_round_dict(player_id=2, points_scored=50),
        ]
        result = compute_leaders(rows, "points_per_game")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].player_id, 2)
        self.assertEqual(result[0].rank, 1)
        self.assertEqual(result[1].player_id, 1)
        self.assertEqual(result[1].rank, 2)

    def test_tied_value_and_games_played_resolved_by_player_id_asc(
        self,
    ) -> None:
        rows = [
            _player_round_dict(player_id=5, points_scored=50),
            _player_round_dict(player_id=2, points_scored=50),
            _player_round_dict(player_id=8, points_scored=50),
        ]
        result = compute_leaders(rows, "points_per_game")
        self.assertEqual([r.player_id for r in result], [2, 5, 8])
        self.assertEqual([r.rank for r in result], [1, 2, 3])


# ---------------------------------------------------------------------------
# TestComputeLeadersDeterministic
# ---------------------------------------------------------------------------


class TestComputeLeadersDeterministic(unittest.TestCase):
    """Repeated calls on identical input return identical rank order."""

    def test_repeated_calls_return_identical_rank_order(self) -> None:
        rows = [
            _player_round_dict(player_id=3, points_scored=30),
            _player_round_dict(player_id=1, points_scored=10),
            _player_round_dict(player_id=2, points_scored=20),
        ]
        first = compute_leaders(rows, "points_per_game")
        second = compute_leaders(rows, "points_per_game")
        self.assertEqual(first, second)


# ---------------------------------------------------------------------------
# TestComputeLeadersRoleMix
# ---------------------------------------------------------------------------


class TestComputeLeadersRoleMix(unittest.TestCase):
    """Role doesn't affect ranking; LeaderRow carries the role through."""

    def test_role_mix_does_not_affect_value_ranking(self) -> None:
        rows = [
            _player_round_dict(player_id=1, role="commander", points_scored=10),
            _player_round_dict(player_id=2, role="heavy", points_scored=50),
            _player_round_dict(player_id=3, role="scout", points_scored=30),
            _player_round_dict(player_id=4, role="medic", points_scored=20),
            _player_round_dict(player_id=5, role="ammo", points_scored=40),
        ]
        result = compute_leaders(rows, "points_per_game", limit=5)
        # Sorted by value desc.
        self.assertEqual([r.player_id for r in result], [2, 5, 3, 4, 1])
        # Roles ride along.
        self.assertEqual(result[0].role, "heavy")
        self.assertEqual(result[1].role, "ammo")
        self.assertEqual(result[2].role, "scout")


# ---------------------------------------------------------------------------
# TestComputeLeadersStatVocabulary
# ---------------------------------------------------------------------------


class TestComputeLeadersStatVocabulary(unittest.TestCase):
    """Locked stat strings + the unknown-stat ``ValueError``."""

    def test_points_per_game_uses_mean(self) -> None:
        rows = [
            _player_round_dict(player_id=1, points_scored=100),
            _player_round_dict(player_id=1, points_scored=200),
        ]
        result = compute_leaders(rows, "points_per_game")
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].value, 150.0)
        self.assertEqual(result[0].games_played, 2)

    def test_tags_per_game_uses_mean(self) -> None:
        rows = [
            _player_round_dict(player_id=1, tags_made=10),
            _player_round_dict(player_id=1, tags_made=4),
        ]
        result = compute_leaders(rows, "tags_per_game")
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].value, 7.0)
        self.assertEqual(result[0].games_played, 2)

    def test_tag_ratio_uses_sum_over_sum_clamped(self) -> None:
        # Multi-row aggregation: must be sum/sum, NOT mean of per-row ratios.
        # Player rows: (tags=10, tagged=2), (tags=2, tagged=8).
        # sum(tags)=12, sum(tagged)=10 → value = 12/10 = 1.2
        # Mean of per-row ratios would be (5 + 0.25)/2 = 2.625 — must NOT
        # be that.
        rows = [
            _player_round_dict(player_id=1, tags_made=10, times_tagged=2),
            _player_round_dict(player_id=1, tags_made=2, times_tagged=8),
        ]
        result = compute_leaders(rows, "tag_ratio")
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0].value, 1.2)

    def test_unknown_stat_raises_value_error(self) -> None:
        rows = [_player_round_dict(player_id=1, points_scored=10)]
        with self.assertRaises(ValueError):
            compute_leaders(rows, "kills_per_game")


# ---------------------------------------------------------------------------
# TestComputeLeadersLimit
# ---------------------------------------------------------------------------


class TestComputeLeadersLimit(unittest.TestCase):
    """``limit`` parameter caps the output length; default is 3."""

    def test_limit_caps_output_length(self) -> None:
        rows = [
            _player_round_dict(player_id=i, points_scored=i * 10) for i in range(1, 11)
        ]
        result = compute_leaders(rows, "points_per_game", limit=3)
        self.assertEqual(len(result), 3)

    def test_limit_higher_than_input_returns_all(self) -> None:
        rows = [
            _player_round_dict(player_id=1, points_scored=10),
            _player_round_dict(player_id=2, points_scored=20),
        ]
        result = compute_leaders(rows, "points_per_game", limit=3)
        self.assertEqual(len(result), 2)

    def test_default_limit_is_three(self) -> None:
        rows = [
            _player_round_dict(player_id=i, points_scored=i * 10) for i in range(1, 11)
        ]
        # No explicit limit kwarg → default 3.
        result = compute_leaders(rows, "points_per_game")
        self.assertEqual(len(result), 3)


# ---------------------------------------------------------------------------
# TestComputeLeadersDefensiveLastWins
# ---------------------------------------------------------------------------


class TestComputeLeadersDefensiveLastWins(unittest.TestCase):
    """When per-player rows disagree on role/team, the last row wins."""

    def test_inconsistent_role_takes_last_row_in_id_order(self) -> None:
        rows = [
            _player_round_dict(
                player_id=1,
                role="scout",
                team_id=100,
                team_name="Old",
                points_scored=10,
            ),
            _player_round_dict(
                player_id=1,
                role="medic",
                team_id=200,
                team_name="New",
                points_scored=20,
            ),
        ]
        result = compute_leaders(rows, "points_per_game")
        self.assertEqual(len(result), 1)
        # Last row's values win (input-order-defined "last").
        self.assertEqual(result[0].role, "medic")
        self.assertEqual(result[0].team_id, 200)
        self.assertEqual(result[0].team_name, "New")


# ---------------------------------------------------------------------------
# TestFindNextFixture
# ---------------------------------------------------------------------------


class TestFindNextFixture(unittest.TestCase):
    """First unplayed fixture in iteration order; ``None`` on all played."""

    def test_empty_fixtures_returns_none(self) -> None:
        self.assertIsNone(find_next_fixture([], set()))

    def test_all_played_returns_none(self) -> None:
        f1 = _LocalScheduleFixture(matchday=1, round_number=1, team_a_id=1, team_b_id=2)
        f2 = _LocalScheduleFixture(matchday=2, round_number=1, team_a_id=1, team_b_id=2)
        played = {
            (frozenset({1, 2}), 1),
            (frozenset({1, 2}), 1),  # dedup'd by set semantics — both f1/f2 same key
        }
        # Both fixtures have same key — both "played" once we have that key.
        result = find_next_fixture([f1, f2], played)
        self.assertIsNone(result)

    def test_first_unplayed_in_iteration_order(self) -> None:
        f1 = _LocalScheduleFixture(matchday=1, round_number=1, team_a_id=1, team_b_id=2)
        f2 = _LocalScheduleFixture(matchday=1, round_number=1, team_a_id=3, team_b_id=4)
        f3 = _LocalScheduleFixture(matchday=2, round_number=1, team_a_id=1, team_b_id=3)
        # f1 played → next unplayed is f2 (iteration order is the list order).
        played = {(frozenset({1, 2}), 1)}
        result = find_next_fixture([f1, f2, f3], played)
        self.assertEqual(result, f2)

    def test_side_agnostic_frozenset_match(self) -> None:
        f1 = _LocalScheduleFixture(matchday=1, round_number=1, team_a_id=1, team_b_id=2)
        f2 = _LocalScheduleFixture(matchday=2, round_number=1, team_a_id=1, team_b_id=2)
        # f1 played as the reversed pair — should still match via frozenset.
        played = {(frozenset({2, 1}), 1)}
        result = find_next_fixture([f1, f2], played)
        # f1 is "played" because the frozenset matches; f2 has the same
        # key so it is also "played". Result is None — both fixtures share
        # one played_key (they only differ by matchday — both with same
        # teams + round_number). This validates the side-agnostic match.
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# TestRoundProgress
# ---------------------------------------------------------------------------


class TestRoundProgress(unittest.TestCase):
    """``(completed, total)`` Round counts."""

    def test_empty_fixtures_returns_zero_zero(self) -> None:
        self.assertEqual(round_progress([], set()), (0, 0))

    def test_no_played_returns_zero_total(self) -> None:
        fixtures = [
            _LocalScheduleFixture(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _LocalScheduleFixture(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _LocalScheduleFixture(matchday=3, round_number=1, team_a_id=2, team_b_id=3),
        ]
        self.assertEqual(round_progress(fixtures, set()), (0, 3))

    def test_all_played_returns_total_total(self) -> None:
        fixtures = [
            _LocalScheduleFixture(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _LocalScheduleFixture(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _LocalScheduleFixture(matchday=3, round_number=1, team_a_id=2, team_b_id=3),
        ]
        played = {
            (frozenset({1, 2}), 1),
            (frozenset({1, 3}), 1),
            (frozenset({2, 3}), 1),
        }
        self.assertEqual(round_progress(fixtures, played), (3, 3))

    def test_partial_played_counts_correctly(self) -> None:
        fixtures = [
            _LocalScheduleFixture(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            _LocalScheduleFixture(matchday=2, round_number=1, team_a_id=1, team_b_id=3),
            _LocalScheduleFixture(matchday=3, round_number=1, team_a_id=2, team_b_id=3),
        ]
        played = {(frozenset({1, 2}), 1)}
        self.assertEqual(round_progress(fixtures, played), (1, 3))

    def test_extra_played_keys_not_counted(self) -> None:
        # Defensive — played_keys contains a key not in fixtures.
        # ``completed`` is derived from fixtures matched against
        # played_keys (NOT len(played_keys)) so extras don't double-count.
        fixtures = [
            _LocalScheduleFixture(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
        ]
        played = {
            (frozenset({1, 2}), 1),
            (frozenset({99, 100}), 1),  # data drift — not in fixtures
        }
        completed, total = round_progress(fixtures, played)
        self.assertEqual(completed, 1)
        self.assertEqual(total, 1)


# ---------------------------------------------------------------------------
# TestNoDjangoImportsLeaked
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """``matches.season_dashboard`` must not transitively import Django.

    Mirrors HX-03 / RES-04 / RV-03 / LG-01 precedent: spawn a fresh
    subprocess, ``import matches.season_dashboard``, then walk
    ``sys.modules`` and assert no entry matches the ``django`` prefix.
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


# Reference for unused import-name warning silencer (LeaderRow is used via
# return values; explicit import retained so any rename surfaces here).
_ = LeaderRow
