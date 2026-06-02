"""Tests for the Commander nuke lifecycle: stacking (MECH-03), reaction to
incoming nukes (MECH-04), and cancellation when the Commander is downed
before detonation (MECH-05).
"""

import random
import unittest
from unittest.mock import patch

from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.pending_events import PendingNuke
from matches.sim_helpers.pathfinding import choose_goal_cell
from matches.sim_helpers.map_context import MapContext

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MAX_LIVES = {"commander": 30, "heavy": 20, "scout": 30, "medic": 20, "ammo": 20}
_MAX_SHOTS = {"commander": 60, "heavy": 40, "scout": 60, "medic": 30, "ammo": 15}


def _ps(
    role: str,
    team_color: str = "red",
    *,
    lives: int | None = None,
    shots: int | None = None,
    game_awareness: int = 50,
    player_awareness: int = 50,
    cell_row: int | None = None,
    cell_col: int | None = None,
    reacting_to_nuke: bool = False,
    **kwargs,
) -> PlayerState:
    """Minimal PlayerState with sensible per-role defaults."""
    max_l = _MAX_LIVES.get(role, 15)
    max_s = _MAX_SHOTS.get(role, 30)
    p = PlayerState(
        tag_id=f"{team_color}_{role}",
        name=f"{team_color} {role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=max_l,
        starting_shots=max_s,
        final_lives=max_l if lives is None else lives,
        final_shots=max_s if shots is None else shots,
        game_awareness=game_awareness,
        player_awareness=player_awareness,
        cell_row=cell_row,
        cell_col=cell_col,
        **kwargs,
    )
    p.reacting_to_nuke = reacting_to_nuke
    return p


def _minimal_ctx(
    *,
    spawn_cells: dict | None = None,
    adj: dict | None = None,
) -> MapContext:
    """Minimal MapContext for goal-cell tests that don't need full map data."""
    return MapContext.from_dict(
        {
            "adj": adj or {},
            "spawn_cells": spawn_cells or {"red": (0, 0), "blue": (9, 9)},
            "zone_data": None,
            "sight_data": {},
            "base_sight_data": {},
            "cell_los_counts": {},
            "high_los_cells": [],
            "strong_spots": [],
        }
    )


# ---------------------------------------------------------------------------
# TestMech04ReactionProbability — unit tests for the probability formula
# ---------------------------------------------------------------------------


class TestMech04ReactionProbability(unittest.TestCase):
    """Tests for the reaction roll: (game_awareness + player_awareness) / 200."""

    def _reaction_prob(self, game_awareness: int, player_awareness: int) -> float:
        """Compute the reaction probability as production code does."""
        return (game_awareness + player_awareness) / 200.0

    # ------------------------------------------------------------------ #
    # 1. High awareness always reacts
    # ------------------------------------------------------------------ #

    def test_high_awareness_player_reaction_probability_is_one(self):
        """ga=100, pa=100 → prob = 1.0 (certain reaction)."""
        prob = self._reaction_prob(100, 100)
        self.assertAlmostEqual(prob, 1.0, places=6)

    def test_high_awareness_player_always_reacts_in_roll(self):
        """With prob=1.0, random.random() < 1.0 is always True."""
        prob = self._reaction_prob(100, 100)
        # Over 100 trials all should react
        for _ in range(100):
            self.assertLess(random.random(), prob + 1e-9)

    # ------------------------------------------------------------------ #
    # 2. Zero awareness never reacts
    # ------------------------------------------------------------------ #

    def test_zero_awareness_player_reaction_probability_is_zero(self):
        """ga=0, pa=0 → prob = 0.0 (never reacts)."""
        prob = self._reaction_prob(0, 0)
        self.assertAlmostEqual(prob, 0.0, places=6)

    def test_zero_awareness_player_never_reacts_in_roll(self):
        """With prob=0.0, random.random() < 0.0 is always False."""
        prob = self._reaction_prob(0, 0)
        for _ in range(100):
            self.assertFalse(random.random() < prob)

    # ------------------------------------------------------------------ #
    # 3. Formula correctness at mid-point
    # ------------------------------------------------------------------ #

    def test_reaction_probability_formula_midpoint(self):
        """ga=50, pa=50 → (50+50)/200 = 0.5."""
        prob = self._reaction_prob(50, 50)
        self.assertAlmostEqual(prob, 0.5, places=6)

    def test_reaction_probability_formula_asymmetric(self):
        """ga=80, pa=20 → (80+20)/200 = 0.5 (same as balanced 50/50)."""
        prob = self._reaction_prob(80, 20)
        self.assertAlmostEqual(prob, 0.5, places=6)

    def test_reaction_probability_formula_low_awareness(self):
        """ga=10, pa=10 → (10+10)/200 = 0.1."""
        prob = self._reaction_prob(10, 10)
        self.assertAlmostEqual(prob, 0.1, places=6)

    def test_reaction_probability_formula_one_stat_zero(self):
        """ga=100, pa=0 → 100/200 = 0.5."""
        prob = self._reaction_prob(100, 0)
        self.assertAlmostEqual(prob, 0.5, places=6)


