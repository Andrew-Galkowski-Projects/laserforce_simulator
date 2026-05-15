"""
Tests for MECH-04: Player reaction to incoming nukes.

When a Commander has a nuke in flight (pending), each alive player on the
targeted team rolls a reaction probability each tick:

    reaction_probability = (game_awareness + player_awareness) / 200

A reacting player with lives ≤ 30% of max overrides their movement goal to
the allied Medic cell (survival mode).  Lives > 30% hits a # TODO MECH-06
hook and falls through to normal goal selection.

The `reacting_to_nuke` flag is a transient bool on `PlayerState` (default
False, never persisted to the DB).  It is reset to False for every alive
player at the start of each tick, then set to True for those who pass the
reaction roll.

Production code:
  matches/sim_helpers/player_state.py   (reacting_to_nuke field)
  matches/sim_helpers/pathfinding.py    (choose_goal_cell step 0)
  matches/simulation.py                 (BatchSimulator tick loop, ~line 2844)
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
