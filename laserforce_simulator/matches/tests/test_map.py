"""
Map-related tests: MAP-01 cell grid / spawn coordinates and MAP-02 pathfinding movement.
"""

import pytest

from matches.models import GameRound, PlayerRoundState, GameEvent
from matches.simulation import ResourceBasedSimulator
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# MAP-01 — cell grid position and map-aware spawn
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMap01CellGrid:
    """Tests for MAP-01: player cell coordinates and map-aware spawning."""

    def _make_arena_map(self, name="TestArena"):
        from core.models import ArenaMap, MapZoneConfig, MapBaseConfig, SightLineConfig
        from core.map_processing import compute_sight_lines

        arena_map = ArenaMap.objects.create(name=name, img_width=200, img_height=200)
        # 4×4 grid: 0=wall, 1=floor, 2=red zone, 3=blue zone
        zone_data = [
            [0, 2, 1, 0],
            [2, 2, 1, 3],
            [0, 1, 1, 3],
            [0, 1, 3, 3],
        ]
        MapZoneConfig.objects.create(
            arena_map=arena_map, zone_size=50, zone_data=zone_data, confirmed=True
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=25, y_px=75
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=175, y_px=125
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            sight_data=compute_sight_lines(zone_data),
        )
        return arena_map

    def test_gameround_has_arena_map_and_zone_size_fields(self):
        team, _ = make_team_with_slots("MapFld")
        gr = GameRound.objects.create(
            team_red=team,
            team_blue=team,
            round_number=1,
        )
        assert gr.arena_map is None
        assert gr.zone_size is None

    def test_playerroundstate_has_cell_fields(self):
        team, players = make_team_with_slots("CellFld")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        state = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["scout"],
            team_color="red",
            role="scout",
            final_lives=10,
            final_shots=10,
        )
        assert state.cell_row is None
        assert state.cell_col is None

    def test_current_zone_property_reads_zone_fallback(self):
        team, players = make_team_with_slots("ZoneProp")
        gr = GameRound.objects.create(team_red=team, team_blue=team, round_number=1)
        state = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["scout"],
            team_color="blue",
            role="scout",
            zone_fallback=2,
            final_lives=10,
            final_shots=10,
        )
        assert state.current_zone == 2

    def test_no_map_simulation_uses_default_zones(self):
        """Without a map, red starts zone 0 and blue starts zone 2."""
        team_red, _ = make_team_with_slots("NoMapR")
        team_blue, _ = make_team_with_slots("NoMapB")
        sim = ResourceBasedSimulator()
        game_round = sim.simulate_single_round_detailed(team_red, team_blue)

        assert game_round.arena_map is None
        assert game_round.zone_size is None

        red_states = game_round.player_states.filter(team_color="red")
        blue_states = game_round.player_states.filter(team_color="blue")
        assert all(s.cell_row is None for s in red_states)
        assert all(s.cell_row is None for s in blue_states)

    def test_map_simulation_stores_arena_map_and_zone_size(self):
        """GameRound stores the arena_map FK and zone_size after map simulation."""
        team_red, _ = make_team_with_slots("MapR")
        team_blue, _ = make_team_with_slots("MapB")
        arena_map = self._make_arena_map("StoreMapTest")
        sim = ResourceBasedSimulator()
        game_round = sim.simulate_single_round_detailed(
            team_red, team_blue, arena_map=arena_map
        )

        assert game_round.arena_map == arena_map
        assert game_round.zone_size == 50

    def test_map_simulation_sets_cell_coordinates(self):
        """Players end the round with valid cell coordinates within the grid bounds."""
        team_red, _ = make_team_with_slots("CellR")
        team_blue, _ = make_team_with_slots("CellB")
        arena_map = self._make_arena_map("CellCoordTest")
        sim = ResourceBasedSimulator()
        game_round = sim.simulate_single_round_detailed(
            team_red, team_blue, arena_map=arena_map
        )

        # All players should have non-None cell coordinates within the 4×4 grid
        for s in game_round.player_states.all():
            assert s.cell_row is not None, f"{s} has no cell_row after map simulation"
            assert s.cell_col is not None, f"{s} has no cell_col after map simulation"
            assert 0 <= s.cell_row <= 3, f"cell_row {s.cell_row} out of bounds"
            assert 0 <= s.cell_col <= 3, f"cell_col {s.cell_col} out of bounds"

    def test_zone_from_cell_maps_correctly(self):
        """_zone_from_cell converts core zone_data types to PlayerRoundState zone indices."""
        sim = ResourceBasedSimulator()
        zone_data = [
            [0, 2, 1, 0],
            [2, 2, 1, 3],
            [0, 1, 1, 3],
            [0, 1, 3, 3],
        ]
        assert sim._zone_from_cell(zone_data, 0, 1) == 0  # cell_type=2 → red zone
        assert sim._zone_from_cell(zone_data, 1, 3) == 2  # cell_type=3 → blue zone
        assert sim._zone_from_cell(zone_data, 1, 2) == 1  # cell_type=1 → neutral zone
        assert (
            sim._zone_from_cell(zone_data, 0, 0) == 1
        )  # cell_type=0 (wall) → neutral zone

    def test_resolve_map_data_returns_spawn_cells_and_zone_data(self):
        """_resolve_map_data returns zone_size, spawn cells, zone_data, and sight_data."""
        arena_map = self._make_arena_map("ResolveTest")
        sim = ResourceBasedSimulator()
        zone_size, spawn_cells, zone_grid, sight_data = sim._resolve_map_data(arena_map)

        assert zone_size == 50
        assert spawn_cells["red"] == (1, 0)
        assert spawn_cells["blue"] == (2, 3)
        assert zone_grid[1][0] == 2  # red zone cell value
        assert isinstance(
            sight_data, dict
        )  # sight_data returned as frozenset-valued dict

    def test_resolve_map_data_unwraps_dict_zone_data(self):
        """_resolve_map_data unwraps the production dict format {"zones": [...], "blocked_edges": {...}}."""
        from core.models import ArenaMap, MapZoneConfig, MapBaseConfig, SightLineConfig
        from core.map_processing import compute_sight_lines

        arena_map = ArenaMap.objects.create(
            name="DictFmt", img_width=100, img_height=100
        )
        raw_zones = [[2, 1], [1, 3]]
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            zone_data={"zones": raw_zones, "blocked_edges": {}},
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=25, y_px=25
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=75, y_px=75
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            sight_data=compute_sight_lines(raw_zones),
        )

        _, _, zone_grid, _ = ResourceBasedSimulator._resolve_map_data(arena_map)
        assert zone_grid == raw_zones

    def test_initial_spawn_zone_derived_from_zone_data(self):
        """Players' starting zone_fallback is derived from zone_data at spawn — tested at init."""
        from core.models import ArenaMap, MapZoneConfig, MapBaseConfig, SightLineConfig
        from core.map_processing import compute_sight_lines

        zone_layout = [[2, 1], [1, 3]]
        arena_map = ArenaMap.objects.create(
            name="ZoneDeriveTest", img_width=100, img_height=100
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            zone_data=zone_layout,
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=25, y_px=25
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=75, y_px=75
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            sight_data=compute_sight_lines(zone_layout),
        )

        team_red, _ = make_team_with_slots("ZoneR")
        team_blue, _ = make_team_with_slots("ZoneB")
        gr = GameRound.objects.create(
            team_red=team_red,
            team_blue=team_blue,
            round_number=1,
            arena_map=arena_map,
            zone_size=50,
        )
        sim = ResourceBasedSimulator()
        _, spawn_cells, zone_data, _ = sim._resolve_map_data(arena_map)

        red_states = sim._initialize_players(
            gr, team_red, "red", spawn_cells, zone_data
        )
        blue_states = sim._initialize_players(
            gr, team_blue, "blue", spawn_cells, zone_data
        )

        # Red spawn cell (0,0): zone_data[0][0]=2 → zone 0 (red_zone)
        for s in red_states:
            assert s.zone_fallback == 0
            assert s.current_zone == 0

        # Blue spawn cell (1,1): zone_data[1][1]=3 → zone 2 (blue_zone)
        for s in blue_states:
            assert s.zone_fallback == 2
            assert s.current_zone == 2

    def test_missing_red_base_config_raises(self):
        """Simulating with a map that has no red base raises ValueError."""
        from core.models import ArenaMap, MapZoneConfig, MapBaseConfig

        arena_map = ArenaMap.objects.create(
            name="NoRedBase", img_width=100, img_height=100
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            zone_data=[[1, 1], [1, 1]],
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=75, y_px=75
        )

        team_red, _ = make_team_with_slots("ErrR")
        team_blue, _ = make_team_with_slots("ErrB")
        sim = ResourceBasedSimulator()
        with pytest.raises(ValueError, match="red base"):
            sim.simulate_single_round_detailed(team_red, team_blue, arena_map=arena_map)

    def test_no_confirmed_config_raises(self):
        """Simulating with a map that has no confirmed zone config raises ValueError."""
        from core.models import ArenaMap, MapZoneConfig

        arena_map = ArenaMap.objects.create(
            name="NoConfig", img_width=100, img_height=100
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            zone_data=[[1, 1], [1, 1]],
            confirmed=False,
        )

        team_red, _ = make_team_with_slots("NoCfgR")
        team_blue, _ = make_team_with_slots("NoCfgB")
        sim = ResourceBasedSimulator()
        with pytest.raises(ValueError, match="confirmed zone configuration"):
            sim.simulate_single_round_detailed(team_red, team_blue, arena_map=arena_map)


