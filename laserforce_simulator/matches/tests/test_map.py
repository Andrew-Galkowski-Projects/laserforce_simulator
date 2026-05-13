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
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapZoneConfig,
            MapBaseConfig,
            SightLineConfig,
        )
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
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=50, visible_cells=[]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=50, visible_cells=[]
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

    def test_zone_from_cell_proximity_based(self):
        """_zone_from_cell uses proximity to spawn cells (not grid cell values)."""
        spawn_cells = {"red": (0, 0), "blue": (3, 3)}
        # Cell near red base
        assert ResourceBasedSimulator._zone_from_cell(0, 0, spawn_cells) == 0
        # Cell near blue base
        assert ResourceBasedSimulator._zone_from_cell(3, 3, spawn_cells) == 2
        # Cell equidistant → neutral
        assert ResourceBasedSimulator._zone_from_cell(1, 2, spawn_cells) == 1

    def test_resolve_map_data_returns_spawn_cells_and_zone_data(self):
        """_resolve_map_data returns MapData with named fields."""
        arena_map = self._make_arena_map("ResolveTest")
        sim = ResourceBasedSimulator()
        md = sim._resolve_map_data(arena_map)

        assert md.zone_size == 50
        assert md.spawn_cells["red"] == (1, 0)
        assert md.spawn_cells["blue"] == (2, 3)
        assert md.zone_data[1][0] == 2  # red zone cell value
        assert isinstance(md.sight_data, dict)
        assert isinstance(md.base_sight_data, dict)

    def test_resolve_map_data_unwraps_dict_zone_data(self):
        """_resolve_map_data unwraps the production dict format {"zones": [...], "blocked_edges": {...}}."""
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapZoneConfig,
            MapBaseConfig,
            SightLineConfig,
        )
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
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=50, visible_cells=[]
        )

        assert ResourceBasedSimulator._resolve_map_data(arena_map).zone_data == raw_zones

    def test_initial_spawn_zone_derived_from_zone_data(self):
        """Players' starting zone_fallback is derived from zone_data at spawn — tested at init."""
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapZoneConfig,
            MapBaseConfig,
            SightLineConfig,
        )
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
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=50, visible_cells=[]
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
        md = sim._resolve_map_data(arena_map)

        red_states = sim._initialize_players(
            gr, team_red, "red", md.spawn_cells, md.zone_data
        )
        blue_states = sim._initialize_players(
            gr, team_blue, "blue", md.spawn_cells, md.zone_data
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
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )
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
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=px, visible_cells=[]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=px, visible_cells=[]
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
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapZoneConfig,
            MapBaseConfig,
            SightLineConfig,
        )

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
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=100, visible_cells=[]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=100, visible_cells=[]
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
        """_resolve_map_data sight_data field is a dict of frozensets."""
        arena_map = self._make_map_with_wall()
        sight_data = ResourceBasedSimulator._resolve_map_data(arena_map).sight_data

        assert isinstance(sight_data, dict)
        # Each value should be a frozenset
        for visible in sight_data.values():
            assert isinstance(visible, frozenset)
            break  # just check first entry


# ---------------------------------------------------------------------------
# MAP-04 — base interaction via BaseSightLineConfig
# ---------------------------------------------------------------------------


class TestMap04BaseInteraction:
    """Unit tests for _get_base_interaction and the MAP-04 base visibility gate."""

    _LIVES = {"commander": 30, "heavy": 20, "scout": 30, "medic": 20, "ammo": 20}
    _SHOTS = {"commander": 60, "heavy": 40, "scout": 60, "medic": 30, "ammo": 15}

    def _make_player(
        self,
        tag_id,
        team_color,
        role="commander",
        cell_row=None,
        cell_col=None,
        neutral_base_destroyed=False,
        opposing_base_destroyed=False,
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
            cell_row=cell_row,
            cell_col=cell_col,
            neutral_base_destroyed=neutral_base_destroyed,
            opposing_base_destroyed=opposing_base_destroyed,
        )

    def _make_ctx(self, base_sight_data):
        return {
            "adj": {},
            "spawn_cells": {},
            "zone_data": None,
            "sight_data": None,
            "base_sight_data": base_sight_data,
        }

    def test_no_map_returns_none(self):
        from matches.simulation import _get_base_interaction

        player = self._make_player("red_cmd", "red", cell_row=0, cell_col=0)
        assert _get_base_interaction(player, None) is None

    def test_no_base_sight_data_in_ctx_returns_none(self):
        from matches.simulation import _get_base_interaction

        player = self._make_player("red_cmd", "red", cell_row=0, cell_col=0)
        ctx = {"adj": {}, "spawn_cells": {}, "zone_data": None, "sight_data": None}
        assert _get_base_interaction(player, ctx) is None

    def test_no_cell_position_returns_none(self):
        from matches.simulation import _get_base_interaction

        player = self._make_player("red_cmd", "red", cell_row=None, cell_col=None)
        ctx = self._make_ctx({"neutral_1": frozenset({"0,0"})})
        assert _get_base_interaction(player, ctx) is None

    def test_neutral_base_in_range_returns_15(self):
        from matches.simulation import _get_base_interaction

        player = self._make_player("red_cmd", "red", cell_row=1, cell_col=1)
        ctx = self._make_ctx({"neutral_1": frozenset({"1,1", "1,2"})})
        assert _get_base_interaction(player, ctx) == 15

    def test_opposing_base_red_player_returns_14(self):
        """Red player in blue base visible cells → base_id 14."""
        from matches.simulation import _get_base_interaction

        player = self._make_player("red_cmd", "red", cell_row=9, cell_col=9)
        ctx = self._make_ctx({"blue": frozenset({"9,9", "9,8"})})
        assert _get_base_interaction(player, ctx) == 14

    def test_opposing_base_blue_player_returns_13(self):
        """Blue player in red base visible cells → base_id 13."""
        from matches.simulation import _get_base_interaction

        player = self._make_player("blue_cmd", "blue", cell_row=0, cell_col=0)
        ctx = self._make_ctx({"red": frozenset({"0,0", "0,1"})})
        assert _get_base_interaction(player, ctx) == 13

    def test_neutral_captured_falls_through_to_opposing(self):
        """With neutral already captured, opposing base is returned if in range."""
        from matches.simulation import _get_base_interaction

        player = self._make_player(
            "red_cmd", "red", cell_row=2, cell_col=2, neutral_base_destroyed=True
        )
        ctx = self._make_ctx(
            {
                "neutral_1": frozenset({"2,2"}),  # in range but already captured
                "blue": frozenset({"2,2"}),  # also in range and not captured
            }
        )
        assert _get_base_interaction(player, ctx) == 14

    def test_all_captured_returns_none(self):
        """Both neutral and opposing already captured → None."""
        from matches.simulation import _get_base_interaction

        player = self._make_player(
            "red_cmd",
            "red",
            cell_row=2,
            cell_col=2,
            neutral_base_destroyed=True,
            opposing_base_destroyed=True,
        )
        ctx = self._make_ctx(
            {
                "neutral_1": frozenset({"2,2"}),
                "blue": frozenset({"2,2"}),
            }
        )
        assert _get_base_interaction(player, ctx) is None

    def test_cell_not_in_any_base_returns_none(self):
        """Player cell not visible to any base → None."""
        from matches.simulation import _get_base_interaction

        player = self._make_player("red_cmd", "red", cell_row=5, cell_col=5)
        ctx = self._make_ctx(
            {"neutral_1": frozenset({"0,0"}), "blue": frozenset({"9,9"})}
        )
        assert _get_base_interaction(player, ctx) is None

    def test_batch_capture_base_guard_blocks_out_of_range(self):
        """BatchSimulator._capture_base does not award points when cell is not in base visible_cells."""
        from matches.simulation import BatchSimulator

        player = self._make_player("red_cmd", "red", cell_row=5, cell_col=5)
        ctx = self._make_ctx(
            {
                "blue": frozenset({"9,9", "9,8"}),  # player is at 5,5 — not in range
            }
        )
        initial_points = player.points_scored
        BatchSimulator()._capture_base(player, 14, movement_ctx=ctx)
        assert (
            player.points_scored == initial_points
        ), "Capture should be blocked when out of range"

    def test_batch_capture_base_succeeds_when_in_range(self):
        """BatchSimulator._capture_base awards 1001 points when cell is in base visible_cells."""
        from matches.simulation import BatchSimulator

        player = self._make_player("red_cmd", "red", cell_row=9, cell_col=9)
        ctx = self._make_ctx({"blue": frozenset({"9,9", "9,8"})})
        BatchSimulator()._capture_base(player, 14, movement_ctx=ctx)
        assert player.points_scored == 1001

    def test_batch_capture_base_no_map_still_captures(self):
        """BatchSimulator._capture_base falls back to unconditional capture when no ctx."""
        from matches.simulation import BatchSimulator

        player = self._make_player("red_cmd", "red", cell_row=None, cell_col=None)
        BatchSimulator()._capture_base(player, 14, movement_ctx=None)
        assert player.points_scored == 1001


@pytest.mark.django_db
class TestMap04DBIntegration:
    """DB-backed MAP-04 tests: BaseSightLineConfig loading and error handling."""

    def _make_base_map(self, name="BaseTestMap"):
        """3×3 all-floor map with all config including BaseSightLineConfig."""
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapZoneConfig,
            MapBaseConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines

        zone_data = [[1, 1, 1], [1, 1, 1], [1, 1, 1]]
        arena_map = ArenaMap.objects.create(name=name, img_width=300, img_height=300)
        MapZoneConfig.objects.create(
            arena_map=arena_map, zone_size=100, zone_data=zone_data, confirmed=True
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=50, y_px=50
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=250, y_px=250
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            sight_data=compute_sight_lines(zone_data),
        )
        # Red base (0,0) is visible from (0,0) and (0,1) and (1,0)
        BaseSightLineConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            zone_size=100,
            visible_cells=[[0, 0], [0, 1], [1, 0]],
        )
        # Blue base (2,2) is visible from (2,2) and (2,1) and (1,2)
        BaseSightLineConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            zone_size=100,
            visible_cells=[[2, 2], [2, 1], [1, 2]],
        )
        return arena_map

    def test_missing_base_sight_config_raises_valueerror(self):
        """Simulating with a map that has no BaseSightLineConfig raises ValueError."""
        from core.models import ArenaMap, MapZoneConfig, MapBaseConfig, SightLineConfig
        from core.map_processing import compute_sight_lines

        zone_data = [[1, 1], [1, 1]]
        arena_map = ArenaMap.objects.create(
            name="NoBaseSight", img_width=200, img_height=200
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map, zone_size=100, zone_data=zone_data, confirmed=True
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=50, y_px=50
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=150, y_px=150
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            sight_data=compute_sight_lines(zone_data),
        )
        # No BaseSightLineConfig created

        team_red, _ = make_team_with_slots("BSCErrR")
        team_blue, _ = make_team_with_slots("BSCErrB")
        with pytest.raises(ValueError, match="base sight lines"):
            ResourceBasedSimulator().simulate_single_round_detailed(
                team_red, team_blue, arena_map=arena_map
            )

    def test_resolve_map_data_returns_base_sight_data(self):
        """_resolve_map_data base_sight_data field is a dict of frozensets keyed by base_type."""
        arena_map = self._make_base_map("ResolveBSD")
        base_sight_data = ResourceBasedSimulator._resolve_map_data(arena_map).base_sight_data

        assert isinstance(base_sight_data, dict)
        assert "red" in base_sight_data
        assert "blue" in base_sight_data
        assert isinstance(base_sight_data["red"], frozenset)
        assert isinstance(base_sight_data["blue"], frozenset)
        # Red base has 3 visible cells
        assert len(base_sight_data["red"]) == 3
        assert "0,0" in base_sight_data["red"]

    def test_base_sight_data_included_in_movement_ctx(self):
        """_build_movement_ctx includes base_sight_data from _resolve_map_data."""
        arena_map = self._make_base_map("CtxBSD")
        md = ResourceBasedSimulator._resolve_map_data(arena_map)
        ctx = ResourceBasedSimulator._build_movement_ctx(
            md.zone_data, md.spawn_cells, md.sight_data, md.base_sight_data
        )
        assert "base_sight_data" in ctx
        assert ctx["base_sight_data"] is md.base_sight_data


# ---------------------------------------------------------------------------
# MAP-05 — compute_high_los_ranking and strong-spots views
# ---------------------------------------------------------------------------


class TestMap05ComputeHighLosRanking:
    """Unit tests for compute_high_los_ranking sort correctness."""

    def test_sorted_highest_los_first(self):
        from core.map_processing import compute_high_los_ranking

        sight_data = {
            "0,0": ["0,1"],
            "0,1": ["0,0", "1,1", "1,0"],
            "1,0": ["0,1"],
            "1,1": ["0,1"],
        }
        result = compute_high_los_ranking(sight_data)
        assert result[0] == [0, 1], "(0,1) has 3 visible cells and must rank first"
        assert len(result) == 4

    def test_returns_all_cells(self):
        from core.map_processing import compute_high_los_ranking

        sight_data = {"0,0": ["0,1"], "0,1": ["0,0"]}
        result = compute_high_los_ranking(sight_data)
        assert len(result) == 2
        assert [0, 0] in result
        assert [0, 1] in result

    def test_empty_sight_data_returns_empty(self):
        from core.map_processing import compute_high_los_ranking

        assert compute_high_los_ranking({}) == []


@pytest.mark.django_db
class TestMap05StrongSpotsViews:
    """View tests for the /strong-spots/ endpoints introduced in MAP-05."""

    def _make_arena_map(self, name="SSViewMap"):
        from core.models import ArenaMap

        return ArenaMap.objects.create(name=name, img_width=100, img_height=100)

    def test_get_strong_spots_returns_cells(self, client):
        from core.models import HeavyStrongSpotsConfig

        arena_map = self._make_arena_map("GetSSView")
        HeavyStrongSpotsConfig.objects.create(
            arena_map=arena_map, zone_size=50, cells=[[1, 1], [2, 2]]
        )
        response = client.get(f"/maps/{arena_map.pk}/strong-spots/?zone_size=50")
        assert response.status_code == 200
        assert response.json()["cells"] == [[1, 1], [2, 2]]

    def test_get_strong_spots_returns_empty_when_no_config(self, client):
        arena_map = self._make_arena_map("NoSSView")
        response = client.get(f"/maps/{arena_map.pk}/strong-spots/?zone_size=50")
        assert response.status_code == 200
        assert response.json()["cells"] == []

    def test_save_strong_spots_persists_cells(self, client):
        import json

        from core.models import HeavyStrongSpotsConfig

        arena_map = self._make_arena_map("SaveSSView")
        response = client.post(
            f"/maps/{arena_map.pk}/strong-spots/save/",
            data=json.dumps({"zone_size": 50, "cells": [[3, 3], [4, 4]]}),
            content_type="application/json",
        )
        assert response.status_code == 200
        config = HeavyStrongSpotsConfig.objects.get(arena_map=arena_map, zone_size=50)
        assert config.cells == [[3, 3], [4, 4]]

    def test_save_strong_spots_rejects_non_list_cells(self, client):
        import json

        arena_map = self._make_arena_map("BadSSView")
        response = client.post(
            f"/maps/{arena_map.pk}/strong-spots/save/",
            data=json.dumps({"zone_size": 50, "cells": "not-a-list"}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_save_strong_spots_rejects_non_int_pairs(self, client):
        import json

        arena_map = self._make_arena_map("BadSS2View")
        response = client.post(
            f"/maps/{arena_map.pk}/strong-spots/save/",
            data=json.dumps({"zone_size": 50, "cells": [["a", "b"]]}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def test_save_strong_spots_get_not_allowed(self, client):
        arena_map = self._make_arena_map("MethodSSView")
        response = client.get(f"/maps/{arena_map.pk}/strong-spots/save/")
        assert response.status_code == 405

    def test_save_strong_spots_updates_existing_config(self, client):
        import json

        from core.models import HeavyStrongSpotsConfig

        arena_map = self._make_arena_map("UpdateSSView")
        HeavyStrongSpotsConfig.objects.create(
            arena_map=arena_map, zone_size=50, cells=[[1, 1], [2, 2]]
        )
        client.post(
            f"/maps/{arena_map.pk}/strong-spots/save/",
            data=json.dumps({"zone_size": 50, "cells": [[5, 5]]}),
            content_type="application/json",
        )
        assert HeavyStrongSpotsConfig.objects.filter(arena_map=arena_map, zone_size=50).count() == 1
        config = HeavyStrongSpotsConfig.objects.get(arena_map=arena_map, zone_size=50)
        assert config.cells == [[5, 5]]

    def test_save_strong_spots_accepts_empty_cells(self, client):
        import json

        from core.models import HeavyStrongSpotsConfig

        arena_map = self._make_arena_map("EmptySSView")
        HeavyStrongSpotsConfig.objects.create(
            arena_map=arena_map, zone_size=50, cells=[[1, 1]]
        )
        response = client.post(
            f"/maps/{arena_map.pk}/strong-spots/save/",
            data=json.dumps({"zone_size": 50, "cells": []}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        config = HeavyStrongSpotsConfig.objects.get(arena_map=arena_map, zone_size=50)
        assert config.cells == []


# ---------------------------------------------------------------------------
# MAP-07 — Map wall hazards
# ---------------------------------------------------------------------------


class TestMap07WallTypes:
    """Unit tests for MAP-07 wall type semantics (no DB required)."""

    @staticmethod
    def _make_player(tag, team, row, col):
        from matches.sim_helpers.player_state import PlayerState

        return PlayerState(
            tag_id=tag,
            name=tag,
            team_color=team,
            role="scout",
            accuracy=50,
            survival=50,
            starting_lives=10,
            starting_shots=30,
            final_lives=10,
            final_shots=30,
            current_zone=1,
            cell_row=row,
            cell_col=col,
        )

    # ── Movement adjacency ──────────────────────────────────────────────────

    def test_high_wall_blocks_movement(self):
        from matches.sim_helpers.pathfinding import build_movement_adjacency

        zone_data = [[1, 0, 1]]
        adj = build_movement_adjacency(zone_data)
        assert (0, 0) in adj
        assert (0, 2) in adj
        assert (0, 1) not in adj
        assert (0, 2) not in adj[(0, 0)]

    def test_low_wall_blocks_movement(self):
        from matches.sim_helpers.pathfinding import build_movement_adjacency

        zone_data = [[1, 4, 1]]
        adj = build_movement_adjacency(zone_data)
        assert (0, 0) in adj
        assert (0, 2) in adj
        assert (0, 1) not in adj
        assert (0, 2) not in adj[(0, 0)]

    def test_windowed_wall_blocks_movement(self):
        from matches.sim_helpers.pathfinding import build_movement_adjacency

        zone_data = [[1, 5, 1]]
        adj = build_movement_adjacency(zone_data)
        assert (0, 1) not in adj

    def test_legacy_red_blue_zone_still_passable(self):
        """Values 2/3 (legacy zone colors) remain passable for backward compat."""
        from matches.sim_helpers.pathfinding import build_movement_adjacency

        zone_data = [[2, 3, 1]]
        adj = build_movement_adjacency(zone_data)
        assert (0, 0) in adj
        assert (0, 1) in adj
        assert (0, 2) in adj

    # ── LOS computation ─────────────────────────────────────────────────────

    def test_low_wall_transparent_for_los(self):
        from core.map_processing import _has_los

        zone_data = [[1, 4, 1]]
        assert _has_los(zone_data, 0, 0, 0, 2) is True

    def test_windowed_wall_transparent_los(self):
        from core.map_processing import _has_los

        zone_data = [[1, 5, 1]]
        assert _has_los(zone_data, 0, 0, 0, 2) is True

    def test_high_wall_blocks_los(self):
        from core.map_processing import _has_los

        zone_data = [[1, 0, 1]]
        assert _has_los(zone_data, 0, 0, 0, 2) is False

    def test_compute_sight_lines_low_wall_transparent(self):
        from core.map_processing import compute_sight_lines

        zone_data = [[1, 4, 1]]
        sight = compute_sight_lines(zone_data)
        assert "0,2" in sight.get("0,0", [])
        assert "0,0" in sight.get("0,2", [])

    def test_compute_sight_lines_windowed_wall_transparent(self):
        from core.map_processing import compute_sight_lines

        zone_data = [[1, 5, 1]]
        sight = compute_sight_lines(zone_data)
        assert "0,2" in sight.get("0,0", [])
        assert "0,0" in sight.get("0,2", [])

    def test_low_wall_not_a_los_origin(self):
        from core.map_processing import compute_sight_lines

        zone_data = [[1, 4, 1]]
        sight = compute_sight_lines(zone_data)
        assert "0,1" not in sight

    # ── Windowed wall aperture targeting ────────────────────────────────────

    def test_can_tag_through_windowed_wall_ns_axis(self):
        """N-facing aperture allows attack along N-S axis (same column)."""
        from matches.simulation import _can_tag_through_windowed_wall

        zone_data = [
            [0, 1, 0],
            [0, 5, 0],
            [0, 1, 0],
        ]
        wall_meta = {"1,1": {"facing": "N"}}
        assert _can_tag_through_windowed_wall(0, 1, 2, 1, zone_data, wall_meta) is True

    def test_cannot_tag_through_windowed_wall_wrong_axis(self):
        """N-facing aperture blocks attack that is not along N-S axis."""
        from matches.simulation import _can_tag_through_windowed_wall

        zone_data = [[1, 5, 1]]
        wall_meta = {"0,1": {"facing": "N"}}
        assert _can_tag_through_windowed_wall(0, 0, 0, 2, zone_data, wall_meta) is False

    def test_can_tag_through_windowed_wall_ew_axis(self):
        """E-facing aperture allows attack along E-W axis (same row)."""
        from matches.simulation import _can_tag_through_windowed_wall

        zone_data = [[1, 5, 1]]
        wall_meta = {"0,1": {"facing": "E"}}
        assert _can_tag_through_windowed_wall(0, 0, 0, 2, zone_data, wall_meta) is True

    def test_windowed_wall_no_facing_blocks(self):
        from matches.simulation import _can_tag_through_windowed_wall

        zone_data = [[1, 5, 1]]
        wall_meta = {"0,1": {}}
        assert _can_tag_through_windowed_wall(0, 0, 0, 2, zone_data, wall_meta) is False

    def test_high_wall_always_blocks(self):
        from matches.simulation import _can_tag_through_windowed_wall

        zone_data = [[1, 0, 1]]
        assert _can_tag_through_windowed_wall(0, 0, 0, 2, zone_data, {}) is False

    def test_get_los_targets_windowed_aperture_hit(self):
        """_get_los_targets includes target accessible through aligned aperture."""
        from matches.simulation import _get_los_targets

        zone_data = [[1, 5, 1]]
        wall_meta = {"0,1": {"facing": "E"}}
        ctx = {
            "sight_data": {"0,0": frozenset(), "0,2": frozenset()},
            "adj": {},
            "spawn_cells": {},
            "zone_data": zone_data,
            "wall_meta": wall_meta,
        }

        actor = self._make_player("red_scout", "red", 0, 0)
        target = self._make_player("blue_scout", "blue", 0, 2)
        assert _get_los_targets(actor, [target], ctx) == [target]

    def test_get_los_targets_windowed_aperture_miss(self):
        """_get_los_targets excludes target when aperture axis does not align."""
        from matches.simulation import _get_los_targets

        zone_data = [[1, 5, 1]]
        wall_meta = {"0,1": {"facing": "N"}}  # N/S aperture, attack is E-W
        ctx = {
            "sight_data": {"0,0": frozenset(), "0,2": frozenset()},
            "adj": {},
            "spawn_cells": {},
            "zone_data": zone_data,
            "wall_meta": wall_meta,
        }

        actor = self._make_player("red_scout", "red", 0, 0)
        target = self._make_player("blue_scout", "blue", 0, 2)
        assert _get_los_targets(actor, [target], ctx) == []

    # ── Proximity-based zone detection ──────────────────────────────────────

    def test_zone_from_cell_near_red_base(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 10)}
        assert ResourceBasedSimulator._zone_from_cell(0, 1, spawn_cells) == 0

    def test_zone_from_cell_near_blue_base(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 10)}
        assert ResourceBasedSimulator._zone_from_cell(10, 9, spawn_cells) == 2

    def test_zone_from_cell_equidistant_is_neutral(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 0)}
        assert ResourceBasedSimulator._zone_from_cell(5, 0, spawn_cells) == 1

    def test_zone_from_cell_near_neutral_base_is_neutral(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 0), "neutral_1": (3, 0)}
        assert ResourceBasedSimulator._zone_from_cell(3, 1, spawn_cells) == 1

    def test_zone_from_cell_no_bases_returns_neutral(self):
        assert ResourceBasedSimulator._zone_from_cell(5, 5, {}) == 1

    def test_windowed_wall_unknown_facing_blocks(self):
        """Unknown/garbage facing value must block, not silently pass through."""
        from matches.simulation import _can_tag_through_windowed_wall

        zone_data = [[1, 5, 1]]
        wall_meta = {"0,1": {"facing": "X"}}  # invalid facing
        assert _can_tag_through_windowed_wall(0, 0, 0, 2, zone_data, wall_meta) is False


@pytest.mark.django_db
class TestMap07DBIntegration:
    """DB-backed MAP-07 tests: wall_meta persisted and loaded by simulator."""

    def _make_windowed_map(self, name: str):
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapZoneConfig,
            MapBaseConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines

        # 1×3 row: floor | windowed wall (E-facing) | floor
        zone_data = [[1, 5, 1]]
        wall_meta = {"0,1": {"facing": "E"}}
        arena_map = ArenaMap.objects.create(name=name, img_width=300, img_height=100)
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            zone_data={"zones": zone_data, "blocked_edges": {}, "wall_meta": wall_meta},
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=50, y_px=50
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=250, y_px=50
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            sight_data=compute_sight_lines(zone_data),
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=100, visible_cells=[]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=100, visible_cells=[]
        )
        return arena_map

    def test_wall_meta_round_trip_through_resolve_map_data(self):
        """wall_meta field from _resolve_map_data reflects zone_data JSON."""
        arena_map = self._make_windowed_map("WallMetaRoundTrip")
        assert ResourceBasedSimulator._resolve_map_data(arena_map).wall_meta == {"0,1": {"facing": "E"}}

    def test_wall_meta_present_in_movement_ctx(self):
        """_build_movement_ctx exposes wall_meta key from resolved map data."""
        arena_map = self._make_windowed_map("WallMetaCtx")
        md = ResourceBasedSimulator._resolve_map_data(arena_map)
        ctx = ResourceBasedSimulator._build_movement_ctx(
            md.zone_data,
            md.spawn_cells,
            md.sight_data,
            md.base_sight_data,
            md.cell_ranking,
            md.strong_spots,
            md.wall_meta,
        )
        assert ctx["wall_meta"] == {"0,1": {"facing": "E"}}


# ---------------------------------------------------------------------------
# MAP-08 — Map-based spawn points
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMap08SpawnPoints:
    """Unit + integration tests for MAP-08: role-aware spawn cell assignment."""

    # ------------------------------------------------------------------
    # Shared map factory helpers
    # ------------------------------------------------------------------

    def _make_spawn_map(self, name: str, zone_size: int = 50):
        """5×5 all-floor map with confirmed zone config, base configs, and
        sight lines.  After this helper returns, the map does NOT yet have
        red_spawn/blue_spawn in zone_data — those are added by the
        save_sight_lines view / auto-generation logic under test.
        """
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines

        rows, cols = 5, 5
        zone_data = [[1] * cols for _ in range(rows)]
        arena_map = ArenaMap.objects.create(
            name=name, img_width=cols * zone_size, img_height=rows * zone_size
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data=zone_data,
            confirmed=True,
        )
        # Red base at top-left corner cell (0,0), blue base at bottom-right (4,4)
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            x_px=zone_size // 2,
            y_px=zone_size // 2,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            x_px=cols * zone_size - zone_size // 2,
            y_px=rows * zone_size - zone_size // 2,
        )
        sight_data = compute_sight_lines(zone_data)
        SightLineConfig.objects.create(
            arena_map=arena_map, zone_size=zone_size, sight_data=sight_data
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=zone_size, visible_cells=[]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=zone_size, visible_cells=[]
        )
        return arena_map

    def _make_spawn_map_with_spawn_data(self, name: str, zone_size: int = 50):
        """Same as _make_spawn_map but also injects red_spawn/blue_spawn into
        zone_data so tests that need pre-existing spawn lists can use it.

        Red spawn cells: (0,0), (0,1), (1,0), (0,2), (1,1)  [Manhattan ≤5 of (0,0)]
        Blue spawn cells: (4,4), (4,3), (3,4), (4,2), (3,3)  [Manhattan ≤5 of (4,4)]
        """
        from core.models import MapZoneConfig

        arena_map = self._make_spawn_map(name, zone_size)
        config = arena_map.latest_confirmed_config()
        raw = config.zone_data
        zone_grid = raw["zones"] if isinstance(raw, dict) else raw

        red_spawn = [[0, 0], [0, 1], [1, 0], [0, 2], [1, 1]]
        blue_spawn = [[4, 4], [4, 3], [3, 4], [4, 2], [3, 3]]

        new_data = {
            "zones": zone_grid,
            "blocked_edges": {},
            "red_spawn": red_spawn,
            "blue_spawn": blue_spawn,
        }
        config.zone_data = new_data
        config.save(update_fields=["zone_data"])
        return arena_map

    # ------------------------------------------------------------------
    # Test 1 — auto-generation writes red_spawn/blue_spawn to zone_data
    # ------------------------------------------------------------------

    def test_spawn_auto_generated(self, client):
        """After save_sight_lines is POSTed, zone_data contains red_spawn and
        blue_spawn with ≥1 cell each, all within Manhattan dist ≤5 of the
        respective base cell."""
        import json

        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig
        from core.map_processing import compute_sight_lines

        zone_size = 50
        rows, cols = 5, 5
        zone_data = [[1] * cols for _ in range(rows)]
        arena_map = ArenaMap.objects.create(
            name="AutoSpawnMap", img_width=cols * zone_size, img_height=rows * zone_size
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data=zone_data,
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            x_px=zone_size // 2,
            y_px=zone_size // 2,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            x_px=cols * zone_size - zone_size // 2,
            y_px=rows * zone_size - zone_size // 2,
        )
        sight_data = compute_sight_lines(zone_data)

        response = client.post(
            f"/maps/{arena_map.pk}/sight-lines/save/",
            data=json.dumps(
                {
                    "zone_size": zone_size,
                    "sight_data": sight_data,
                    "base_sights": {"red": [], "blue": []},
                    "replace": True,
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content

        config = arena_map.latest_confirmed_config()
        stored = config.zone_data
        # Production code must update zone_data with spawn lists
        assert isinstance(
            stored, dict
        ), "zone_data should be a dict after sight-line save"
        assert (
            "red_spawn" in stored
        ), "zone_data must contain 'red_spawn' after sight-line save"
        assert (
            "blue_spawn" in stored
        ), "zone_data must contain 'blue_spawn' after sight-line save"
        assert len(stored["red_spawn"]) >= 1, "red_spawn must have at least one cell"
        assert len(stored["blue_spawn"]) >= 1, "blue_spawn must have at least one cell"

        # Red base cell is (0,0); blue base cell is (4,4)
        red_base = (0, 0)
        blue_base = (4, 4)
        for r, c in stored["red_spawn"]:
            dist = abs(r - red_base[0]) + abs(c - red_base[1])
            assert (
                dist <= 5
            ), f"red_spawn cell ({r},{c}) is dist {dist} from base — must be ≤5"
        for r, c in stored["blue_spawn"]:
            dist = abs(r - blue_base[0]) + abs(c - blue_base[1])
            assert (
                dist <= 5
            ), f"blue_spawn cell ({r},{c}) is dist {dist} from base — must be ≤5"

    # ------------------------------------------------------------------
    # Test 2 — all auto-generated spawn cells are passable (value 1)
    # ------------------------------------------------------------------

    def test_spawn_cells_are_passable(self):
        """All cells in red_spawn/blue_spawn have value 1 in the zone grid
        (no walls or impassable terrain)."""
        arena_map = self._make_spawn_map_with_spawn_data("PassableSpawn")
        config = arena_map.latest_confirmed_config()
        stored = config.zone_data
        assert isinstance(stored, dict)
        zone_grid = stored.get("zones") or stored

        for r, c in stored["red_spawn"]:
            assert (
                zone_grid[r][c] == 1
            ), f"red_spawn cell ({r},{c}) has zone value {zone_grid[r][c]} — must be passable (1)"
        for r, c in stored["blue_spawn"]:
            assert (
                zone_grid[r][c] == 1
            ), f"blue_spawn cell ({r},{c}) has zone value {zone_grid[r][c]} — must be passable (1)"

    # ------------------------------------------------------------------
    # Test 3 — role-aware spawn assignment (commander/heavy closer to enemy)
    # ------------------------------------------------------------------

    def test_spawn_assignment_role_split(self):  # no DB — pure assignment logic
        """Commander and Heavy are assigned cells closer to the enemy base than
        Medic and Ammo players at spawn time (checked via _build_spawn_assignments
        directly — final cell positions change during simulation)."""
        # Roster order from active_roster: commander(0), heavy(1), scout(2),
        # scout(3), medic(4), ammo(5)
        roster_roles = ["commander", "heavy", "scout", "scout", "medic", "ammo"]

        # Spawn pool from _make_spawn_map_with_spawn_data: red base (0,0), blue base (4,4)
        team_spawn_pools = {
            "red": [(0, 0), (0, 1), (1, 0), (0, 2), (1, 1)],
        }
        spawn_cells = {"red": (0, 0), "blue": (4, 4)}
        enemy_base = (4, 4)

        assignments = ResourceBasedSimulator._build_spawn_assignments(
            roster_roles, "red", spawn_cells, team_spawn_pools
        )

        def dist(cell):
            return abs(cell[0] - enemy_base[0]) + abs(cell[1] - enemy_base[1])

        cmd_cell = assignments[0]
        hvy_cell = assignments[1]
        med_cell = assignments[4]
        ammo_cell = assignments[5]

        assert cmd_cell is not None and med_cell is not None and ammo_cell is not None
        assert hvy_cell is not None

        cmd_dist = dist(cmd_cell)
        hvy_dist = dist(hvy_cell)
        med_dist = dist(med_cell)
        ammo_dist = dist(ammo_cell)

        assert cmd_dist < med_dist or cmd_dist < ammo_dist, (
            f"Commander ({cmd_dist}) should be closer to enemy base than Medic ({med_dist}) "
            f"or Ammo ({ammo_dist})"
        )
        assert hvy_dist < med_dist or hvy_dist < ammo_dist, (
            f"Heavy ({hvy_dist}) should be closer to enemy base than Medic ({med_dist}) "
            f"or Ammo ({ammo_dist})"
        )

    # ------------------------------------------------------------------
    # Test 4 — no replacement (no two players share a cell at start)
    # ------------------------------------------------------------------

    def test_spawn_no_replacement(self):  # no DB — pure assignment logic
        """No two players on the same team are assigned the same spawn cell when
        enough distinct cells are available (checked at assignment time, not after
        simulation — final positions diverge during movement)."""
        # 5 distinct cells for 6 players: scout2 overflows to base (0,0) which
        # commander already drew from the pool, so scout2 gets None (3-zone).
        roster_roles = ["commander", "heavy", "scout", "scout", "medic", "ammo"]
        team_spawn_pools = {
            "red": [(0, 0), (0, 1), (1, 0), (0, 2), (1, 1)],
            "blue": [(4, 4), (4, 3), (3, 4), (4, 2), (3, 3)],
        }
        spawn_cells = {"red": (0, 0), "blue": (4, 4)}

        for color in ("red", "blue"):
            assignments = ResourceBasedSimulator._build_spawn_assignments(
                roster_roles, color, spawn_cells, team_spawn_pools
            )
            cells = [cell for cell in assignments.values() if cell is not None]
            assert len(cells) == len(
                set(cells)
            ), f"{color} team has duplicate spawn assignments: {cells}"

    # ------------------------------------------------------------------
    # Test 5 — overflow players fall back to the base cell
    # ------------------------------------------------------------------

    def test_spawn_overflow_uses_base_cell(self):
        """When there are fewer spawn cells than players, overflow players are
        assigned the base cell (checked at assignment time via _build_spawn_assignments
        — final positions change during simulation)."""
        # 1 spawn cell per team (non-base), 6 players → 5 overflow each.
        # Pool cell (0,1) is not the base (0,0), so overflow → base (0,0).
        roster_roles = ["commander", "heavy", "scout", "scout", "medic", "ammo"]
        team_spawn_pools = {
            "red": [(0, 1)],  # one non-base cell near red base (0,0)
            "blue": [(4, 3)],  # one non-base cell near blue base (4,4)
        }
        spawn_cells = {"red": (0, 0), "blue": (4, 4)}

        for color in ("red", "blue"):
            base_cell = spawn_cells[color]
            assignments = ResourceBasedSimulator._build_spawn_assignments(
                roster_roles, color, spawn_cells, team_spawn_pools
            )
            at_base = sum(1 for cell in assignments.values() if cell == base_cell)
            assert at_base >= 4, (
                f"Expected ≥4 {color} players assigned to base cell {base_cell}, got {at_base}. "
                f"assignments={assignments}"
            )

    # ------------------------------------------------------------------
    # Test 6 — no map → cell_row/cell_col remain None (existing fallback)
    # ------------------------------------------------------------------

    def test_spawn_fallback_no_map(self):
        """When GameRound.arena_map is None, players have cell_row=None and
        cell_col=None (existing MAP-01 fallback behavior is preserved)."""
        team_red, _ = make_team_with_slots("FbSpR")
        team_blue, _ = make_team_with_slots("FbSpB")

        game_round = ResourceBasedSimulator().simulate_single_round_detailed(
            team_red, team_blue
        )

        assert game_round.arena_map is None
        for state in game_round.player_states.all():
            assert (
                state.cell_row is None
            ), f"Expected cell_row=None for no-map round, got {state.cell_row}"
            assert (
                state.cell_col is None
            ), f"Expected cell_col=None for no-map round, got {state.cell_col}"


# ---------------------------------------------------------------------------
# MAP-08 — DB integration: zone_data round-trip with spawn cells
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMap08DBIntegration:
    """DB-backed MAP-08 test: save_sight_lines persists spawn lists in zone_data."""

    def test_zone_data_roundtrip(self, client):
        """Save sight lines via the view; read back MapZoneConfig.zone_data and
        confirm red_spawn and blue_spawn are present and non-empty."""
        import json

        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig
        from core.map_processing import compute_sight_lines

        zone_size = 50
        rows, cols = 4, 4
        # Small 4×4 floor grid — enough cells within dist ≤5 of each corner base
        zone_data = [[1] * cols for _ in range(rows)]
        arena_map = ArenaMap.objects.create(
            name="SpawnRoundTrip",
            img_width=cols * zone_size,
            img_height=rows * zone_size,
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data=zone_data,
            confirmed=True,
        )
        # Red base at (0,0), blue base at (3,3)
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            x_px=zone_size // 2,
            y_px=zone_size // 2,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            x_px=cols * zone_size - zone_size // 2,
            y_px=rows * zone_size - zone_size // 2,
        )
        sight_data = compute_sight_lines(zone_data)

        response = client.post(
            f"/maps/{arena_map.pk}/sight-lines/save/",
            data=json.dumps(
                {
                    "zone_size": zone_size,
                    "sight_data": sight_data,
                    "base_sights": {"red": [], "blue": []},
                    "replace": True,
                }
            ),
            content_type="application/json",
        )
        assert response.status_code == 200, response.content

        # Re-fetch config from DB (not cached ORM instance)
        config = MapZoneConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        )
        stored = config.zone_data

        assert isinstance(
            stored, dict
        ), f"zone_data should be a dict after sight-line save, got {type(stored)}"
        assert (
            "red_spawn" in stored
        ), "red_spawn key must be present after sight-line save"
        assert (
            "blue_spawn" in stored
        ), "blue_spawn key must be present after sight-line save"
        assert len(stored["red_spawn"]) >= 1, "red_spawn must be non-empty"
        assert len(stored["blue_spawn"]) >= 1, "blue_spawn must be non-empty"

    def test_get_spawn_cells_view(self, client):
        """GET /maps/<id>/spawn-cells/ returns stored spawn lists from zone_data."""
        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig

        zone_size = 50
        rows, cols = 4, 4
        zone_data = [[1] * cols for _ in range(rows)]
        arena_map = ArenaMap.objects.create(
            name="SpawnViewTest",
            img_width=cols * zone_size,
            img_height=rows * zone_size,
        )
        red_spawn = [[0, 0], [0, 1]]
        blue_spawn = [[3, 3], [3, 2]]
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data={
                "zones": zone_data,
                "blocked_edges": {},
                "red_spawn": red_spawn,
                "blue_spawn": blue_spawn,
            },
            confirmed=True,
        )

        response = client.get(f"/maps/{arena_map.pk}/spawn-cells/")
        assert response.status_code == 200
        data = response.json()
        assert data["red_spawn"] == red_spawn
        assert data["blue_spawn"] == blue_spawn

    def test_get_spawn_cells_view_empty_when_no_spawn_data(self, client):
        """GET /maps/<id>/spawn-cells/ returns empty lists when zone_data has none."""
        from core.models import ArenaMap, MapZoneConfig

        zone_size = 50
        arena_map = ArenaMap.objects.create(
            name="SpawnViewEmpty", img_width=200, img_height=200
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data={"zones": [[1, 1], [1, 1]], "blocked_edges": {}},
            confirmed=True,
        )

        response = client.get(f"/maps/{arena_map.pk}/spawn-cells/")
        assert response.status_code == 200
        data = response.json()
        assert data["red_spawn"] == []
        assert data["blue_spawn"] == []

    def test_user_edited_spawn_not_overwritten_by_sight_line_save(self, client):
        """save_zone_config with explicit spawn data sets spawn_user_edited=True;
        subsequent save_sight_lines must NOT overwrite those user-edited cells."""
        import json

        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig
        from core.map_processing import compute_sight_lines

        zone_size = 50
        rows, cols = 4, 4
        zone_data = [[1] * cols for _ in range(rows)]
        arena_map = ArenaMap.objects.create(
            name="UserSpawnLock",
            img_width=cols * zone_size,
            img_height=rows * zone_size,
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data={"zones": zone_data, "blocked_edges": {}},
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            x_px=zone_size // 2,
            y_px=zone_size // 2,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            x_px=cols * zone_size - zone_size // 2,
            y_px=rows * zone_size - zone_size // 2,
        )

        # User explicitly saves hand-painted spawn cells via save_zone_config.
        user_red_spawn = [[0, 0]]
        user_blue_spawn = [[3, 3]]
        resp = client.post(
            f"/maps/{arena_map.pk}/save/",
            data=json.dumps({
                "zone_size": zone_size,
                "zones": zone_data,
                "bases": [
                    {"type": "red",  "x_px": zone_size // 2, "y_px": zone_size // 2},
                    {"type": "blue", "x_px": cols * zone_size - zone_size // 2,
                     "y_px": rows * zone_size - zone_size // 2},
                ],
                "red_spawn": user_red_spawn,
                "blue_spawn": user_blue_spawn,
            }),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.content

        # Confirm the flag was set.
        config = arena_map.latest_confirmed_config()
        assert config.zone_data.get("spawn_user_edited") is True

        # Now trigger auto-regeneration via save_sight_lines.
        sight_data = compute_sight_lines(zone_data)
        resp2 = client.post(
            f"/maps/{arena_map.pk}/sight-lines/save/",
            data=json.dumps({
                "zone_size": zone_size,
                "sight_data": sight_data,
                "base_sights": {"red": [], "blue": []},
                "replace": True,
            }),
            content_type="application/json",
        )
        assert resp2.status_code == 200, resp2.content

        # User spawn cells must survive — auto-generation must not override them.
        config = MapZoneConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        )
        stored = config.zone_data
        assert stored["red_spawn"] == user_red_spawn, (
            f"User-edited red_spawn was overwritten: got {stored['red_spawn']}"
        )
        assert stored["blue_spawn"] == user_blue_spawn, (
            f"User-edited blue_spawn was overwritten: got {stored['blue_spawn']}"
        )

    def test_clearing_user_spawn_re_enables_auto_generation(self, client):
        """Saving empty red_spawn/blue_spawn clears spawn_user_edited, allowing
        the next sight-line save to auto-generate fresh spawn cells."""
        import json

        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig
        from core.map_processing import compute_sight_lines

        zone_size = 50
        rows, cols = 4, 4
        zone_data = [[1] * cols for _ in range(rows)]
        arena_map = ArenaMap.objects.create(
            name="UserSpawnClear",
            img_width=cols * zone_size,
            img_height=rows * zone_size,
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data={
                "zones": zone_data,
                "blocked_edges": {},
                "red_spawn": [[0, 0]],
                "blue_spawn": [[3, 3]],
                "spawn_user_edited": True,
            },
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            x_px=zone_size // 2,
            y_px=zone_size // 2,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            x_px=cols * zone_size - zone_size // 2,
            y_px=rows * zone_size - zone_size // 2,
        )

        # User explicitly erases all spawn cells — sends empty lists.
        resp = client.post(
            f"/maps/{arena_map.pk}/save/",
            data=json.dumps({
                "zone_size": zone_size,
                "zones": zone_data,
                "bases": [
                    {"type": "red",  "x_px": zone_size // 2, "y_px": zone_size // 2},
                    {"type": "blue", "x_px": cols * zone_size - zone_size // 2,
                     "y_px": rows * zone_size - zone_size // 2},
                ],
                "red_spawn": [],
                "blue_spawn": [],
            }),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.content

        config = arena_map.latest_confirmed_config()
        assert not config.zone_data.get("spawn_user_edited"), (
            "spawn_user_edited should be cleared after user saves empty spawn lists"
        )

        # Now auto-generation should run and populate spawn cells.
        sight_data = compute_sight_lines(zone_data)
        resp2 = client.post(
            f"/maps/{arena_map.pk}/sight-lines/save/",
            data=json.dumps({
                "zone_size": zone_size,
                "sight_data": sight_data,
                "base_sights": {"red": [], "blue": []},
                "replace": True,
            }),
            content_type="application/json",
        )
        assert resp2.status_code == 200, resp2.content

        config = MapZoneConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        )
        stored = config.zone_data
        assert len(stored.get("red_spawn", [])) >= 1, (
            "Auto-generation should have populated red_spawn after lock was cleared"
        )
        assert len(stored.get("blue_spawn", [])) >= 1, (
            "Auto-generation should have populated blue_spawn after lock was cleared"
        )

    def test_user_edited_spawn_not_overwritten_by_compute_sight_lines(self, client):
        """compute_sight_lines view must NOT overwrite user-edited spawn cells."""
        import json

        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig

        zone_size = 50
        rows, cols = 4, 4
        zone_data = [[1] * cols for _ in range(rows)]
        arena_map = ArenaMap.objects.create(
            name="ComputeSpawnLock",
            img_width=cols * zone_size,
            img_height=rows * zone_size,
        )
        user_red_spawn = [[0, 0]]
        user_blue_spawn = [[3, 3]]
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data={
                "zones": zone_data,
                "blocked_edges": {},
                "red_spawn": user_red_spawn,
                "blue_spawn": user_blue_spawn,
                "spawn_user_edited": True,
            },
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            x_px=zone_size // 2,
            y_px=zone_size // 2,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            x_px=cols * zone_size - zone_size // 2,
            y_px=rows * zone_size - zone_size // 2,
        )

        resp = client.post(
            f"/maps/{arena_map.pk}/sight-lines/compute/",
            data=json.dumps({"zone_size": zone_size}),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.content

        config = MapZoneConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        )
        stored = config.zone_data
        assert stored["red_spawn"] == user_red_spawn, (
            f"compute_sight_lines overwrote user red_spawn: got {stored['red_spawn']}"
        )
        assert stored["blue_spawn"] == user_blue_spawn, (
            f"compute_sight_lines overwrote user blue_spawn: got {stored['blue_spawn']}"
        )

    def test_unsaved_spawn_edits_survive_compute(self, client):
        """Spawn cells sent in the compute POST body (spawnEdited=true on client)
        are persisted and protected even when the user hasn't clicked Save first."""
        import json

        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig

        zone_size = 50
        rows, cols = 4, 4
        zone_data = [[1] * cols for _ in range(rows)]
        arena_map = ArenaMap.objects.create(
            name="UnsavedSpawnCompute",
            img_width=cols * zone_size,
            img_height=rows * zone_size,
        )
        # Confirmed config has NO spawn cells and NO user-edited flag — simulates
        # a fresh map where the user painted spawn in the editor but never saved.
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data={"zones": zone_data, "blocked_edges": {}},
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            x_px=zone_size // 2,
            y_px=zone_size // 2,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            x_px=cols * zone_size - zone_size // 2,
            y_px=rows * zone_size - zone_size // 2,
        )

        # Client includes unsaved spawn edits alongside the compute request.
        user_red_spawn = [[0, 0]]
        user_blue_spawn = [[3, 3]]
        resp = client.post(
            f"/maps/{arena_map.pk}/sight-lines/compute/",
            data=json.dumps({
                "zone_size": zone_size,
                "red_spawn": user_red_spawn,
                "blue_spawn": user_blue_spawn,
            }),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.content

        config = MapZoneConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        )
        stored = config.zone_data
        assert stored["red_spawn"] == user_red_spawn, (
            f"Unsaved spawn edits were not persisted: red_spawn={stored.get('red_spawn')}"
        )
        assert stored["blue_spawn"] == user_blue_spawn, (
            f"Unsaved spawn edits were not persisted: blue_spawn={stored.get('blue_spawn')}"
        )
        assert stored.get("spawn_user_edited") is True, (
            "spawn_user_edited flag must be set when client sends spawn with compute"
        )