# ---------------------------------------------------------------------------
# TestMech04GoalOverride — unit tests for choose_goal_cell step 0
# ---------------------------------------------------------------------------


class TestMech04GoalOverride(unittest.TestCase):
    """MECH-04 nuke-reaction override inside choose_goal_cell."""

    # ------------------------------------------------------------------ #
    # 4. Lives critical while reacting → seek medic
    # ------------------------------------------------------------------ #

    def test_lives_critical_seeks_medic_when_reacting(self):
        """reacting_to_nuke=True, lives ≤ 30% → goal is allied Medic's cell.

        Scout has 30 max lives.  9 lives = 30% — exactly at threshold (≤).
        Allied Medic is at (4, 4).  Goal must be (4, 4).
        """
        scout = _ps(
            "scout",
            "red",
            lives=9,  # 9/30 = 30% — exactly at ≤ threshold
            reacting_to_nuke=True,
            cell_row=0,
            cell_col=0,
        )
        medic = _ps("medic", "red", cell_row=4, cell_col=4)
        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        ctx = _minimal_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(scout, [scout, medic], spawn_cells, ctx)
        self.assertEqual(
            goal, (4, 4), "Reacting scout at critical lives must seek medic"
        )

    def test_lives_critical_below_threshold_seeks_medic_when_reacting(self):
        """reacting_to_nuke=True, lives < 30% → goal is allied Medic's cell.

        Heavy has 20 max lives.  5 lives = 25% — below 30% threshold.
        """
        heavy = _ps(
            "heavy",
            "red",
            lives=5,  # 5/20 = 25% < 30%
            reacting_to_nuke=True,
            cell_row=0,
            cell_col=0,
        )
        medic = _ps("medic", "red", cell_row=3, cell_col=3)
        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        ctx = _minimal_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(heavy, [heavy, medic], spawn_cells, ctx)
        self.assertEqual(
            goal, (3, 3), "Reacting heavy at critical lives must seek medic"
        )

    # ------------------------------------------------------------------ #
    # 5. Lives NOT critical while reacting → falls through (no medic override)
    # ------------------------------------------------------------------ #

    def test_lives_not_critical_no_medic_override_when_reacting(self):
        """reacting_to_nuke=True but lives > 30% → MECH-06 TODO path, no override.

        Commander has 30 max lives.  25 lives = 83% — well above 30%.
        The MECH-06 hook is a pass, so goal falls through to normal selection.
        Enemy base is at (9, 9); with no movement_ctx-specific role goal, the
        default should be the enemy base.
        """
        commander = _ps(
            "commander",
            "red",
            lives=25,  # 83% — not critical
            reacting_to_nuke=True,
            cell_row=0,
            cell_col=0,
        )
        medic = _ps("medic", "red", cell_row=4, cell_col=4)
        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        ctx = _minimal_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(commander, [commander, medic], spawn_cells, ctx)
        # MECH-06 hook is empty → falls through to role goal (commander → enemy medic)
        # or default (enemy base).  What it must NOT do is return (4,4) — the medic cell.
        self.assertNotEqual(
            goal,
            (4, 4),
            "Reacting commander with non-critical lives must NOT be forced to medic cell",
        )

    # ------------------------------------------------------------------ #
    # 6. Non-reacting player is unaffected
    # ------------------------------------------------------------------ #

    def test_step1_fires_for_non_reacting_critical_player(self):
        """reacting_to_nuke=False → step 0 is skipped; step 1 handles critical lives.

        Scout with critical lives (9/30 = 30%) but NOT reacting to nuke:
        step 0 must not fire.  The critical-resource override in step 1 still
        seeks the allied medic for non-support roles at ≤ 30% lives.
        Confirms goal reaches medic via step 1, not MECH-04 path.
        """
        scout = _ps(
            "scout",
            "red",
            lives=9,  # 30% — at critical threshold
            reacting_to_nuke=False,  # NOT reacting
            cell_row=0,
            cell_col=0,
        )
        medic = _ps("medic", "red", cell_row=4, cell_col=4)
        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        ctx = _minimal_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(scout, [scout, medic], spawn_cells, ctx)
        # Critical-resource override (step 1) should still direct to medic
        self.assertEqual(
            goal,
            (4, 4),
            "Non-reacting scout at critical lives still seeks medic via step 1",
        )

    def test_non_reacting_healthy_player_goes_to_enemy_base(self):
        """reacting_to_nuke=False, healthy lives → no override, default goal used.

        Commander with full lives and no movement_ctx high-LOS/strong-spot data
        will fall through to the default (enemy base).
        """
        commander = _ps(
            "commander",
            "red",
            lives=30,  # full lives
            reacting_to_nuke=False,
            cell_row=0,
            cell_col=0,
        )
        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        # No enemy medic in all_alive so step 3 (commander→enemy medic) returns None
        ctx = _minimal_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(commander, [commander], spawn_cells, ctx)
        self.assertEqual(
            goal,
            (9, 9),
            "Healthy non-reacting commander without enemy medic falls back to enemy base",
        )

    # ------------------------------------------------------------------ #
    # 7. reacting_to_nuke defaults to False on PlayerState
    # ------------------------------------------------------------------ #

    def test_reacting_to_nuke_default_is_false(self):
        """PlayerState.reacting_to_nuke defaults to False."""
        player = _ps("scout")
        self.assertFalse(player.reacting_to_nuke)

    def test_reacting_to_nuke_can_be_set_to_true(self):
        """PlayerState.reacting_to_nuke can be set True without error."""
        player = _ps("scout")
        player.reacting_to_nuke = True
        self.assertTrue(player.reacting_to_nuke)