# ---------------------------------------------------------------------------
# MAP-02 — cell-aware pathfinding movement
# ---------------------------------------------------------------------------


_FLOOR_5X5 = [[1] * 5 for _ in range(5)]
_FLOOR_10X10 = [[1] * 10 for _ in range(10)]


@pytest.mark.django_db
class TestMap02CellMovement:
    """Tests for MAP-02: cell-aware pathfinding movement."""

    def _make_map(self, name, zone_data, zone_size=100):
        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig, SightLineConfig
        from core.map_processing import compute_sight_lines

        rows, cols, px = len(zone_data), len(zone_data[0]), zone_size
        arena_map = ArenaMap.objects.create(
            name=name, img_width=cols * px, img_height=rows * px
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map, zone_size=px, zone_data=zone_data, confirmed=True
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=px // 2, y_px=px // 2
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            x_px=cols * px - px // 2,
            y_px=rows * px - px // 2,
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=px,
            sight_data=compute_sight_lines(zone_data),
        )
        return arena_map

    def test_build_movement_adjacency_excludes_walls(self):
        from matches.sim_helpers.pathfinding import build_movement_adjacency

        zone_data = [
            [1, 1, 1],
            [1, 0, 1],
            [1, 1, 1],
        ]
        adj = build_movement_adjacency(zone_data)

        assert (1, 1) not in adj
        for neighbors in adj.values():
            assert (1, 1) not in neighbors

    def test_astar_next_step_finds_path(self):
        from matches.sim_helpers.pathfinding import (
            astar_next_step,
            build_movement_adjacency,
        )

        adj = build_movement_adjacency(_FLOOR_5X5)
        assert astar_next_step((0, 0), (4, 4), adj) in ((0, 1), (1, 0))

    def test_astar_next_step_no_path(self):
        from matches.sim_helpers.pathfinding import (
            astar_next_step,
            build_movement_adjacency,
        )

        adj = build_movement_adjacency([[1, 0, 1], [1, 0, 1], [1, 0, 1]])
        assert astar_next_step((0, 0), (0, 2), adj) == (0, 0)

    def test_astar_next_step_same_cell(self):
        from matches.sim_helpers.pathfinding import (
            astar_next_step,
            build_movement_adjacency,
        )

        adj = build_movement_adjacency(_FLOOR_5X5)
        assert astar_next_step((2, 2), (2, 2), adj) == (2, 2)

    def test_elevation_stub_returns_zero_without_data(self):
        from matches.sim_helpers.pathfinding import _elevation_at

        assert _elevation_at(0, 0) == 0
        assert _elevation_at(3, 7, None) == 0

    def test_elevation_data_present_returns_value(self):
        from matches.sim_helpers.pathfinding import _elevation_at

        elev = {(1, 2): 5.0, (0, 0): 1.5}
        assert _elevation_at(1, 2, elev) == 5.0
        assert _elevation_at(0, 0, elev) == 1.5
        assert _elevation_at(9, 9, elev) == 0  # not in data → fallback 0

    def test_movement_cost_uphill_is_1_5x(self):
        from matches.sim_helpers.pathfinding import _movement_cost

        elev = {(0, 0): 0, (0, 1): 3}
        assert _movement_cost((0, 0), (0, 1), elev) == 1.5

    def test_movement_cost_downhill_and_flat_are_1x(self):
        from matches.sim_helpers.pathfinding import _movement_cost

        elev = {(0, 0): 5, (0, 1): 3}
        assert _movement_cost((0, 0), (0, 1), elev) == 1.0  # downhill
        assert _movement_cost((0, 0), (0, 1), None) == 1.0  # flat (no data)

    def test_choose_goal_cell_shots_critical_navigates_to_ammo(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell
        from matches.sim_helpers.player_state import PlayerState

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        ammo = PlayerState(
            tag_id="red_ammo",
            name="ammo",
            team_color="red",
            role="ammo",
            accuracy=50,
            survival=50,
            starting_lives=10,
            starting_shots=15,
            final_lives=5,
            final_shots=10,
            cell_row=3,
            cell_col=4,
        )
        # commander with ≤ 30% of 60 max shots → shots-critical
        attacker = PlayerState(
            tag_id="red_commander",
            name="cmd",
            team_color="red",
            role="commander",
            accuracy=50,
            survival=50,
            starting_lives=15,
            starting_shots=60,
            final_lives=15,
            final_shots=9,
            cell_row=0,
            cell_col=0,
        )
        goal = choose_goal_cell(attacker, [attacker, ammo], spawn_cells)
        assert goal == (3, 4), f"Expected ammo's cell, got {goal}"

    def test_choose_goal_cell_default_is_enemy_base(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell
        from matches.sim_helpers.player_state import PlayerState

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        player = PlayerState(
            tag_id="red_commander",
            name="cmd",
            team_color="red",
            role="commander",
            accuracy=50,
            survival=50,
            starting_lives=15,
            starting_shots=60,
            final_lives=15,
            final_shots=60,
            cell_row=0,
            cell_col=0,
        )
        assert choose_goal_cell(player, [player], spawn_cells) == (9, 9)

    def test_move_to_cell_creates_game_event_with_metadata(self):
        import random

        random.seed(42)

        arena_map = self._make_map("Test5x5", _FLOOR_5X5)
        team_red, _ = make_team_with_slots("MovR")
        team_blue, _ = make_team_with_slots("MovB")

        game_round = ResourceBasedSimulator().simulate_single_round_detailed(
            team_red, team_blue, arena_map=arena_map
        )

        cell_move_events = [
            e
            for e in GameEvent.objects.filter(
                game_round=game_round, event_type="movement"
            )
            if "cell_row" in e.metadata and "cell_col" in e.metadata
        ]
        assert (
            len(cell_move_events) > 0
        ), "Expected at least one cell-based movement event"

        ev = cell_move_events[0]
        assert isinstance(ev.metadata["cell_row"], int)
        assert isinstance(ev.metadata["cell_col"], int)
        assert "actor_role" in ev.metadata
        assert "new_zone" in ev.metadata

    def test_fallback_no_map(self):
        import random

        random.seed(42)

        team_red, _ = make_team_with_slots("FbR")
        team_blue, _ = make_team_with_slots("FbB")

        game_round = ResourceBasedSimulator().simulate_single_round_detailed(
            team_red, team_blue
        )

        for event in GameEvent.objects.filter(
            game_round=game_round, event_type="movement"
        ):
            assert (
                "cell_row" not in event.metadata
            ), "Fallback movement should not record cell coordinates"

    def test_player_advances_toward_enemy_base(self):
        import random

        random.seed(42)

        arena_map = self._make_map("Test10x10", _FLOOR_10X10)
        team_red, _ = make_team_with_slots("ReachR")
        team_blue, _ = make_team_with_slots("ReachB")

        game_round = ResourceBasedSimulator().simulate_single_round_detailed(
            team_red, team_blue, arena_map=arena_map
        )

        red_spawn = (0, 0)
        any_advanced = any(
            s.cell_row is not None
            and (abs(s.cell_row - red_spawn[0]) + abs(s.cell_col - red_spawn[1])) > 0
            for s in game_round.player_states.filter(team_color="red")
        )
        assert (
            any_advanced
        ), "At least one red player should have advanced from their spawn cell"

    def test_batch_simulator_runs_with_map(self):
        """BatchSimulator.run() with arena_map uses cell-aware movement."""
        import random

        random.seed(42)

        from matches.simulation import BatchSimulator

        arena_map = self._make_map("BatchTest5x5", _FLOOR_5X5)
        team_red, _ = make_team_with_slots("BatR")
        team_blue, _ = make_team_with_slots("BatB")

        result = BatchSimulator().run(team_red, team_blue, n=3, arena_map=arena_map)

        assert result["n"] == 3
        assert result["red_wins"] + result["blue_wins"] + result["ties"] == 3
        assert "avg_red_score" in result


# ---------------------------------------------------------------------------
# MAP-03 — line-of-sight based targeting
# ---------------------------------------------------------------------------


class TestMap03LOSTargeting:
    """Unit tests for _get_los_targets and MAP-03 LOS targeting logic."""

    _LIVES = {"commander": 30, "heavy": 20, "scout": 30, "medic": 20, "ammo": 20}
    _SHOTS = {"commander": 60, "heavy": 40, "scout": 60, "medic": 30, "ammo": 15}

    def _make_player(
        self, tag_id, team_color, role, zone=0, cell_row=None, cell_col=None
    ):
        from matches.sim_helpers.player_state import PlayerState

        return PlayerState(
            tag_id=tag_id,
            name=tag_id,
            team_color=team_color,
            role=role,
            accuracy=50,
            survival=50,
            starting_lives=self._LIVES[role],
            starting_shots=self._SHOTS[role],
            final_lives=self._LIVES[role],
            final_shots=self._SHOTS[role],
            current_zone=zone,
            cell_row=cell_row,
            cell_col=cell_col,
        )

    def test_no_map_filters_by_zone(self):
        from matches.simulation import _get_los_targets

        actor = self._make_player("red_cmd", "red", "commander", zone=0)
        same_zone = self._make_player("blue_cmd", "blue", "commander", zone=0)
        diff_zone = self._make_player("blue_hvy", "blue", "heavy", zone=2)

        result = _get_los_targets(actor, [same_zone, diff_zone], movement_ctx=None)
        assert result == [same_zone]

    def test_with_map_filters_by_sight_data(self):
        from matches.simulation import _get_los_targets

        sight_data = {
            "0,0": frozenset(["0,1", "1,0"]),
            "0,1": frozenset(["0,0", "0,2"]),
        }
        ctx = {
            "sight_data": sight_data,
            "adj": {},
            "spawn_cells": {},
            "zone_data": None,
        }

        actor = self._make_player("red_cmd", "red", "commander", cell_row=0, cell_col=0)
        visible = self._make_player(
            "blue_cmd", "blue", "commander", cell_row=0, cell_col=1
        )
        invisible = self._make_player(
            "blue_hvy", "blue", "heavy", cell_row=5, cell_col=5
        )

        result = _get_los_targets(actor, [visible, invisible], ctx)
        assert result == [visible]

    def test_wall_blocks_los(self):
        """A target whose cell key is not in actor's visible set is excluded."""
        from matches.simulation import _get_los_targets

        # (0,0) can only see (1,0) — (0,2) is on the other side of a wall
        sight_data = {
            "0,0": frozenset(["1,0"]),
            "0,2": frozenset(["1,2"]),
        }
        ctx = {
            "sight_data": sight_data,
            "adj": {},
            "spawn_cells": {},
            "zone_data": None,
        }

        actor = self._make_player("red_cmd", "red", "commander", cell_row=0, cell_col=0)
        behind_wall = self._make_player(
            "blue_cmd", "blue", "commander", cell_row=0, cell_col=2
        )

        result = _get_los_targets(actor, [behind_wall], ctx)
        assert result == []

    def test_actor_with_no_cell_falls_back_to_zone(self):
        """Actor without a cell position falls back to zone-based filtering."""
        from matches.simulation import _get_los_targets

        sight_data = {"0,0": frozenset(["0,1"])}
        ctx = {
            "sight_data": sight_data,
            "adj": {},
            "spawn_cells": {},
            "zone_data": None,
        }

        actor = self._make_player(
            "red_cmd", "red", "commander", zone=1, cell_row=None, cell_col=None
        )
        same_zone = self._make_player(
            "blue_cmd", "blue", "commander", zone=1, cell_row=0, cell_col=1
        )
        diff_zone = self._make_player(
            "blue_hvy", "blue", "heavy", zone=2, cell_row=5, cell_col=5
        )

        result = _get_los_targets(actor, [same_zone, diff_zone], ctx)
        assert result == [same_zone]

    def test_target_with_no_cell_excluded_when_map_active(self):
        """A target without a cell position is excluded when sight_data is in use."""
        from matches.simulation import _get_los_targets

        sight_data = {"0,0": frozenset(["0,1"])}
        ctx = {
            "sight_data": sight_data,
            "adj": {},
            "spawn_cells": {},
            "zone_data": None,
        }

        actor = self._make_player("red_cmd", "red", "commander", cell_row=0, cell_col=0)
        no_cell_target = self._make_player(
            "blue_cmd", "blue", "commander", cell_row=None, cell_col=None
        )

        result = _get_los_targets(actor, [no_cell_target], ctx)
        assert result == []

    def test_los_is_bidirectional(self):
        """If A can see B, B can also see A (sight_data is bidirectional)."""
        from matches.simulation import _get_los_targets

        sight_data = {
            "0,0": frozenset(["0,1"]),
            "0,1": frozenset(["0,0"]),
        }
        ctx = {
            "sight_data": sight_data,
            "adj": {},
            "spawn_cells": {},
            "zone_data": None,
        }

        a = self._make_player("red_cmd", "red", "commander", cell_row=0, cell_col=0)
        b = self._make_player("blue_cmd", "blue", "commander", cell_row=0, cell_col=1)

        assert _get_los_targets(a, [b], ctx) == [b]
        assert _get_los_targets(b, [a], ctx) == [a]


@pytest.mark.django_db
class TestMap03DBIntegration:
    """DB-backed MAP-03 tests: missing sight config error and full-round LOS simulation."""

    def _make_map_with_wall(self):
        """3×3 map with a wall column in the middle blocking LOS between left and right."""
        from core.models import ArenaMap, MapZoneConfig, MapBaseConfig, SightLineConfig

        # Columns: 0=passable, 1=wall, 2=passable
        zone_data = [
            [1, 0, 1],
            [1, 0, 1],
            [1, 0, 1],
        ]
        arena_map = ArenaMap.objects.create(
            name="WallMapLOS", img_width=300, img_height=300
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map, zone_size=100, zone_data=zone_data, confirmed=True
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=50, y_px=150
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=250, y_px=150
        )
        # Sight lines: left column can see each other; right column can see each other;
        # no cross-wall visibility.
        from core.map_processing import compute_sight_lines

        sight_data = compute_sight_lines(zone_data)
        SightLineConfig.objects.create(
            arena_map=arena_map, zone_size=100, sight_data=sight_data
        )
        return arena_map

    def test_missing_sight_config_raises_valueerror(self):
        """Simulating with a map that has no SightLineConfig raises ValueError."""
        from core.models import ArenaMap, MapZoneConfig, MapBaseConfig
        from matches.tests.conftest import make_team_with_slots

        arena_map = ArenaMap.objects.create(
            name="NoSight", img_width=100, img_height=100
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            zone_data=[[1, 1], [1, 1]],
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=25, y_px=25
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=75, y_px=75
        )

        team_red, _ = make_team_with_slots("SightErrR")
        team_blue, _ = make_team_with_slots("SightErrB")
        with pytest.raises(ValueError, match="sight lines"):
            ResourceBasedSimulator().simulate_single_round_detailed(
                team_red, team_blue, arena_map=arena_map
            )

    def test_walled_map_produces_no_cross_wall_tags(self):
        """With a wall separating both teams, no tags occur across the wall.

        The 3×3 fixture has a full wall column in the middle (col 1 is all 0).
        There is no path around the wall, so players are permanently confined to
        their spawn column and sight_data contains no cross-wall entries.
        """
        import random
        from matches.tests.conftest import make_team_with_slots

        random.seed(42)

        arena_map = self._make_map_with_wall()
        team_red, _ = make_team_with_slots("WallR")
        team_blue, _ = make_team_with_slots("WallB")

        game_round = ResourceBasedSimulator().simulate_single_round_detailed(
            team_red, team_blue, arena_map=arena_map
        )

        # With teams pinned to opposite sides of the wall, no enemy tags possible
        tag_events = list(
            GameEvent.objects.filter(game_round=game_round, event_type="tag")
        )
        assert (
            len(tag_events) == 0
        ), f"Expected no tags across the wall, but got {len(tag_events)}"

    def test_resolve_map_data_returns_sight_data(self):
        """_resolve_map_data 4th return value is a dict of frozensets."""
        arena_map = self._make_map_with_wall()
        _, _, _, sight_data = ResourceBasedSimulator._resolve_map_data(arena_map)

        assert isinstance(sight_data, dict)
        # Each value should be a frozenset
        for visible in sight_data.values():
            assert isinstance(visible, frozenset)
            break  # just check first entry
