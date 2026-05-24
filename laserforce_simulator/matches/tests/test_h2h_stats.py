"""HX-03 — Pure-unit tests for ``matches/h2h_stats.py``.

No DB, no Django imports in the assertion path. The seam contract is
locked at ``.claude/worktrees/hx-03-seam-contract.md``. Mirrors the
HX-01 ``test_career_stats.py`` / HX-02 ``test_role_benchmarks.py``
precedent — hand-crafted dict literals through ``_match_dict`` /
``_round_dict`` / ``_player_round_dict`` keyword helpers.

The three flat dict lists crossing the seam (view → pure module) are
pinned by the contract:

* ``matches_list`` — one entry per H2H Match, shape
  ``{match_id, winner_team_id, date_played, is_simulated}``.
* ``rounds_list`` — one entry per Round in the unified basket
  (already normalised from team_a perspective by the view), shape
  ``{round_id, date_played, team_a_score, team_b_score,
  team_a_survivors, team_b_survivors, match_id, arena_map_id,
  arena_map_name, is_simulated}``.
* ``player_rounds_list`` — one entry per PlayerRoundState (already
  attributed to team_a or team_b by the view), shape
  ``{player_id, player_name, team_id, mvp, round_id}``.

Locked test class names and method names mirror the **Tests** section
of the seam contract verbatim — do not rename without re-syncing the
contract.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from matches.h2h_stats import (
    compute_avg_survivors,
    compute_match_record,
    compute_per_map_breakdown,
    compute_round_record,
    compute_score_margin,
    cumulative_wl_series,
    margin_series,
    top_impactful_per_team,
)

# ---------------------------------------------------------------------------
# Pure-unit fixture helpers
# ---------------------------------------------------------------------------


def _match_dict(
    *,
    match_id: int = 1,
    winner_team_id: int | None = None,
    date_played: object = datetime(2026, 5, 22, 12, 0, 0),
    is_simulated: bool = True,
) -> dict:
    """Build one ``matches_list`` entry with every key populated."""
    return {
        "match_id": match_id,
        "winner_team_id": winner_team_id,
        "date_played": date_played,
        "is_simulated": is_simulated,
    }


def _round_dict(
    *,
    round_id: int = 1,
    date_played: object = datetime(2026, 5, 22, 12, 0, 0),
    team_a_score: int = 0,
    team_b_score: int = 0,
    team_a_survivors: int = 0,
    team_b_survivors: int = 0,
    match_id: int | None = None,
    arena_map_id: int | None = None,
    arena_map_name: str | None = None,
    is_simulated: bool = True,
) -> dict:
    """Build one ``rounds_list`` entry with every key populated."""
    return {
        "round_id": round_id,
        "date_played": date_played,
        "team_a_score": team_a_score,
        "team_b_score": team_b_score,
        "team_a_survivors": team_a_survivors,
        "team_b_survivors": team_b_survivors,
        "match_id": match_id,
        "arena_map_id": arena_map_id,
        "arena_map_name": arena_map_name,
        "is_simulated": is_simulated,
    }


def _player_round_dict(
    *,
    player_id: int = 1,
    player_name: str = "Player",
    team_id: int = 100,
    mvp: float = 0.0,
    round_id: int = 1,
) -> dict:
    """Build one ``player_rounds_list`` entry with every key populated."""
    return {
        "player_id": player_id,
        "player_name": player_name,
        "team_id": team_id,
        "mvp": mvp,
        "round_id": round_id,
    }


# Stable team ids used across cases.
TEAM_A_ID = 100
TEAM_B_ID = 200


# ---------------------------------------------------------------------------
# §A — compute_match_record
# ---------------------------------------------------------------------------


class TestComputeMatchRecord(unittest.TestCase):
    """W/L/T over H2H Matches; defensive on unknown winner ids."""

    def test_empty_input_returns_zeros(self) -> None:
        self.assertEqual(
            compute_match_record([], TEAM_A_ID, TEAM_B_ID),
            {"wins": 0, "losses": 0, "ties": 0, "n": 0},
        )

    def test_team_a_wins_losses_ties_counted_correctly(self) -> None:
        matches = [
            _match_dict(match_id=1, winner_team_id=TEAM_A_ID),
            _match_dict(match_id=2, winner_team_id=TEAM_A_ID),
            _match_dict(match_id=3, winner_team_id=TEAM_B_ID),
            _match_dict(match_id=4, winner_team_id=None),
        ]
        result = compute_match_record(matches, TEAM_A_ID, TEAM_B_ID)
        self.assertEqual(result["wins"], 2)
        self.assertEqual(result["losses"], 1)
        self.assertEqual(result["ties"], 1)
        self.assertEqual(result["n"], 4)

    def test_null_winner_counts_as_tie(self) -> None:
        matches = [_match_dict(match_id=1, winner_team_id=None)]
        result = compute_match_record(matches, TEAM_A_ID, TEAM_B_ID)
        self.assertEqual(result["ties"], 1)
        self.assertEqual(result["wins"], 0)
        self.assertEqual(result["losses"], 0)
        self.assertEqual(result["n"], 1)

    def test_unknown_winner_id_counts_as_tie_defensive(self) -> None:
        """A winner_team_id that is neither team_a nor team_b (legacy /
        corrupt) is counted as a tie, never raised."""
        matches = [_match_dict(match_id=1, winner_team_id=99_999)]
        result = compute_match_record(matches, TEAM_A_ID, TEAM_B_ID)
        self.assertEqual(result["ties"], 1)
        self.assertEqual(result["n"], 1)


# ---------------------------------------------------------------------------
# §B — compute_round_record
# ---------------------------------------------------------------------------


class TestComputeRoundRecord(unittest.TestCase):
    """W/L/T per Round across the unified basket."""

    def test_empty_input_returns_zeros(self) -> None:
        self.assertEqual(
            compute_round_record([]),
            {"wins": 0, "losses": 0, "ties": 0, "n": 0},
        )

    def test_higher_team_a_score_is_win(self) -> None:
        rounds = [_round_dict(round_id=1, team_a_score=100, team_b_score=50)]
        result = compute_round_record(rounds)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["losses"], 0)
        self.assertEqual(result["ties"], 0)
        self.assertEqual(result["n"], 1)

    def test_lower_is_loss(self) -> None:
        rounds = [_round_dict(round_id=1, team_a_score=10, team_b_score=200)]
        result = compute_round_record(rounds)
        self.assertEqual(result["losses"], 1)
        self.assertEqual(result["wins"], 0)
        self.assertEqual(result["ties"], 0)
        self.assertEqual(result["n"], 1)

    def test_equal_is_tie(self) -> None:
        rounds = [_round_dict(round_id=1, team_a_score=50, team_b_score=50)]
        result = compute_round_record(rounds)
        self.assertEqual(result["ties"], 1)
        self.assertEqual(result["wins"], 0)
        self.assertEqual(result["losses"], 0)
        self.assertEqual(result["n"], 1)


# ---------------------------------------------------------------------------
# §C — compute_score_margin
# ---------------------------------------------------------------------------


class TestComputeScoreMargin(unittest.TestCase):
    """Mean signed margin (team_a − team_b) per Round."""

    def test_empty_input_zero_no_div_by_zero(self) -> None:
        result = compute_score_margin([])
        self.assertEqual(result, {"mean_margin": 0.0, "n": 0})

    def test_signed_mean_from_team_a_perspective(self) -> None:
        """Three rounds margins +50, +100, +0 → mean = 50.0."""
        rounds = [
            _round_dict(round_id=1, team_a_score=150, team_b_score=100),
            _round_dict(round_id=2, team_a_score=200, team_b_score=100),
            _round_dict(round_id=3, team_a_score=50, team_b_score=50),
        ]
        result = compute_score_margin(rounds)
        self.assertEqual(result["n"], 3)
        self.assertAlmostEqual(result["mean_margin"], 50.0)

    def test_negative_margin_when_team_b_dominates(self) -> None:
        """Two rounds: −100, −50 → mean −75.0 (negative from team_a view)."""
        rounds = [
            _round_dict(round_id=1, team_a_score=0, team_b_score=100),
            _round_dict(round_id=2, team_a_score=50, team_b_score=100),
        ]
        result = compute_score_margin(rounds)
        self.assertEqual(result["n"], 2)
        self.assertAlmostEqual(result["mean_margin"], -75.0)


# ---------------------------------------------------------------------------
# §D — compute_avg_survivors
# ---------------------------------------------------------------------------


class TestComputeAvgSurvivors(unittest.TestCase):
    """Per-team mean survivors per Round (two independent numbers)."""

    def test_empty_input_zeros(self) -> None:
        result = compute_avg_survivors([])
        self.assertEqual(result, {"team_a_avg": 0.0, "team_b_avg": 0.0, "n": 0})

    def test_per_team_mean_independent(self) -> None:
        """team_a survivors: 6, 4, 2 → mean 4.0; team_b: 0, 5, 1 → mean 2.0."""
        rounds = [
            _round_dict(round_id=1, team_a_survivors=6, team_b_survivors=0),
            _round_dict(round_id=2, team_a_survivors=4, team_b_survivors=5),
            _round_dict(round_id=3, team_a_survivors=2, team_b_survivors=1),
        ]
        result = compute_avg_survivors(rounds)
        self.assertEqual(result["n"], 3)
        self.assertAlmostEqual(result["team_a_avg"], 4.0)
        self.assertAlmostEqual(result["team_b_avg"], 2.0)


# ---------------------------------------------------------------------------
# §E — top_impactful_per_team
# ---------------------------------------------------------------------------


class TestTopImpactfulPerTeam(unittest.TestCase):
    """Top cumulative-MVP player per team; deterministic tiebreaker."""

    def test_empty_input_both_teams_none(self) -> None:
        result = top_impactful_per_team([], TEAM_A_ID, TEAM_B_ID)
        self.assertEqual(result, {"team_a": None, "team_b": None})

    def test_top_player_per_team_by_cumulative_mvp(self) -> None:
        """team_a: P1 sums 2.5+1.5=4.0 vs P2 sums 3.0 → P1 wins.
        team_b: P3 sums 6.0 vs P4 sums 2.0+2.0=4.0 → P3 wins.
        """
        rows = [
            _player_round_dict(
                player_id=1, player_name="Alpha", team_id=TEAM_A_ID, mvp=2.5, round_id=1
            ),
            _player_round_dict(
                player_id=1, player_name="Alpha", team_id=TEAM_A_ID, mvp=1.5, round_id=2
            ),
            _player_round_dict(
                player_id=2, player_name="Beta", team_id=TEAM_A_ID, mvp=3.0, round_id=1
            ),
            _player_round_dict(
                player_id=3, player_name="Gamma", team_id=TEAM_B_ID, mvp=6.0, round_id=1
            ),
            _player_round_dict(
                player_id=4, player_name="Delta", team_id=TEAM_B_ID, mvp=2.0, round_id=1
            ),
            _player_round_dict(
                player_id=4, player_name="Delta", team_id=TEAM_B_ID, mvp=2.0, round_id=2
            ),
        ]
        result = top_impactful_per_team(rows, TEAM_A_ID, TEAM_B_ID)
        self.assertEqual(result["team_a"]["player_id"], 1)
        self.assertEqual(result["team_a"]["name"], "Alpha")
        self.assertAlmostEqual(result["team_a"]["mvp_total"], 4.0)
        self.assertEqual(result["team_a"]["games"], 2)
        self.assertEqual(result["team_b"]["player_id"], 3)
        self.assertEqual(result["team_b"]["name"], "Gamma")
        self.assertAlmostEqual(result["team_b"]["mvp_total"], 6.0)
        self.assertEqual(result["team_b"]["games"], 1)

    def test_player_appearing_on_both_teams_attributed_per_round(self) -> None:
        """Same player_id rows split across team_a and team_b (per-Round
        attribution by the view layer); each pool sums only its own rows.

        Player 7 plays one round for team_a (mvp=10) and one for team_b
        (mvp=4). team_a sees only the 10; team_b sees only the 4 (which
        may still be the top on team_b if no one else appeared).
        """
        rows = [
            _player_round_dict(
                player_id=7,
                player_name="Switch",
                team_id=TEAM_A_ID,
                mvp=10.0,
                round_id=1,
            ),
            _player_round_dict(
                player_id=7,
                player_name="Switch",
                team_id=TEAM_B_ID,
                mvp=4.0,
                round_id=2,
            ),
        ]
        result = top_impactful_per_team(rows, TEAM_A_ID, TEAM_B_ID)
        # team_a pool: just the 10.0
        self.assertIsNotNone(result["team_a"])
        self.assertEqual(result["team_a"]["player_id"], 7)
        self.assertAlmostEqual(result["team_a"]["mvp_total"], 10.0)
        self.assertEqual(result["team_a"]["games"], 1)
        # team_b pool: just the 4.0 (same player_id, different team_id)
        self.assertIsNotNone(result["team_b"])
        self.assertEqual(result["team_b"]["player_id"], 7)
        self.assertAlmostEqual(result["team_b"]["mvp_total"], 4.0)
        self.assertEqual(result["team_b"]["games"], 1)

    def test_tiebreaker_lower_player_id_wins(self) -> None:
        """Equal mvp_total on team_a → lower player_id wins."""
        rows = [
            _player_round_dict(
                player_id=42,
                player_name="Forty-Two",
                team_id=TEAM_A_ID,
                mvp=5.0,
                round_id=1,
            ),
            _player_round_dict(
                player_id=7,
                player_name="Seven",
                team_id=TEAM_A_ID,
                mvp=5.0,
                round_id=2,
            ),
        ]
        result = top_impactful_per_team(rows, TEAM_A_ID, TEAM_B_ID)
        self.assertEqual(result["team_a"]["player_id"], 7)
        self.assertAlmostEqual(result["team_a"]["mvp_total"], 5.0)

    def test_only_team_a_has_rows_returns_team_b_none(self) -> None:
        rows = [
            _player_round_dict(
                player_id=1, player_name="Solo", team_id=TEAM_A_ID, mvp=3.0, round_id=1
            ),
        ]
        result = top_impactful_per_team(rows, TEAM_A_ID, TEAM_B_ID)
        self.assertIsNotNone(result["team_a"])
        self.assertIsNone(result["team_b"])


# ---------------------------------------------------------------------------
# §F — compute_per_map_breakdown
# ---------------------------------------------------------------------------


class TestComputePerMapBreakdown(unittest.TestCase):
    """Per-arena_map W/L/T + margin table; deterministic sort."""

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(compute_per_map_breakdown([]), [])

    def test_one_row_per_arena_map(self) -> None:
        """Two maps, each with one Round → 2 rows."""
        rounds = [
            _round_dict(
                round_id=1,
                team_a_score=100,
                team_b_score=50,
                arena_map_id=1,
                arena_map_name="Arena Alpha",
            ),
            _round_dict(
                round_id=2,
                team_a_score=30,
                team_b_score=80,
                arena_map_id=2,
                arena_map_name="Arena Beta",
            ),
        ]
        result = compute_per_map_breakdown(rounds)
        self.assertEqual(len(result), 2)
        ids = {row["arena_map_id"] for row in result}
        self.assertEqual(ids, {1, 2})
        # The two map names round-trip through the breakdown rows.
        names = {row["arena_map_name"] for row in result}
        self.assertEqual(names, {"Arena Alpha", "Arena Beta"})

    def test_arena_map_none_labelled_no_map_3_zone(self) -> None:
        """A Round with arena_map_id=None gets a single row labelled
        'No map (3-zone)'."""
        rounds = [
            _round_dict(
                round_id=1,
                team_a_score=10,
                team_b_score=10,
                arena_map_id=None,
                arena_map_name=None,
            ),
        ]
        result = compute_per_map_breakdown(rounds)
        self.assertEqual(len(result), 1)
        row = result[0]
        self.assertIsNone(row["arena_map_id"])
        self.assertEqual(row["arena_map_name"], "No map (3-zone)")
        self.assertEqual(row["games"], 1)
        self.assertEqual(row["ties"], 1)

    def test_sorted_by_games_desc(self) -> None:
        """Map with 3 games sorts above a map with 1 game."""
        rounds = [
            # Map 1: 1 game.
            _round_dict(
                round_id=10,
                team_a_score=10,
                team_b_score=20,
                arena_map_id=1,
                arena_map_name="Alpha",
            ),
            # Map 2: 3 games.
            _round_dict(
                round_id=20,
                team_a_score=10,
                team_b_score=5,
                arena_map_id=2,
                arena_map_name="Beta",
            ),
            _round_dict(
                round_id=21,
                team_a_score=20,
                team_b_score=10,
                arena_map_id=2,
                arena_map_name="Beta",
            ),
            _round_dict(
                round_id=22,
                team_a_score=5,
                team_b_score=5,
                arena_map_id=2,
                arena_map_name="Beta",
            ),
        ]
        result = compute_per_map_breakdown(rounds)
        self.assertEqual(len(result), 2)
        # Map 2 (3 games) comes first.
        self.assertEqual(result[0]["arena_map_id"], 2)
        self.assertEqual(result[0]["games"], 3)
        self.assertEqual(result[1]["arena_map_id"], 1)
        self.assertEqual(result[1]["games"], 1)


# ---------------------------------------------------------------------------
# §G — margin_series
# ---------------------------------------------------------------------------


class TestMarginSeries(unittest.TestCase):
    """Chart data — signed margin per Round chronologically."""

    def test_empty_input_empty_list(self) -> None:
        self.assertEqual(margin_series([]), [])

    def test_chronological_with_date_then_round_id_tiebreaker(self) -> None:
        """Rounds sorted by (date_played, round_id) ascending; 1-based idx."""
        shared = datetime(2026, 5, 22, 12, 0, 0)
        later = datetime(2026, 5, 23, 12, 0, 0)
        rounds = [
            # Feed in shuffled order to verify the sort.
            _round_dict(
                round_id=5, date_played=shared, team_a_score=10, team_b_score=0
            ),
            _round_dict(
                round_id=2, date_played=shared, team_a_score=20, team_b_score=5
            ),
            _round_dict(round_id=1, date_played=later, team_a_score=0, team_b_score=50),
        ]
        result = margin_series(rounds)
        self.assertEqual(len(result), 3)
        # 1-based round index in chronological order.
        self.assertEqual(result[0][0], 1)
        self.assertEqual(result[1][0], 2)
        self.assertEqual(result[2][0], 3)
        # (date_played, round_id) tiebreaker: id=2 before id=5 (same date).
        self.assertEqual(result[0][1], 15)  # round_id=2 → margin 20-5=15
        self.assertEqual(result[1][1], 10)  # round_id=5 → margin 10-0=10
        self.assertEqual(result[2][1], -50)  # later date → margin 0-50=-50

    def test_returns_list_of_lists_not_tuples(self) -> None:
        """The outer container and each entry must be ``list`` (not tuple)
        so ``json_script`` serialises cleanly."""
        rounds = [_round_dict(round_id=1, team_a_score=5, team_b_score=2)]
        result = margin_series(rounds)
        self.assertIsInstance(result, list)
        for entry in result:
            self.assertIsInstance(entry, list)
            self.assertNotIsInstance(entry, tuple)


# ---------------------------------------------------------------------------
# §H — cumulative_wl_series
# ---------------------------------------------------------------------------


class TestCumulativeWlSeries(unittest.TestCase):
    """Chart data — cumulative (team_a_wins − team_b_wins) Round-level."""

    def test_empty_input_empty_list(self) -> None:
        self.assertEqual(cumulative_wl_series([]), [])

    def test_ties_do_not_move_running_diff(self) -> None:
        """W, T, L → running diff +1, +1, 0."""
        rounds = [
            _round_dict(round_id=1, team_a_score=10, team_b_score=0),
            _round_dict(round_id=2, team_a_score=5, team_b_score=5),
            _round_dict(round_id=3, team_a_score=0, team_b_score=10),
        ]
        result = cumulative_wl_series(rounds)
        self.assertEqual(len(result), 3)
        self.assertEqual([r[0] for r in result], [1, 2, 3])
        self.assertEqual([r[1] for r in result], [1, 1, 0])

    def test_returns_list_of_lists_not_tuples(self) -> None:
        rounds = [_round_dict(round_id=1, team_a_score=5, team_b_score=2)]
        result = cumulative_wl_series(rounds)
        self.assertIsInstance(result, list)
        for entry in result:
            self.assertIsInstance(entry, list)
            self.assertNotIsInstance(entry, tuple)


# ---------------------------------------------------------------------------
# §I — Defensive: no Django imports leaked into the pure module
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """Mirrors the HX-01 / HX-02 / RES-04 / RV-03 precedent.

    Importing ``matches.h2h_stats`` in a fresh subprocess must not pull in
    ``django.*`` or ``matches.models`` — the pure module's import allowlist
    is ``typing`` + ``collections.defaultdict``.
    """

    def test_clean_import_in_subprocess(self) -> None:
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
            import matches.h2h_stats  # noqa: F401

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


if __name__ == "__main__":
    unittest.main()
