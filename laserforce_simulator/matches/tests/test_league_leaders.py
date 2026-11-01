"""LG-01z-m -- pure-unit tests for ``matches/league_leaders_logic.py``.

``TestComputeLeaderboards*`` / ``TestNoDjangoImportsLeaked`` -- no DB; the
only Django touch the module makes is importing the ``LeaderRow`` dataclass,
which the purity check confirms does not transitively load Django. The HTML
League Leaders screen's view tests went with the screen (ANG-08); the API
serves the same boards (``GET /api/leagues/<id>/league-leaders/``).
"""

from __future__ import annotations

import unittest

from matches.league_leaders_logic import compute_leaderboards
from laserforce_simulator.testing import run_project_python

# ===========================================================================
# Pure-unit: compute_leaderboards
# ===========================================================================


def _row(
    player_id,
    *,
    name=None,
    role="scout",
    team_id=1,
    team_name="T",
    tags=0,
    tagged=0,
    score=0,
) -> dict:
    return {
        "player_id": player_id,
        "player_name": name or f"P{player_id}",
        "role": role,
        "team_id": team_id,
        "team_name": team_name,
        "tags_made": tags,
        "times_tagged": tagged,
        "points_scored": score,
    }


class TestComputeLeaderboardsEmpty(unittest.TestCase):
    def test_empty_input_returns_four_empty_lists(self) -> None:
        out = compute_leaderboards([])
        self.assertEqual(
            set(out), {"avg_tags", "avg_score", "fewest_tagged", "tag_ratio"}
        )
        for board in out.values():
            self.assertEqual(board, [])


class TestComputeLeaderboardsVerbs(unittest.TestCase):
    def test_avg_tags_is_mean_descending(self) -> None:
        rows = [
            _row(1, tags=10),
            _row(1, tags=20),  # player 1 mean = 15
            _row(2, tags=5),  # player 2 mean = 5
        ]
        board = compute_leaderboards(rows)["avg_tags"]
        self.assertEqual([r.player_id for r in board], [1, 2])
        self.assertAlmostEqual(board[0].value, 15.0)
        self.assertEqual(board[0].rank, 1)
        self.assertEqual(board[0].games_played, 2)

    def test_avg_score_is_mean_descending(self) -> None:
        rows = [_row(1, score=100), _row(2, score=300), _row(2, score=100)]
        board = compute_leaderboards(rows)["avg_score"]
        # player 2 mean = 200, player 1 mean = 100
        self.assertEqual([r.player_id for r in board], [2, 1])
        self.assertAlmostEqual(board[0].value, 200.0)

    def test_fewest_tagged_is_mean_ascending(self) -> None:
        rows = [_row(1, tagged=2), _row(2, tagged=8), _row(3, tagged=0)]
        board = compute_leaderboards(rows)["fewest_tagged"]
        # least-tagged leads
        self.assertEqual([r.player_id for r in board], [3, 1, 2])
        self.assertAlmostEqual(board[0].value, 0.0)

    def test_tag_ratio_is_sum_over_sum_not_mean_of_ratios(self) -> None:
        # player 1: tags 1/tagged 0, then tags 0/tagged 4
        #   sum/sum = 1 / max(4,1) = 0.25 (mean-of-ratios would be huge)
        rows = [_row(1, tags=1, tagged=0), _row(1, tags=0, tagged=4)]
        board = compute_leaderboards(rows)["tag_ratio"]
        self.assertAlmostEqual(board[0].value, 0.25)

    def test_tag_ratio_zero_tagged_uses_max_one_denominator(self) -> None:
        rows = [_row(1, tags=7, tagged=0)]
        board = compute_leaderboards(rows)["tag_ratio"]
        # 7 / max(0, 1) == 7.0, no ZeroDivisionError
        self.assertAlmostEqual(board[0].value, 7.0)


class TestComputeLeaderboardsTiebreak(unittest.TestCase):
    def test_tie_breaks_games_desc_then_player_id_asc(self) -> None:
        # All three players have avg_tags == 5, differing games + ids.
        rows = [
            _row(3, tags=5),  # 1 game
            _row(2, tags=5),  # 2 games
            _row(2, tags=5),
            _row(1, tags=5),  # 1 game
        ]
        board = compute_leaderboards(rows)["avg_tags"]
        # value tie -> games desc (player 2 has 2 games) -> player_id asc.
        self.assertEqual([r.player_id for r in board], [2, 1, 3])


class TestComputeLeaderboardsLimit(unittest.TestCase):
    def test_limit_caps_each_board(self) -> None:
        rows = [_row(i, tags=i) for i in range(1, 21)]
        out = compute_leaderboards(rows, limit=10)
        for board in out.values():
            self.assertEqual(len(board), 10)

    def test_default_limit_is_ten(self) -> None:
        rows = [_row(i, tags=i) for i in range(1, 31)]
        out = compute_leaderboards(rows)
        self.assertEqual(len(out["avg_tags"]), 10)


class TestComputeLeaderboardsDeterministic(unittest.TestCase):
    def test_repeated_calls_equal(self) -> None:
        rows = [_row(1, tags=3, score=9, tagged=2), _row(2, tags=3, score=9, tagged=1)]
        self.assertEqual(compute_leaderboards(rows), compute_leaderboards(rows))


class TestComputeLeaderboardsLastRowWins(unittest.TestCase):
    def test_last_row_supplies_displayed_name_team_role(self) -> None:
        rows = [
            _row(1, name="Old", team_name="OldT", role="scout", tags=4),
            _row(1, name="New", team_name="NewT", role="medic", tags=4),
        ]
        board = compute_leaderboards(rows)["avg_tags"]
        self.assertEqual(board[0].player_name, "New")
        self.assertEqual(board[0].team_name, "NewT")
        self.assertEqual(board[0].role, "medic")


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """``matches.league_leaders_logic`` must not transitively import Django.

    Spawn a fresh subprocess, ``import matches.league_leaders_logic``, then
    walk ``sys.modules`` and assert no entry matches the ``django`` prefix.
    (The ``LeaderRow`` dataclass import must stay Django-free.)
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
            import matches.league_leaders_logic  # noqa: F401
            leaked = sorted(
                m for m in sys.modules
                if m == "django" or m.startswith("django.")
            )
            if leaked:
                print("LEAK:" + ",".join(leaked))
                sys.exit(1)
            sys.exit(0)
            """)
        result = run_project_python(script, timeout=30)
        self.assertEqual(
            result.returncode,
            0,
            f"Django import leaked into matches.league_leaders_logic.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )
