"""
Tests for MECH-06: Player memory system + teamwork/communication stat wiring.

Implementation added:
  - player_memory dict on PlayerState
  - medic_hit_times list on PlayerState
  - score_broadcast_state dict + score_broadcast_next on PlayerState
  - _STALE_THRESHOLD in pathfinding.py: heavy/medic/ammo=60s, scout/commander=15s
  - _cell_from_memory helper in pathfinding.py
  - _known_enemies_from_memory helper in pathfinding.py
  - _apply_teamwork_bias in pathfinding.py
  - _apply_score_broadcast_weights in weights.py
  - Communication broadcast per-tick in both simulators (_broadcast_communication)
  - Nuke activation broadcast (_apply_nuke_activation_broadcast)
  - Medic-under-fire alert (_check_medic_under_fire)

Production code:
  matches/sim_helpers/pathfinding.py   (_cell_from_memory, _known_enemies_from_memory,
                                        _apply_teamwork_bias, _STALE_THRESHOLD)
  matches/sim_helpers/weights.py       (_apply_score_broadcast_weights)
  matches/sim_helpers/player_state.py  (player_memory, medic_hit_times,
                                        score_broadcast_state, score_broadcast_next)
  matches/simulation.py                (_broadcast_communication,
                                        _apply_nuke_activation_broadcast,
                                        _check_medic_under_fire)
"""

import random
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from matches.sim_helpers.pathfinding import (
    _STALE_THRESHOLD,
    _cell_from_memory,
    _known_enemies_from_memory,
    _apply_teamwork_bias,
    choose_goal_cell,
)
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.weights import _apply_score_broadcast_weights

