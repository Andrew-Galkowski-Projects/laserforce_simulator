"""MOVE-08: unit tests for ``sim_helpers.downed_option`` (the Downed option).

Hand-built grids: ``.`` is floor, ``#`` is wall. Line of sight is a crude
stand-in: a cell sees every floor cell in its row and column. The roll is
driven by patching ``downed_option.random.random``: options are drawn in
the order Stay hidden, Shuffle to cover, Make a trail, so 0.0 picks Stay
hidden and 0.999 picks the last allowed option.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

import pytest

from matches.sim_helpers import downed_option
from matches.sim_helpers.downed_option import (
    MAKE_A_TRAIL,
    SHUFFLE_TO_COVER,
    STAY_HIDDEN,
    choose_downed_option,
    choose_resupply_trail,
    downed_goal,
    option_weights,
)
from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.round_context import RoundContext
from matches.sim_helpers.support_survival import Visit
from matches.sim_helpers.time_constants import RESPAWN_TICKS


def _map(rows: list[str], los_counts: dict | None = None) -> MapContext:
    floor = {
        (r, c) for r, line in enumerate(rows) for c, ch in enumerate(line) if ch == "."
    }
    adj = {
        (r, c): [
            n for n in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)) if n in floor
        ]
        for (r, c) in floor
    }
    sight = {
        f"{r},{c}": frozenset(f"{r2},{c2}" for (r2, c2) in floor if r2 == r or c2 == c)
        for (r, c) in floor
    }
    counts = los_counts or {f"{r},{c}": 10 for (r, c) in floor}
    return MapContext(
        adj=adj,
        spawn_cells={},
        zone_data=None,
        sight_data=sight,
        base_sight_data={},
        cell_los_counts=counts,
        high_los_cells=[],
        strong_spots=[],
        wall_meta={},
        team_spawn_pools={},
        # 1 ft a square, so the feet distances read as squares: trail and
        # tagger 10, shuffle 5.
        feet_per_square=1.0,
    )


OPEN = _map(["." * 30] * 15)


def _ps(tag: str, team: str, cell: tuple[int, int], role: str = "scout") -> PlayerState:
    p = PlayerState(
        tag_id=tag,
        name=tag,
        team_color=team,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=10,
        starting_shots=20,
        final_lives=10,
        final_shots=20,
    )
    p.cell_row, p.cell_col = cell
    return p


def _ctx(mc: MapContext | None, everyone: list | None = None) -> RoundContext:
    return RoundContext(
        events=EventLog(persist=False), movement_ctx=mc, all_alive=everyone or []
    )


def _remember(player: PlayerState, enemy: PlayerState, tick: int) -> None:
    player.player_memory[enemy.tag_id] = {
        "cell": (enemy.cell_row, enemy.cell_col),
        "timestamp": tick,
        "role": enemy.role,
    }


def _down(player, tagger, tick, mc, roll: float) -> None:
    """What ``record_down`` does for the option: roll it, then stamp."""
    with mock.patch.object(downed_option.random, "random", lambda: roll):
        choose_downed_option(player, tagger, tick, _ctx(mc))
    player.last_downed_time = tick


class TestOptionWeights(unittest.TestCase):
    """The tunable formula: stay = 20 x (1.5 - p) x (1.5 - (speed+stamina)/200)."""

    def _weights(self, p_rank_cell_count: int, speed: int, stamina: int, allowed):
        # Cells' LOS counts 0..4 on a 1x5 strip: rank p = 0, .25, .5, .75, 1.
        mc = _map(["....."], {f"0,{c}": c for c in range(5)})
        p = _ps("blue_scout_1", "blue", (0, p_rank_cell_count))
        p.speed, p.stamina = speed, stamina
        return option_weights(p, allowed, mc)

    def test_stay_runs_from_5_to_45(self) -> None:
        self.assertAlmostEqual(self._weights(0, 0, 0, True)[STAY_HIDDEN], 45.0)
        self.assertAlmostEqual(self._weights(4, 100, 100, True)[STAY_HIDDEN], 5.0)
        self.assertAlmostEqual(self._weights(2, 50, 50, True)[STAY_HIDDEN], 20.0)

    def test_shuffle_and_trail_are_fixed(self) -> None:
        w = self._weights(2, 50, 50, True)
        self.assertEqual((w[SHUFFLE_TO_COVER], w[MAKE_A_TRAIL]), (50, 30))

    def test_trail_weight_is_zero_when_not_allowed(self) -> None:
        self.assertEqual(self._weights(2, 50, 50, False)[MAKE_A_TRAIL], 0)


class TestTrailAllowed(unittest.TestCase):
    def test_tagger_ten_squares_away_blocks_the_trail(self) -> None:
        for gap, option in ((10, SHUFFLE_TO_COVER), (11, MAKE_A_TRAIL)):
            with self.subTest(gap=gap):
                downed = _ps("blue_scout_1", "blue", (7, 2))
                tagger = _ps("red_scout_1", "red", (7, 2 + gap))
                _remember(downed, tagger, 100)
                _down(downed, tagger, 100, OPEN, 0.999)
                self.assertEqual(downed.downed_plan.option, option)

    def test_walking_distance_not_straight_line(self) -> None:
        # Tagger 4 columns away across a wall; the walk round it is 15+.
        mc = _map(
            [
                "..........",
                "....#.....",
                "....#.....",
                "....#.....",
                "....#.....",
                "....#.....",
                "....#.....",
                "....#.....",
            ]
        )
        downed = _ps("blue_scout_1", "blue", (7, 2))
        tagger = _ps("red_scout_1", "red", (7, 6))
        _down(downed, tagger, 100, mc, 0.999)
        self.assertEqual(downed.downed_plan.option, MAKE_A_TRAIL)


class TestStayHidden(unittest.TestCase):
    def test_stay_hides_on_the_spot(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 2))
        _down(downed, None, 100, OPEN, 0.0)
        self.assertEqual(downed.downed_plan.option, STAY_HIDDEN)
        self.assertTrue(downed.is_hiding)
        self.assertEqual(downed_goal(downed, 101, OPEN), (7, 2))


class TestShuffleToCover(unittest.TestCase):
    def setUp(self) -> None:
        self.downed = _ps("blue_scout_1", "blue", (7, 3))
        self.enemy = _ps("red_heavy", "red", (7, 20))
        _remember(self.downed, self.enemy, 100)
        # Uniform LOS counts give p = 0, so stay = 30, shuffle = 50, trail =
        # 30 (or 0): 0.5 of the total lands in shuffle either way.
        self.shuffle_roll = 0.5

    def test_nearest_cell_the_remembered_enemy_cannot_see(self) -> None:
        _down(self.downed, self.enemy, 100, OPEN, self.shuffle_roll)
        self.assertEqual(self.downed.downed_plan.option, SHUFFLE_TO_COVER)
        # Row 7 is the enemy's row; (6, 3) is one step and off the row/column.
        self.assertEqual(downed_goal(self.downed, 101, OPEN), (6, 3))
        self.assertFalse(self.downed.is_hiding)

    def test_no_cover_in_reach_becomes_stay_hidden(self) -> None:
        # A 1-wide corridor on the enemy's row: every reachable cell is seen.
        mc = _map(["." * 30])
        downed = _ps("blue_scout_1", "blue", (0, 3))
        enemy = _ps("red_heavy", "red", (0, 20))
        _remember(downed, enemy, 100)
        _down(downed, enemy, 100, mc, self.shuffle_roll)
        self.assertEqual(downed.downed_plan.option, STAY_HIDDEN)
        self.assertTrue(downed.is_hiding)

    def test_no_remembered_enemy_becomes_stay_hidden(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 3))
        _down(downed, None, 100, OPEN, self.shuffle_roll)
        self.assertEqual(downed.downed_plan.option, STAY_HIDDEN)

    def test_already_in_cover_becomes_stay_hidden(self) -> None:
        downed = _ps("blue_scout_1", "blue", (6, 3))  # off row 7 and col 20
        _remember(downed, self.enemy, 100)
        _down(downed, self.enemy, 100, OPEN, self.shuffle_roll)
        self.assertEqual(downed.downed_plan.option, STAY_HIDDEN)
        self.assertTrue(downed.is_hiding)

    def test_cover_is_re_picked_when_an_enemy_now_sees_it(self) -> None:
        _down(self.downed, self.enemy, 100, OPEN, self.shuffle_roll)
        watcher = _ps("red_scout_1", "red", (6, 25))  # sees row 6
        _remember(self.downed, watcher, 101)
        self.assertEqual(downed_goal(self.downed, 102, OPEN), (8, 3))


class TestMakeATrail(unittest.TestCase):
    def test_turn_cell_is_ten_away_and_farthest_from_the_tagger(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 14))
        tagger = _ps("red_scout_1", "red", (7, 29))
        _down(downed, tagger, 100, OPEN, 0.999)
        plan = downed.downed_plan
        self.assertEqual(plan.option, MAKE_A_TRAIL)
        self.assertEqual(plan.turn_cell, (7, 4))
        self.assertEqual(downed_goal(downed, 101, OPEN), (7, 4))

    def test_goes_back_to_the_spot_after_the_turn(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 14))
        tagger = _ps("red_scout_1", "red", (7, 29))
        _down(downed, tagger, 100, OPEN, 0.999)
        downed.cell_row, downed.cell_col = (7, 4)
        self.assertEqual(downed_goal(downed, 105, OPEN), (7, 14))

    def test_dead_end_falls_back_to_the_farthest_within_ten(self) -> None:
        mc = _map(["......"])  # at most 5 squares from (0, 0)
        downed = _ps("blue_scout_1", "blue", (0, 0))
        _down(downed, None, 100, mc, 0.999)
        self.assertEqual(downed.downed_plan.turn_cell, (0, 5))


class TestReDown(unittest.TestCase):
    def test_a_reset_window_down_rolls_a_fresh_option_from_the_new_cell(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 14))
        tagger = _ps("red_scout_1", "red", (7, 29))
        _down(downed, tagger, 100, OPEN, 0.999)
        downed.cell_row, downed.cell_col = (7, 10)
        _down(downed, None, 110, OPEN, 0.0)
        self.assertEqual(downed.downed_plan.option, STAY_HIDDEN)
        self.assertEqual(downed.downed_plan.spot, (7, 10))


class TestPlanEndsAtActive(unittest.TestCase):
    def test_active_clears_the_plan_and_the_commitment(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 14))
        tagger = _ps("red_scout_1", "red", (7, 29))
        _down(downed, tagger, 100, OPEN, 0.999)
        downed._committed_goal = ((1, 1), False, 999)
        self.assertIsNone(downed_goal(downed, 100 + RESPAWN_TICKS, OPEN))
        self.assertIsNone(downed.downed_plan)
        self.assertIsNone(downed._committed_goal)


class TestGoalSelectionOverrides(unittest.TestCase):
    """``choose_goal_cell`` reads the Downed goal after the nuke exception and
    the critical-resource override."""

    def _goal(self, player, everyone, tick):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        return choose_goal_cell(
            player, everyone, {}, OPEN, "only_move", tick, time_domain="ticks"
        )

    def _trailing(self):
        downed = _ps("blue_scout_1", "blue", (7, 14))
        tagger = _ps("red_scout_1", "red", (7, 29))
        _down(downed, tagger, 100, OPEN, 0.999)
        return downed, tagger

    def test_downed_goal_beats_role_positioning(self) -> None:
        downed, tagger = self._trailing()
        self.assertEqual(self._goal(downed, [downed, tagger], 101), (7, 4))

    def test_nuke_reaction_wins_only_when_active_before_detonation(self) -> None:
        downed, tagger = self._trailing()
        commander = _ps("red_commander", "red", (2, 2), role="commander")
        _remember(downed, commander, 101)
        downed.reacting_to_nuke = True
        active = 100 + RESPAWN_TICKS
        for detonates, goal in ((active + 1, (2, 2)), (active, (7, 4))):
            with self.subTest(detonates=detonates):
                downed.nuke_detonates_at = detonates
                self.assertEqual(
                    self._goal(downed, [downed, tagger, commander], 101), goal
                )

    def test_a_visit_goal_beats_the_downed_option(self) -> None:
        # CAL-03c (#591): step 1's walk to the Medic is a Resupply visit's goal.
        downed, tagger = self._trailing()
        medic = _ps("blue_medic", "blue", (1, 1), role="medic")
        downed.final_lives = 2
        downed.visit = Visit(phase="go", started=90, goal=(1, 1))
        self.assertEqual(self._goal(downed, [downed, tagger, medic], 101), (1, 1))

    def test_low_lives_alone_no_longer_beat_the_downed_option(self) -> None:
        # With no visit (none starts while Downed) the trail holds.
        downed, tagger = self._trailing()
        medic = _ps("blue_medic", "blue", (1, 1), role="medic")
        downed.final_lives = 2
        self.assertEqual(self._goal(downed, [downed, tagger, medic], 101), (7, 4))


class TestResupplyTrail(unittest.TestCase):
    def _resupply(self, everyone, roll):
        mate = everyone[0]
        with mock.patch.object(downed_option.random, "random", lambda: roll):
            choose_resupply_trail(mate, 100, _ctx(OPEN, everyone))
        mate.last_downed_time = 100
        return mate

    def test_twenty_five_percent_when_no_enemy_within_ten(self) -> None:
        for roll, has_plan in ((0.24, True), (0.25, False)):
            with self.subTest(roll=roll):
                mate = _ps("blue_scout_1", "blue", (7, 14))
                far = _ps("red_heavy", "red", (7, 29))
                self._resupply([mate, far], roll)
                self.assertEqual(mate.downed_plan is not None, has_plan)

    def test_trail_returns_to_the_resupply_cell(self) -> None:
        mate = _ps("blue_scout_1", "blue", (7, 14))
        far = _ps("red_heavy", "red", (7, 29))
        self._resupply([mate, far], 0.0)
        self.assertEqual(mate.downed_plan.option, MAKE_A_TRAIL)
        self.assertEqual(mate.downed_plan.spot, (7, 14))

    def test_a_resupply_trail_drops_a_hide(self) -> None:
        mate = _ps("blue_scout_1", "blue", (7, 14))
        mate.is_hiding = True
        far = _ps("red_heavy", "red", (7, 29))
        self._resupply([mate, far], 0.0)
        self.assertEqual(mate.downed_plan.option, MAKE_A_TRAIL)
        self.assertFalse(mate.is_hiding)

    def test_a_combo_resupply_rolls_once(self) -> None:
        mate = _ps("blue_scout_1", "blue", (7, 14))
        far = _ps("red_heavy", "red", (7, 29))
        draws: list = []

        def roll() -> float:
            draws.append(1)
            return 0.9  # no trail, so a second call would roll again

        with mock.patch.object(downed_option.random, "random", roll):
            choose_resupply_trail(mate, 100, _ctx(OPEN, [mate, far]))
            choose_resupply_trail(mate, 100, _ctx(OPEN, [mate, far]))
        self.assertEqual(len(draws), 1)

    def test_no_roll_when_an_enemy_is_within_ten(self) -> None:
        mate = _ps("blue_scout_1", "blue", (7, 14))
        near = _ps("red_heavy", "red", (7, 20))

        def no_draw() -> float:
            raise AssertionError("drew a roll")

        with mock.patch.object(downed_option.random, "random", no_draw):
            choose_resupply_trail(mate, 100, _ctx(OPEN, [mate, near]))
        self.assertIsNone(mate.downed_plan)


class TestFeetScale(unittest.TestCase):
    """MOVE-08: at 5 ft a square (Syracuse) the trail goes out 2 squares,
    the tagger must be more than 2 away, and the shuffle reaches 1."""

    def setUp(self) -> None:
        self.mc = _map(["." * 30] * 15)
        self.mc.feet_per_square = 5.0

    def test_trail_turns_two_squares_out(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 14))
        tagger = _ps("red_scout_1", "red", (7, 17))  # 15 ft: allowed
        _down(downed, tagger, 100, self.mc, 0.999)
        self.assertEqual(downed.downed_plan.option, MAKE_A_TRAIL)
        self.assertEqual(downed.downed_plan.turn_cell, (7, 12))

    def test_tagger_two_squares_away_blocks_the_trail(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 14))
        tagger = _ps("red_scout_1", "red", (7, 16))  # 10 ft: not allowed
        _down(downed, tagger, 100, self.mc, 0.999)
        self.assertNotEqual(downed.downed_plan.option, MAKE_A_TRAIL)

    def test_shuffle_reaches_one_square_only(self) -> None:
        # The only unseen cells are two squares away: shuffle becomes stay.
        mc = _map(["." * 30, "#" * 29 + ".", "." * 30])
        mc.feet_per_square = 5.0
        downed = _ps("blue_scout_1", "blue", (0, 28))  # (1, 29) is 2 away
        enemy = _ps("red_heavy", "red", (0, 5))
        _remember(downed, enemy, 100)
        _down(downed, enemy, 100, mc, 0.5)
        self.assertEqual(downed.downed_plan.option, STAY_HIDDEN)


class TestInertWithoutMap(unittest.TestCase):
    def test_no_map_no_plan(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 3))
        _down(downed, None, 100, None, 0.0)
        self.assertIsNone(downed.downed_plan)
        mate = _ps("blue_scout_2", "blue", (7, 3))
        choose_resupply_trail(mate, 100, _ctx(None, [mate]))
        self.assertIsNone(mate.downed_plan)


class TestEngineWiring(unittest.TestCase):
    def test_record_down_clears_hiding_and_passes_the_tagger(self) -> None:
        from matches.sim_helpers.down import record_down

        downed = _ps("blue_scout_1", "blue", (7, 14))
        downed.is_hiding = True
        far = _ps("red_scout_1", "red", (7, 29))
        with mock.patch.object(downed_option.random, "random", lambda: 0.999):
            record_down(downed, 100, _ctx(OPEN), tagger=far)
        self.assertEqual(downed.downed_plan.option, MAKE_A_TRAIL)
        self.assertFalse(downed.is_hiding)

    def test_every_down_site_passes_its_tagger(self) -> None:
        from matches.sim_helpers import shot
        from matches.simulation import BatchSimulator, entrypoints

        seen: list = []

        def spy(player, tick, ctx, tagger=None):
            seen.append(tagger)

        with mock.patch.object(shot, "record_down", spy), mock.patch.object(
            entrypoints, "record_down", spy
        ):
            attacker = _ps("red_scout_1", "red", (7, 1))
            defender = _ps("blue_scout_1", "blue", (7, 2))
            defender.shields = 1
            with mock.patch.object(shot.random, "randint", lambda a, b: 1):
                shot.resolve_shot(
                    attacker,
                    defender,
                    50,
                    kind=shot.SHOT_KIND_INITIAL,
                    ctx=_ctx(None, [attacker, defender]),
                )
            BatchSimulator()._complete_missile(attacker, defender, 80)
            commander = _ps("red_commander", "red", (1, 1), role="commander")
            BatchSimulator()._complete_nuke(commander, 90, [defender])
        self.assertEqual(seen, [attacker, attacker, commander])

    def test_resupply_reset_rolls_the_resupply_trail(self) -> None:
        from matches.sim_helpers.combat import attempt_resupply

        medic = _ps("blue_medic", "blue", (7, 13), role="medic")
        mate = _ps("blue_scout_1", "blue", (7, 14))
        mate.final_lives = 5
        far = _ps("red_heavy", "red", (7, 29))
        with mock.patch.object(downed_option.random, "random", lambda: 0.0):
            attempt_resupply(medic, mate, 100, ctx=_ctx(OPEN, [medic, mate, far]))
        self.assertEqual(mate.downed_plan.option, MAKE_A_TRAIL)


class TestStationaryWhileDowned(unittest.TestCase):
    """Hide / hold / capture_base rolls do not freeze a moving Downed option,
    and the roll does not clear Stay hidden's hide."""

    def _trailing(self):
        downed = _ps("blue_scout_1", "blue", (7, 14))
        tagger = _ps("red_scout_1", "red", (7, 29))
        _down(downed, tagger, 100, OPEN, 0.999)
        return downed, tagger

    def test_hold_and_capture_base_do_not_freeze_a_trail(self) -> None:
        from matches.simulation import BatchSimulator

        downed, tagger = self._trailing()
        downed.is_holding = True
        downed.last_chosen_action = "capture_base"
        BatchSimulator()._advance_player(downed, [downed, tagger], 101, OPEN, "")
        self.assertNotEqual((downed.cell_row, downed.cell_col), (7, 14))

    def test_critical_resource_moves_a_stay_hidden_player(self) -> None:
        """Decision 14 reaches Stay hidden too: at 30% lives or less a Downed
        non-support player leaves the hide for their Medic. CAL-03c (#591): the
        walk is its Resupply visit's goal, which goes on through a Down."""
        from matches.simulation import BatchSimulator

        downed = _ps("blue_scout_1", "blue", (7, 2))
        _down(downed, None, 100, OPEN, 0.0)
        medic = _ps("blue_medic", "blue", (7, 20), role="medic")
        downed.final_lives = 2
        downed.visit = Visit(phase="go", started=90, goal=(7, 20))
        BatchSimulator()._advance_player(downed, [downed, medic], 101, OPEN, "")
        self.assertFalse(downed.is_hiding)
        self.assertNotEqual((downed.cell_row, downed.cell_col), (7, 2))

    def test_no_medic_alive_keeps_stay_hidden(self) -> None:
        from matches.simulation import BatchSimulator

        downed = _ps("blue_scout_1", "blue", (7, 2))
        _down(downed, None, 100, OPEN, 0.0)
        downed.final_lives = 2
        BatchSimulator()._advance_player(downed, [downed], 101, OPEN, "")
        self.assertTrue(downed.is_hiding)
        self.assertEqual((downed.cell_row, downed.cell_col), (7, 2))

    def test_nuke_cancel_moves_a_stay_hidden_player(self) -> None:
        """Decision 13 reaches Stay hidden too, when Active in time."""
        from matches.simulation import BatchSimulator

        downed = _ps("blue_scout_1", "blue", (7, 2))
        _down(downed, None, 100, OPEN, 0.0)
        commander = _ps("red_commander", "red", (7, 20), role="commander")
        _remember(downed, commander, 101)
        downed.reacting_to_nuke = True
        downed.nuke_detonates_at = 100 + RESPAWN_TICKS + 1
        BatchSimulator()._advance_player(downed, [downed, commander], 101, OPEN, "")
        self.assertNotEqual((downed.cell_row, downed.cell_col), (7, 2))

    def test_stay_hidden_holds_without_an_override(self) -> None:
        from matches.simulation import BatchSimulator

        downed = _ps("blue_scout_1", "blue", (7, 2))
        _down(downed, None, 100, OPEN, 0.0)
        BatchSimulator()._advance_player(downed, [downed], 101, OPEN, "")
        self.assertEqual((downed.cell_row, downed.cell_col), (7, 2))
        self.assertTrue(downed.is_hiding)

    def test_a_hide_roll_does_not_freeze_a_trail(self) -> None:
        from matches.simulation import BatchSimulator

        downed, _ = self._trailing()
        BatchSimulator._apply_hide(downed, 101)
        self.assertFalse(downed.is_hiding)
        active = _ps("blue_heavy", "blue", (1, 1))
        BatchSimulator._apply_hide(active, 101)
        self.assertTrue(active.is_hiding)

    def test_the_action_roll_keeps_stay_hidden_hiding(self) -> None:
        from matches.sim_helpers import combat

        downed = _ps("blue_scout_1", "blue", (7, 2))
        _down(downed, None, 100, OPEN, 0.0)
        with mock.patch.object(
            combat.random, "choices", lambda population, weights: ["capture_base"]
        ):
            combat.plan_action(downed, [downed], 101)
        self.assertTrue(downed.is_hiding)


