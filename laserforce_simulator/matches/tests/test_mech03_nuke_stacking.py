"""
Tests for MECH-03: Commander nuke-stacking gate.

A high-awareness Commander stacks special points before firing so it can chain
back-to-back nukes.  The gate function `_commander_nuke_gate(sp, ga)` determines
whether the weight for `use_special` is set or stays at zero:

  ga < 30  → fires when sp > 20
  ga < 50  → fires when sp > 40
  ga < 70  → fires when sp > 60
  ga >= 70 → fires when sp > 80 (always waits for maximum stack)

sp > 80 always returns True regardless of ga.

Production code:
  matches/sim_helpers/weights.py  (_commander_nuke_gate + _get_commander_weights)
"""

import random
import unittest

from matches.sim_helpers.role_constants import SPECIAL_COST
from matches.sim_helpers.weights import (
    _commander_nuke_gate,
    _get_commander_weights,
)

random.seed(42)

# ---------------------------------------------------------------------------
# Action index layout (mirrors weights.py / combat.py conventions)
# ---------------------------------------------------------------------------

_ACTION_IDX = {
    "tag_player": 0,
    "only_move": 1,
    "hide": 2,
    "capture_base": 3,
    "use_special": 4,
    "resupply_ally": 5,
    "missile_player": 6,
}

_BASE = [70, 30, 0, 0, 0, 0, 0]


# ---------------------------------------------------------------------------
# Minimal mock player — satisfies the duck-type interface for weights.py
# ---------------------------------------------------------------------------


class _MockCommander:
    """Lightweight stand-in for PlayerRoundState / PlayerState (commander role)."""

    def __init__(
        self,
        final_special: int = 0,
        game_awareness: int = 50,
        special_usage: int = 50,
        missiles_landed: int = 5,  # no missiles by default → avoids missile weight noise
        team_color: str = "red",
        current_zone: int = 0,
        final_lives: int = 30,
        final_shots: int = 60,
    ) -> None:
        self.role = "commander"
        self.team_color = team_color
        self.current_zone = current_zone
        self.final_special = final_special
        self.special_cost = SPECIAL_COST["commander"]
        self.game_awareness = game_awareness
        self.special_usage = special_usage
        self.missiles_landed = missiles_landed
        self.final_lives = final_lives
        self.final_shots = final_shots

        # role constants needed by weight helper internals
        self.starting_lives = 30
        self.starting_shots = 60
        self.max_lives = 30
        self.max_shots = 60
        self.resupply_efficiency = 50

        # special-active flag (scout-specific but referenced generically)
        self.special_active_until = 0

    @property
    def missiles_used(self) -> int:
        return self.missiles_landed

    @property
    def can_capture_base_in_current_zone(self) -> bool:
        # Zone 0 = red home base, which is NOT capturable for red team.
        # Return False by default so base-capture weight branch is not triggered.
        return False

    def is_active_at(self, second: int) -> bool:
        return True


# ---------------------------------------------------------------------------
# TestCommanderNukeGate — pure unit tests for the gate function itself
# ---------------------------------------------------------------------------