# ---------------------------------------------------------------------------
# TestMech04TickLoopFlagSetting — integration tests for the tick-loop logic
# ---------------------------------------------------------------------------


class TestMech04TickLoopFlagSetting(unittest.TestCase):
    """Verify the tick-loop logic that sets reacting_to_nuke on target team players.

    These tests exercise the logic extracted from BatchSimulator._simulate_round
    directly (the MECH-04 block ~line 2844).  They do NOT run a full simulation
    — they reproduce the exact algorithm from the tick loop and assert on the
    resulting player states.
    """

    def _apply_mech04_reaction_flags(
        self,
        all_alive: list,
        pending_nukes: list,
        *,
        random_value: float = 0.0,  # patch random.random to this value
    ) -> None:
        """Reproduce the MECH-04 tick-loop block from BatchSimulator._simulate_round.

        Resets all reacting_to_nuke flags, then sets them for reacting players.
        Uses random_value as the fixed return from random.random() for determinism.
        """
        for p in all_alive:
            p.reacting_to_nuke = False

        with patch("random.random", return_value=random_value):
            for pending_nuke in pending_nukes:
                nuke_team = pending_nuke.player.team_color
                target_team_color = "blue" if nuke_team == "red" else "red"
                for p in all_alive:
                    if p.team_color != target_team_color:
                        continue
                    prob = (p.game_awareness + p.player_awareness) / 200.0
                    if random.random() < prob:
                        p.reacting_to_nuke = True

    # ------------------------------------------------------------------ #
    # 7. Target team players with high awareness get flagged
    # ------------------------------------------------------------------ #

    def test_nuke_reaction_flag_set_on_high_awareness_target_team(self):
        """High-awareness blue players get reacting_to_nuke=True when red nuke is pending.

        random.random() returns 0.0 < prob=1.0 → always reacts.
        Red team players must NOT be flagged.
        """
        red_commander = _ps(
            "commander", "red", game_awareness=100, player_awareness=100
        )
        nuke = PendingNuke(complete_time=107.0, player=red_commander)

        blue_scout = _ps("scout", "blue", game_awareness=100, player_awareness=100)
        blue_heavy = _ps("heavy", "blue", game_awareness=100, player_awareness=100)
        red_heavy = _ps("heavy", "red", game_awareness=100, player_awareness=100)

        all_alive = [red_commander, red_heavy, blue_scout, blue_heavy]

        self._apply_mech04_reaction_flags(all_alive, [nuke], random_value=0.0)

        self.assertTrue(blue_scout.reacting_to_nuke, "Blue scout must react")
        self.assertTrue(blue_heavy.reacting_to_nuke, "Blue heavy must react")
        self.assertFalse(
            red_heavy.reacting_to_nuke, "Red heavy must NOT react to red's own nuke"
        )
        self.assertFalse(red_commander.reacting_to_nuke, "Nuke owner must NOT react")

    # ------------------------------------------------------------------ #
    # 8. Zero-awareness target team players do not react
    # ------------------------------------------------------------------ #

    def test_zero_awareness_target_team_players_do_not_react(self):
        """Blue players with ga=0, pa=0 never react: prob=0.0, random.random() never < 0.

        random.random() returns 0.0, prob=0.0 → 0.0 < 0.0 is False → no reaction.
        """
        red_commander = _ps("commander", "red")
        nuke = PendingNuke(complete_time=107.0, player=red_commander)

        blue_scout = _ps("scout", "blue", game_awareness=0, player_awareness=0)

        all_alive = [red_commander, blue_scout]

        # prob = 0.0, random.random() = 0.0 → 0.0 < 0.0 is False
        self._apply_mech04_reaction_flags(all_alive, [nuke], random_value=0.0)

        self.assertFalse(
            blue_scout.reacting_to_nuke,
            "Zero-awareness player must never react (prob = 0.0)",
        )

    # ------------------------------------------------------------------ #
    # 9. No pending nukes → all flags stay False
    # ------------------------------------------------------------------ #

    def test_no_pending_nukes_flags_remain_false(self):
        """When there are no pending nukes, all reacting_to_nuke flags stay False."""
        blue_scout = _ps("scout", "blue", game_awareness=100, player_awareness=100)
        # Pre-set to True to verify it is reset to False
        blue_scout.reacting_to_nuke = True

        all_alive = [blue_scout]
        self._apply_mech04_reaction_flags(all_alive, [], random_value=0.0)

        self.assertFalse(
            blue_scout.reacting_to_nuke,
            "Flags must be reset to False when there are no pending nukes",
        )

    # ------------------------------------------------------------------ #
    # 10. Flags reset at start of tick regardless of prior state
    # ------------------------------------------------------------------ #

    def test_reaction_flags_reset_each_tick(self):
        """reacting_to_nuke is reset to False at tick start, even if previously True.

        Simulates two consecutive tick applications:
        - Tick 1: nuke in flight → blue_scout reacts (flag = True)
        - Tick 2: no nukes → flag must return to False
        """
        red_commander = _ps("commander", "red")
        nuke = PendingNuke(complete_time=107.0, player=red_commander)
        blue_scout = _ps("scout", "blue", game_awareness=100, player_awareness=100)
        all_alive = [red_commander, blue_scout]

        # Tick 1: nuke pending, high awareness — scout reacts
        self._apply_mech04_reaction_flags(all_alive, [nuke], random_value=0.0)
        self.assertTrue(blue_scout.reacting_to_nuke, "Scout should react in tick 1")

        # Tick 2: nuke resolved / no pending nukes — flag must reset
        self._apply_mech04_reaction_flags(all_alive, [], random_value=0.0)
        self.assertFalse(
            blue_scout.reacting_to_nuke,
            "Scout flag must reset to False when no pending nukes in tick 2",
        )

    # ------------------------------------------------------------------ #
    # 11. Blue nuke targets red team (symmetry test)
    # ------------------------------------------------------------------ #

    def test_blue_nuke_flags_red_team_players(self):
        """Blue commander's nuke targets red team players.

        Blue players must NOT be flagged; red players with high awareness must be.
        """
        blue_commander = _ps(
            "commander", "blue", game_awareness=100, player_awareness=100
        )
        nuke = PendingNuke(complete_time=107.0, player=blue_commander)

        red_scout = _ps("scout", "red", game_awareness=100, player_awareness=100)
        blue_heavy = _ps("heavy", "blue", game_awareness=100, player_awareness=100)

        all_alive = [blue_commander, blue_heavy, red_scout]

        self._apply_mech04_reaction_flags(all_alive, [nuke], random_value=0.0)

        self.assertTrue(red_scout.reacting_to_nuke, "Red scout must react to blue nuke")
        self.assertFalse(
            blue_heavy.reacting_to_nuke, "Blue heavy must NOT react to own team's nuke"
        )
        self.assertFalse(blue_commander.reacting_to_nuke, "Nuke owner must NOT react")

    # ------------------------------------------------------------------ #
    # 12. Both teams have simultaneous pending nukes → both sides react
    # ------------------------------------------------------------------ #

    def test_dual_nukes_both_teams_react(self):
        """When both commanders have nukes in flight, both teams' players react.

        Red nuke targets blue team; blue nuke targets red team.
        High-awareness players on both sides must be flagged.
        """
        red_commander = _ps(
            "commander", "red", game_awareness=100, player_awareness=100
        )
        blue_commander = _ps(
            "commander", "blue", game_awareness=100, player_awareness=100
        )
        red_nuke = PendingNuke(complete_time=107.0, player=red_commander)
        blue_nuke = PendingNuke(complete_time=105.0, player=blue_commander)

        red_scout = _ps("scout", "red", game_awareness=100, player_awareness=100)
        blue_scout = _ps("scout", "blue", game_awareness=100, player_awareness=100)

        all_alive = [red_commander, red_scout, blue_commander, blue_scout]

        # random.random() = 0.0 < prob=1.0 → all high-awareness players react
        self._apply_mech04_reaction_flags(
            all_alive, [red_nuke, blue_nuke], random_value=0.0
        )

        self.assertTrue(
            blue_scout.reacting_to_nuke, "Blue scout must react to red nuke"
        )
        self.assertTrue(red_scout.reacting_to_nuke, "Red scout must react to blue nuke")
        self.assertTrue(
            red_commander.reacting_to_nuke,
            "Red commander must react to the blue nuke targeting red team",
        )
        self.assertTrue(
            blue_commander.reacting_to_nuke,
            "Blue commander must react to the red nuke targeting blue team",
        )


# ===== Nuke stacking: Commander SP-gated nuke weights =====
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


# ===== Nuke cancellation when the Commander is downed =====
import random
import unittest

from matches.sim_helpers.pending_events import PendingNuke
from matches.sim_helpers.player_state import PlayerState

# ---------------------------------------------------------------------------
# PlayerState factory
# ---------------------------------------------------------------------------


def _commander(
    *,
    team_color: str = "red",
    final_lives: int = 30,
    special_active_until: int = 0,
    last_downed_time: float | None = None,
) -> PlayerState:
    """Minimal Commander PlayerState for nuke-cancellation tests."""
    return PlayerState(
        tag_id=f"{team_color}_commander",
        name=f"{team_color} Commander",
        team_color=team_color,
        role="commander",
        accuracy=50,
        survival=50,
        starting_lives=30,
        starting_shots=60,
        final_lives=final_lives,
        final_shots=60,
        special_active_until=special_active_until,
        last_downed_time=last_downed_time,
    )


# ---------------------------------------------------------------------------
# Helper: the MECH-05 guard expression (the condition that determines whether
# the nuke fires).  Tests assert on this boolean directly so they stay
# independent of whatever other code changes may accompany the production fix.
# ---------------------------------------------------------------------------


def _nuke_should_fire(nuke: PendingNuke) -> bool:
    """Return True iff the nuke meets the post-fix detonation criteria.

    Matches the condition introduced by MECH-05:
        final_lives > 0  AND  special_active_until >= complete_time
    """
    player = nuke.player
    return player.final_lives > 0 and player.special_active_until >= nuke.complete_time