from matches.simulation import (
    _broadcast_communication,
    _update_player_memory,
    _apply_nuke_activation_broadcast,
    _check_medic_under_fire,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MAX_LIVES = {"commander": 30, "heavy": 20, "scout": 30, "medic": 20, "ammo": 20}
_MAX_SHOTS = {"commander": 60, "heavy": 40, "scout": 60, "medic": 30, "ammo": 15}

_ACTION_IDX = {
    "tag_player": 0,
    "change_zone": 1,
    "hide": 2,
    "capture_base": 3,
    "use_special": 4,
    "resupply_ally": 5,
    "missile_player": 6,
    "request_resupply": 7,
}


def _ps(
    role: str,
    team_color: str = "red",
    *,
    lives: int | None = None,
    shots: int | None = None,
    teamwork: int = 50,
    communication: int = 50,
    cell_row: int | None = None,
    cell_col: int | None = None,
    player_memory: dict | None = None,
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
        teamwork=teamwork,
        communication=communication,
        cell_row=cell_row,
        cell_col=cell_col,
        **kwargs,
    )
    if player_memory is not None:
        p.player_memory = player_memory
    return p


def _minimal_ctx(
    *,
    spawn_cells: dict | None = None,
    adj: dict | None = None,
    sight_data: dict | None = None,
    high_los_cells: list | None = None,
    strong_spots: list | None = None,
) -> MapContext:
    """Minimal MapContext for unit tests."""
    return MapContext.from_dict(
        {
            "adj": adj or {},
            "spawn_cells": spawn_cells or {"red": (0, 0), "blue": (9, 9)},
            "zone_data": None,
            "sight_data": sight_data or {},
            "base_sight_data": {},
            "cell_los_counts": {},
            "high_los_cells": high_los_cells or [],
            "strong_spots": strong_spots or [],
        }
    )


def _memory_entry(cell: tuple, timestamp: float, role: str) -> dict:
    return {"cell": cell, "timestamp": timestamp, "role": role}


# ---------------------------------------------------------------------------
# TestStaleThresholdValues — verify the threshold constants
# ---------------------------------------------------------------------------


class TestStaleThresholdValues(unittest.TestCase):
    """The _STALE_THRESHOLD dict has the correct per-role values."""

    def test_heavy_threshold_is_60(self):
        self.assertEqual(_STALE_THRESHOLD["heavy"], 60)

    def test_medic_threshold_is_60(self):
        self.assertEqual(_STALE_THRESHOLD["medic"], 60)

    def test_ammo_threshold_is_60(self):
        self.assertEqual(_STALE_THRESHOLD["ammo"], 60)

    def test_scout_threshold_is_15(self):
        self.assertEqual(_STALE_THRESHOLD["scout"], 15)

    def test_commander_threshold_is_15(self):
        self.assertEqual(_STALE_THRESHOLD["commander"], 15)


# ---------------------------------------------------------------------------
# TestCellFromMemory — unit tests for _cell_from_memory
# ---------------------------------------------------------------------------


class TestCellFromMemory(unittest.TestCase):
    """Tests for pathfinding._cell_from_memory."""

    # ------------------------------------------------------------------ #
    # 1. Returns correct cell for fresh entries by role
    # ------------------------------------------------------------------ #

    def test_heavy_fresh_at_59s_returns_cell(self):
        """Heavy entry 59 s old (age < 60) → cell returned."""
        player = _ps("scout", "red")
        player.player_memory = {"blue_heavy": _memory_entry((3, 4), 0.0, "heavy")}
        result = _cell_from_memory(player, "blue_heavy", 59.0)
        self.assertEqual(result, (3, 4))

    def test_heavy_exactly_60s_returns_cell(self):
        """Heavy entry exactly 60 s old (age == 60, not > 60) → cell returned."""
        player = _ps("scout", "red")
        player.player_memory = {"blue_heavy": _memory_entry((3, 4), 0.0, "heavy")}
        result = _cell_from_memory(player, "blue_heavy", 60.0)
        self.assertEqual(result, (3, 4))

    def test_heavy_at_61s_returns_none(self):
        """Heavy entry 61 s old (age > 60) → None (stale)."""
        player = _ps("scout", "red")
        player.player_memory = {"blue_heavy": _memory_entry((3, 4), 0.0, "heavy")}
        result = _cell_from_memory(player, "blue_heavy", 61.0)
        self.assertIsNone(result)

    def test_scout_fresh_at_14s_returns_cell(self):
        """Scout entry 14 s old (age < 15) → cell returned."""
        player = _ps("scout", "red")
        player.player_memory = {"blue_scout": _memory_entry((5, 6), 10.0, "scout")}
        result = _cell_from_memory(player, "blue_scout", 24.0)
        self.assertEqual(result, (5, 6))

    def test_scout_at_16s_returns_none(self):
        """Scout entry 16 s old (age > 15) → None (stale)."""
        player = _ps("scout", "red")
        player.player_memory = {"blue_scout": _memory_entry((5, 6), 0.0, "scout")}
        result = _cell_from_memory(player, "blue_scout", 16.0)
        self.assertIsNone(result)

    def test_commander_at_16s_returns_none(self):
        """Commander entry 16 s old (age > 15) → None (stale)."""
        player = _ps("scout", "red")
        player.player_memory = {
            "blue_commander": _memory_entry((9, 9), 0.0, "commander")
        }
        result = _cell_from_memory(player, "blue_commander", 16.0)
        self.assertIsNone(result)

    def test_medic_at_59s_returns_cell(self):
        """Medic (as remembered target) 59 s old → cell returned (threshold 60 s)."""
        player = _ps("heavy", "red")
        player.player_memory = {"blue_medic": _memory_entry((2, 3), 0.0, "medic")}
        result = _cell_from_memory(player, "blue_medic", 59.0)
        self.assertEqual(result, (2, 3))

    def test_ammo_at_61s_returns_none(self):
        """Ammo (as remembered target) 61 s old → None (stale, threshold 60 s)."""
        player = _ps("heavy", "red")
        player.player_memory = {"blue_ammo": _memory_entry((1, 1), 0.0, "ammo")}
        result = _cell_from_memory(player, "blue_ammo", 61.0)
        self.assertIsNone(result)

    # ------------------------------------------------------------------ #
    # 2. Missing / empty memory
    # ------------------------------------------------------------------ #

    def test_no_memory_returns_none(self):
        """Player with no player_memory → None."""
        player = _ps("scout", "red")
        player.player_memory = {}
        result = _cell_from_memory(player, "blue_heavy", 10.0)
        self.assertIsNone(result)

    def test_missing_entry_returns_none(self):
        """player_memory exists but target_tag_id not in it → None."""
        player = _ps("scout", "red")
        player.player_memory = {"blue_medic": _memory_entry((2, 2), 0.0, "medic")}
        result = _cell_from_memory(player, "blue_commander", 5.0)
        self.assertIsNone(result)

    def test_entry_with_no_cell_returns_none(self):
        """Memory entry with cell=None → None."""
        player = _ps("scout", "red")
        player.player_memory = {
            "blue_heavy": {"cell": None, "timestamp": 0.0, "role": "heavy"}
        }
        result = _cell_from_memory(player, "blue_heavy", 10.0)
        self.assertIsNone(result)

    # ------------------------------------------------------------------ #
    # 3. Role stored in entry drives threshold — no all_alive scan needed
    # ------------------------------------------------------------------ #

    def test_role_in_entry_drives_staleness_not_caller_role(self):
        """Staleness is driven by the remembered player's role stored in the entry.

        A red Scout remembering a blue Heavy: 59 s old is fresh (Heavy threshold=60 s).
        If staleness were driven by the observer's role (scout=15 s) this would be stale.
        """
        observer = _ps("scout", "red")
        observer.player_memory = {"blue_heavy": _memory_entry((3, 4), 0.0, "heavy")}
        result = _cell_from_memory(observer, "blue_heavy", 59.0)
        self.assertEqual(
            result, (3, 4), "Heavy threshold (60 s) must apply, not Scout's 15 s"
        )

    def test_unknown_role_defaults_to_scout_threshold_15(self):
        """Entry with unknown role defaults to stale quickly (15 s like scout)."""
        player = _ps("heavy", "red")
        player.player_memory = {
            "blue_unknown": {"cell": (1, 1), "timestamp": 0.0, "role": "tank"}
        }
        result_fresh = _cell_from_memory(player, "blue_unknown", 14.0)
        result_stale = _cell_from_memory(player, "blue_unknown", 16.0)
        self.assertEqual(result_fresh, (1, 1))
        self.assertIsNone(result_stale)

    # ------------------------------------------------------------------ #
    # 4. Cell coordinates are returned as ints
    # ------------------------------------------------------------------ #

    def test_cell_returned_as_int_tuple(self):
        """Cell coordinates are cast to int even if stored as floats."""
        player = _ps("scout", "red")
        player.player_memory = {"blue_heavy": _memory_entry((3.0, 4.0), 0.0, "heavy")}
        result = _cell_from_memory(player, "blue_heavy", 10.0)
        self.assertEqual(result, (3, 4))
        self.assertIsInstance(result[0], int)
        self.assertIsInstance(result[1], int)


# ---------------------------------------------------------------------------
# TestKnownEnemiesFromMemory — unit tests for _known_enemies_from_memory
# ---------------------------------------------------------------------------


class TestKnownEnemiesFromMemory(unittest.TestCase):
    """Tests for pathfinding._known_enemies_from_memory."""

    def test_returns_non_stale_enemy_cells(self):
        """Fresh enemy entries are included in the result list."""
        player = _ps("scout", "red")
        player.player_memory = {
            "blue_heavy": _memory_entry((3, 4), 0.0, "heavy"),
        }
        cells = _known_enemies_from_memory(player, "blue", 10.0)
        self.assertIn((3, 4), cells)

    def test_filters_out_allies(self):
        """Entries that do not start with enemy_color are excluded."""
        player = _ps("scout", "red")
        player.player_memory = {
            "red_medic": _memory_entry((1, 1), 0.0, "medic"),  # ally
            "blue_heavy": _memory_entry((3, 4), 0.0, "heavy"),  # enemy
        }
        cells = _known_enemies_from_memory(player, "blue", 10.0)
        self.assertEqual(cells, [(3, 4)])
        self.assertNotIn((1, 1), cells)

    def test_filters_out_stale_enemies(self):
        """Stale enemy entries are excluded."""
        player = _ps("scout", "red")
        player.player_memory = {
            "blue_scout": _memory_entry((5, 6), 0.0, "scout"),  # 16 s old → stale
        }
        cells = _known_enemies_from_memory(player, "blue", 16.0)
        self.assertEqual(cells, [])

    def test_mixed_fresh_and_stale_returns_only_fresh(self):
        """Only non-stale enemy entries are returned."""
        player = _ps("scout", "red")
        player.player_memory = {
            "blue_heavy": _memory_entry((3, 4), 0.0, "heavy"),  # 10 s old → fresh
            "blue_scout": _memory_entry((5, 6), 0.0, "scout"),  # 16 s old → stale
        }
        cells = _known_enemies_from_memory(player, "blue", 16.0)
        self.assertIn((3, 4), cells)
        self.assertNotIn((5, 6), cells)

    def test_empty_memory_returns_empty_list(self):
        """Player with no memory entries returns empty list."""
        player = _ps("scout", "red")
        player.player_memory = {}
        cells = _known_enemies_from_memory(player, "blue", 10.0)
        self.assertEqual(cells, [])

    def test_entry_without_cell_excluded(self):
        """Memory entry with cell=None is not included."""
        player = _ps("scout", "red")
        player.player_memory = {
            "blue_heavy": {"cell": None, "timestamp": 0.0, "role": "heavy"},
        }
        cells = _known_enemies_from_memory(player, "blue", 10.0)
        self.assertEqual(cells, [])

    def test_blue_player_sees_red_enemies(self):
        """Works for a blue observer looking for red enemies."""
        player = _ps("scout", "blue")
        player.player_memory = {
            "red_commander": _memory_entry((9, 9), 0.0, "commander"),
            "blue_medic": _memory_entry((1, 1), 0.0, "medic"),
        }
        cells = _known_enemies_from_memory(player, "red", 10.0)
        self.assertIn((9, 9), cells)
        self.assertNotIn((1, 1), cells)


# ---------------------------------------------------------------------------
# TestApplyTeamworkBias — unit tests for _apply_teamwork_bias
# ---------------------------------------------------------------------------


class TestApplyTeamworkBias(unittest.TestCase):
    """Tests for pathfinding._apply_teamwork_bias."""

    def _ctx_with_sight(self, sight_data: dict, high_los_cells: list) -> MapContext:
        return MapContext.from_dict(
            {
                "adj": {},
                "spawn_cells": {"red": (0, 0), "blue": (9, 9)},
                "zone_data": None,
                "sight_data": sight_data,
                "base_sight_data": {},
                "cell_los_counts": {},
                "high_los_cells": high_los_cells,
                "strong_spots": [],
            }
        )

    def test_teamwork_le_50_returns_original_goal(self):
        """When teamwork <= 50, no bias applied: original goal unchanged."""
        player = _ps("scout", "red", teamwork=50, cell_row=0, cell_col=0)
        ally = _ps("medic", "red", cell_row=5, cell_col=5)
        ctx = self._ctx_with_sight({}, [])

        goal = (7, 7)
        result = _apply_teamwork_bias(player, goal, [ally], 0, 0, ctx)
        self.assertEqual(result, goal)

    def test_teamwork_0_returns_original_goal(self):
        """teamwork=0 (well below threshold) never biases."""
        player = _ps("scout", "red", teamwork=0, cell_row=0, cell_col=0)
        ally = _ps("medic", "red", cell_row=5, cell_col=5)
        ctx = self._ctx_with_sight({}, [(3, 3)])

        goal = (7, 7)
        result = _apply_teamwork_bias(player, goal, [ally], 0, 0, ctx)
        self.assertEqual(result, goal)

    def test_no_allies_alive_returns_original_goal(self):
        """When no living allies with positions, bias is skipped."""
        player = _ps("scout", "red", teamwork=100, cell_row=0, cell_col=0)
        # No allies in all_alive except the player themselves
        ctx = self._ctx_with_sight({}, [(3, 3)])

        goal = (7, 7)
        result = _apply_teamwork_bias(player, goal, [player], 0, 0, ctx)
        self.assertEqual(result, goal)

    def test_goal_already_in_ally_los_returns_original_goal(self):
        """When goal is already visible from an ally, no bias needed — return goal."""
        random.seed(42)
        goal = (5, 5)
        player = _ps("scout", "red", teamwork=100, cell_row=0, cell_col=0)
        ally = _ps("medic", "red", cell_row=3, cell_col=3)

        # ally at (3,3) can see (5,5)
        sight_data = {"3,3": frozenset(["5,5", "4,4"])}
        ctx = self._ctx_with_sight(sight_data, [(5, 5)])

        result = _apply_teamwork_bias(player, goal, [ally], 0, 0, ctx)
        self.assertEqual(result, goal, "Goal already in ally LOS: no bias should occur")

    def test_teamwork_gt_50_with_teamwork_cell_can_return_different_goal(self):
        """teamwork=100 with a valid teamwork cell may return that cell instead.

        With random.seed(42), random.random() returns a value < 1.0 (certain),
        so the teamwork cell should be chosen.
        """
        random.seed(0)  # seed so random.random() < 1.0 (probability = 1.0)
        player = _ps("scout", "red", teamwork=100, cell_row=0, cell_col=0)
        ally = _ps("medic", "red", cell_row=3, cell_col=3)

        goal = (8, 8)  # not in ally LOS
        # Ally at (3,3) can see (2,2) which is a high-LOS cell
        sight_data = {"3,3": frozenset(["2,2"])}
        ctx = self._ctx_with_sight(sight_data, [(2, 2)])

        # With teamwork=100, prob=1.0, random.random() will always be < 1.0
        with patch("matches.sim_helpers.pathfinding.random.random", return_value=0.0):
            result = _apply_teamwork_bias(player, goal, [player, ally], 0, 0, ctx)
        self.assertEqual(
            result, (2, 2), "teamwork=100 should redirect to the teamwork cell"
        )

    def test_no_high_los_cells_returns_original_goal(self):
        """When there are no high-LOS cells, the original goal is unchanged."""
        player = _ps("scout", "red", teamwork=100, cell_row=0, cell_col=0)
        ally = _ps("medic", "red", cell_row=3, cell_col=3)
        sight_data = {"3,3": frozenset(["5,5"])}
        ctx = self._ctx_with_sight(sight_data, [])  # no high-LOS cells

        goal = (7, 7)
        result = _apply_teamwork_bias(player, goal, [player, ally], 0, 0, ctx)
        self.assertEqual(result, goal)

    def test_none_goal_with_teamwork_can_still_bias(self):
        """When goal is None, teamwork bias can return a teamwork cell."""
        player = _ps("scout", "red", teamwork=100, cell_row=0, cell_col=0)
        ally = _ps("medic", "red", cell_row=3, cell_col=3)

        sight_data = {"3,3": frozenset(["2,2"])}
        ctx = self._ctx_with_sight(sight_data, [(2, 2)])

        with patch("matches.sim_helpers.pathfinding.random.random", return_value=0.0):
            result = _apply_teamwork_bias(player, None, [player, ally], 0, 0, ctx)
        self.assertEqual(result, (2, 2))


# ---------------------------------------------------------------------------
# TestApplyScoreBroadcastWeights — unit tests for _apply_score_broadcast_weights
# ---------------------------------------------------------------------------


class TestApplyScoreBroadcastWeights(unittest.TestCase):
    """Tests for weights._apply_score_broadcast_weights."""

    def _weights(self) -> list:
        """Baseline weight array: [70, 30, 0, 0, 0, 0, 0, 0]."""
        return [70, 30, 0, 0, 0, 0, 0, 0]

    def test_no_state_no_weight_change(self):
        """score_broadcast_state not set → weights unchanged."""
        player = _ps("scout", "red")
        player.score_broadcast_state = {}  # falsy empty dict
        w = self._weights()
        _apply_score_broadcast_weights(w, _ACTION_IDX, player, [player], 100.0)
        self.assertEqual(w, [70, 30, 0, 0, 0, 0, 0, 0])

    def test_losing_team_tag_player_increases_by_10(self):
        """Losing team: tag_player += 10."""
        player = _ps("scout", "red")
        player.score_broadcast_state = {"winning_team": "blue", "timestamp": 180.0}
        w = self._weights()
        _apply_score_broadcast_weights(w, _ACTION_IDX, player, [player], 200.0)
        self.assertEqual(w[_ACTION_IDX["tag_player"]], 80)

    def test_losing_team_change_zone_decreases(self):
        """Losing team: change_zone -= 10 (clamped >= 0)."""
        player = _ps("scout", "red")
        player.score_broadcast_state = {"winning_team": "blue", "timestamp": 180.0}
        w = self._weights()
        _apply_score_broadcast_weights(w, _ACTION_IDX, player, [player], 200.0)
        self.assertEqual(w[_ACTION_IDX["change_zone"]], 20)

    def test_losing_team_hide_decreases_clamped_at_zero(self):
        """Losing team: hide -= 10, clamped at 0 (starts at 0 so stays 0)."""
        player = _ps("scout", "red")
        player.score_broadcast_state = {"winning_team": "blue", "timestamp": 180.0}
        w = self._weights()
        # hide starts at 0; 0 - 10 = -10 but clamped to 0
        _apply_score_broadcast_weights(w, _ACTION_IDX, player, [player], 200.0)
        self.assertEqual(w[_ACTION_IDX["hide"]], 0)

    def test_winning_low_lives_medic_dead_hide_increases(self):
        """Winning + low lives + medic dead → hide += 20."""
        max_l = _MAX_LIVES["scout"]
        player = _ps("scout", "red", lives=int(max_l * 0.3))
        player.score_broadcast_state = {"winning_team": "red", "timestamp": 180.0}
        w = self._weights()
        # No medic in all_alive
        _apply_score_broadcast_weights(w, _ACTION_IDX, player, [player], 200.0)
        self.assertEqual(w[_ACTION_IDX["hide"]], 20)

    def test_winning_low_lives_medic_dead_tag_player_decreases(self):
        """Winning + low lives + medic dead → tag_player -= 20."""
        max_l = _MAX_LIVES["scout"]
        player = _ps("scout", "red", lives=int(max_l * 0.3))
        player.score_broadcast_state = {"winning_team": "red", "timestamp": 180.0}
        w = self._weights()
        _apply_score_broadcast_weights(w, _ACTION_IDX, player, [player], 200.0)
        self.assertEqual(w[_ACTION_IDX["tag_player"]], 50)

    def test_winning_low_lives_medic_alive_no_weight_change(self):
        """Winning + low lives + medic alive → no weight change (handled in pathfinding)."""
        max_l = _MAX_LIVES["scout"]
        scout = _ps("scout", "red", lives=int(max_l * 0.3))
        scout.score_broadcast_state = {"winning_team": "red", "timestamp": 180.0}
        medic = _ps("medic", "red")  # medic alive
        w = self._weights()
        _apply_score_broadcast_weights(w, _ACTION_IDX, scout, [scout, medic], 200.0)
        # No weight change when medic is alive (pathfinding handles it)
        self.assertEqual(w, [70, 30, 0, 0, 0, 0, 0, 0])

    def test_winning_not_low_lives_no_weight_change(self):
        """Winning but not low lives → no weight change at all."""
        player = _ps("scout", "red", lives=_MAX_LIVES["scout"])  # full lives
        player.score_broadcast_state = {"winning_team": "red", "timestamp": 180.0}
        w = self._weights()
        _apply_score_broadcast_weights(w, _ACTION_IDX, player, [player], 200.0)
        self.assertEqual(w, [70, 30, 0, 0, 0, 0, 0, 0])

    def test_tied_team_no_weight_change(self):
        """winning_team='tied' → no bias (neither team is losing)."""
        player = _ps("scout", "red")
        player.score_broadcast_state = {"winning_team": "tied", "timestamp": 180.0}
        w = self._weights()
        _apply_score_broadcast_weights(w, _ACTION_IDX, player, [player], 200.0)
        self.assertEqual(w, [70, 30, 0, 0, 0, 0, 0, 0])


# ---------------------------------------------------------------------------
# TestNukeActivationBroadcast — unit tests for _apply_nuke_activation_broadcast
# ---------------------------------------------------------------------------


class TestNukeActivationBroadcast(unittest.TestCase):
    """Tests for simulation._apply_nuke_activation_broadcast."""

    def test_enemy_players_receive_commander_cell_in_memory(self):
        """After nuke fires, the targeted team players learn the Commander's cell."""
        commander = _ps("commander", "red", cell_row=5, cell_col=7)

        blue_scout = _ps("scout", "blue")
        blue_heavy = _ps("heavy", "blue")
        target_team = [blue_scout, blue_heavy]

        _apply_nuke_activation_broadcast(commander, target_team, 120.0)

        cmd_tag = "red_commander"
        self.assertIn(cmd_tag, blue_scout.player_memory)
        self.assertEqual(blue_scout.player_memory[cmd_tag]["cell"], (5, 7))

    def test_enemy_memory_contains_correct_timestamp(self):
        """Memory entry's timestamp matches the second at which the nuke fired."""
        commander = _ps("commander", "red", cell_row=5, cell_col=7)
        blue_scout = _ps("scout", "blue")
        _apply_nuke_activation_broadcast(commander, [blue_scout], 300.0)

        entry = blue_scout.player_memory["red_commander"]
        self.assertEqual(entry["timestamp"], 300.0)

    def test_enemy_memory_entry_has_commander_role(self):
        """Memory entry's role is 'commander'."""
        commander = _ps("commander", "red", cell_row=5, cell_col=7)
        blue_scout = _ps("scout", "blue")
        _apply_nuke_activation_broadcast(commander, [blue_scout], 300.0)

        entry = blue_scout.player_memory["red_commander"]
        self.assertEqual(entry["role"], "commander")

    def test_dead_enemy_players_are_not_updated(self):
        """Players with final_lives=0 are skipped."""
        commander = _ps("commander", "red", cell_row=5, cell_col=7)
        blue_scout = _ps("scout", "blue", lives=0)
        _apply_nuke_activation_broadcast(commander, [blue_scout], 300.0)

        self.assertEqual(blue_scout.player_memory, {})

    def test_commander_with_no_cell_skips_broadcast(self):
        """Commander without a cell_row does nothing (graceful no-op)."""
        commander = _ps("commander", "red")
        # cell_row remains None by default
        blue_scout = _ps("scout", "blue")
        _apply_nuke_activation_broadcast(commander, [blue_scout], 300.0)

        self.assertEqual(blue_scout.player_memory, {})

    def test_ally_players_are_not_updated(self):
        """Ally players in the target_team list with wrong team are still updated.

        _apply_nuke_activation_broadcast operates on whoever is in target_team_players;
        the caller is responsible for passing only enemy-team players.  This test
        verifies that if a red player is accidentally included they also get updated.
        But typically allies should NOT be in target_team_players.
        """
        commander = _ps("commander", "red", cell_row=5, cell_col=7)
        red_scout = _ps("scout", "red")  # ally accidentally in the list
        _apply_nuke_activation_broadcast(commander, [red_scout], 300.0)

        # The function doesn't filter by team; it updates everyone in the list.
        # The test documents this behavior (caller is responsible for filtering).
        self.assertIn("red_commander", red_scout.player_memory)

    def test_broadcast_only_updates_enemy_team_when_called_correctly(self):
        """When called with only enemy-team targets, only enemies are updated."""
        commander = _ps("commander", "red", cell_row=5, cell_col=7)
        red_ally = _ps("heavy", "red")  # NOT passed to the function
        blue_enemy = _ps("scout", "blue")

        _apply_nuke_activation_broadcast(commander, [blue_enemy], 300.0)

        self.assertIn("red_commander", blue_enemy.player_memory)
        self.assertEqual(red_ally.player_memory, {})


# ---------------------------------------------------------------------------
# TestMedicUnderFireAlert — unit tests for _check_medic_under_fire
# ---------------------------------------------------------------------------


class TestMedicUnderFireAlert(unittest.TestCase):
    """Tests for simulation._check_medic_under_fire."""

    def test_first_hit_no_broadcast(self):
        """Only 1 hit recorded → no alert broadcast to allies."""
        medic = _ps("medic", "red", cell_row=3, cell_col=3)
        ally = _ps("heavy", "red")

        _check_medic_under_fire(medic, [medic, ally], 100.0)

        self.assertEqual(ally.player_memory, {}, "Single hit must not trigger alert")

    def test_second_hit_within_12s_triggers_alert(self):
        """Two hits within 12 s → all alive allies learn medic's cell."""
        medic = _ps("medic", "red", cell_row=3, cell_col=3)
        ally = _ps("heavy", "red")

        _check_medic_under_fire(medic, [medic, ally], 100.0)  # hit 1
        _check_medic_under_fire(medic, [medic, ally], 108.0)  # hit 2 (8 s later)

        self.assertIn(
            "red_medic", ally.player_memory, "Two hits in 12 s must alert allies"
        )

    def test_alert_contains_correct_cell(self):
        """Alert updates allies' memory with the medic's current cell."""
        medic = _ps("medic", "red", cell_row=3, cell_col=3)
        ally = _ps("heavy", "red")

        _check_medic_under_fire(medic, [medic, ally], 100.0)
        _check_medic_under_fire(medic, [medic, ally], 108.0)

        entry = ally.player_memory.get("red_medic", {})
        self.assertEqual(entry.get("cell"), (3, 3))

    def test_hits_older_than_12s_trimmed_no_alert(self):
        """Two hits 15 s apart: first is trimmed before second is evaluated → no alert."""
        medic = _ps("medic", "red", cell_row=3, cell_col=3)
        ally = _ps("heavy", "red")

        _check_medic_under_fire(medic, [medic, ally], 100.0)  # hit 1 at t=100
        _check_medic_under_fire(
            medic, [medic, ally], 115.0
        )  # hit 2 at t=115 (15 s later)

        # At t=115, hit at t=100 is 15 s old → age=15 > 12 → trimmed.
        # Only 1 hit remains, so no alert.
        self.assertEqual(
            ally.player_memory, {}, "Hits > 12 s apart must not trigger alert"
        )

    def test_medic_without_cell_no_broadcast(self):
        """When medic has no cell_row/cell_col → no broadcast (graceful no-op)."""
        medic = _ps("medic", "red")  # cell_row=None, cell_col=None
        ally = _ps("heavy", "red")

        _check_medic_under_fire(medic, [medic, ally], 100.0)
        _check_medic_under_fire(medic, [medic, ally], 108.0)

        self.assertEqual(
            ally.player_memory, {}, "Medic with no cell must not broadcast"
        )

    def test_medic_itself_not_updated(self):
        """The medic itself is excluded from the alert broadcast."""
        medic = _ps("medic", "red", cell_row=3, cell_col=3)
        ally = _ps("heavy", "red")

        _check_medic_under_fire(medic, [medic, ally], 100.0)
        _check_medic_under_fire(medic, [medic, ally], 108.0)

        self.assertNotIn(
            "red_medic", medic.player_memory, "Medic must not update its own memory"
        )

    def test_dead_ally_not_updated(self):
        """Allies with final_lives=0 are skipped."""
        medic = _ps("medic", "red", cell_row=3, cell_col=3)
        dead_ally = _ps("heavy", "red", lives=0)

        _check_medic_under_fire(medic, [medic, dead_ally], 100.0)
        _check_medic_under_fire(medic, [medic, dead_ally], 108.0)

        self.assertEqual(dead_ally.player_memory, {})

    def test_alert_timestamp_is_current_second(self):
        """Memory entry timestamp reflects the second of the second hit."""
        medic = _ps("medic", "red", cell_row=3, cell_col=3)
        ally = _ps("heavy", "red")

        _check_medic_under_fire(medic, [medic, ally], 100.0)
        _check_medic_under_fire(medic, [medic, ally], 108.0)

        entry = ally.player_memory.get("red_medic", {})
        self.assertEqual(entry.get("timestamp"), 108.0)

    def test_three_hits_within_12s_still_alerts(self):
        """Three hits within 12 s → alert fires (>= 2 condition)."""
        medic = _ps("medic", "red", cell_row=3, cell_col=3)
        ally = _ps("scout", "red")

        _check_medic_under_fire(medic, [medic, ally], 100.0)
        _check_medic_under_fire(medic, [medic, ally], 105.0)
        _check_medic_under_fire(medic, [medic, ally], 109.0)

        self.assertIn("red_medic", ally.player_memory)


# ---------------------------------------------------------------------------
# TestMech04NukeReactionWithMemory
# ---------------------------------------------------------------------------


class TestMech04NukeReactionWithMemory(unittest.TestCase):
    """When reacting_to_nuke=True and lives > 30%, fresh Commander memory → goal is that cell."""

    def test_reacting_with_memory_of_enemy_commander_returns_commander_cell(self):
        """reacting_to_nuke=True, lives > 30%, enemy Commander in fresh memory → goal is commander cell."""
        scout = _ps("scout", "red", lives=_MAX_LIVES["scout"], cell_row=0, cell_col=0)
        scout.reacting_to_nuke = True
        # Store fresh memory of blue commander at (9, 9)
        scout.player_memory = {
            "blue_commander": _memory_entry((9, 9), 500.0, "commander")
        }

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        ctx = _minimal_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(scout, [scout], spawn_cells, ctx, second=505.0)
        self.assertEqual(
            goal,
            (9, 9),
            "Fresh commander memory must direct nuke-reacting scout to commander cell",
        )

    def test_reacting_with_stale_commander_memory_falls_through(self):
        """reacting_to_nuke=True, lives > 30%, enemy Commander memory stale → falls through to normal behavior."""
        scout = _ps("scout", "red", lives=_MAX_LIVES["scout"], cell_row=0, cell_col=0)
        scout.reacting_to_nuke = True
        # Commander memory is 20 s old → stale (commander threshold = 15 s)
        scout.player_memory = {
            "blue_commander": _memory_entry((9, 9), 0.0, "commander")
        }

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        ctx = _minimal_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(scout, [scout], spawn_cells, ctx, second=20.0)
        # Goal should NOT be (9,9) from stale memory; falls through to default (enemy base)
        # Default enemy base is also (9,9) in this ctx... let's use different coords
        spawn_cells2 = {"red": (0, 0), "blue": (5, 5)}
        ctx2 = _minimal_ctx(spawn_cells=spawn_cells2)
        scout2 = _ps("scout", "red", lives=_MAX_LIVES["scout"], cell_row=0, cell_col=0)
        scout2.reacting_to_nuke = True
        scout2.player_memory = {
            "blue_commander": _memory_entry((9, 9), 0.0, "commander")
        }

        goal2 = choose_goal_cell(scout2, [scout2], spawn_cells2, ctx2, second=20.0)
        self.assertNotEqual(
            goal2, (9, 9), "Stale commander memory must not redirect goal"
        )

    def test_reacting_without_commander_in_memory_falls_through(self):
        """reacting_to_nuke=True, lives > 30%, no commander in memory → falls through."""
        scout = _ps("scout", "red", lives=_MAX_LIVES["scout"], cell_row=0, cell_col=0)
        scout.reacting_to_nuke = True
        scout.player_memory = {}  # empty — no commander known

        spawn_cells = {"red": (0, 0), "blue": (5, 5)}
        ctx = _minimal_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(scout, [scout], spawn_cells, ctx, second=100.0)
        # No commander in memory → falls through to default (enemy base at (5,5))
        self.assertEqual(goal, (5, 5), "No commander in memory → default to enemy base")


# ---------------------------------------------------------------------------
# TestCommunicationBroadcast — unit tests for _broadcast_communication
# ---------------------------------------------------------------------------


class TestCommunicationBroadcast(unittest.TestCase):
    """Tests for simulation._broadcast_communication."""

    def _ctx_with_zone_data(self, rows: int, cols: int) -> MapContext:
        """MapContext with a rows×cols zone_data grid of 1s (all passable)."""
        zone_data = [[1] * cols for _ in range(rows)]
        return MapContext.from_dict(
            {
                "adj": {},
                "spawn_cells": {"red": (0, 0), "blue": (rows - 1, cols - 1)},
                "zone_data": zone_data,
                "sight_data": {},
                "base_sight_data": {},
                "cell_los_counts": {},
                "high_los_cells": [],
                "strong_spots": [],
            }
        )

    def test_communication_0_never_broadcasts(self):
        """communication=0 → random roll not even attempted; ally never updated."""
        actor = _ps("scout", "red", communication=0, cell_row=0, cell_col=0)
        actor.player_memory = {"blue_heavy": _memory_entry((3, 4), 10.0, "heavy")}
        ally = _ps("medic", "red", cell_row=1, cell_col=1)

        # Even with random.random() returning 0.0 (always below threshold), if
        # communication == 0 the function returns immediately.
        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [ally], None, 20.0)

        self.assertEqual(ally.player_memory, {}, "communication=0 must never broadcast")

    def test_communication_100_always_broadcasts(self):
        """communication=100 → random.random()*100 < 100 always True; ally always updated."""
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {"blue_heavy": _memory_entry((3, 4), 10.0, "heavy")}
        ally = _ps("medic", "red", cell_row=1, cell_col=1)

        # random.random() returns 0.0 → 0.0 * 100 = 0.0 < 100 → True
        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, ally], None, 20.0)

        self.assertIn("blue_heavy", ally.player_memory)

    def test_broadcast_range_within_half_diagonal_ally_updated(self):
        """Ally within Euclidean half-diagonal of 10×10 map (~7.07) receives update."""
        # 10×10 map → diagonal = sqrt(200) ≈ 14.14 → half = ~7.07
        ctx = self._ctx_with_zone_data(10, 10)
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {"blue_heavy": _memory_entry((5, 5), 10.0, "heavy")}
        # Ally at (5, 0): dist = sqrt(25) = 5.0 < 7.07
        ally_near = _ps("medic", "red", cell_row=5, cell_col=0)

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, ally_near], ctx, 20.0)

        self.assertIn(
            "blue_heavy",
            ally_near.player_memory,
            "Ally within range must receive update",
        )

    def test_broadcast_range_outside_half_diagonal_ally_not_updated(self):
        """Ally beyond Euclidean half-diagonal of the map does NOT receive update."""
        # 10×10 map → half-diagonal ≈ 7.07
        ctx = self._ctx_with_zone_data(10, 10)
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {"blue_heavy": _memory_entry((5, 5), 10.0, "heavy")}
        # Ally at (9, 9): dist = sqrt(81+81) = sqrt(162) ≈ 12.7 > 7.07
        ally_far = _ps("medic", "red", cell_row=9, cell_col=9)

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, ally_far], ctx, 20.0)

        self.assertEqual(
            ally_far.player_memory, {}, "Ally beyond range must NOT receive update"
        )

    def test_broadcast_shares_only_enemy_entries(self):
        """Only enemy memory entries are shared; own-team entries are not propagated."""
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {
            "blue_heavy": _memory_entry((3, 4), 10.0, "heavy"),  # enemy
            "red_medic": _memory_entry(
                (1, 1), 10.0, "medic"
            ),  # ally — should NOT be shared
        }
        ally = _ps("medic", "red", cell_row=1, cell_col=1)

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, ally], None, 20.0)

        self.assertIn("blue_heavy", ally.player_memory, "Enemy entry must be shared")
        self.assertNotIn(
            "red_medic", ally.player_memory, "Ally entry must NOT be shared"
        )

    def test_broadcast_does_not_update_enemies(self):
        """Enemy players in all_alive are skipped (team filter)."""
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {"blue_heavy": _memory_entry((3, 4), 10.0, "heavy")}
        blue_enemy = _ps("heavy", "blue", cell_row=1, cell_col=1)

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, blue_enemy], None, 20.0)

        self.assertEqual(
            blue_enemy.player_memory, {}, "Enemy players must not receive broadcasts"
        )

    def test_broadcast_updates_newer_entries_only(self):
        """Ally's existing memory is only overwritten if actor's entry is newer."""
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {
            "blue_heavy": _memory_entry((9, 9), 5.0, "heavy")
        }  # older
        ally = _ps("medic", "red", cell_row=1, cell_col=1)
        ally.player_memory = {
            "blue_heavy": _memory_entry((3, 3), 10.0, "heavy")
        }  # newer

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, ally], None, 20.0)

        # Ally's entry at timestamp=10 is newer than actor's timestamp=5 → not overwritten
        self.assertEqual(ally.player_memory["blue_heavy"]["cell"], (3, 3))

    def test_broadcast_actor_is_not_self_updated(self):
        """Actor is excluded from the ally broadcast loop (ally is actor check)."""
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {"blue_heavy": _memory_entry((3, 4), 10.0, "heavy")}
        initial_memory_copy = dict(actor.player_memory)

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor], None, 20.0)

        # Memory unchanged (actor skips itself)
        self.assertEqual(actor.player_memory, initial_memory_copy)

    def test_broadcast_shares_only_one_entry(self):
        """Only the single highest-priority enemy entry is shared per call."""
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {
            "blue_scout": _memory_entry((1, 1), 10.0, "scout"),
            "blue_heavy": _memory_entry((2, 2), 10.0, "heavy"),
        }
        ally = _ps("medic", "red", cell_row=0, cell_col=1)

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, ally], None, 20.0)

        # Only blue_heavy (highest priority) should be transferred
        self.assertIn("blue_heavy", ally.player_memory)
        self.assertNotIn("blue_scout", ally.player_memory)

    def test_broadcast_priority_heavy_beats_commander(self):
        """Heavy outranks Commander in broadcast priority."""
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {
            "blue_commander": _memory_entry((1, 1), 10.0, "commander"),
            "blue_heavy": _memory_entry((2, 2), 10.0, "heavy"),
        }
        ally = _ps("medic", "red", cell_row=0, cell_col=1)

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, ally], None, 20.0)

        self.assertIn("blue_heavy", ally.player_memory)
        self.assertNotIn("blue_commander", ally.player_memory)

    def test_broadcast_priority_commander_beats_medic(self):
        """Commander outranks Medic in broadcast priority."""
        actor = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {
            "blue_medic": _memory_entry((1, 1), 10.0, "medic"),
            "blue_commander": _memory_entry((2, 2), 10.0, "commander"),
        }
        ally = _ps("ammo", "red", cell_row=0, cell_col=1)

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, ally], None, 20.0)

        self.assertIn("blue_commander", ally.player_memory)
        self.assertNotIn("blue_medic", ally.player_memory)

    def test_broadcast_priority_scout_is_lowest(self):
        """Scout is broadcast only when no higher-priority enemy entry exists."""
        actor = _ps("heavy", "red", communication=100, cell_row=0, cell_col=0)
        actor.player_memory = {
            "blue_scout": _memory_entry((5, 5), 10.0, "scout"),
        }
        ally = _ps("medic", "red", cell_row=0, cell_col=1)

        with patch("random.random", return_value=0.0):
            _broadcast_communication(actor, [actor, ally], None, 20.0)

        self.assertIn("blue_scout", ally.player_memory)


