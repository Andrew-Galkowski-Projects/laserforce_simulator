"""MOVE-08a: map scale and player speed in feet (#537).

Every distance is a feet constant turned into squares by the round's map
(``MapContext.squares``), and player speed is feet per second, so the scale
holds whatever the grid size. Syracuse: 30 px a square at 1/6 ft a pixel is
5 ft a square.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path

import pytest

from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.pathfinding import (
    MAX_PLAYER_FEET_PER_SECOND,
    feet_per_second,
    squares_this_tick,
)
from matches.sim_helpers.player_state import PlayerState

SYRACUSE = Path(__file__).resolve().parents[3] / "data" / "maps" / "syracuse.json"


def _ctx(feet_per_square: float) -> MapContext:
    return MapContext(
        adj={},
        spawn_cells={},
        zone_data=[[1]],
        sight_data=None,
        base_sight_data={},
        cell_los_counts={},
        high_los_cells=[],
        strong_spots=[],
        wall_meta={},
        team_spawn_pools={},
        feet_per_square=feet_per_square,
    )


def _ps(speed: int = 50) -> PlayerState:
    return PlayerState(
        tag_id="red_scout_1",
        name="s",
        team_color="red",
        role="scout",
        accuracy=50,
        survival=50,
        starting_lives=10,
        starting_shots=20,
        final_lives=10,
        final_shots=20,
        speed=speed,
    )


class TestSquares(unittest.TestCase):
    def test_feet_round_to_squares_at_least_one(self) -> None:
        five = _ctx(5.0)
        self.assertEqual(five.squares(10), 2)
        self.assertEqual(five.squares(6), 1)
        self.assertEqual(five.squares(1), 1)  # never 0
        self.assertEqual(five.squares(8), 2)  # 1.6 rounds up, not down
        self.assertEqual(_ctx(1.0).squares(10), 10)
        self.assertEqual(_ctx(2.5).squares(10), 4)

    def test_hand_built_context_defaults_to_five_feet(self) -> None:
        ctx = MapContext(
            adj={},
            spawn_cells={},
            zone_data=None,
            sight_data=None,
            base_sight_data={},
            cell_los_counts={},
            high_los_cells=[],
            strong_spots=[],
            wall_meta={},
            team_spawn_pools={},
        )
        self.assertEqual(ctx.feet_per_square, 5.0)


class TestSpeed(unittest.TestCase):
    def test_top_speed_runs_from_half_to_full(self) -> None:
        self.assertEqual(MAX_PLAYER_FEET_PER_SECOND, 10)
        self.assertEqual(feet_per_second(_ps(0)), 5.0)
        self.assertEqual(feet_per_second(_ps(50)), 7.5)
        self.assertEqual(feet_per_second(_ps(100)), 10.0)

    def test_fraction_carries_across_ticks(self) -> None:
        # 7.5 ft/s at 5 ft a square = 1.5 sq/s = 0.75 a tick: 3 in 4 ticks.
        p = _ps(50)
        steps = [squares_this_tick(p, _ctx(5.0)) for _ in range(4)]
        self.assertEqual(steps, [0, 1, 1, 1])
        self.assertEqual(sum(steps), 3)

    def test_only_move_doubles(self) -> None:
        p = _ps(100)  # 2 sq/s = 1 a tick
        self.assertEqual(squares_this_tick(p, _ctx(5.0), multiplier=2), 2)

    def test_finer_grid_moves_more_squares_in_the_same_time(self) -> None:
        coarse, fine = _ps(100), _ps(100)
        c = sum(squares_this_tick(coarse, _ctx(5.0)) for _ in range(8))
        f = sum(squares_this_tick(fine, _ctx(2.5)) for _ in range(8))
        self.assertEqual((c, f), (8, 16))
        self.assertTrue(math.isclose(f * 2.5, c * 5.0))


@pytest.mark.django_db
class TestMapScaleFromTheMap:
    def test_feet_per_square_is_zone_size_times_feet_per_pixel(self) -> None:
        from matches.lfstats_calibration import load_map_fixture
        from matches.sim_helpers.map_loader import load_map_context

        arena = load_map_fixture(SYRACUSE)
        ctx, zone_size = load_map_context(arena)
        assert ctx is not None and zone_size == 30
        assert arena.feet_per_pixel == pytest.approx(1 / 6)
        assert ctx.feet_per_square == pytest.approx(5.0)

        arena.feet_per_pixel = 1 / 3
        arena.save()
        ctx, _ = load_map_context(arena)
        assert ctx is not None
        assert ctx.feet_per_square == pytest.approx(10.0)