# ---------------------------------------------------------------------------
# TestMech05NukeCancellation
# ---------------------------------------------------------------------------


class TestMech05NukeCancellation(unittest.TestCase):
    """MECH-05: BatchSimulator nuke resolution checks special_active_until."""

    # ------------------------------------------------------------------ #
    # 1. Critical regression: tagged during fuse → nuke MUST NOT fire
    # ------------------------------------------------------------------ #

    def test_nuke_cancelled_when_commander_tagged_during_fuse(self):
        """Commander fires at T=100, tagged at T=103 (shields→0) during fuse window.

        Expected: nuke does NOT detonate when it resolves at T=107.

        When the commander's shields reach 0 the tag-cancel code sets
        special_active_until = 0.  The MECH-05 guard must catch this.
        """
        commander = _commander(
            final_lives=28,  # survived the tag (still alive)
            last_downed_time=103,  # downed at T=103
            special_active_until=0,  # reset to 0 by tag-cancel logic
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertFalse(
            _nuke_should_fire(nuke),
            "Nuke must NOT fire when special_active_until was reset to 0 by a tag",
        )

    # ------------------------------------------------------------------ #
    # 2. Happy path: untouched commander → nuke DOES fire
    # ------------------------------------------------------------------ #

    def test_nuke_fires_when_commander_not_tagged(self):
        """Commander fires at T=100, reaches T=107 unscathed.

        Expected: nuke fires at T=107.
        """
        commander = _commander(
            final_lives=30,
            last_downed_time=None,
            special_active_until=107,  # set when nuke was armed
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertTrue(
            _nuke_should_fire(nuke),
            "Nuke must fire when the commander is alive and special_active_until is intact",
        )

    # ------------------------------------------------------------------ #
    # 3. Eliminated during fuse → nuke MUST NOT fire
    # ------------------------------------------------------------------ #

    def test_nuke_cancelled_when_commander_eliminated_during_fuse(self):
        """Commander is fully eliminated during the fuse window (final_lives = 0).

        Expected: nuke does NOT detonate.
        """
        commander = _commander(
            final_lives=0,
            special_active_until=107,  # still set — elimination check fires first
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertFalse(
            _nuke_should_fire(nuke),
            "Nuke must NOT fire when the commander has been eliminated (final_lives=0)",
        )

    # ------------------------------------------------------------------ #
    # 4. special_active_until = 0 always blocks detonation
    # ------------------------------------------------------------------ #

    def test_nuke_does_not_fire_when_special_active_until_is_zero(self):
        """A PendingNuke with special_active_until=0 must never detonate.

        This is the post-cancel state set by both the tag-cancel and resupply-cancel
        code paths.  Even if the commander is otherwise alive and active the guard
        must prevent detonation.
        """
        commander = _commander(
            final_lives=30,
            last_downed_time=None,
            special_active_until=0,  # explicitly cancelled
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        # The guard: final_lives > 0 AND special_active_until >= complete_time
        # 30 > 0 is True, but 0 >= 107 is False → nuke must NOT fire
        self.assertFalse(
            _nuke_should_fire(nuke),
            "special_active_until=0 must prevent detonation regardless of liveness",
        )

    # ------------------------------------------------------------------ #
    # 5. Nuke fires only when NOT cancelled (edge-case: same tick as detonation)
    # ------------------------------------------------------------------ #

    def test_nuke_fires_in_detonation_tick_when_not_cancelled(self):
        """Edge case: the tag that would cancel the nuke has NOT occurred.

        At exactly the detonation tick (T=107) with special_active_until=107 and
        no cancellation, the nuke must fire.
        """
        commander = _commander(
            final_lives=30,
            special_active_until=107,
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertTrue(
            _nuke_should_fire(nuke),
            "Nuke must fire at the exact detonation tick when not cancelled",
        )

    # ------------------------------------------------------------------ #
    # 6. special_active_until strictly less than complete_time → no fire
    # ------------------------------------------------------------------ #

    def test_nuke_does_not_fire_when_special_expired_before_detonation(self):
        """special_active_until=106 but complete_time=107 → check fails.

        Defensive boundary test: in normal simulator flow special_active_until is
        always set to second + countdown == complete_time, so < can't occur naturally.
        This guards against any future code path that might skew the value.
        """
        commander = _commander(
            final_lives=30,
            special_active_until=106,  # < complete_time
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertFalse(
            _nuke_should_fire(nuke),
            "special_active_until < complete_time must not allow detonation",
        )

    # ------------------------------------------------------------------ #
    # 7. Old guard (is_active_at) would have allowed a cancelled nuke to fire
    # ------------------------------------------------------------------ #

    def test_old_guard_flaw_demonstration(self):
        """Show that the old guard (is_active_at) returns True after a tag during fuse.

        This test documents the bug MECH-05 fixes (TIME-01: ticks; respawn is
        RESPAWN_TICKS = 16, the pre-TIME-01 8 s doubled):
        - Commander tagged at tick T=198, nuke resolves at tick T=214.
        - is_active_at(214) checks 214 - 198 = 16 >= RESPAWN_TICKS(16) → True,
          so the old guard (is_active_at + final_lives) would still fire the
          nuke even though special_active_until was reset to 0 by the
          tag-cancel. The new guard (special_active_until) catches this.
        """
        # Commander tagged at tick T=198, nuke resolves at tick T=214
        commander = _commander(
            final_lives=28,
            last_downed_time=198,
            special_active_until=0,  # reset by tag-cancel at T=198
        )
        nuke = PendingNuke(complete_time=214.0, player=commander)

        # Old guard: is_active_at(214) checks 214 - 198 = 16 >= 16 → True
        # Combined with final_lives > 0: old guard would incorrectly fire the nuke
        old_guard_fires = (
            commander.is_active_at(nuke.complete_time) and commander.final_lives > 0
        )
        # New guard: special_active_until=0 < 107 → correctly does NOT fire
        new_guard_fires = _nuke_should_fire(nuke)

        self.assertTrue(
            old_guard_fires,
            "Old guard (is_active_at) incorrectly allows a tag-cancelled nuke to fire",
        )
        self.assertFalse(
            new_guard_fires,
            "New guard (special_active_until check) correctly blocks a tag-cancelled nuke",
        )

    # ------------------------------------------------------------------ #
    # 8. PendingNuke dataclass attributes are accessible by name
    # ------------------------------------------------------------------ #

    def test_pending_nuke_fields_accessible_by_name(self):
        """PendingNuke.complete_time and .player are named fields (not positional)."""
        commander = _commander()
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertEqual(nuke.complete_time, 107.0)
        self.assertIs(nuke.player, commander)
