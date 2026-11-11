"""MOVE-06+07: unit tests for ``sim_helpers.goal_scoring`` and route noise (#540).

Positioning goals are a scored draw: ``score = value - 0.5 x walk_ft/100 -
0.5 x crowding``; the top 8 are drawn with weights ``exp((score - best) /
T)``, ``T = 0.05 + 0.45 x (1 - positioning/100)``. A drawn goal is held
until reached. Each player's A* routes carry fixed per-cell noise.
"""

from __future__ import annotations

import unittest
from unittest import mock

import pytest

from matches.sim_helpers import goal_scoring
from matches.sim_helpers.goal_scoring import (
    draw_goal,
    enemy_base_cells,
    score_candidates,
    temperature,
)
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.pathfinding import astar_path
from matches.sim_helpers.support_survival import Visit
from matches.sim_helpers.player_state import PlayerState


def _map(rows: list[str], feet_per_square: float = 5.0) -> MapContext:
    floor = {
        (r, c) for r, line in enumerate(rows) for c, ch in enumerate(line) if ch == "."
    }
    adj = {
        (r, c): [
            n for n in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)) if n in floor
        ]
        for (r, c) in floor
    }
    return MapContext(
        adj=adj,
        spawn_cells={"red": (0, 0), "blue": (len(rows) - 1, len(rows[0]) - 1)},
        zone_data=[[1 if ch == "." else 0 for ch in line] for line in rows],
        sight_data=None,
        base_sight_data={},
        cell_los_counts={f"{r},{c}": 10 for (r, c) in floor},
        high_los_cells=[],
        strong_spots=[],
        wall_meta={},
        team_spawn_pools={},
        feet_per_square=feet_per_square,
    )


OPEN = _map(["." * 40] * 20)


def _ps(tag: str, team: str, cell: tuple[int, int], positioning: int = 50):
    p = PlayerState(
        tag_id=tag,
        name=tag,
        team_color=team,
        role="scout",
        accuracy=50,
        survival=50,
        starting_lives=10,
        starting_shots=20,
        final_lives=10,
        final_shots=20,
        positioning=positioning,
    )
    p.cell_row, p.cell_col = cell
    return p


def _flat(cell) -> float:
    return 1.0


class TestScore(unittest.TestCase):
    def test_value_counts_and_distance_costs(self) -> None:
        me = _ps("red_scout_1", "red", (5, 5))
        near, far = (5, 7), (5, 25)  # 10 ft and 100 ft
        scores = dict(
            (cell, s)
            for s, cell in score_candidates(me, [near, far], _flat, [me], OPEN)
        )
        self.assertAlmostEqual(scores[near], 1.0 - 0.5 * 0.1)
        self.assertAlmostEqual(scores[far], 1.0 - 0.5 * 1.0)

    def test_value_function_is_used(self) -> None:
        me = _ps("red_scout_1", "red", (5, 5))
        scores = dict(
            (c, s)
            for s, c in score_candidates(
                me, [(5, 6), (5, 4)], lambda c: 1.0 if c == (5, 6) else 0.0, [me], OPEN
            )
        )
        self.assertGreater(scores[(5, 6)], scores[(5, 4)] + 0.9)

    def test_crowding_by_a_teammate_within_15_ft(self) -> None:
        me = _ps("red_scout_1", "red", (5, 5))
        mate = _ps("red_scout_2", "red", (10, 10))
        crowded, clear = (10, 13), (10, 14)  # 3 and 4 squares from the mate
        scores = dict(
            (c, s)
            for s, c in score_candidates(me, [crowded, clear], _flat, [me, mate], OPEN)
        )
        walk = {crowded: 13 * 5, clear: 14 * 5}
        self.assertAlmostEqual(scores[crowded], 1.0 - 0.5 * walk[crowded] / 100 - 0.5)
        self.assertAlmostEqual(scores[clear], 1.0 - 0.5 * walk[clear] / 100)

    def test_a_teammates_committed_goal_crowds_too(self) -> None:
        me = _ps("red_scout_1", "red", (5, 5))
        mate = _ps("red_scout_2", "red", (0, 39))
        mate._committed_goal = ((10, 10), False, 9999)
        ((score, _),) = score_candidates(me, [(10, 10)], _flat, [me, mate], OPEN)
        self.assertAlmostEqual(score, 1.0 - 0.5 * 50 / 100 - 0.5)

    def test_expired_commitments_do_not_crowd(self) -> None:
        me = _ps("red_scout_1", "red", (5, 5))
        mate = _ps("red_scout_2", "red", (0, 39))
        mate._committed_goal = ((10, 10), False, 20)
        clear = 1.0 - 0.5 * 50 / 100

        def score(tick):
            ((s, _),) = score_candidates(me, [(10, 10)], _flat, [me, mate], OPEN, tick)
            return s

        self.assertAlmostEqual(score(19), clear - 0.5)  # live: crowds
        self.assertAlmostEqual(score(20), clear)  # expired
        mate.cell_row, mate.cell_col = (10, 10)  # standing on its own goal
        mate._committed_goal = ((10, 10), False, 99)
        self.assertAlmostEqual(score(19), clear - 0.5)  # a teammate counts once

    def test_enemies_do_not_crowd(self) -> None:
        me = _ps("red_scout_1", "red", (5, 5))
        enemy = _ps("blue_scout_1", "blue", (5, 7))
        ((score, _),) = score_candidates(me, [(5, 7)], _flat, [me, enemy], OPEN)
        self.assertAlmostEqual(score, 1.0 - 0.5 * 0.1)

    def test_unreachable_cells_are_dropped(self) -> None:
        mc = _map(["...#...."])
        me = _ps("red_scout_1", "red", (0, 0))
        cells = [c for _, c in score_candidates(me, [(0, 2), (0, 6)], _flat, [me], mc)]
        self.assertEqual(cells, [(0, 2)])


