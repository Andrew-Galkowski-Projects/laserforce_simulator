"""MECH-07d (spec #542, Trail avoidance): routes around a remembered Downed
enemy's Trail. A cost and never a block; scoring's walk distances ignore it."""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Any

import pytest

from matches.sim_helpers import pathfinding
from matches.sim_helpers.follow_rule import BandCell, Trail, open_trail
from matches.sim_helpers.goal_scoring import score_candidates
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.modes import trail_costs
from matches.sim_helpers.pathfinding import (
    astar_advance_cached,
    astar_path,
    build_movement_adjacency,
)
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.time_constants import RESPAWN_TICKS
from matches.simulation import BatchSimulator
from matches.tests.test_modes import _player, _rosters, _syracuse

Cell = tuple[int, int]

DOWN_TICK = 100
NOW = DOWN_TICK + 5
BAND = {(0, 1): BandCell(1, 0), (0, 2): BandCell(0, 0), (0, 3): BandCell(1, 0)}
BAND_COSTS = {(0, 1): 3.0, (0, 2): 3.0, (0, 3): 3.0}


def _downed(
    role: str = "medic",
    team: str = "blue",
    tag_id: str | None = None,
    band: dict[Cell, BandCell] | None = None,
) -> PlayerState:
    """A player Downed at ``DOWN_TICK``, with an open Trail."""
    player = _player(role, team)
    if tag_id is not None:
        player.tag_id = tag_id
    player.last_downed_time = DOWN_TICK
    player._trail = Trail(
        start_tick=DOWN_TICK, path=[(0, 2)], band=dict(BAND if band is None else band)
    )
    return player


def _remember(
    player: PlayerState,
    other: PlayerState,
    status: str | None = "downed",
    timestamp: int = NOW,
) -> None:
    entry = {"cell": (0, 2), "timestamp": timestamp, "role": other.role}
    if status is not None:
        entry["status"] = status
    player.player_memory[other.tag_id_key] = entry


# Two parallel lanes: row 0 runs straight, and row 1 goes round for 2 extra steps.
TWO_LANES = build_movement_adjacency(
    [
        [1, 1, 1, 1, 1],
        [1, 1, 1, 1, 1],
    ]
)
STRAIGHT = [(0, 1), (0, 2), (0, 3), (0, 4)]


class TestAstarExtraCosts:
    def test_a_costly_cell_sends_the_route_down_the_parallel_lane(self) -> None:
        path = astar_path((0, 0), (0, 4), TWO_LANES, extra_costs={(0, 2): 3.0})
        assert (0, 2) not in path
        assert path[-1] == (0, 4)

    def test_a_cost_below_the_detour_keeps_the_straight_route(self) -> None:
        path = astar_path((0, 0), (0, 4), TWO_LANES, extra_costs={(0, 2): 1.0})
        assert path == STRAIGHT

    def test_the_cost_adds_up_per_step_into_each_cell(self) -> None:
        # Each alone is below the 2-step detour; together they are above it.
        costs = {(0, 1): 1.5, (0, 2): 1.5, (0, 3): 1.5}
        assert (0, 2) not in astar_path((0, 0), (0, 4), TWO_LANES, extra_costs=costs)

    def test_when_the_costly_cells_are_the_only_way_the_route_still_crosses(
        self,
    ) -> None:
        corridor = build_movement_adjacency([[1, 1, 1, 1, 1]])
        costs = {(0, 1): 3.0, (0, 2): 3.0, (0, 3): 3.0}
        assert astar_path((0, 0), (0, 4), corridor, extra_costs=costs) == STRAIGHT

    def test_omitted_or_empty_costs_leave_seeded_routes_unchanged(self) -> None:
        open_grid = build_movement_adjacency([[1] * 12 for _ in range(12)])
        for seed in range(6):
            plain = astar_path((0, 0), (11, 11), open_grid, route_seed=seed)
            assert (
                astar_path((0, 0), (11, 11), open_grid, route_seed=seed, extra_costs={})
                == plain
            )
            # The same route noise decides it, with a cost on a cell it never uses.
            off_route = {(11, 0): 3.0} if (11, 0) not in plain else {(0, 11): 3.0}
            assert (
                astar_path(
                    (0, 0), (11, 11), open_grid, route_seed=seed, extra_costs=off_route
                )
                == plain
            )