class TestNukeDetonationTick(unittest.TestCase):
    def test_flags_store_the_earliest_detonation_for_the_target_team(self) -> None:
        from matches.sim_helpers.pending_events import PendingNuke
        from matches.simulation.round_loop import _apply_nuke_reaction_flags

        commander = _ps("red_commander", "red", (1, 1), role="commander")
        target = _ps("blue_scout_1", "blue", (7, 2))
        mate = _ps("red_scout_1", "red", (7, 3))
        nukes = [
            PendingNuke(complete_time=130, player=commander),
            PendingNuke(complete_time=120, player=commander),
        ]
        _apply_nuke_reaction_flags([commander, target, mate], nukes)
        self.assertEqual(target.nuke_detonates_at, 120)
        self.assertIsNone(mate.nuke_detonates_at)


@pytest.mark.django_db
class TestSeededRoundsMove:
    """Real seeded rounds: far more Downs now see the player leave their Down
    cell before they are Active (MECH-10's probe: about 5% of Downed ticks
    had any movement before MOVE-08). A shuffle or a trail finishes in a
    few ticks and then waits, so the measure is per Down, not per tick."""

    def test_downs_move_off_the_down_cell(self, monkeypatch) -> None:
        import random

        from pathlib import Path

        from matches.lfstats_calibration import load_map_fixture
        from matches.sim_helpers.map_loader import load_map_context
        from matches.simulation import BatchSimulator, entrypoints
        from matches.tests.conftest import make_team_with_slots

        moved_by_plan: dict = {}
        real_advance = entrypoints.BatchSimulator._advance_player

        def spy(self, player, all_alive, second, movement_ctx, prev_action):
            real_advance(self, player, all_alive, second, movement_ctx, prev_action)
            plan: Any = player.downed_plan
            if plan is not None and not player.is_active_at(second):
                moved = (player.cell_row, player.cell_col) != plan.spot
                moved_by_plan[id(plan)] = moved_by_plan.get(id(plan), False) or moved

        monkeypatch.setattr(entrypoints.BatchSimulator, "_advance_player", spy)
        red, _ = make_team_with_slots("DORed")
        blue, _ = make_team_with_slots("DOBlue")
        # Syracuse, not an open test floor: an open floor has no walls, so no
        # cover, and every Shuffle would fall back to Stay hidden.
        syracuse = Path(__file__).resolve().parents[3] / "data/maps/syracuse.json"
        movement_ctx, _ = load_map_context(load_map_fixture(syracuse))
        for seed in range(1, 4):
            random.seed(seed)
            BatchSimulator()._simulate_round(
                list(red.active_roster),
                list(blue.active_roster),
                movement_ctx=movement_ctx,
            )
        assert moved_by_plan, "no Downed option was rolled"
        share = sum(moved_by_plan.values()) / len(moved_by_plan)
        # MOVE-08 decision 33: about 27% on Syracuse (Stay hidden dominates
        # because cover within 5 ft is rare); 20% is well clear of the ~5%
        # before the Downed option.
        assert share > 0.2, (share, len(moved_by_plan))


if __name__ == "__main__":
    unittest.main()