class TestTemperature(unittest.TestCase):
    def test_positioning_sets_sharpness(self) -> None:
        self.assertAlmostEqual(temperature(_ps("a", "red", (0, 0), 100)), 0.05)
        self.assertAlmostEqual(temperature(_ps("a", "red", (0, 0), 50)), 0.275)
        self.assertAlmostEqual(temperature(_ps("a", "red", (0, 0), 0)), 0.5)


class TestDraw(unittest.TestCase):
    def _cells(self):
        # Ten cells along row 5, 1..10 squares away: closer scores higher.
        return [(5, 5 + k) for k in range(1, 11)]

    def test_only_the_top_eight_are_drawable(self) -> None:
        me = _ps("red_scout_1", "red", (5, 5), positioning=0)
        with mock.patch.object(goal_scoring.random, "random", lambda: 0.9999999):
            pick = draw_goal(me, self._cells(), _flat, [me], OPEN)
        self.assertEqual(pick, (5, 13))  # the 8th-best, never the 9th / 10th

    def test_a_low_roll_takes_the_best(self) -> None:
        me = _ps("red_scout_1", "red", (5, 5), positioning=0)
        with mock.patch.object(goal_scoring.random, "random", lambda: 0.0):
            self.assertEqual(draw_goal(me, self._cells(), _flat, [me], OPEN), (5, 6))

    def test_sharp_players_pick_the_best_more_often(self) -> None:
        import random

        def best_share(positioning: int) -> float:
            me = _ps("red_scout_1", "red", (5, 5), positioning=positioning)
            random.seed(7)
            picks = [
                draw_goal(me, self._cells(), _flat, [me], OPEN) for _ in range(400)
            ]
            return picks.count((5, 6)) / len(picks)

        self.assertGreater(best_share(100), best_share(0) + 0.05)

    def test_no_reachable_candidate_gives_none(self) -> None:
        me = _ps("red_scout_1", "red", (0, 0))
        self.assertIsNone(draw_goal(me, [], _flat, [me], OPEN))


class TestEnemyBaseCells(unittest.TestCase):
    def test_floor_cells_within_15_ft_walk_of_the_base(self) -> None:
        cells = set(enemy_base_cells("blue", OPEN))
        base = OPEN.spawn_cells["blue"]  # (19, 39)
        self.assertIn(base, cells)
        self.assertIn((16, 39), cells)  # 3 squares = 15 ft
        self.assertNotIn((15, 39), cells)  # 4 squares
        self.assertTrue(all(abs(r - 19) + abs(c - 39) <= 3 for r, c in cells))

    def test_no_base_gives_nothing(self) -> None:
        self.assertEqual(enemy_base_cells("green", OPEN), [])

    def test_a_base_in_a_wall_walks_from_the_nearest_floor_cell(self) -> None:
        # MECH-17: Syracuse's bases sit in the wall, so the set was empty there and the
        # search (and the enemy-base default) had nowhere to go.
        mc = _map(["." * 10, "." * 10, "." * 9 + "#"])  # the blue base is (2, 9)
        cells = set(enemy_base_cells("blue", mc))
        self.assertIn((1, 9), cells)  # the anchor: nearest, ties by row
        self.assertIn((2, 8), cells)
        self.assertNotIn((2, 9), cells)
        self.assertNotIn((1, 5), cells)  # 4 squares from the anchor

    def test_a_base_off_the_main_floor_walks_from_the_main_floor(self) -> None:
        # MECH-17: Syracuse's red base opens on a pocket nobody else can reach.
        mc = _map(["." * 12, "#" * 12, "..#########."])
        mc.spawn_cells["blue"] = (2, 0)
        cells = set(enemy_base_cells("blue", mc))
        self.assertIn((0, 0), cells)
        self.assertNotIn((2, 0), cells)
        self.assertNotIn((2, 1), cells)

    def test_with_no_floor_the_set_is_empty(self) -> None:
        self.assertEqual(enemy_base_cells("blue", _map(["###"])), [])


