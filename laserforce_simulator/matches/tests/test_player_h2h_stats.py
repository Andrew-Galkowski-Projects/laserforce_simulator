"""HX-04 — Pure-unit tests for ``matches/player_h2h_stats.py``.

No DB, no Django imports in the assertion path. The seam contract is
locked at ``.claude/worktrees/hx-04-seam-contract.md``. Mirrors the
HX-03 ``test_h2h_stats.py`` precedent — hand-crafted dict literals
through a ``_round_dict`` keyword helper.

The single flat dict list crossing the seam (view → pure module) is
pinned by the contract — ``rounds_list``, each entry shape:

``{round_id, date_played, player_a_team_score, player_b_team_score,
   tags_a_to_b, tags_b_to_a, role_a, role_b, match_id,
   arena_map_id, arena_map_name, is_simulated}``

Locked test class names and method names mirror the **Tests** section
of the seam contract verbatim — do not rename without re-syncing the
contract.
"""

from __future__ import annotations

import unittest
from datetime import datetime

from matches.player_h2h_stats import (
    compute_per_map_breakdown,
    compute_per_role_breakdown,
    compute_round_record,
    compute_score_margin,
    compute_tag_stats,
    cumulative_wl_series,
    margin_series,
)

# ---------------------------------------------------------------------------
# Pure-unit fixture helper
# ---------------------------------------------------------------------------


def _round_dict(
    *,
    round_id: int = 1,
    date_played: object = datetime(2026, 5, 22, 12, 0, 0),
    player_a_team_score: int = 0,
    player_b_team_score: int = 0,
    tags_a_to_b: int = 0,
    tags_b_to_a: int = 0,
    role_a: str = "commander",
    role_b: str = "commander",
    match_id: int | None = None,
    arena_map_id: int | None = None,
    arena_map_name: str | None = None,
    is_simulated: bool = True,
) -> dict:
    """Build one ``rounds_list`` entry with every contracted key populated."""
    return {
        "round_id": round_id,
        "date_played": date_played,
        "player_a_team_score": player_a_team_score,
        "player_b_team_score": player_b_team_score,
        "tags_a_to_b": tags_a_to_b,
        "tags_b_to_a": tags_b_to_a,
        "role_a": role_a,
        "role_b": role_b,
        "match_id": match_id,
        "arena_map_id": arena_map_id,
        "arena_map_name": arena_map_name,
        "is_simulated": is_simulated,
    }


# ---------------------------------------------------------------------------
# §A — compute_round_record
# ---------------------------------------------------------------------------


class TestComputeRoundRecord(unittest.TestCase):
    """W/L/T per Round from player_a's perspective."""

    def test_empty_input_returns_zeros(self) -> None:
        self.assertEqual(
            compute_round_record([]),
            {"wins": 0, "losses": 0, "ties": 0, "n": 0},
        )

    def test_player_a_score_higher_is_win(self) -> None:
        rounds = [
            _round_dict(round_id=1, player_a_team_score=100, player_b_team_score=50)
        ]
        result = compute_round_record(rounds)
        self.assertEqual(result["wins"], 1)
        self.assertEqual(result["losses"], 0)
        self.assertEqual(result["ties"], 0)
        self.assertEqual(result["n"], 1)

    def test_player_a_score_lower_is_loss(self) -> None:
        rounds = [
            _round_dict(round_id=1, player_a_team_score=10, player_b_team_score=200)
        ]
        result = compute_round_record(rounds)
        self.assertEqual(result["losses"], 1)
        self.assertEqual(result["wins"], 0)
        self.assertEqual(result["ties"], 0)
        self.assertEqual(result["n"], 1)

    def test_equal_scores_is_tie(self) -> None:
        rounds = [
            _round_dict(round_id=1, player_a_team_score=50, player_b_team_score=50)
        ]
        result = compute_round_record(rounds)
        self.assertEqual(result["ties"], 1)
        self.assertEqual(result["wins"], 0)
        self.assertEqual(result["losses"], 0)
        self.assertEqual(result["n"], 1)

    def test_mixed_w_l_t_counted_correctly(self) -> None:
        rounds = [
            _round_dict(round_id=1, player_a_team_score=100, player_b_team_score=50),
            _round_dict(round_id=2, player_a_team_score=100, player_b_team_score=50),
            _round_dict(round_id=3, player_a_team_score=10, player_b_team_score=200),
            _round_dict(round_id=4, player_a_team_score=50, player_b_team_score=50),
        ]
        result = compute_round_record(rounds)
        self.assertEqual(result["wins"], 2)
        self.assertEqual(result["losses"], 1)
        self.assertEqual(result["ties"], 1)
        self.assertEqual(result["n"], 4)