class TestOpenTrail:
    def test_a_player_with_no_trail_has_none(self) -> None:
        assert open_trail(_player("medic"), NOW) is None

    def test_an_open_trail_is_returned(self) -> None:
        enemy = _downed()
        assert open_trail(enemy, NOW) is enemy._trail

    def test_it_is_open_until_the_tick_the_player_is_active(self) -> None:
        enemy = _downed()
        assert open_trail(enemy, DOWN_TICK + RESPAWN_TICKS - 1) is enemy._trail
        assert open_trail(enemy, DOWN_TICK + RESPAWN_TICKS) is None

    def test_a_trail_with_no_down_time_is_not_open(self) -> None:
        enemy = _downed()
        enemy.last_downed_time = None
        assert open_trail(enemy, NOW) is None


class TestTrailCosts:
    @pytest.mark.parametrize("status", ["downed", "reset_window"])
    def test_a_remembered_downed_enemy_costs_each_band_cell(self, status: str) -> None:
        player, enemy = _player("commander"), _downed()
        _remember(player, enemy, status=status)
        assert trail_costs(player, [player, enemy], NOW) == BAND_COSTS

    @pytest.mark.parametrize("status", ["active", None])
    def test_an_enemy_remembered_as_active_costs_nothing(
        self, status: str | None
    ) -> None:
        # None: nuke and medic-alert entries carry no status, which reads as active.
        player, enemy = _player("commander"), _downed()
        _remember(player, enemy, status=status)
        assert trail_costs(player, [player, enemy], NOW) == {}

    def test_an_unseen_enemy_costs_nothing(self) -> None:
        player, enemy = _player("commander"), _downed()
        assert trail_costs(player, [player, enemy], NOW) == {}

    def test_a_stale_memory_costs_nothing(self) -> None:
        # A Scout entry goes stale after 30 ticks: 30 is fresh, 31 is not.
        player, enemy = _player("commander"), _downed("scout")
        _remember(player, enemy, timestamp=NOW - 30)
        assert trail_costs(player, [player, enemy], NOW) == BAND_COSTS
        _remember(player, enemy, timestamp=NOW - 31)
        assert trail_costs(player, [player, enemy], NOW) == {}

    def test_freshness_follows_the_time_domain(self) -> None:
        # A Medic entry 90 old: inside the 120 ticks, past the 60 seconds.
        player, enemy = _player("commander"), _downed("medic")
        _remember(player, enemy, timestamp=NOW - 90)
        assert trail_costs(player, [player, enemy], NOW, "ticks") == BAND_COSTS
        assert trail_costs(player, [player, enemy], NOW, "seconds") == {}

    def test_memory_is_read_by_the_enemy_tag_id(self) -> None:
        player = _player("commander")
        enemy = _downed("scout", tag_id="blue_scout_1")
        player.player_memory["blue_scout"] = {
            "cell": (0, 2),
            "timestamp": NOW,
            "role": "scout",
            "status": "downed",
        }
        assert trail_costs(player, [player, enemy], NOW) == {}
        _remember(player, enemy)
        assert trail_costs(player, [player, enemy], NOW) == BAND_COSTS

    def test_a_teammate_trail_costs_nothing(self) -> None:
        player, ally = _player("commander"), _downed(team="red")
        _remember(player, ally)
        assert trail_costs(player, [player, ally], NOW) == {}

    def test_an_enemy_not_in_all_alive_costs_nothing(self) -> None:
        player, enemy = _player("commander"), _downed()
        _remember(player, enemy)
        assert trail_costs(player, [player], NOW) == {}

    def test_a_closed_trail_costs_nothing(self) -> None:
        player, enemy = _player("commander"), _downed()
        _remember(player, enemy, timestamp=DOWN_TICK + RESPAWN_TICKS)
        assert trail_costs(player, [player, enemy], DOWN_TICK + RESPAWN_TICKS) == {}

    def test_a_remembered_enemy_with_no_trail_costs_nothing(self) -> None:
        player, enemy = _player("commander"), _player("medic", "blue")
        _remember(player, enemy)
        assert trail_costs(player, [player, enemy], NOW) == {}

    def test_overlapping_trails_cost_the_same_in_any_order(self) -> None:
        # all_alive is reshuffled each tick, so its order must not change the costs.
        player = _player("commander")
        medic = _downed("medic")
        ammo = _downed("ammo", band={(0, 3): BandCell(0, 0), (0, 4): BandCell(1, 0)})
        _remember(player, medic)
        _remember(player, ammo)
        expected = {**BAND_COSTS, (0, 4): 3.0}
        assert trail_costs(player, [player, medic, ammo], NOW) == expected
        assert trail_costs(player, [ammo, medic, player], NOW) == expected