class TestRouteNoise(unittest.TestCase):
    def test_same_seed_same_route(self) -> None:
        a = astar_path((0, 0), (15, 30), OPEN.adj, route_seed=11)
        b = astar_path((0, 0), (15, 30), OPEN.adj, route_seed=11)
        self.assertEqual(a, b)

    def test_different_seeds_vary_the_route(self) -> None:
        routes = {
            tuple(astar_path((0, 0), (15, 30), OPEN.adj, route_seed=s))
            for s in range(8)
        }
        self.assertGreater(len(routes), 1)

    def test_routes_are_valid_and_near_shortest(self) -> None:
        shortest = len(astar_path((0, 0), (15, 30), OPEN.adj))
        for seed in range(8):
            with self.subTest(seed=seed):
                path = astar_path((0, 0), (15, 30), OPEN.adj, route_seed=seed)
                self.assertEqual(path[-1], (15, 30))
                prev = (0, 0)
                for cell in path:
                    self.assertIn(cell, OPEN.adj[prev])
                    prev = cell
                self.assertLessEqual(len(path), 1.5 * shortest)

    def test_no_rng_inside_a_star(self) -> None:
        import random

        def boom() -> float:
            raise AssertionError("A* drew a random number")

        with mock.patch.object(random, "random", boom):
            astar_path((0, 0), (15, 30), OPEN.adj, route_seed=3)

    def test_no_seed_is_the_plain_shortest_route(self) -> None:
        self.assertEqual(
            astar_path((0, 0), (0, 10), OPEN.adj),
            [(0, c) for c in range(1, 11)],
        )