# ---------------------------------------------------------------------------
# §B — compute_score_margin
# ---------------------------------------------------------------------------


class TestComputeScoreMargin(unittest.TestCase):
    """Mean signed (player_a_team_score − player_b_team_score) per Round."""

    def test_empty_input_zero_no_div_by_zero(self) -> None:
        result = compute_score_margin([])
        self.assertEqual(result, {"mean_margin": 0.0, "n": 0})

    def test_signed_mean_from_player_a_perspective(self) -> None:
        """Margins +50, +100, +0 → mean = 50.0."""
        rounds = [
            _round_dict(round_id=1, player_a_team_score=150, player_b_team_score=100),
            _round_dict(round_id=2, player_a_team_score=200, player_b_team_score=100),
            _round_dict(round_id=3, player_a_team_score=50, player_b_team_score=50),
        ]
        result = compute_score_margin(rounds)
        self.assertEqual(result["n"], 3)
        self.assertAlmostEqual(result["mean_margin"], 50.0)

    def test_negative_margin_when_player_b_team_dominates(self) -> None:
        """Margins −100, −50 → mean −75.0."""
        rounds = [
            _round_dict(round_id=1, player_a_team_score=0, player_b_team_score=100),
            _round_dict(round_id=2, player_a_team_score=50, player_b_team_score=100),
        ]
        result = compute_score_margin(rounds)
        self.assertEqual(result["n"], 2)
        self.assertAlmostEqual(result["mean_margin"], -75.0)


# ---------------------------------------------------------------------------
# §C — compute_tag_stats
# ---------------------------------------------------------------------------


class TestComputeTagStats(unittest.TestCase):
    """Per-round mean + raw totals for tags a→b and b→a."""

    def test_empty_input_all_zeros(self) -> None:
        result = compute_tag_stats([])
        self.assertEqual(
            result,
            {
                "avg_tags_a_to_b": 0.0,
                "avg_tags_b_to_a": 0.0,
                "total_tags_a_to_b": 0,
                "total_tags_b_to_a": 0,
                "n": 0,
            },
        )

    def test_avg_is_per_round_mean_not_sum_over_sum(self) -> None:
        """Two rounds: a→b tags 4, 0; b→a tags 1, 3.

        per-round mean a→b = (4+0)/2 = 2.0
        per-round mean b→a = (1+3)/2 = 2.0
        """
        rounds = [
            _round_dict(round_id=1, tags_a_to_b=4, tags_b_to_a=1),
            _round_dict(round_id=2, tags_a_to_b=0, tags_b_to_a=3),
        ]
        result = compute_tag_stats(rounds)
        self.assertEqual(result["n"], 2)
        self.assertAlmostEqual(result["avg_tags_a_to_b"], 2.0)
        self.assertAlmostEqual(result["avg_tags_b_to_a"], 2.0)

    def test_total_is_raw_sum(self) -> None:
        rounds = [
            _round_dict(round_id=1, tags_a_to_b=4, tags_b_to_a=1),
            _round_dict(round_id=2, tags_a_to_b=0, tags_b_to_a=3),
            _round_dict(round_id=3, tags_a_to_b=2, tags_b_to_a=5),
        ]
        result = compute_tag_stats(rounds)
        self.assertEqual(result["total_tags_a_to_b"], 6)
        self.assertEqual(result["total_tags_b_to_a"], 9)
        self.assertEqual(result["n"], 3)

    def test_direction_is_independent_a_to_b_vs_b_to_a(self) -> None:
        """An asymmetric round must not swap the two directions."""
        rounds = [_round_dict(round_id=1, tags_a_to_b=10, tags_b_to_a=0)]
        result = compute_tag_stats(rounds)
        self.assertEqual(result["total_tags_a_to_b"], 10)
        self.assertEqual(result["total_tags_b_to_a"], 0)
        self.assertAlmostEqual(result["avg_tags_a_to_b"], 10.0)
        self.assertAlmostEqual(result["avg_tags_b_to_a"], 0.0)