def _walker_and_trail() -> tuple[PlayerState, PlayerState]:
    """A walker at the start of ``TWO_LANES`` who remembers a Trail across lane 0."""
    walker, enemy = _player("commander"), _downed()
    _remember(walker, enemy)
    return walker, enemy


def _route_left(walker: PlayerState) -> list[Cell]:
    assert walker._path_cache is not None
    return walker._path_cache[1]


class TestAdvanceCachedCosts:
    def test_a_recompute_routes_around_the_trail(self) -> None:
        walker, enemy = _walker_and_trail()
        astar_advance_cached(
            walker, (0, 0), (0, 4), TWO_LANES, 1, all_alive=[walker, enemy], tick=NOW
        )
        assert not BAND.keys() & set(_route_left(walker))

    def test_with_no_all_alive_the_route_is_the_plain_one(self) -> None:
        walker, _ = _walker_and_trail()
        astar_advance_cached(walker, (0, 0), (0, 4), TWO_LANES, 1)
        assert _route_left(walker) == STRAIGHT[1:]

    def test_a_new_trail_does_not_invalidate_a_committed_route(self) -> None:
        walker, enemy = _player("commander"), _downed()
        astar_advance_cached(
            walker, (0, 0), (0, 4), TWO_LANES, 1, all_alive=[walker, enemy], tick=NOW
        )
        assert _route_left(walker) == STRAIGHT[1:]
        _remember(walker, enemy)
        reached = astar_advance_cached(
            walker, (0, 1), (0, 4), TWO_LANES, 1, all_alive=[walker, enemy], tick=NOW
        )
        assert reached == (0, 2)

    def test_costs_are_computed_only_on_a_recompute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # It runs per tick per player, so a re-step must not touch the Trails.
        calls: list[float] = []
        real = pathfinding.trail_costs

        def spy(player: PlayerState, all_alive: list, tick: float) -> dict:
            calls.append(tick)
            return real(player, all_alive, tick)

        monkeypatch.setattr(pathfinding, "trail_costs", spy)
        walker, enemy = _walker_and_trail()
        alive = [walker, enemy]
        reached = astar_advance_cached(
            walker, (0, 0), (0, 4), TWO_LANES, 1, all_alive=alive, tick=NOW
        )
        reached = astar_advance_cached(
            walker, reached, (0, 4), TWO_LANES, 1, all_alive=alive, tick=NOW + 1
        )
        assert calls == [NOW]
        # A new goal is a recompute, so it reads the Trails again.
        astar_advance_cached(
            walker, reached, (1, 4), TWO_LANES, 1, all_alive=alive, tick=NOW + 2
        )
        assert calls == [NOW, NOW + 2]


class TestDeterminism:
    def test_routing_around_a_trail_draws_no_rng(self) -> None:
        walker, enemy = _walker_and_trail()
        before = random.getstate()
        astar_advance_cached(
            walker, (0, 0), (0, 4), TWO_LANES, 1, all_alive=[walker, enemy], tick=NOW
        )
        assert random.getstate() == before


class TestGoalScoringIgnoresTrails:
    def test_walk_distance_scores_are_the_same_with_a_trail_remembered(self) -> None:
        ctx = MapContext(
            adj=TWO_LANES,
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
        walker, enemy = _player("commander", cell_row=0, cell_col=0), _downed()
        cells = [(0, 2), (0, 4)]

        def scores() -> list:
            return score_candidates(
                walker, cells, lambda _cell: 1.0, [walker, enemy], ctx, NOW
            )

        unseen = scores()
        _remember(walker, enemy)
        assert scores() == unseen


@pytest.mark.django_db
class TestRoundWiring:
    def test_a_seeded_map_round_routes_with_trail_costs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        costs_seen: list[Mapping] = []
        real = pathfinding.astar_path

        def spy(*args: Any, **kwargs: Any) -> list:
            extra = kwargs.get("extra_costs")
            if extra:
                costs_seen.append(extra)
            return real(*args, **kwargs)

        monkeypatch.setattr(pathfinding, "astar_path", spy)
        red, blue = _rosters("TrailWire")
        random.seed(13)
        BatchSimulator()._simulate_round(red, blue, movement_ctx=_syracuse())
        assert costs_seen
        assert all(set(c.values()) == {3.0} for c in costs_seen)

    def test_same_seed_same_round(self) -> None:
        red, blue = _rosters("TrailSame")
        ctx = _syracuse()

        def run() -> tuple:
            random.seed(17)
            result, red_players, blue_players = BatchSimulator()._simulate_round(
                red, blue, movement_ctx=ctx
            )
            routes = {p.tag_id: p.movement_routes for p in red_players + blue_players}
            return result, routes

        assert run() == run()