class TestGoalSelectionDraws(unittest.TestCase):
    """``choose_goal_cell`` draws positioning goals, crowding spreads
    teammates, and a drawn goal is held until reached."""

    def _scouts(self):
        mc = _map(["." * 40] * 20)
        mc.high_los_cells = [(5, 20), (5, 21), (15, 20)]
        a = _ps("red_scout_1", "red", (5, 5), positioning=100)
        b = _ps("red_scout_2", "red", (5, 6), positioning=100)
        return mc, a, b

    def _goal(self, player, everyone, mc, tick):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        return choose_goal_cell(
            player, everyone, mc.spawn_cells, mc, "only_move", tick, "ticks"
        )

    def test_a_teammates_goal_pushes_the_next_scout_elsewhere(self) -> None:
        mc, a, b = self._scouts()
        with mock.patch.object(goal_scoring.random, "random", lambda: 0.0):
            goal_a = self._goal(a, [a, b], mc, 10)
            goal_b = self._goal(b, [a, b], mc, 10)
        self.assertEqual(goal_a, (5, 20))
        self.assertEqual(goal_b, (15, 20))  # (5, 21) is crowded by a's goal

    def test_a_drawn_goal_is_held_until_reached(self) -> None:
        mc, a, b = self._scouts()
        with mock.patch.object(goal_scoring.random, "random", lambda: 0.0):
            first = self._goal(a, [a], mc, 10)
        with mock.patch.object(goal_scoring.random, "random", lambda: 0.9999):
            held = self._goal(a, [a], mc, 10 + 400)
        self.assertEqual(first, held)

    def test_reaching_the_goal_draws_again(self) -> None:
        mc, a, _ = self._scouts()
        with mock.patch.object(goal_scoring.random, "random", lambda: 0.0):
            first = self._goal(a, [a], mc, 10)
        a.cell_row, a.cell_col = first
        draws: list = []

        def roll() -> float:
            draws.append(1)
            return 0.0

        with mock.patch.object(goal_scoring.random, "random", roll):
            self._goal(a, [a], mc, 11)
        self.assertEqual(len(draws), 1)

    def test_an_action_goal_interrupts_a_held_goal(self) -> None:
        """Decision 10: a held positioning goal yields to an action goal."""
        from matches.sim_helpers.pathfinding import choose_goal_cell

        mc, a, _ = self._scouts()
        enemy = _ps("blue_scout_1", "blue", (1, 1))
        with mock.patch.object(goal_scoring.random, "random", lambda: 0.0):
            held = self._goal(a, [a], mc, 10)
        # MECH-17: tag goals read memory. This map has no sight data, so the goal is the
        # remembered enemy's cell. An action goal still interrupts a held one.
        a.player_memory[enemy.tag_id_key] = {
            "cell": (1, 1),
            "timestamp": 11,
            "role": "scout",
        }
        chase = choose_goal_cell(
            a, [a, enemy], mc.spawn_cells, mc, "tag_player", 11, "ticks"
        )
        self.assertNotEqual(chase, held)
        self.assertEqual(chase, (1, 1))
        self.assertTrue(a._committed_goal[1])  # now an action commitment

    def test_a_reactive_override_still_interrupts(self) -> None:
        mc, a, _ = self._scouts()
        medic = _ps("red_medic", "red", (0, 0))
        medic.role = "medic"
        with mock.patch.object(goal_scoring.random, "random", lambda: 0.0):
            self._goal(a, [a, medic], mc, 10)
        # CAL-03c (#591): a Resupply visit's goal, which replaced step 1's walk.
        a.visit = Visit(phase="go", started=11, goal=(0, 0))
        self.assertEqual(self._goal(a, [a, medic], mc, 11), (0, 0))

    def test_the_enemy_base_default_spreads_players(self) -> None:
        mc = _map(["." * 40] * 20)
        a = _ps("red_commander", "red", (5, 5), positioning=100)
        a.role = "commander"
        b = _ps("red_heavy", "red", (5, 6), positioning=100)
        b.role = "heavy"
        b.final_lives = 3  # unhealthy Heavy without supports: default goal
        with mock.patch.object(goal_scoring.random, "random", lambda: 0.0):
            ga = self._goal(a, [a, b], mc, 10)
            gb = self._goal(b, [a, b], mc, 10)
        base = set(enemy_base_cells("blue", mc))
        self.assertIn(ga, base)
        self.assertIn(gb, base)
        # The nearest base-area cell, not the single base cell the old
        # default used: (16, 39) ties (17, 38) etc. at 45 squares and wins
        # on (row, col); the base cell (19, 39) is 48 away.
        self.assertEqual(ga, (16, 39))

    def test_route_cache_uses_the_players_seed(self) -> None:
        from matches.sim_helpers.pathfinding import astar_advance_cached

        plain = _ps("red_scout_1", "red", (0, 0))
        noisy = [_ps(f"red_scout_{s}", "red", (0, 0)) for s in range(8)]
        for s, p in enumerate(noisy):
            p.route_seed = s
        astar_advance_cached(plain, (0, 0), (15, 30), OPEN.adj, 1)
        expected = [
            astar_path((0, 0), (15, 30), OPEN.adj, route_seed=s) for s in range(8)
        ]
        for s, p in enumerate(noisy):
            astar_advance_cached(p, (0, 0), (15, 30), OPEN.adj, 1)
            self.assertEqual(p._path_cache[1], expected[s][1:])

    def test_rounds_give_map_players_route_seeds(self) -> None:
        from matches.simulation import BatchSimulator
        from matches.simulation.entrypoints import _SIMULATION_STATS, _PlayerData

        data = _PlayerData(1, "p", {s: 50 for s in _SIMULATION_STATS})
        roster = [("scout", data)]
        with mock.patch.object(BatchSimulator, "ROUND_TICKS", 1):
            _, reds, blues = BatchSimulator()._simulate_round(
                roster, roster, movement_ctx=OPEN
            )
        seeds = [p.route_seed for p in reds + blues]
        self.assertTrue(all(isinstance(s, int) for s in seeds))


class TestPositioningStat(unittest.TestCase):
    def test_player_state_defaults_positioning_to_50(self) -> None:
        self.assertEqual(_ps("a", "red", (0, 0)).positioning, 50)

    def test_positioning_is_a_simulation_stat(self) -> None:
        from matches.simulation.entrypoints import _SIMULATION_STATS

        self.assertIn("positioning", _SIMULATION_STATS)

    def test_a_pre_move_06_snapshot_still_loads(self) -> None:
        """Rounds saved before MOVE-06 have no ``positioning`` in their roster
        snapshot; a GEN-01 re-simulation fills the default instead of
        raising ``KeyError``."""
        from matches.simulation.entrypoints import (
            _SIMULATION_STATS,
            _roster_from_snapshot,
        )

        old = {s: 60 for s in _SIMULATION_STATS if s != "positioning"}
        ((role, data),) = _roster_from_snapshot(
            [{"player_id": 1, "name": "x", "role": "scout", "stats": old}]
        )
        self.assertEqual(data.stat_for_simulation("positioning", role), 50)
        self.assertEqual(data.stat_for_simulation("accuracy", role), 60)