# ---------------------------------------------------------------------------
# §D — compute_per_role_breakdown
# ---------------------------------------------------------------------------


class TestComputePerRoleBreakdown(unittest.TestCase):
    """One row per unique role player_a played; aggregates within that role."""

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(compute_per_role_breakdown([]), [])

    def test_one_row_per_unique_role_a_value(self) -> None:
        """Two rounds as commander + one as heavy → 2 rows."""
        rounds = [
            _round_dict(
                round_id=1,
                role_a="commander",
                role_b="commander",
                player_a_team_score=100,
                player_b_team_score=50,
            ),
            _round_dict(
                round_id=2,
                role_a="commander",
                role_b="heavy",
                player_a_team_score=80,
                player_b_team_score=80,
            ),
            _round_dict(
                round_id=3,
                role_a="heavy",
                role_b="scout",
                player_a_team_score=10,
                player_b_team_score=200,
            ),
        ]
        result = compute_per_role_breakdown(rounds)
        roles = {row["role"] for row in result}
        self.assertEqual(roles, {"commander", "heavy"})
        self.assertEqual(len(result), 2)

    def test_sorted_by_games_desc_role_asc_tiebreaker(self) -> None:
        """commander: 3 games, ammo: 3 games, heavy: 1 game.

        Tiebreaker: role ascending alphabetically → ammo before commander.
        """
        rounds = []
        for i in range(3):
            rounds.append(
                _round_dict(
                    round_id=10 + i,
                    role_a="commander",
                    player_a_team_score=100,
                    player_b_team_score=50,
                )
            )
        for i in range(3):
            rounds.append(
                _round_dict(
                    round_id=20 + i,
                    role_a="ammo",
                    player_a_team_score=50,
                    player_b_team_score=100,
                )
            )
        rounds.append(
            _round_dict(
                round_id=30,
                role_a="heavy",
                player_a_team_score=10,
                player_b_team_score=10,
            )
        )
        result = compute_per_role_breakdown(rounds)
        self.assertEqual(len(result), 3)
        # ammo and commander tied at 3 games; ammo sorts first (asc tiebreaker).
        self.assertEqual(result[0]["role"], "ammo")
        self.assertEqual(result[0]["games"], 3)
        self.assertEqual(result[1]["role"], "commander")
        self.assertEqual(result[1]["games"], 3)
        # heavy (1 game) last.
        self.assertEqual(result[2]["role"], "heavy")
        self.assertEqual(result[2]["games"], 1)

    def test_per_role_aggregates_use_only_that_role_rounds(self) -> None:
        """commander rounds: +50, +50 → wins=2, mean_margin=50.
        heavy round: −100 → losses=1, mean_margin=−100.

        Tag totals are isolated per-role: commander a→b totals 5+3=8 over
        2 games → avg 4.0; heavy a→b totals 1 over 1 game → avg 1.0.
        """
        rounds = [
            _round_dict(
                round_id=1,
                role_a="commander",
                player_a_team_score=100,
                player_b_team_score=50,
                tags_a_to_b=5,
                tags_b_to_a=2,
            ),
            _round_dict(
                round_id=2,
                role_a="commander",
                player_a_team_score=150,
                player_b_team_score=100,
                tags_a_to_b=3,
                tags_b_to_a=4,
            ),
            _round_dict(
                round_id=3,
                role_a="heavy",
                player_a_team_score=0,
                player_b_team_score=100,
                tags_a_to_b=1,
                tags_b_to_a=9,
            ),
        ]
        result = compute_per_role_breakdown(rounds)
        by_role = {row["role"]: row for row in result}
        # commander
        cmd = by_role["commander"]
        self.assertEqual(cmd["games"], 2)
        self.assertEqual(cmd["wins"], 2)
        self.assertEqual(cmd["losses"], 0)
        self.assertEqual(cmd["ties"], 0)
        self.assertAlmostEqual(cmd["mean_margin"], 50.0)
        self.assertAlmostEqual(cmd["avg_tags_a_to_b"], 4.0)
        self.assertAlmostEqual(cmd["avg_tags_b_to_a"], 3.0)
        # heavy
        hvy = by_role["heavy"]
        self.assertEqual(hvy["games"], 1)
        self.assertEqual(hvy["losses"], 1)
        self.assertEqual(hvy["wins"], 0)
        self.assertAlmostEqual(hvy["mean_margin"], -100.0)
        self.assertAlmostEqual(hvy["avg_tags_a_to_b"], 1.0)
        self.assertAlmostEqual(hvy["avg_tags_b_to_a"], 9.0)


