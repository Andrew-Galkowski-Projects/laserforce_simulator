"""RES-04 — Pure unit tests for ``reconstruct_cell_occupancy``.

These tests describe the contract pinned by §7.1 of the RES-04 seam contract
(``.claude/worktrees/res-04-seam-contract.md``). The module under test is a
pure-Python helper: imports here are limited to ``stdlib`` plus the helper
itself so the suite remains DB-free and Django-free.

Until the Code agent lands ``matches/sim_helpers/cell_occupancy.py`` every
test in this module will fail at import time with an ``ImportError`` — that
is expected and the seam-contract precedent for spec-first tests.
"""

from __future__ import annotations

import unittest

from matches.sim_helpers.cell_occupancy import reconstruct_cell_occupancy


class TestReconstructCellOccupancy(unittest.TestCase):
    """Pure-unit coverage of the apportionment / rounding algorithm.

    Each test builds an inline ``adj`` dict so the A* expansion inside
    ``reconstruct_cell_occupancy`` walks the exact route the test cares
    about; we do not call ``build_movement_adjacency`` from this file.
    """

    # ----- Empty-trail edge cases ----------------------------------------

    def test_empty_trail_survived_credits_spawn(self) -> None:
        """Survived (sentinel 1801) + no Advances → spawn cell credited the
        full ``round_ticks``."""
        spawn = (3, 4)
        result = reconstruct_cell_occupancy(
            movement_trail=[],
            spawn_cell=spawn,
            round_ticks=1800,
            eliminated_at=1801,
            adj={spawn: []},
        )
        self.assertEqual(result, {spawn: 1800})

    def test_empty_trail_eliminated_at_zero_yields_empty(self) -> None:
        """Eliminated at tick 0 + no Advances → no credit; output is empty."""
        spawn = (0, 0)
        result = reconstruct_cell_occupancy(
            movement_trail=[],
            spawn_cell=spawn,
            round_ticks=1800,
            eliminated_at=0,
            adj={spawn: []},
        )
        self.assertEqual(result, {})

    # ----- 1-cell Advance (worked example) -------------------------------

    def test_single_one_cell_advance(self) -> None:
        """Worked example from the seam contract (§2 / §7.1 #3).

        Trail: ``[((0,0),(0,1),10)]``, ``round_ticks=20``,
        ``eliminated_at=1801``.

        Float math (per the contract algorithm):
          * Stationary `[0, 10)` → +10 to (0,0).
          * Advance at ts=10 → 0.5 to (0,0), 0.5 to (0,1).
          * Trailing stationary `[11, 20)` → +9 to (0,1).
          * Totals: (0,0)=10.5, (0,1)=9.5.

        Banker's rounding (Python ``round``): both round to 10.
        """
        adj = {(0, 0): [(0, 1)], (0, 1): [(0, 0)]}
        result = reconstruct_cell_occupancy(
            movement_trail=[((0, 0), (0, 1), 10)],
            spawn_cell=(0, 0),
            round_ticks=20,
            eliminated_at=1801,
            adj=adj,
        )
        self.assertEqual(result, {(0, 0): 10, (0, 1): 10})

    # ----- Multi-cell Advance --------------------------------------------

    def test_multi_cell_advance_apportions_evenly(self) -> None:
        """Multi-cell Advance with too-little surrounding time: all credit
        rounds to zero and the result is empty.

        Trail: ``[((0,0),(0,3),0)]``, route ``(0,0)→(0,1)→(0,2)→(0,3)``.
        ``round_ticks=1``, ``eliminated_at=1801``. ``N=4`` cells walked,
        each gets ``1/4 = 0.25`` tick → all round to 0 → omitted.
        """
        adj = {
            (0, 0): [(0, 1)],
            (0, 1): [(0, 0), (0, 2)],
            (0, 2): [(0, 1), (0, 3)],
            (0, 3): [(0, 2)],
        }
        result = reconstruct_cell_occupancy(
            movement_trail=[((0, 0), (0, 3), 0)],
            spawn_cell=(0, 0),
            round_ticks=1,
            eliminated_at=1801,
            adj=adj,
        )
        self.assertEqual(result, {})

    def test_multi_cell_advance_with_long_run(self) -> None:
        """Same multi-cell route as above but the player rests on the
        destination for many ticks afterward.

        ``round_ticks=100``: only (0,3) crosses the rounding threshold
        because trailing stationary credits it 99 + 0.25. The other route
        cells stay at 0.25 each (round to 0, omitted).
        """
        adj = {
            (0, 0): [(0, 1)],
            (0, 1): [(0, 0), (0, 2)],
            (0, 2): [(0, 1), (0, 3)],
            (0, 3): [(0, 2)],
        }
        result = reconstruct_cell_occupancy(
            movement_trail=[((0, 0), (0, 3), 0)],
            spawn_cell=(0, 0),
            round_ticks=100,
            eliminated_at=1801,
            adj=adj,
        )
        self.assertIn((0, 3), result)
        self.assertEqual(result[(0, 3)], 99)
        # The fractional 0.25-credit cells must be omitted (round() to 0).
        self.assertNotIn((0, 0), result)
        self.assertNotIn((0, 1), result)
        self.assertNotIn((0, 2), result)

    # ----- Stationary between two Advances -------------------------------

    def test_stationary_between_two_advances(self) -> None:
        """Two 1-cell Advances with a 9-tick rest on the middle cell.

        Trail: ``[((0,0),(0,1),5), ((0,1),(0,2),15)]``, ``round_ticks=20``.
        Per-cell float totals (algorithm):
          * (0,0): 5 (stationary) + 0.5 (Advance 1 split) = 5.5
          * (0,1): 0.5 (Advance 1 split) + 9 (stationary 6..14)
                  + 0.5 (Advance 2 split) = 10.0
          * (0,2): 0.5 (Advance 2 split) + 4 (trailing 16..19) = 4.5

        Banker's rounding: 5.5→6, 10.0→10, 4.5→4.
        """
        adj = {
            (0, 0): [(0, 1)],
            (0, 1): [(0, 0), (0, 2)],
            (0, 2): [(0, 1)],
        }
        result = reconstruct_cell_occupancy(
            movement_trail=[((0, 0), (0, 1), 5), ((0, 1), (0, 2), 15)],
            spawn_cell=(0, 0),
            round_ticks=20,
            eliminated_at=1801,
            adj=adj,
        )
        self.assertEqual(result.get((0, 1)), 10)
        self.assertEqual(result.get((0, 0)), 6)
        self.assertEqual(result.get((0, 2)), 4)

    # ----- Elimination cutoff --------------------------------------------

    def test_post_elimination_cutoff(self) -> None:
        """No credit accumulates past ``eliminated_at`` for an early-out.

        Trail empty, ``eliminated_at=50``, ``round_ticks=1800``. The spawn
        cell gets exactly 50 ticks (the early cutoff). Sum equals 50.
        """
        spawn = (7, 7)
        result = reconstruct_cell_occupancy(
            movement_trail=[],
            spawn_cell=spawn,
            round_ticks=1800,
            eliminated_at=50,
            adj={spawn: []},
        )
        self.assertEqual(result, {spawn: 50})
        self.assertEqual(sum(result.values()), 50)

    # ----- Rounding-slack reconciliation ---------------------------------

    def test_sum_reconciliation_within_rounding_slack(self) -> None:
        """For a realistic deterministic trail the integer cell sum cannot
        exceed ``min(round_ticks, eliminated_at)``, and the rounding-slack
        deviation is bounded by ``len(result)`` (≤ 0.5 per cell).

        Fixture: a 6-cell loop walked across 5 Advances at evenly-spaced
        ticks across a 60-tick window (no elimination — survived sentinel).
        """
        loop = [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5)]
        # 4-connected linear adj (only neighbours)
        adj = {
            loop[i]: (
                [loop[i - 1]]
                if i == len(loop) - 1
                else ([loop[i + 1]] if i == 0 else [loop[i - 1], loop[i + 1]])
            )
            for i in range(len(loop))
        }
        trail = [(loop[i], loop[i + 1], (i + 1) * 10) for i in range(5)]
        round_ticks = 60
        result = reconstruct_cell_occupancy(
            movement_trail=trail,
            spawn_cell=loop[0],
            round_ticks=round_ticks,
            eliminated_at=1801,
            adj=adj,
        )
        cap = min(round_ticks, 1801)
        total = sum(result.values())
        self.assertLessEqual(total, cap)
        # Rounding slack: cumulative |delta| ≤ 0.5 per cell.
        self.assertLessEqual(abs(total - cap), len(result))

    # ----- Module-purity guard -------------------------------------------

    def test_pure_no_django_imports(self) -> None:
        """The helper module must not leak ``django`` or ``models`` names.

        This pins the seam contract's "no Django imports" requirement
        (§2 — "Pure Python. No Django imports.").
        """
        import matches.sim_helpers.cell_occupancy as m

        self.assertNotIn("django", dir(m))
        self.assertNotIn("models", dir(m))


if __name__ == "__main__":
    unittest.main()