@pytest.mark.django_db
class TestPositioningReachesTheEngine:
    def test_make_players_reads_positioning(self) -> None:
        from matches.simulation import BatchSimulator, _precompute_roster
        from matches.tests.conftest import make_team_with_slots

        team, players = make_team_with_slots("PosStat")
        players["scout"].positioning = 90
        players["scout"].save()
        team.refresh_from_db()
        roster = list(team.active_roster)
        live = BatchSimulator()._make_players(roster, "red")
        pre = BatchSimulator()._make_players(_precompute_roster(roster), "red")
        assert max(p.positioning for p in live) == pre_max(pre) >= 80


def pre_max(players) -> int:
    return max(p.positioning for p in players)


@pytest.mark.django_db
class TestSerialEqualsParallelOnAMap:
    """CODING_STANDARDS determinism: the new draws (route seeds, goal draws)
    run only on map rounds, so serial == parallel is pinned on a map."""

    def test_map_rounds_match_across_workers(self) -> None:
        from matches.simulation import BatchSimulator
        from matches.tests.conftest import make_team_with_slots
        from matches.tests.test_batch_sim import _move01_make_map

        red, _ = make_team_with_slots("SPRed")
        blue, _ = make_team_with_slots("SPBlue")
        arena = _move01_make_map("SerialParallelArena")
        sim = BatchSimulator()
        serial = sim.run(red, blue, n=4, master_seed=321, arena_map=arena)
        parallel = sim.run(red, blue, n=4, master_seed=321, arena_map=arena, workers=2)
        assert serial == parallel


@pytest.mark.django_db
class TestSeededRoundsSpread:
    """Real seeded Syracuse rounds: 2+ teammates share one goal in fewer
    than 15% of team-ticks (baseline 29-57%).

    Twelve rounds, because single rounds range from 9% to 24% (MECH-07): the same
    limit over two seeds passed or failed on which seeds they were. The margin is
    thin (14.5% at MECH-07), so an RNG change can move it; see PLAN.md decision 31.
    """

    # CAL-03b (#590) read 41.0% (HOLD with step 1's walk to a support's true cell);
    # CAL-03c (#591) replaced that walk with Resupply visits and it passes again.
    def test_teammates_rarely_share_a_goal(self, monkeypatch) -> None:
        import random
        from collections import Counter, defaultdict
        from pathlib import Path

        from matches.lfstats_calibration import load_map_fixture
        from matches.sim_helpers.map_loader import load_map_context
        from matches.simulation import BatchSimulator, entrypoints
        from matches.tests.conftest import make_team_with_slots

        by_team_tick: dict = defaultdict(dict)
        real = entrypoints.choose_goal_cell

        def spy(
            player,
            all_alive,
            spawn_cells,
            movement_ctx=None,
            intended_action="",
            second=0.0,
            time_domain="seconds",
        ):
            goal = real(
                player,
                all_alive,
                spawn_cells,
                movement_ctx,
                intended_action,
                second,
                time_domain,
            )
            if goal is not None:
                key = (seed_box[0], second, player.team_color)
                by_team_tick[key][player.tag_id] = goal
            return goal

        monkeypatch.setattr(entrypoints, "choose_goal_cell", spy)
        seed_box = [0]
        red, _ = make_team_with_slots("GSRed")
        blue, _ = make_team_with_slots("GSBlue")
        syracuse = Path(__file__).resolve().parents[3] / "data/maps/syracuse.json"
        movement_ctx, _ = load_map_context(load_map_fixture(syracuse))
        for seed in range(1, 13):
            seed_box[0] = seed
            random.seed(seed)
            BatchSimulator()._simulate_round(
                list(red.active_roster),
                list(blue.active_roster),
                movement_ctx=movement_ctx,
            )
        shared = sum(
            1
            for goals in by_team_tick.values()
            if any(n > 1 for n in Counter(goals.values()).values())
        )
        share = shared / len(by_team_tick)
        assert share < 0.15, share


if __name__ == "__main__":
    unittest.main()