# ---------------------------------------------------------------------------
# §E — compute_per_map_breakdown
# ---------------------------------------------------------------------------


class TestComputePerMapBreakdown(unittest.TestCase):
    """Per-arena_map W/L/T + margin table; `None` labelled `No map (3-zone)`."""

    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(compute_per_map_breakdown([]), [])

    def test_one_row_per_arena_map_id(self) -> None:
        rounds = [
            _round_dict(
                round_id=1,
                player_a_team_score=100,
                player_b_team_score=50,
                arena_map_id=1,
                arena_map_name="Arena Alpha",
            ),
            _round_dict(
                round_id=2,
                player_a_team_score=30,
                player_b_team_score=80,
                arena_map_id=2,
                arena_map_name="Arena Beta",
            ),
        ]
        result = compute_per_map_breakdown(rounds)
        self.assertEqual(len(result), 2)
        ids = {row["arena_map_id"] for row in result}
        self.assertEqual(ids, {1, 2})
        names = {row["arena_map_name"] for row in result}
        self.assertEqual(names, {"Arena Alpha", "Arena Beta"})

    def test_arena_map_id_none_labelled_no_map_3_zone(self) -> None:
        rounds = [
            _round_dict(
                round_id=1,
                player_a_team_score=10,
                player_b_team_score=10,
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

    def test_sorted_by_games_desc_with_none_last(self) -> None:
        """Map 2: 3 games; Map 1: 2 games; no-map: 1 game.

        Sort: games desc; tiebreaker arena_map_id asc; ``None`` last.
        """
        rounds = []
        # Map 2: 3 games.
        for i in range(3):
            rounds.append(
                _round_dict(
                    round_id=20 + i,
                    player_a_team_score=10,
                    player_b_team_score=5,
                    arena_map_id=2,
                    arena_map_name="Beta",
                )
            )
        # Map 1: 2 games.
        for i in range(2):
            rounds.append(
                _round_dict(
                    round_id=10 + i,
                    player_a_team_score=20,
                    player_b_team_score=10,
                    arena_map_id=1,
                    arena_map_name="Alpha",
                )
            )
        # No-map: 1 game.
        rounds.append(
            _round_dict(
                round_id=30,
                player_a_team_score=5,
                player_b_team_score=5,
                arena_map_id=None,
                arena_map_name=None,
            )
        )
        result = compute_per_map_breakdown(rounds)
        self.assertEqual(len(result), 3)
        # Map 2 first (3 games).
        self.assertEqual(result[0]["arena_map_id"], 2)
        self.assertEqual(result[0]["games"], 3)
        # Map 1 second (2 games, id asc tiebreaker not invoked here).
        self.assertEqual(result[1]["arena_map_id"], 1)
        self.assertEqual(result[1]["games"], 2)
        # None last.
        self.assertIsNone(result[2]["arena_map_id"])
        self.assertEqual(result[2]["arena_map_name"], "No map (3-zone)")
        self.assertEqual(result[2]["games"], 1)


# ---------------------------------------------------------------------------
# §F — margin_series
# ---------------------------------------------------------------------------


class TestMarginSeries(unittest.TestCase):
    """Chart data — signed margin per Round chronologically."""

    def test_empty_input_empty_list(self) -> None:
        self.assertEqual(margin_series([]), [])

    def test_chronological_by_date_played_then_round_id(self) -> None:
        """Rounds sorted by (date_played, round_id) ascending; 1-based idx."""
        shared = datetime(2026, 5, 22, 12, 0, 0)
        later = datetime(2026, 5, 23, 12, 0, 0)
        rounds = [
            # Shuffled input.
            _round_dict(
                round_id=5,
                date_played=shared,
                player_a_team_score=10,
                player_b_team_score=0,
            ),
            _round_dict(
                round_id=2,
                date_played=shared,
                player_a_team_score=20,
                player_b_team_score=5,
            ),
            _round_dict(
                round_id=1,
                date_played=later,
                player_a_team_score=0,
                player_b_team_score=50,
            ),
        ]
        result = margin_series(rounds)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0][0], 1)
        self.assertEqual(result[1][0], 2)
        self.assertEqual(result[2][0], 3)
        # (date_played, round_id) tiebreaker: id=2 before id=5 (same date).
        self.assertEqual(result[0][1], 15)  # 20−5
        self.assertEqual(result[1][1], 10)  # 10−0
        self.assertEqual(result[2][1], -50)  # later date

    def test_returns_list_of_lists_not_tuples(self) -> None:
        rounds = [_round_dict(round_id=1, player_a_team_score=5, player_b_team_score=2)]
        result = margin_series(rounds)
        self.assertIsInstance(result, list)
        for entry in result:
            self.assertIsInstance(entry, list)
            self.assertNotIsInstance(entry, tuple)