# ---------------------------------------------------------------------------
# TestPlayerStateMemoryFields — verify PlayerState field defaults
# ---------------------------------------------------------------------------


class TestPlayerStateMemoryFields(unittest.TestCase):
    """Verify MECH-06 fields exist on PlayerState with correct defaults."""

    def test_player_memory_defaults_to_empty_dict(self):
        player = _ps("scout", "red")
        self.assertEqual(player.player_memory, {})

    def test_player_memory_is_independent_per_instance(self):
        """Each PlayerState has its own player_memory dict (no shared mutable default)."""
        p1 = _ps("scout", "red")
        p2 = _ps("heavy", "blue")
        p1.player_memory["blue_scout"] = _memory_entry((1, 1), 0.0, "scout")
        self.assertEqual(p2.player_memory, {})

    def test_medic_hit_times_defaults_to_empty_list(self):
        player = _ps("medic", "red")
        self.assertEqual(player.medic_hit_times, [])

    def test_medic_hit_times_is_independent_per_instance(self):
        """Each PlayerState has its own medic_hit_times list."""
        p1 = _ps("medic", "red")
        p2 = _ps("medic", "blue")
        p1.medic_hit_times.append(100.0)
        self.assertEqual(p2.medic_hit_times, [])

    def test_score_broadcast_state_defaults_to_empty_dict(self):
        player = _ps("scout", "red")
        self.assertEqual(player.score_broadcast_state, {})

    def test_score_broadcast_next_defaults_to_180(self):
        player = _ps("scout", "red")
        self.assertEqual(player.score_broadcast_next, 180)