class TestCommanderNukeGate(unittest.TestCase):
    """Tests for `_commander_nuke_gate(sp, ga) -> bool`."""

    # --- Low-awareness tier (ga < 30): fires at sp > 20 ---

    def test_low_ga_sp_above_threshold_fires(self):
        """ga=25, sp=21 → True (fires at minimum threshold for low awareness)."""
        self.assertTrue(_commander_nuke_gate(sp=21, ga=25))

    def test_low_ga_sp_below_20_does_not_fire(self):
        """ga=25, sp=15 → False (sp <= 20 cannot fire regardless of ga)."""
        self.assertFalse(_commander_nuke_gate(sp=15, ga=25))

    def test_low_ga_sp_exactly_20_does_not_fire(self):
        """ga=25, sp=20 → False (threshold is strictly > 20, not >=)."""
        self.assertFalse(_commander_nuke_gate(sp=20, ga=25))

    # --- Medium-low awareness tier (30 <= ga < 50): fires at sp > 40 ---

    def test_mid_low_ga_sp_21_does_not_fire(self):
        """ga=35, sp=21 → False (ga >= 30 requires sp > 40 before firing)."""
        self.assertFalse(_commander_nuke_gate(sp=21, ga=35))

    def test_mid_low_ga_sp_above_40_fires(self):
        """ga=35, sp=41 → True (sp > 40, ga < 50)."""
        self.assertTrue(_commander_nuke_gate(sp=41, ga=35))

    def test_mid_low_ga_sp_exactly_40_does_not_fire(self):
        """ga=35, sp=40 → False (threshold is strictly > 40, not >=)."""
        self.assertFalse(_commander_nuke_gate(sp=40, ga=35))

    # --- Medium-high awareness tier (50 <= ga < 70): fires at sp > 60 ---

    def test_mid_high_ga_sp_41_does_not_fire(self):
        """ga=65, sp=41 → False (ga >= 50 requires sp > 60 before firing)."""
        self.assertFalse(_commander_nuke_gate(sp=41, ga=65))

    def test_mid_high_ga_sp_above_60_fires(self):
        """ga=65, sp=61 → True (sp > 60, ga < 70)."""
        self.assertTrue(_commander_nuke_gate(sp=61, ga=65))

    # --- High-awareness tier (ga >= 70): fires only at sp > 80 ---

    def test_high_ga_sp_61_does_not_fire(self):
        """ga=75, sp=61 → False (ga >= 70 waits until sp > 80)."""
        self.assertFalse(_commander_nuke_gate(sp=61, ga=75))

    def test_high_ga_sp_exactly_80_does_not_fire(self):
        """ga=75, sp=80 → False (threshold is strictly > 80, not >=)."""
        self.assertFalse(_commander_nuke_gate(sp=80, ga=75))

    def test_high_ga_sp_above_80_fires(self):
        """ga=75, sp=81 → True (sp > 80 always fires)."""
        self.assertTrue(_commander_nuke_gate(sp=81, ga=75))

    # --- Max-awareness corner cases ---

    def test_max_ga_sp_above_80_fires(self):
        """ga=100, sp=81 → True (sp > 80 always fires regardless of ga)."""
        self.assertTrue(_commander_nuke_gate(sp=81, ga=100))

    def test_max_ga_sp_61_does_not_fire(self):
        """ga=100, sp=61 → False (ga=100 >= 70, waits until sp > 80)."""
        self.assertFalse(_commander_nuke_gate(sp=61, ga=100))


# ---------------------------------------------------------------------------
# TestCommanderNukeWeightGating — integration tests through _get_commander_weights
# ---------------------------------------------------------------------------


class TestCommanderNukeWeightGating(unittest.TestCase):
    """
    Confirm that `_get_commander_weights` routes `use_special` weight correctly
    based on `_commander_nuke_gate`.

    A player-only list is passed (no allies/enemies in zone) so the only
    variable driving `use_special` is the gate decision.
    """

    def _fresh(self) -> list:
        return list(_BASE)

    def test_low_ga_sp_above_threshold_use_special_nonzero(self):
        """Low-ga Commander (ga=20) with sp=25 should get use_special > 0.

        Gate: ga=20 < 30, sp=25 > 20 → True → weight is set.
        """
        player = _MockCommander(final_special=25, game_awareness=20)
        w = _get_commander_weights(
            player, _ACTION_IDX, self._fresh(), [player], second=0
        )
        self.assertGreater(
            w[_ACTION_IDX["use_special"]],
            0,
            "Low-awareness Commander at sp=25 should have use_special > 0",
        )

    def test_high_ga_sp_below_max_threshold_use_special_zero(self):
        """High-ga Commander (ga=80) with sp=25 should get use_special == 0.

        Gate: ga=80 >= 70, sp=25 is not > 80 → False → weight stays 0.
        """
        player = _MockCommander(final_special=25, game_awareness=80)
        w = _get_commander_weights(
            player, _ACTION_IDX, self._fresh(), [player], second=0
        )
        self.assertEqual(
            w[_ACTION_IDX["use_special"]],
            0,
            "High-awareness Commander at sp=25 should stack (use_special == 0)",
        )

    def test_high_ga_sp_above_max_threshold_use_special_nonzero(self):
        """High-ga Commander (ga=80) with sp=85 should get use_special > 0.

        Gate: sp=85 > 80 → True (unconditional top tier) → weight is set.
        """
        player = _MockCommander(final_special=85, game_awareness=80)
        w = _get_commander_weights(
            player, _ACTION_IDX, self._fresh(), [player], second=0
        )
        self.assertGreater(
            w[_ACTION_IDX["use_special"]],
            0,
            "High-awareness Commander at sp=85 (> 80) should fire (use_special > 0)",
        )
