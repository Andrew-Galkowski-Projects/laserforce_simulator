"""
Map-related tests: MAP-01 cell grid / spawn coordinates and MAP-02 pathfinding movement.

SIM-09 note: tests that used to call ``ResourceBasedSimulator`` static helpers
(``_zone_from_cell`` / ``_resolve_map_data`` / ``_build_movement_ctx`` /
``_build_spawn_assignments``) now call the free functions in
``matches.sim_helpers.map_loader``. End-to-end RBS round integration tests
have been ported to ``BatchSimulator`` with ``patch.object(BatchSimulator,
"ROUND_TICKS", 40)`` for speed. A few RBS-specific tests (legacy
``_simulate_combat_exchange`` patching, ``_move_to_cell`` event-buffer
emission) had no clean BatchSim equivalent and were dropped here — equivalent
in-memory movement is already covered by ``test_batch_sim.py`` /
``test_hold_overwatch.py``.
"""

import pytest
from unittest.mock import patch

from matches.models import GameRound, PlayerRoundState, GameEvent
from matches.simulation import BatchSimulator
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.map_loader import (
    build_spawn_assignments,
    resolve_map_data,
    zone_from_cell,
)
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
        # SIM-09: BatchSimulator now does single-round persistence; patch
        # ROUND_TICKS for test speed (40 ticks = 20 s).
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            game_round = BatchSimulator().simulate_single_round_detailed(
                team_red, team_blue
            )

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
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            game_round = BatchSimulator().simulate_single_round_detailed(
                team_red, team_blue, arena_map=arena_map
            )

        assert game_round.arena_map == arena_map
        assert game_round.zone_size == 50

    def test_map_simulation_sets_cell_coordinates(self):
        """Players end the round with valid cell coordinates within the grid bounds."""
        team_red, _ = make_team_with_slots("CellR")
        team_blue, _ = make_team_with_slots("CellB")
        arena_map = self._make_arena_map("CellCoordTest")
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            game_round = BatchSimulator().simulate_single_round_detailed(
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
        assert zone_from_cell(0, 0, spawn_cells) == 0
        # Cell near blue base
        assert zone_from_cell(3, 3, spawn_cells) == 2
        # Cell equidistant → neutral
        assert zone_from_cell(1, 2, spawn_cells) == 1

    def test_resolve_map_data_returns_spawn_cells_and_zone_data(self):
        """resolve_map_data returns MapData with named fields."""
        arena_map = self._make_arena_map("ResolveTest")
        md = resolve_map_data(arena_map)

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

        assert resolve_map_data(arena_map).zone_data == raw_zones

    # SIM-09 note: test_initial_spawn_zone_derived_from_zone_data was dropped —
    # it exercised RBS's ``_initialize_players`` which creates DB-backed
    # ``PlayerRoundState`` rows. BatchSim's ``_make_players`` builds
    # ``PlayerState`` dataclasses; that path is covered by test_batch_sim.py.

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
        with pytest.raises(ValueError, match="red base"):
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                BatchSimulator().simulate_single_round_detailed(
                    team_red, team_blue, arena_map=arena_map
                )

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
        with pytest.raises(ValueError, match="confirmed zone configuration"):
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                BatchSimulator().simulate_single_round_detailed(
                    team_red, team_blue, arena_map=arena_map
                )


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

    # SIM-09 note: test_move_to_cell_creates_game_event_with_metadata and
    # test_fallback_no_map exercised the legacy RBS-only ``cell_row``/
    # ``cell_col`` movement-event metadata shape (pre-MOVE-01). The compact
    # ``start_row``/``start_col``/``end_row``/``end_col`` shape is covered by
    # ``TestMove01CompactMovementEvent`` below.

    def test_player_advances_toward_enemy_base(self):
        import random

        random.seed(42)

        arena_map = self._make_map("Test10x10", _FLOOR_10X10)
        team_red, _ = make_team_with_slots("ReachR")
        team_blue, _ = make_team_with_slots("ReachB")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            game_round = BatchSimulator().simulate_single_round_detailed(
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
        ctx = MapContext.from_dict(
            {
                "sight_data": sight_data,
                "adj": {},
                "spawn_cells": {},
                "zone_data": None,
            }
        )

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
        ctx = MapContext.from_dict(
            {
                "sight_data": sight_data,
                "adj": {},
                "spawn_cells": {},
                "zone_data": None,
            }
        )

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
        ctx = MapContext.from_dict(
            {
                "sight_data": sight_data,
                "adj": {},
                "spawn_cells": {},
                "zone_data": None,
            }
        )

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
        ctx = MapContext.from_dict(
            {
                "sight_data": sight_data,
                "adj": {},
                "spawn_cells": {},
                "zone_data": None,
            }
        )

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
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                BatchSimulator().simulate_single_round_detailed(
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

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            game_round = BatchSimulator().simulate_single_round_detailed(
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
        sight_data = resolve_map_data(arena_map).sight_data

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
        return MapContext.from_dict(
            {
                "adj": {},
                "spawn_cells": {},
                "zone_data": None,
                "sight_data": None,
                "base_sight_data": base_sight_data,
            }
        )

    def test_no_map_returns_none(self):
        from matches.simulation import _get_base_interaction

        player = self._make_player("red_cmd", "red", cell_row=0, cell_col=0)
        assert _get_base_interaction(player, None) is None

    def test_no_base_sight_data_in_ctx_returns_none(self):
        from matches.simulation import _get_base_interaction

        player = self._make_player("red_cmd", "red", cell_row=0, cell_col=0)
        ctx = MapContext.from_dict(
            {"adj": {}, "spawn_cells": {}, "zone_data": None, "sight_data": None}
        )
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
        initial_points = player.counters.points_scored
        BatchSimulator()._capture_base(player, 14, movement_ctx=ctx)
        assert (
            player.counters.points_scored == initial_points
        ), "Capture should be blocked when out of range"

    def test_batch_capture_base_succeeds_when_in_range(self):
        """BatchSimulator._capture_base awards 1001 points when cell is in base visible_cells."""
        from matches.simulation import BatchSimulator

        player = self._make_player("red_cmd", "red", cell_row=9, cell_col=9)
        ctx = self._make_ctx({"blue": frozenset({"9,9", "9,8"})})
        BatchSimulator()._capture_base(player, 14, movement_ctx=ctx)
        assert player.counters.points_scored == 1001

    def test_batch_capture_base_no_map_still_captures(self):
        """BatchSimulator._capture_base falls back to unconditional capture when no ctx."""
        from matches.simulation import BatchSimulator

        player = self._make_player("red_cmd", "red", cell_row=None, cell_col=None)
        BatchSimulator()._capture_base(player, 14, movement_ctx=None)
        assert player.counters.points_scored == 1001

    def test_downed_player_does_not_plan_capture(self):
        """MECH-15: a player in the respawn cooldown plans no base capture.

        Force the weighted roll to pick ``capture_base`` so the only thing the
        downed player's emptiness can be attributed to is the active-state gate.
        """
        from matches.sim_helpers.combat import plan_action

        ctx = self._make_ctx({"blue": frozenset({"9,9", "9,8"})})
        downed = self._make_player("red_cmd", "red", cell_row=9, cell_col=9)
        down_tick = 100
        downed.last_downed_time = down_tick  # in the respawn cooldown
        with patch(
            "matches.sim_helpers.combat.random.choices",
            return_value=["capture_base"],
        ):
            plans = plan_action(
                downed, [downed], down_tick + 5, ctx, time_domain="ticks"
            )
        assert not any(
            p["type"] == "capture_base" for p in plans
        ), "Downed player must not plan a base capture during the respawn cooldown"

    def test_active_player_plans_capture(self):
        """MECH-15 control: once the respawn cooldown ends, the same player in
        range plans the capture again (boundary at ``RESPAWN_TICKS``)."""
        from matches.sim_helpers.combat import plan_action
        from matches.sim_helpers.time_constants import RESPAWN_TICKS

        ctx = self._make_ctx({"blue": frozenset({"9,9", "9,8"})})
        active = self._make_player("red_cmd", "red", cell_row=9, cell_col=9)
        down_tick = 100
        active.last_downed_time = down_tick
        with patch(
            "matches.sim_helpers.combat.random.choices",
            return_value=["capture_base"],
        ):
            plans = plan_action(
                active, [active], down_tick + RESPAWN_TICKS, ctx, time_domain="ticks"
            )
        assert any(
            p["type"] == "capture_base" for p in plans
        ), "Active player in range should plan the capture once the cooldown ends"


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
            with patch.object(BatchSimulator, "ROUND_TICKS", 40):
                BatchSimulator().simulate_single_round_detailed(
                    team_red, team_blue, arena_map=arena_map
                )

    def test_resolve_map_data_returns_base_sight_data(self):
        """_resolve_map_data base_sight_data field is a dict of frozensets keyed by base_type."""
        arena_map = self._make_base_map("ResolveBSD")
        base_sight_data = resolve_map_data(arena_map).base_sight_data

        assert isinstance(base_sight_data, dict)
        assert "red" in base_sight_data
        assert "blue" in base_sight_data
        assert isinstance(base_sight_data["red"], frozenset)
        assert isinstance(base_sight_data["blue"], frozenset)
        # Red base has 3 visible cells
        assert len(base_sight_data["red"]) == 3
        assert "0,0" in base_sight_data["red"]

    def test_base_sight_data_included_in_movement_ctx(self):
        """load_map_context produces a MapContext exposing base_sight_data."""
        from matches.sim_helpers.map_loader import load_map_context

        arena_map = self._make_base_map("CtxBSD")
        ctx, _zone_size = load_map_context(arena_map)
        assert ctx is not None
        assert "base_sight_data" in ctx
        # base_sight_data identity is exposed via the MapContext field /
        # legacy dict-style shim — both MUST agree on shape.
        assert isinstance(ctx["base_sight_data"], dict)
        assert "red" in ctx["base_sight_data"] or "blue" in ctx["base_sight_data"]


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
        assert (
            HeavyStrongSpotsConfig.objects.filter(
                arena_map=arena_map, zone_size=50
            ).count()
            == 1
        )
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
        ctx = MapContext.from_dict(
            {
                "sight_data": {"0,0": frozenset(), "0,2": frozenset()},
                "adj": {},
                "spawn_cells": {},
                "zone_data": zone_data,
                "wall_meta": wall_meta,
            }
        )

        actor = self._make_player("red_scout", "red", 0, 0)
        target = self._make_player("blue_scout", "blue", 0, 2)
        assert _get_los_targets(actor, [target], ctx) == [target]

    def test_get_los_targets_windowed_aperture_miss(self):
        """_get_los_targets excludes target when aperture axis does not align."""
        from matches.simulation import _get_los_targets

        zone_data = [[1, 5, 1]]
        wall_meta = {"0,1": {"facing": "N"}}  # N/S aperture, attack is E-W
        ctx = MapContext.from_dict(
            {
                "sight_data": {"0,0": frozenset(), "0,2": frozenset()},
                "adj": {},
                "spawn_cells": {},
                "zone_data": zone_data,
                "wall_meta": wall_meta,
            }
        )

        actor = self._make_player("red_scout", "red", 0, 0)
        target = self._make_player("blue_scout", "blue", 0, 2)
        assert _get_los_targets(actor, [target], ctx) == []

    # ── Proximity-based zone detection ──────────────────────────────────────

    def test_zone_from_cell_near_red_base(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 10)}
        assert zone_from_cell(0, 1, spawn_cells) == 0

    def test_zone_from_cell_near_blue_base(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 10)}
        assert zone_from_cell(10, 9, spawn_cells) == 2

    def test_zone_from_cell_equidistant_is_neutral(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 0)}
        assert zone_from_cell(5, 0, spawn_cells) == 1

    def test_zone_from_cell_near_neutral_base_is_neutral(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 0), "neutral_1": (3, 0)}
        assert zone_from_cell(3, 1, spawn_cells) == 1

    def test_zone_from_cell_no_bases_returns_neutral(self):
        assert zone_from_cell(5, 5, {}) == 1

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
        assert resolve_map_data(arena_map).wall_meta == {"0,1": {"facing": "E"}}

    def test_wall_meta_present_in_movement_ctx(self):
        """load_map_context's MapContext exposes wall_meta from the zone config."""
        from matches.sim_helpers.map_loader import load_map_context

        arena_map = self._make_windowed_map("WallMetaCtx")
        ctx, _zone_size = load_map_context(arena_map)
        assert ctx is not None
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

        assignments = build_spawn_assignments(
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
            assignments = build_spawn_assignments(
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
            assignments = build_spawn_assignments(
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

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            game_round = BatchSimulator().simulate_single_round_detailed(
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
            data=json.dumps(
                {
                    "zone_size": zone_size,
                    "zones": zone_data,
                    "bases": [
                        {"type": "red", "x_px": zone_size // 2, "y_px": zone_size // 2},
                        {
                            "type": "blue",
                            "x_px": cols * zone_size - zone_size // 2,
                            "y_px": rows * zone_size - zone_size // 2,
                        },
                    ],
                    "red_spawn": user_red_spawn,
                    "blue_spawn": user_blue_spawn,
                }
            ),
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
        assert resp2.status_code == 200, resp2.content

        # User spawn cells must survive — auto-generation must not override them.
        config = MapZoneConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        )
        stored = config.zone_data
        assert (
            stored["red_spawn"] == user_red_spawn
        ), f"User-edited red_spawn was overwritten: got {stored['red_spawn']}"
        assert (
            stored["blue_spawn"] == user_blue_spawn
        ), f"User-edited blue_spawn was overwritten: got {stored['blue_spawn']}"

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
            data=json.dumps(
                {
                    "zone_size": zone_size,
                    "zones": zone_data,
                    "bases": [
                        {"type": "red", "x_px": zone_size // 2, "y_px": zone_size // 2},
                        {
                            "type": "blue",
                            "x_px": cols * zone_size - zone_size // 2,
                            "y_px": rows * zone_size - zone_size // 2,
                        },
                    ],
                    "red_spawn": [],
                    "blue_spawn": [],
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.content

        config = arena_map.latest_confirmed_config()
        assert not config.zone_data.get(
            "spawn_user_edited"
        ), "spawn_user_edited should be cleared after user saves empty spawn lists"

        # Now auto-generation should run and populate spawn cells.
        sight_data = compute_sight_lines(zone_data)
        resp2 = client.post(
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
        assert resp2.status_code == 200, resp2.content

        config = MapZoneConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        )
        stored = config.zone_data
        assert (
            len(stored.get("red_spawn", [])) >= 1
        ), "Auto-generation should have populated red_spawn after lock was cleared"
        assert (
            len(stored.get("blue_spawn", [])) >= 1
        ), "Auto-generation should have populated blue_spawn after lock was cleared"

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
        assert (
            stored["red_spawn"] == user_red_spawn
        ), f"compute_sight_lines overwrote user red_spawn: got {stored['red_spawn']}"
        assert (
            stored["blue_spawn"] == user_blue_spawn
        ), f"compute_sight_lines overwrote user blue_spawn: got {stored['blue_spawn']}"

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
            data=json.dumps(
                {
                    "zone_size": zone_size,
                    "red_spawn": user_red_spawn,
                    "blue_spawn": user_blue_spawn,
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200, resp.content

        config = MapZoneConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        )
        stored = config.zone_data
        assert (
            stored["red_spawn"] == user_red_spawn
        ), f"Unsaved spawn edits were not persisted: red_spawn={stored.get('red_spawn')}"
        assert (
            stored["blue_spawn"] == user_blue_spawn
        ), f"Unsaved spawn edits were not persisted: blue_spawn={stored.get('blue_spawn')}"
        assert (
            stored.get("spawn_user_edited") is True
        ), "spawn_user_edited flag must be set when client sends spawn with compute"


# ---------------------------------------------------------------------------
# MAP-02 / STAT-03 — speed-scaled multi-cell movement per tick
# ---------------------------------------------------------------------------

_FLOOR_6X6 = [[1] * 6 for _ in range(6)]
_CORRIDOR_1X10 = [[1] * 10]  # single row, cols 0..9 — a straight path


class TestAstarPathAndAdvance:
    """Pure-unit tests for the path-returning and multi-step A* helpers."""

    def _adj(self, grid):
        from matches.sim_helpers.pathfinding import build_movement_adjacency

        return build_movement_adjacency(grid)

    def test_astar_path_full_path_start_to_goal(self):
        from matches.sim_helpers.pathfinding import astar_path

        adj = self._adj(_FLOOR_6X6)
        path = astar_path((0, 0), (0, 5), adj)
        # Straight horizontal run: 5 steps, excludes start, ends at goal.
        assert path == [(0, 1), (0, 2), (0, 3), (0, 4), (0, 5)]

    def test_astar_path_consecutive_cells_adjacent(self):
        from matches.sim_helpers.pathfinding import astar_path

        adj = self._adj(_FLOOR_6X6)
        path = astar_path((0, 0), (5, 5), adj)
        assert len(path) == 10  # Manhattan distance on open grid
        assert path[-1] == (5, 5)
        prev = (0, 0)
        for cell in path:
            assert abs(cell[0] - prev[0]) + abs(cell[1] - prev[1]) == 1
            prev = cell

    def test_astar_path_no_path_is_empty(self):
        from matches.sim_helpers.pathfinding import astar_path

        adj = self._adj([[1, 0, 1], [1, 0, 1], [1, 0, 1]])
        assert astar_path((0, 0), (0, 2), adj) == []

    def test_astar_path_same_cell_is_empty(self):
        from matches.sim_helpers.pathfinding import astar_path

        adj = self._adj(_FLOOR_6X6)
        assert astar_path((2, 2), (2, 2), adj) == []

    def test_astar_next_step_equals_first_path_cell(self):
        # Regression guard: refactored astar_next_step must equal path[0].
        from matches.sim_helpers.pathfinding import astar_next_step, astar_path

        adj = self._adj(_FLOOR_6X6)
        for goal in ((5, 5), (0, 5), (3, 1), (4, 0)):
            path = astar_path((0, 0), goal, adj)
            assert astar_next_step((0, 0), goal, adj) == path[0]

    def test_astar_advance_full_steps_reaches_goal(self):
        from matches.sim_helpers.pathfinding import astar_advance

        adj = self._adj(_CORRIDOR_1X10)
        assert astar_advance((0, 0), (0, 9), adj, 99) == (0, 9)

    def test_astar_advance_partial_steps(self):
        from matches.sim_helpers.pathfinding import astar_advance

        adj = self._adj(_CORRIDOR_1X10)
        assert astar_advance((0, 0), (0, 9), adj, 1) == (0, 1)
        assert astar_advance((0, 0), (0, 9), adj, 3) == (0, 3)
        assert astar_advance((0, 0), (0, 9), adj, 5) == (0, 5)

    def test_astar_advance_one_step_equals_next_step(self):
        from matches.sim_helpers.pathfinding import astar_advance, astar_next_step

        adj = self._adj(_FLOOR_6X6)
        for goal in ((5, 5), (0, 5), (3, 1)):
            assert astar_advance((0, 0), goal, adj, 1) == astar_next_step(
                (0, 0), goal, adj
            )

    def test_astar_advance_zero_or_negative_steps_returns_start(self):
        from matches.sim_helpers.pathfinding import astar_advance

        adj = self._adj(_CORRIDOR_1X10)
        assert astar_advance((0, 0), (0, 9), adj, 0) == (0, 0)
        assert astar_advance((0, 0), (0, 9), adj, -3) == (0, 0)

    def test_astar_advance_no_path_returns_start(self):
        from matches.sim_helpers.pathfinding import astar_advance

        adj = self._adj([[1, 0, 1], [1, 0, 1], [1, 0, 1]])
        assert astar_advance((0, 0), (0, 2), adj, 5) == (0, 0)


class TestSpeedCellsPerTick:
    """STAT-03 / PLAN.md: ceil(speed/100 * max_movement), max_movement 5..10."""

    def test_max_movement_clamped_5_to_10(self):
        from matches.sim_helpers.pathfinding import max_movement_for_map

        assert max_movement_for_map([[1] * 5 for _ in range(5)]) == 5  # tiny → 5
        assert max_movement_for_map([[1] * 89 for _ in range(55)]) == 8  # 89//10
        assert max_movement_for_map([[1] * 200 for _ in range(10)]) == 10  # clamp

    def test_cells_to_move_formula(self):
        import math

        from matches.sim_helpers.pathfinding import (
            cells_to_move,
            max_movement_for_map,
        )

        big = [[1] * 89 for _ in range(55)]
        mm = max_movement_for_map(big)  # 8
        assert cells_to_move(100, big) == math.ceil(1.0 * mm)
        assert cells_to_move(50, big) == math.ceil(0.5 * mm)
        assert cells_to_move(1, big) == 1  # min 1 floor
        assert cells_to_move(0, big) == 1  # never freeze a mover

    def test_higher_speed_moves_at_least_as_far(self):
        from matches.sim_helpers.pathfinding import cells_to_move

        big = [[1] * 89 for _ in range(55)]
        assert cells_to_move(90, big) >= cells_to_move(40, big) >= 1


class TestPlayerStateSpeedField:
    def test_player_state_has_speed_default_50(self):
        from matches.sim_helpers.player_state import PlayerState

        ps = PlayerState(
            tag_id="red_scout",
            name="s",
            team_color="red",
            role="scout",
            accuracy=50,
            survival=50,
            starting_lives=15,
            starting_shots=30,
            final_lives=15,
            final_shots=30,
        )
        assert ps.speed == 50


class TestMoveInMemoryMultiCell:
    """BatchSimulator._move_player_in_memory advances speed-many cells/tick."""

    def _ctx(self, grid):
        from matches.sim_helpers.pathfinding import build_movement_adjacency

        return MapContext.from_dict(
            {
                "adj": build_movement_adjacency(grid),
                "spawn_cells": {"red": (0, 0), "blue": (0, len(grid[0]) - 1)},
                "zone_data": grid,
                "sight_data": None,
            }
        )

    def _player(self, speed):
        from matches.sim_helpers.player_state import PlayerState

        return PlayerState(
            tag_id="red_scout",
            name="s",
            team_color="red",
            role="scout",
            accuracy=50,
            survival=50,
            starting_lives=15,
            starting_shots=30,
            final_lives=15,
            final_shots=30,
            speed=speed,
            cell_row=0,
            cell_col=0,
        )

    def test_moves_multiple_cells_in_one_tick(self):
        from matches.sim_helpers.pathfinding import cells_to_move
        from matches.simulation import BatchSimulator

        grid = [[1] * 30]  # long corridor so the goal is far
        ctx = self._ctx(grid)
        player = self._player(speed=50)
        expected = cells_to_move(50, grid)
        assert expected > 1, "test precondition: speed 50 should move >1 cell"

        # MOVE-01: _move_player_in_memory signature is now
        # (player, second, goal_cell, movement_ctx, multiplier=1).
        BatchSimulator()._move_player_in_memory(player, 0, (0, 29), ctx)

        assert (player.cell_row, player.cell_col) == (0, expected)

    def test_higher_speed_travels_farther_in_one_tick(self):
        from matches.simulation import BatchSimulator

        grid = [[1] * 40]
        ctx = self._ctx(grid)
        slow = self._player(speed=20)
        fast = self._player(speed=100)

        BatchSimulator()._move_player_in_memory(slow, 0, (0, 39), ctx)
        BatchSimulator()._move_player_in_memory(fast, 0, (0, 39), ctx)

        assert fast.cell_col > slow.cell_col >= 1

    def test_stops_at_goal_without_overshoot(self):
        from matches.simulation import BatchSimulator

        grid = [[1] * 30]
        ctx = self._ctx(grid)
        player = self._player(speed=100)  # would move many cells

        BatchSimulator()._move_player_in_memory(player, 0, (0, 2), ctx)

        assert (player.cell_row, player.cell_col) == (0, 2)


# ---------------------------------------------------------------------------
# MOVE-01 — movement decoupled from the weighted action
#
# Locked decisions encoded here (see CONTEXT.md / matches/CLAUDE.md / ADR-0007):
#   1. action `change_zone` renamed `only_move` (index 1 unchanged).
#   2. every non-stationary player Advances toward their goal cell EVERY tick,
#      regardless of which weighted action was chosen (map path only).
#   3. Stationary = is_hiding True OR chosen action == capture_base.
#      Every other action (tag/missile/use_special/resupply/request/only_move)
#      Advances while acting.
#   4. only_move = ONE single 2× step: cells_to_move(...) * 2 in one
#      astar_advance call (vs the normal cells_to_move(...) for normal ticks).
#   5. compact movement GameEvent: start cell + end cell + timestamp in
#      metadata, NO route/path list, emitted ONLY when the cell changed.
#   6. 3-zone fallback (movement_ctx is None) unchanged — old weighted
#      _change_zone behaviour on the only_move roll.
#   7. determinism preserved: Advance/A* consume no RNG.
# ---------------------------------------------------------------------------


_FLOOR_30X1 = [[1] * 30]


def _move01_player(role, *, speed=50, cell_row=0, cell_col=0, **kw):
    """Lightweight in-memory PlayerState for MOVE-01 pure-unit movement tests."""
    from matches.sim_helpers.player_state import PlayerState

    defaults = dict(
        tag_id=f"red_{role}",
        name=role,
        team_color="red",
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=15,
        starting_shots=30,
        final_lives=15,
        final_shots=30,
        speed=speed,
        cell_row=cell_row,
        cell_col=cell_col,
    )
    defaults.update(kw)
    return PlayerState(**defaults)


def _move01_ctx(grid):
    """MapContext over a 1-D corridor; goal = far end of the corridor."""
    from matches.sim_helpers.pathfinding import build_movement_adjacency

    return MapContext.from_dict(
        {
            "adj": build_movement_adjacency(grid),
            "spawn_cells": {"red": (0, 0), "blue": (0, len(grid[0]) - 1)},
            "zone_data": grid,
            "sight_data": None,
        }
    )


class TestMove01OnlyMoveDoubleStep:
    """Decision 4: only_move = a single 2× step in ONE astar_advance call;
    a normal non-stationary tick Advances the normal cells_to_move distance."""

    def test_astar_advance_double_steps_reaches_double_distance(self):
        """Pure-unit: astar_advance with steps*2 ends ~twice as far as steps."""
        from matches.sim_helpers.pathfinding import (
            astar_advance,
            build_movement_adjacency,
            cells_to_move,
        )

        grid = _FLOOR_30X1
        adj = build_movement_adjacency(grid)
        steps = cells_to_move(50, grid)
        assert steps >= 1

        normal_end = astar_advance((0, 0), (0, 29), adj, steps)
        double_end = astar_advance((0, 0), (0, 29), adj, steps * 2)

        # One-D corridor: column index == distance travelled from start.
        assert normal_end == (0, steps)
        assert double_end == (0, steps * 2)
        assert double_end[1] == 2 * normal_end[1]

    def test_move_player_in_memory_multiplier_2_doubles_advance(self):
        """BatchSim: multiplier=2 covers twice the cells of multiplier=1
        for the same start/goal/speed, in one move call."""
        from matches.simulation import BatchSimulator
        from matches.sim_helpers.pathfinding import cells_to_move

        grid = _FLOOR_30X1
        ctx = _move01_ctx(grid)
        normal_p = _move01_player("scout", speed=50)
        double_p = _move01_player("scout", speed=50)
        step = cells_to_move(50, grid)
        assert step >= 1

        BatchSimulator()._move_player_in_memory(normal_p, 0, (0, 29), ctx, 1)
        BatchSimulator()._move_player_in_memory(double_p, 0, (0, 29), ctx, 2)

        assert (normal_p.cell_row, normal_p.cell_col) == (0, step)
        assert (double_p.cell_row, double_p.cell_col) == (0, step * 2)
        assert double_p.cell_col == 2 * normal_p.cell_col

    def test_advance_player_only_move_action_uses_double_step(self):
        """_advance_player reads last_chosen_action == 'only_move' and applies
        the 2× multiplier; a non-only_move action uses the 1× distance."""
        from matches.simulation import BatchSimulator
        from matches.sim_helpers.pathfinding import cells_to_move

        grid = _FLOOR_30X1
        ctx = _move01_ctx(grid)
        step = cells_to_move(50, grid)

        only_move_p = _move01_player("scout", speed=50)
        only_move_p.last_chosen_action = "only_move"
        tag_p = _move01_player("scout", speed=50)
        tag_p.last_chosen_action = "tag_player"

        sim = BatchSimulator()
        sim._advance_player(only_move_p, [only_move_p], 0, ctx, "")
        sim._advance_player(tag_p, [tag_p], 0, ctx, "")

        assert only_move_p.cell_col == step * 2
        assert tag_p.cell_col == step
        assert only_move_p.cell_col == 2 * tag_p.cell_col


class TestMove01AlwaysOnAdvance:
    """Decisions 2 & 3: every non-stationary player Advances every tick
    regardless of the weighted action; stationary players do not."""

    def test_tag_action_still_advances_toward_goal(self):
        """A non-stationary action (tag) Advances the normal distance —
        movement is NOT gated by picking only_move."""
        from matches.simulation import BatchSimulator
        from matches.sim_helpers.pathfinding import cells_to_move

        grid = _FLOOR_30X1
        ctx = _move01_ctx(grid)
        player = _move01_player("scout", speed=50)
        player.last_chosen_action = "tag_player"
        step = cells_to_move(50, grid)

        BatchSimulator()._advance_player(player, [player], 0, ctx, "")

        assert (player.cell_row, player.cell_col) == (0, step)

    def test_resupply_action_still_advances(self):
        """resupply_ally is non-stationary → the player still Advances."""
        from matches.simulation import BatchSimulator

        grid = _FLOOR_30X1
        ctx = _move01_ctx(grid)
        player = _move01_player("medic", speed=50)
        player.last_chosen_action = "resupply_ally"

        BatchSimulator()._advance_player(player, [player], 0, ctx, "")

        assert player.cell_col > 0, "non-stationary medic must Advance toward goal"

    def test_is_hiding_is_stationary_no_advance(self):
        """Stationary: is_hiding True → no Advance, no trail entry this tick."""
        from matches.simulation import BatchSimulator

        grid = _FLOOR_30X1
        ctx = _move01_ctx(grid)
        player = _move01_player("scout", speed=50)
        player.is_hiding = True
        player.last_chosen_action = "hide"

        BatchSimulator()._advance_player(player, [player], 0, ctx, "")

        assert (player.cell_row, player.cell_col) == (0, 0)
        assert getattr(player, "movement_trail", []) == []

    def test_capture_base_is_stationary_no_advance(self):
        """Stationary: chosen action == capture_base → anchored, no Advance."""
        from matches.simulation import BatchSimulator

        grid = _FLOOR_30X1
        ctx = _move01_ctx(grid)
        player = _move01_player("scout", speed=50)
        player.last_chosen_action = "capture_base"

        BatchSimulator()._advance_player(player, [player], 0, ctx, "")

        assert (player.cell_row, player.cell_col) == (0, 0)
        assert getattr(player, "movement_trail", []) == []

    def test_use_special_is_not_stationary_advances(self):
        """use_special is NOT in the stationary set → player still Advances."""
        from matches.simulation import BatchSimulator

        grid = _FLOOR_30X1
        ctx = _move01_ctx(grid)
        player = _move01_player("commander", speed=50)
        player.last_chosen_action = "use_special"

        BatchSimulator()._advance_player(player, [player], 0, ctx, "")

        assert player.cell_col > 0


@pytest.mark.django_db
class TestMove01BaselineZeroPlayerMovesAcrossMap:
    """The core bug MOVE-01 fixes: a role whose baseline only_move (index 1)
    weight is 0 (commander/medic/ammo) still traverses the map over a round
    when a map is active, because the Advance is decoupled from the roll."""

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

    def test_baseline_zero_only_move_roles_advance_from_spawn(self):
        """Commander/Medic/Ammo (baseline only_move weight 0) move off their
        spawn cell over a round when a map is active (pre-MOVE-01 they would
        almost never roll only_move and so never moved).

        Uses ``replay_round`` with an explicit seed for determinism — SIM-09's
        ``simulate_single_round_detailed`` intentionally draws its own fresh
        per-round seed from OS entropy and so ignores ``random.seed(...)``
        at the test level. The MOVE-01 invariant we want to pin here is
        about movement, not persistence, so the in-memory path is the right
        seam.
        """
        from matches.sim_helpers.map_loader import load_map_context

        arena_map = self._make_map("Move01Baseline10x10", _FLOOR_10X10)
        team_red, _ = make_team_with_slots("M01R")
        team_blue, _ = make_team_with_slots("M01B")
        red_roster = list(team_red.active_roster)
        blue_roster = list(team_blue.active_roster)
        ctx, _ = load_map_context(arena_map)

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            (
                _result,
                red_players,
                _blue_players,
                _events,
            ) = BatchSimulator().replay_round(
                red_roster, blue_roster, seed=42, flipped=False, movement_ctx=ctx
            )

        red_spawn = (0, 0)
        moved_roles = set()
        for p in red_players:
            if p.cell_row is None:
                continue
            dist = abs(p.cell_row - red_spawn[0]) + abs(p.cell_col - red_spawn[1])
            if dist > 0:
                moved_roles.add(p.role)

        for role in ("commander", "medic", "ammo"):
            assert role in moved_roles, (
                f"{role} (baseline only_move weight 0) must still Advance across "
                f"the map every tick under MOVE-01; moved roles were {moved_roles}"
            )


@pytest.mark.django_db
class TestMove01CompactMovementEvent:
    """Decision 5: movement GameEvent carries a compact start + end + timestamp;
    NO route/path list; no event on a no-op / stationary tick."""

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

    # Any key that would represent a stored full route/path list. The compact
    # event must contain NONE of these — the route is recomputed at replay.
    _ROUTE_KEYS = ("route", "path", "cells", "cell_path", "trail", "steps")

    def test_movement_event_has_compact_start_end_timestamp(self):
        import random

        random.seed(42)

        arena_map = self._make_map("Move01Compact10x10", _FLOOR_10X10)
        team_red, _ = make_team_with_slots("CmpR")
        team_blue, _ = make_team_with_slots("CmpB")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            game_round = BatchSimulator().simulate_single_round_detailed(
                team_red, team_blue, arena_map=arena_map
            )

        move_events = list(
            GameEvent.objects.filter(game_round=game_round, event_type="movement")
        )
        assert move_events, "expected at least one compact movement event"

        for ev in move_events:
            md = ev.metadata
            # start cell present
            assert "start_row" in md and "start_col" in md, md
            # end cell present
            assert "end_row" in md and "end_col" in md, md
            # timestamp present on the event itself
            assert ev.timestamp is not None
            # NO route/path list stored anywhere in metadata
            for bad in self._ROUTE_KEYS:
                assert bad not in md, (
                    f"compact movement metadata must not store a route list; "
                    f"found {bad!r} in {md}"
                )
            for v in md.values():
                assert not isinstance(
                    v, (list, tuple)
                ), f"movement metadata must be flat scalars, got list/tuple: {md}"
            # the cell actually changed (no no-op events emitted)
            assert (md["start_row"], md["start_col"]) != (
                md["end_row"],
                md["end_col"],
            ), f"no movement event should be emitted for a no-op Advance: {md}"

    def test_no_movement_event_when_no_map(self):
        """Regression: 3-zone fallback movement never records cell coords."""
        import random

        random.seed(42)

        team_red, _ = make_team_with_slots("NoMapR")
        team_blue, _ = make_team_with_slots("NoMapB")

        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            game_round = BatchSimulator().simulate_single_round_detailed(
                team_red, team_blue
            )

        for ev in GameEvent.objects.filter(
            game_round=game_round, event_type="movement"
        ):
            assert "start_row" not in ev.metadata
            assert "cell_row" not in ev.metadata


# SIM-09 note: TestMove01MoveToCellEventEmission and
# TestMove01ThreeZoneFallbackUnchanged were dropped — they exercised
# RBS-specific ``_move_to_cell(player, second, goal, ctx, buf, ...)`` event-
# buffer semantics and ``_simulate_combat_exchange`` direct invocation. The
# equivalent BatchSim in-memory movement appends to ``PlayerState.movement_trail``
# (no event-buffer parameter); end-to-end compact-event shape is already
# covered by ``TestMove01CompactMovementEvent`` above.


class TestMove01SelectLosCell:
    """MOVE-01: `_select_los_cell` — deterministic LOS-biased cell pick.

    Closes the code-review WARNING: the helper underpins Medic (sheltered,
    low LOS) and Ammo (exposed, high LOS) goal selection and must be fully
    deterministic and hash-independent (SIM-07/08: serial == per-process
    parallel). Pure unit, no DB."""

    def test_medic_picks_lowest_los(self):
        from matches.sim_helpers.pathfinding import _select_los_cell

        keys = frozenset({"0,0", "0,1", "5,5"})
        los = {"0,0": 9, "0,1": 9, "5,5": 2}
        # prefer_high=False (Medic): minimise LOS → the 5,5 cell.
        assert _select_los_cell(keys, los, 0, 0, prefer_high=False) == (5, 5)

    def test_ammo_picks_highest_los(self):
        from matches.sim_helpers.pathfinding import _select_los_cell

        keys = frozenset({"0,0", "0,1", "5,5"})
        los = {"0,0": 1, "0,1": 1, "5,5": 8}
        # prefer_high=True (Ammo): maximise LOS → the 5,5 cell.
        assert _select_los_cell(keys, los, 0, 0, prefer_high=True) == (5, 5)

    def test_tie_broken_by_nearest_then_coords(self):
        from matches.sim_helpers.pathfinding import _select_los_cell

        # All equal LOS → tie-break must be the cell nearest (from_row,col).
        keys = frozenset({"9,9", "1,0", "0,3"})
        los = {"9,9": 4, "1,0": 4, "0,3": 4}
        assert _select_los_cell(keys, los, 0, 0, prefer_high=False) == (1, 0)
        assert _select_los_cell(keys, los, 0, 0, prefer_high=True) == (1, 0)

    def test_deterministic_and_hash_independent(self):
        from matches.sim_helpers.pathfinding import _select_los_cell

        # Same elements, many rebuilt frozensets (insertion order varies, and
        # str hashing is PYTHONHASHSEED-randomised) → identical result every
        # call. Equidistant ties resolve by (r, c) ascending.
        los = {f"{r},{c}": 3 for r in range(6) for c in range(6)}
        results = set()
        for _ in range(25):
            keys = frozenset(f"{r},{c}" for r in range(6) for c in range(6))
            results.add(_select_los_cell(keys, los, 2, 2, prefer_high=False))
        assert results == {(2, 2)}  # exactly at the origin → nearest, stable

    def test_empty_returns_none(self):
        from matches.sim_helpers.pathfinding import _select_los_cell

        assert _select_los_cell(frozenset(), {}, 0, 0, prefer_high=False) is None


# ---------------------------------------------------------------------------
# MOVE-02 — path commitment via a goal-keyed A* cache (BatchSimulator only)
#
# Behavioural spec: docs/adr/0008-path-commitment-via-goal-keyed-cache.md +
# CONTEXT.md "Path commitment".
#
# Locked decisions encoded here:
#   1. The player follows the route computed when its Goal cell was set;
#      `astar_path` is NOT re-run every move tick — exactly once per committed
#      route (behavioural proxy for the documented ~8x perf win, NO timing).
#   2. A recompute (fresh `astar_path`) happens ONLY on invalidation:
#        (a) the Goal cell changes between ticks,
#        (b) the cached route is exhausted (player reached the goal) and a new
#            goal is chosen next tick,
#        (c) the player is knocked off-path (Down/respawn) → cache cleared
#            (the BatchSim life-loss sites set ``player._path_cache = None``),
#        (d) the next cached cell is no longer passable (not in adj).
#   3. `only_move` (2x cells_to_move) consumes 2x cells along the SAME
#      committed route with NO extra recompute vs a normal tick on it.
#   4. With >=2 equal-cost shortest routes, the cache commits to ONE route —
#      the walked cell sequence is stable (no per-tick "wobble"). Locks
#      ADR-0008 option (c).
#   5. Scope is BatchSimulator only (RBS deliberately uncached per ADR-0008).
#
# BEHAVIOURAL tests: a call counter wraps pathfinding.astar_path and drives
# BatchSimulator._move_player_in_memory / _advance_player over several ticks.
# No wall-clock timing, no Score-Calibration point assertions (re-baseline
# pending per ADR-0008), no mock-returns-mock.
# ---------------------------------------------------------------------------


_FLOOR_3X8 = [[1] * 8 for _ in range(3)]  # small open map: many equal routes


class _AstarCounter:
    """Wrap ``pathfinding.astar_path`` with a call counter, preserving its
    real behaviour (no mock return value).

    ``astar_advance`` and the MOVE-02 ``astar_advance_cached`` both call the
    module-global ``astar_path`` inside ``pathfinding``, so patching the name
    on that module counts every real recompute regardless of caller.
    """

    def __init__(self):
        self.calls = 0
        self._orig = None

    def __enter__(self):
        import importlib

        self._pf = importlib.import_module("matches.sim_helpers.pathfinding")
        self._orig = self._pf.astar_path
        orig = self._orig
        counter = self

        def _counting_astar_path(*args, **kwargs):
            counter.calls += 1
            return orig(*args, **kwargs)

        self._pf.astar_path = _counting_astar_path
        return self

    def __exit__(self, *exc):
        self._pf.astar_path = self._orig
        return False


def _move02_ctx(grid, *, spawn_blue=None):
    """MapContext over ``grid``; blue spawn defaults to the far corner so the
    default Goal (enemy base) is a long fixed walk from (0, 0)."""
    from matches.sim_helpers.pathfinding import build_movement_adjacency

    rows, cols = len(grid), len(grid[0])
    return MapContext.from_dict(
        {
            "adj": build_movement_adjacency(grid),
            "spawn_cells": {
                "red": (0, 0),
                "blue": spawn_blue or (rows - 1, cols - 1),
            },
            "zone_data": grid,
            "sight_data": None,
        }
    )


def _move02_player(role="scout", *, speed=20, cell_row=0, cell_col=0, **kw):
    """Slow PlayerState so a long route takes several ticks to traverse
    (speed=20 → 1 cell/tick on a small map; multiple ticks per route)."""
    from matches.sim_helpers.player_state import PlayerState

    defaults = dict(
        tag_id=f"red_{role}",
        name=role,
        team_color="red",
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=15,
        starting_shots=30,
        final_lives=15,
        final_shots=30,
        speed=speed,
        cell_row=cell_row,
        cell_col=cell_col,
    )
    defaults.update(kw)
    return PlayerState(**defaults)


@pytest.mark.django_db  # PlayerState alone needs no DB; keep file parity
class TestMove02CacheHitAvoidsRecompute:
    """Decision 1: a fixed unchanging Goal ⇒ exactly ONE astar_path call
    across the whole multi-tick traversal (path commitment, not per-tick
    re-planning)."""

    def test_fixed_goal_recomputes_path_exactly_once_over_many_ticks(self):
        from matches.simulation import BatchSimulator

        grid = _FLOOR_3X8
        ctx = _move02_ctx(grid)
        # Default goal (enemy base) is the fixed blue spawn — never changes.
        goal = ctx.get_spawn_cells()["blue"]
        player = _move02_player(speed=20)  # 1 cell/tick → many ticks
        sim = BatchSimulator()

        ticks_walked = 0
        with _AstarCounter() as counter:
            for tick in range(50):
                before = (player.cell_row, player.cell_col)
                sim._move_player_in_memory(player, tick, goal, ctx)
                ticks_walked += 1
                if (player.cell_row, player.cell_col) == goal:
                    break
                assert (player.cell_row, player.cell_col) != before, (
                    "player must make progress each tick toward a reachable " "goal"
                )

        assert ticks_walked > 1, "precondition: a multi-tick traversal"
        assert (player.cell_row, player.cell_col) == goal
        assert counter.calls == 1, (
            "MOVE-02 path commitment: astar_path must be computed ONCE for a "
            f"fixed Goal across {ticks_walked} move ticks, not per tick (got "
            f"{counter.calls})"
        )


@pytest.mark.django_db
class TestMove02InvalidationTriggers:
    """Decision 2: each documented invalidation forces exactly one recompute;
    a same-goal continuation is a cache hit (no recompute)."""

    def test_goal_change_between_ticks_recomputes(self):
        from matches.simulation import BatchSimulator

        ctx = _move02_ctx(_FLOOR_3X8)
        player = _move02_player(speed=20)
        sim = BatchSimulator()

        goal_a = (2, 7)
        goal_b = (0, 7)  # different Goal cell → must invalidate the commit
        with _AstarCounter() as counter:
            sim._move_player_in_memory(player, 0, goal_a, ctx)
            after_first = counter.calls
            sim._move_player_in_memory(player, 1, goal_a, ctx)  # same goal
            after_same = counter.calls
            sim._move_player_in_memory(player, 2, goal_b, ctx)  # new goal
            after_change = counter.calls

        assert after_first == 1
        assert after_same == 1, "same Goal next tick must be a cache hit"
        assert after_change == 2, "a changed Goal must trigger one recompute"

    def test_cache_exhausted_then_new_goal_recomputes(self):
        from matches.simulation import BatchSimulator

        ctx = _move02_ctx(_FLOOR_3X8)
        # Fast enough to reach goal_a in a single tick → route exhausted.
        player = _move02_player(speed=100, cell_row=0, cell_col=0)
        sim = BatchSimulator()

        goal_a = (0, 1)  # 1 cell away → reached immediately, cache exhausted
        goal_b = (2, 7)  # brand-new goal next tick → must recompute
        with _AstarCounter() as counter:
            sim._move_player_in_memory(player, 0, goal_a, ctx)
            assert (player.cell_row, player.cell_col) == goal_a
            after_reach = counter.calls
            sim._move_player_in_memory(player, 1, goal_b, ctx)
            after_new = counter.calls

        assert after_reach == 1
        assert after_new == 2, (
            "an exhausted cache (goal reached) followed by a new goal must "
            "recompute, not reuse a stale/empty route"
        )

    def test_downed_respawn_cache_clear_recomputes(self):
        """Decision 2c: a Down/respawn knocks the player off the committed
        path. The BatchSim life-loss sites set ``player._path_cache = None``;
        the next move must therefore recompute (not re-step a stale route)."""
        from matches.simulation import BatchSimulator

        ctx = _move02_ctx(_FLOOR_3X8)
        player = _move02_player(speed=20)
        goal = (2, 7)
        sim = BatchSimulator()

        with _AstarCounter() as counter:
            sim._move_player_in_memory(player, 0, goal, ctx)
            sim._move_player_in_memory(player, 1, goal, ctx)
            assert counter.calls == 1, "two ticks on one route = 1 compute"

            # Reproduce exactly what the simulator's life-loss sites do on a
            # Down (see BatchSimulator tag/followup/missile/nuke resolution):
            # teleport-ish state change + clear the committed path cache.
            player.final_lives -= 1
            player.last_downed_time = 2
            player.cell_row, player.cell_col = 0, 0
            player._path_cache = None  # MOVE-02 Down/respawn invalidation

            sim._move_player_in_memory(player, 3, goal, ctx)
            after_respawn = counter.calls

        assert after_respawn == 2, (
            "MOVE-02: a cleared path cache (Down/respawn) must recompute the "
            f"route on the next move (got {after_respawn} total calls)"
        )

    def test_blocked_next_cell_recomputes(self):
        """Decision 2d: when the next cached cell becomes non-passable (no
        longer in adj), the player must re-plan around it."""
        from matches.sim_helpers.pathfinding import build_movement_adjacency
        from matches.simulation import BatchSimulator

        grid = [[1] * 8 for _ in range(3)]
        ctx = _move02_ctx(grid)
        player = _move02_player(speed=20)
        goal = (0, 7)
        sim = BatchSimulator()

        with _AstarCounter() as counter:
            sim._move_player_in_memory(player, 0, goal, ctx)
            assert counter.calls == 1

            # Block the cell directly ahead of the player on its committed
            # route by rebuilding adjacency from a grid with a wall there.
            ahead_r, ahead_c = player.cell_row, player.cell_col + 1
            blocked_grid = [row[:] for row in grid]
            blocked_grid[ahead_r][ahead_c] = 0  # high wall
            new_adj = build_movement_adjacency(blocked_grid)
            ctx.adj = new_adj  # MapContext.adj field (see map_context.py)

            sim._move_player_in_memory(player, 1, goal, ctx)
            after_block = counter.calls

        assert (ahead_r, ahead_c) not in new_adj, "precondition: cell blocked"
        assert after_block == 2, (
            "a blocked next cached cell must trigger exactly one recompute "
            f"(got {after_block} total calls)"
        )


@pytest.mark.django_db
class TestMove02OnlyMoveDoubleStepNoExtraRecompute:
    """Decision 3: an `only_move` tick consumes 2x cells along the SAME
    committed route with no extra recompute vs a normal tick on it."""

    def test_only_move_consumes_double_on_committed_route_no_recompute(self):
        from matches.simulation import BatchSimulator
        from matches.sim_helpers.pathfinding import cells_to_move

        grid = _FLOOR_30X1  # long 1-D corridor: column == distance travelled
        ctx = _move01_ctx(grid)
        goal = (0, 29)
        step = cells_to_move(50, grid)
        assert step >= 1

        normal_p = _move01_player("scout", speed=50)
        only_move_p = _move01_player("scout", speed=50)
        sim = BatchSimulator()

        with _AstarCounter() as counter:
            # First tick commits the route for each player (1 compute each).
            sim._move_player_in_memory(normal_p, 0, goal, ctx, 1)
            sim._move_player_in_memory(only_move_p, 0, goal, ctx, 2)
            after_commit = counter.calls

        # only_move covered 2x the cells in that single committed tick.
        assert only_move_p.cell_col == 2 * normal_p.cell_col
        assert only_move_p.cell_col == step * 2
        assert after_commit == 2, (
            "exactly one recompute per player on the first committed tick; "
            f"the only_move 2x step must NOT add a recompute (got "
            f"{after_commit})"
        )

        # A subsequent only_move tick on the SAME committed route adds no
        # recompute compared to the normal player's cache hit.
        with _AstarCounter() as counter2:
            sim._move_player_in_memory(normal_p, 1, goal, ctx, 1)
            sim._move_player_in_memory(only_move_p, 1, goal, ctx, 2)
        assert counter2.calls == 0, (
            "both players continue along their committed routes — an "
            f"only_move 2x step is still a cache hit (got {counter2.calls})"
        )


@pytest.mark.django_db
class TestMove02RouteCommitmentRegression:
    """Decision 4 / ADR-0008: with >=2 equal-cost shortest routes, the cache
    commits the player to ONE route — the walked cell sequence is stable
    (no per-tick re-pick "wobble")."""

    def test_committed_route_is_stable_no_wobble(self):
        from matches.simulation import BatchSimulator

        # 3x8 open grid: from (0,0) to (2,7) there are MANY equal-cost
        # Manhattan shortest paths (9 steps each). Pre-MOVE-02 per-tick
        # recompute could re-pick a different equal-cost route each tick.
        ctx = _move02_ctx(_FLOOR_3X8)
        goal = (2, 7)
        player = _move02_player(speed=20)  # 1 cell/tick → many ticks
        sim = BatchSimulator()

        walked = [(player.cell_row, player.cell_col)]
        for tick in range(40):
            sim._move_player_in_memory(player, tick, goal, ctx)
            walked.append((player.cell_row, player.cell_col))
            if (player.cell_row, player.cell_col) == goal:
                break

        assert walked[-1] == goal, "player must reach the fixed goal"

        # The committed route equals the single A* path computed once from the
        # start cell (path commitment), NOT a per-tick re-derived mixture.
        from matches.sim_helpers.pathfinding import astar_path

        committed = astar_path((0, 0), goal, ctx.get_adjacency())
        actual_route = walked[1:]  # drop the duplicated start cell
        assert actual_route == committed, (
            "MOVE-02 route commitment: the walked sequence must be exactly "
            "the single route A* picked when the Goal was set (stable, no "
            f"wobble).\n committed: {committed}\n walked:    {actual_route}"
        )

        # Every consecutive pair is adjacent (no teleporting between equal-cost
        # routes mid-traversal).
        for a, b in zip(walked, walked[1:]):
            assert (
                abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1
            ), f"non-adjacent hop {a} -> {b} indicates route wobble"