# ---------------------------------------------------------------------------
# TestUpdatePlayerMemory — unit tests for _update_player_memory
# ---------------------------------------------------------------------------


class TestUpdatePlayerMemory(unittest.TestCase):
    """Tests for simulation._update_player_memory return value and status field."""

    def _active_player(self, role: str, team: str, row: int, col: int) -> PlayerState:
        p = _ps(role, team, cell_row=row, cell_col=col)
        p.last_downed_time = None
        return p

    def _downed_player(self, role: str, team: str, row: int, col: int, second: float) -> PlayerState:
        """Player downed at `second` — is_taggable_at(second) is False."""
        p = _ps(role, team, cell_row=row, cell_col=col)
        p.last_downed_time = second
        return p

    def _reset_window_player(self, role: str, team: str, row: int, col: int, second: float) -> PlayerState:
        """Player downed 5 s ago — taggable but not active."""
        p = _ps(role, team, cell_row=row, cell_col=col)
        p.last_downed_time = second - 5
        return p

    # ------------------------------------------------------------------
    # Return value: True when new info, False when same cell+status
    # ------------------------------------------------------------------

    def test_new_entry_returns_true(self):
        """First observation of an enemy → returns True."""
        observer = _ps("scout", "red")
        enemy = self._active_player("heavy", "blue", 3, 4)
        result = _update_player_memory(observer, [enemy], 10.0)
        self.assertTrue(result, "First observation of a player must return True")

    def test_same_cell_same_status_returns_false(self):
        """Seeing the same active player at the same cell again → returns False."""
        observer = _ps("scout", "red")
        enemy = self._active_player("heavy", "blue", 3, 4)
        _update_player_memory(observer, [enemy], 10.0)
        result = _update_player_memory(observer, [enemy], 10.5)
        self.assertFalse(result, "No new info when cell and status unchanged → False")

    def test_changed_cell_returns_true(self):
        """Enemy seen at a new cell → returns True."""
        observer = _ps("scout", "red")
        enemy = self._active_player("heavy", "blue", 3, 4)
        _update_player_memory(observer, [enemy], 10.0)
        enemy.cell_row, enemy.cell_col = 5, 6
        result = _update_player_memory(observer, [enemy], 10.5)
        self.assertTrue(result, "Enemy moved to new cell → True")

    def test_changed_status_returns_true(self):
        """Enemy status changes from active to downed → returns True."""
        observer = _ps("scout", "red")
        enemy = self._active_player("heavy", "blue", 3, 4)
        _update_player_memory(observer, [enemy], 10.0)
        # Now enemy is downed
        enemy.last_downed_time = 10.5
        result = _update_player_memory(observer, [enemy], 10.5)
        self.assertTrue(result, "Status change active→downed must return True")

    def test_empty_seen_list_returns_false(self):
        """Empty seen list → no change, returns False."""
        observer = _ps("scout", "red")
        result = _update_player_memory(observer, [], 10.0)
        self.assertFalse(result)

    # ------------------------------------------------------------------
    # Status field in memory entries
    # ------------------------------------------------------------------

    def test_active_status_stored(self):
        """Active player → status='active' in memory."""
        observer = _ps("scout", "red")
        enemy = self._active_player("heavy", "blue", 3, 4)
        _update_player_memory(observer, [enemy], 10.0)
        entry = observer.player_memory.get("blue_heavy", {})
        self.assertEqual(entry.get("status"), "active")

    def test_downed_status_stored(self):
        """Player downed this tick → status='downed' in memory."""
        observer = _ps("scout", "red")
        second = 50.0
        enemy = self._downed_player("heavy", "blue", 3, 4, second)
        _update_player_memory(observer, [enemy], second)
        entry = observer.player_memory.get("blue_heavy", {})
        self.assertEqual(entry.get("status"), "downed")

    def test_reset_window_status_stored(self):
        """Player 5 s after being downed (taggable, not active) → status='reset_window'."""
        observer = _ps("scout", "red")
        second = 50.0
        enemy = self._reset_window_player("heavy", "blue", 3, 4, second)
        _update_player_memory(observer, [enemy], second)
        entry = observer.player_memory.get("blue_heavy", {})
        self.assertEqual(entry.get("status"), "reset_window")

    def test_entry_includes_cell_role_and_status(self):
        """Memory entry always contains cell, role, and status."""
        observer = _ps("scout", "red")
        enemy = self._active_player("commander", "blue", 7, 2)
        _update_player_memory(observer, [enemy], 30.0)
        entry = observer.player_memory.get("blue_commander", {})
        self.assertIn("cell", entry)
        self.assertIn("role", entry)
        self.assertIn("status", entry)
        self.assertIn("timestamp", entry)

    def test_multiple_seen_all_stored(self):
        """All seen players are stored; returns True when any is new."""
        observer = _ps("scout", "red")
        e1 = self._active_player("heavy", "blue", 1, 1)
        e2 = self._active_player("medic", "blue", 2, 2)
        result = _update_player_memory(observer, [e1, e2], 5.0)
        self.assertTrue(result)
        self.assertIn("blue_heavy", observer.player_memory)
        self.assertIn("blue_medic", observer.player_memory)

    # ------------------------------------------------------------------
    # Broadcast gating in the tick loop (via _update_player_memory return)
    # ------------------------------------------------------------------

    def test_broadcast_fires_on_first_sighting(self):
        """Broadcast is triggered when a player is seen for the first time."""
        observer = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        enemy = self._active_player("heavy", "blue", 3, 4)
        ally = _ps("medic", "red", cell_row=1, cell_col=1)

        with patch("random.random", return_value=0.0):
            new_info = _update_player_memory(observer, [enemy], 10.0)
            if new_info:
                _broadcast_communication(observer, [observer, ally], None, 10.0)

        self.assertIn("blue_heavy", ally.player_memory, "Ally should receive first sighting")

    def test_broadcast_suppressed_on_repeated_sighting(self):
        """Broadcast is NOT triggered when the same enemy is seen at the same cell/status."""
        observer = _ps("scout", "red", communication=100, cell_row=0, cell_col=0)
        enemy = self._active_player("heavy", "blue", 3, 4)
        ally = _ps("medic", "red", cell_row=1, cell_col=1)

        # First sighting populates memory
        _update_player_memory(observer, [enemy], 10.0)

        # Second sighting: same cell, same status — no broadcast
        with patch("random.random", return_value=0.0):
            new_info = _update_player_memory(observer, [enemy], 10.5)
            if new_info:
                _broadcast_communication(observer, [observer, ally], None, 10.5)

        self.assertNotIn(
            "blue_heavy",
            ally.player_memory,
            "Ally should NOT receive repeated sighting with no new info",
        )


if __name__ == "__main__":
    unittest.main()