# ---------------------------------------------------------------------------
# §G — cumulative_wl_series
# ---------------------------------------------------------------------------


class TestCumulativeWlSeries(unittest.TestCase):
    """Chart data — cumulative (a_wins − b_wins) Round-level."""

    def test_empty_input_empty_list(self) -> None:
        self.assertEqual(cumulative_wl_series([]), [])

    def test_ties_do_not_move_running_diff(self) -> None:
        """W, T, L → running diff +1, +1, 0."""
        rounds = [
            _round_dict(round_id=1, player_a_team_score=10, player_b_team_score=0),
            _round_dict(round_id=2, player_a_team_score=5, player_b_team_score=5),
            _round_dict(round_id=3, player_a_team_score=0, player_b_team_score=10),
        ]
        result = cumulative_wl_series(rounds)
        self.assertEqual(len(result), 3)
        self.assertEqual([r[0] for r in result], [1, 2, 3])
        self.assertEqual([r[1] for r in result], [1, 1, 0])

    def test_running_diff_reaches_correct_final_value(self) -> None:
        """W, W, L, W → final running diff = +2."""
        rounds = [
            _round_dict(round_id=1, player_a_team_score=10, player_b_team_score=0),
            _round_dict(round_id=2, player_a_team_score=10, player_b_team_score=0),
            _round_dict(round_id=3, player_a_team_score=0, player_b_team_score=10),
            _round_dict(round_id=4, player_a_team_score=10, player_b_team_score=0),
        ]
        result = cumulative_wl_series(rounds)
        self.assertEqual(len(result), 4)
        self.assertEqual([r[1] for r in result], [1, 2, 1, 2])

    def test_returns_list_of_lists_not_tuples(self) -> None:
        rounds = [_round_dict(round_id=1, player_a_team_score=5, player_b_team_score=2)]
        result = cumulative_wl_series(rounds)
        self.assertIsInstance(result, list)
        for entry in result:
            self.assertIsInstance(entry, list)
            self.assertNotIsInstance(entry, tuple)


# ---------------------------------------------------------------------------
# §H — Defensive: no Django imports leaked into the pure module
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """Mirrors the HX-01 / HX-02 / HX-03 / RES-04 / RV-03 precedent.

    Importing ``matches.player_h2h_stats`` in a fresh subprocess must not
    pull in ``django.*`` or ``matches.models`` — the pure module's import
    allowlist is ``typing`` + ``collections.defaultdict``.
    """

    def test_import_matches_player_h2h_stats_loads_no_django_modules(self) -> None:
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
            import matches.player_h2h_stats  # noqa: F401

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
