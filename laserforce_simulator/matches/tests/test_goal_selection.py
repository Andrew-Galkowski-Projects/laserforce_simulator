"""
MAP-05 — Role-aware, action-aware goal selection tests.

Pure unit tests for choose_goal_cell and DB integration tests for
resolve_map_data / load_map_context returning MAP-05 config.
SIM-09: migrated from RBS static helpers to sim_helpers.map_loader free functions.
"""

import pytest

from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.support_survival import Visit
from matches.sim_helpers.map_loader import load_map_context, resolve_map_data

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LIVES = {"commander": 30, "heavy": 20, "scout": 30, "medic": 20, "ammo": 20}
_SHOTS = {"commander": 60, "heavy": 40, "scout": 60, "medic": 30, "ammo": 15}


def _make_player(
    tag_id,
    team_color,
    role,
    *,
    lives=None,
    shots=None,
    cell_row=None,
    cell_col=None,
):
    from matches.sim_helpers.player_state import PlayerState

    max_lives = _LIVES[role]
    max_shots = _SHOTS[role]
    return PlayerState(
        tag_id=tag_id,
        name=tag_id,
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=max_lives,
        starting_shots=max_shots,
        final_lives=max_lives if lives is None else lives,
        final_shots=max_shots if shots is None else shots,
        cell_row=cell_row,
        cell_col=cell_col,
    )


def _base_ctx(
    *,
    adj=None,
    sight_data=None,
    cell_los_counts=None,
    high_los_cells=None,
    strong_spots=None,
    spawn_cells=None,
):
    return MapContext.from_dict(
        {
            "adj": adj or {},
            "spawn_cells": spawn_cells or {"red": (0, 0), "blue": (9, 9)},
            "zone_data": None,
            "sight_data": sight_data or {},
            "base_sight_data": {},
            "cell_los_counts": cell_los_counts or {},
            "high_los_cells": high_los_cells or [],
            "strong_spots": strong_spots or [],
        }
    )


# ---------------------------------------------------------------------------
# MAP-05 Unit tests — no DB required
# ---------------------------------------------------------------------------


class TestMap05RoleAwareGoal:
    """Unit tests for choose_goal_cell: role-specific and action-driven paths.

    MOVE-06: positioning goals are a scored draw (goal_scoring); the draw is
    pinned to roll 0 here, which always takes the best-scored cell, so these
    tests keep pinning "the nearest good cell scores best" deterministically.
    """

    @pytest.fixture(autouse=True)
    def _best_draw(self, monkeypatch):
        from matches.sim_helpers import goal_scoring

        monkeypatch.setattr(goal_scoring.random, "random", lambda: 0.0)

    def test_scout_takes_the_best_scored_high_los_cell(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        high_los_cells = [(5, 5), (2, 2)]
        ctx = _base_ctx(high_los_cells=high_los_cells, spawn_cells=spawn_cells)

        goal = choose_goal_cell(scout, [scout], spawn_cells, ctx)
        # (2,2) is Manhattan distance 4, (5,5) is 10 — expect nearest
        assert goal == (2, 2)

    def test_scout_falls_back_to_enemy_base_when_no_high_los_cells(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        ctx = _base_ctx(high_los_cells=[], spawn_cells=spawn_cells)

        goal = choose_goal_cell(scout, [scout], spawn_cells, ctx)
        assert goal == (9, 9)

    def test_heavy_takes_the_best_scored_strong_spot_when_healthy(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        heavy = _make_player("red_heavy", "red", "heavy", cell_row=0, cell_col=0)
        strong_spots = [(6, 6), (2, 2)]
        ctx = _base_ctx(strong_spots=strong_spots, spawn_cells=spawn_cells)

        goal = choose_goal_cell(heavy, [heavy], spawn_cells, ctx)
        # (2,2) is closer than (6,6)
        assert goal == (2, 2)

    def test_heavy_goes_to_medic_when_lives_low(self):
        # MECH-07: this is step 1's critical override (30% or less), which still
        # reads the true cell until MECH-13. Between 30% and 50%, a Heavy uses
        # remembered support cells: test_modes.TestLowHealthHeavy.
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        # 5/20 lives = 25% — below 50% threshold
        heavy = _make_player(
            "red_heavy", "red", "heavy", lives=5, cell_row=0, cell_col=0
        )
        medic = _make_player("red_medic", "red", "medic", cell_row=4, cell_col=4)
        strong_spots = [(3, 3)]
        ctx = _base_ctx(strong_spots=strong_spots, spawn_cells=spawn_cells)
        # CAL-03c (#591): step 1 is a Resupply visit's goal, which replaced the
        # walk to the Medic's true cell.
        heavy.visit = Visit(phase="go", started=0, goal=(4, 4))

        goal = choose_goal_cell(heavy, [heavy, medic], spawn_cells, ctx)
        assert goal == (4, 4)

    def test_heavy_goes_to_ammo_when_shots_low(self):
        # MECH-07: step 1's critical override again; see the test above.
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        # 10/40 shots = 25% — below 50% threshold; full lives
        heavy = _make_player(
            "red_heavy", "red", "heavy", shots=10, cell_row=0, cell_col=0
        )
        ammo = _make_player("red_ammo", "red", "ammo", cell_row=5, cell_col=5)
        strong_spots = [(3, 3)]
        ctx = _base_ctx(strong_spots=strong_spots, spawn_cells=spawn_cells)
        heavy.visit = Visit(phase="go", started=0, goal=(5, 5))  # CAL-03c (#591)

        goal = choose_goal_cell(heavy, [heavy, ammo], spawn_cells, ctx)
        assert goal == (5, 5)

    def test_medic_takes_the_best_scored_hidden_cell_in_heavy_sight(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        medic = _make_player("red_medic", "red", "medic", cell_row=0, cell_col=0)
        heavy = _make_player("red_heavy", "red", "heavy", cell_row=2, cell_col=2)
        # MECH-07c: the invariant holds for a Heavy the Medic remembers. The Defense
        # rule reads memory, so this Medic has to have seen it.
        medic.player_memory["red_heavy"] = {
            "cell": (2, 2),
            "timestamp": 0,
            "role": "heavy",
        }
        # Heavy can see (2,1) and (2,3)
        sight_data = {"2,2": frozenset({"2,1", "2,3"})}
        cell_los_counts = {"2,1": 1, "2,3": 10}
        ctx = _base_ctx(
            sight_data=sight_data,
            cell_los_counts=cell_los_counts,
            spawn_cells=spawn_cells,
        )

        goal = choose_goal_cell(medic, [medic, heavy], spawn_cells, ctx)
        # Medic picks lowest-LOS cell in Heavy's visible set
        assert goal == (2, 1)

    def test_ammo_takes_the_best_scored_hidden_cell_in_heavy_sight(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        ammo = _make_player("red_ammo", "red", "ammo", cell_row=0, cell_col=0)
        heavy = _make_player("red_heavy", "red", "heavy", cell_row=2, cell_col=2)
        # MECH-07c: as for the Medic above, the Defense rule reads memory.
        ammo.player_memory["red_heavy"] = {
            "cell": (2, 2),
            "timestamp": 0,
            "role": "heavy",
        }
        sight_data = {"2,2": frozenset({"2,1", "2,3"})}
        cell_los_counts = {"2,1": 1, "2,3": 10}
        ctx = _base_ctx(
            sight_data=sight_data,
            cell_los_counts=cell_los_counts,
            spawn_cells=spawn_cells,
        )

        goal = choose_goal_cell(ammo, [ammo, heavy], spawn_cells, ctx)
        # support_survival's hidden support cells (2026-11-06): the Ammo values
        # cover like the Medic. The old open-cell preference is measured with
        # the switch off in test_support_survival.
        assert goal == (2, 1)

    def test_medic_with_no_los_counts_still_draws_a_cell_the_heavy_sees(self):
        # MECH-07c replaces the old fallback to the Heavy's own cell. With no counts
        # every rank is 0, so the cells differ only by the walk: the Defense rule
        # still positions the Medic, and never on the Heavy's true cell.
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        medic = _make_player("red_medic", "red", "medic", cell_row=0, cell_col=0)
        heavy = _make_player("red_heavy", "red", "heavy", cell_row=3, cell_col=3)
        medic.player_memory["red_heavy"] = {
            "cell": (3, 3),
            "timestamp": 0,
            "role": "heavy",
        }
        sight_data = {"3,3": frozenset({"3,2", "3,4"})}
        ctx = _base_ctx(
            sight_data=sight_data, cell_los_counts={}, spawn_cells=spawn_cells
        )

        goal = choose_goal_cell(medic, [medic, heavy], spawn_cells, ctx)
        assert goal == (3, 2)

    def test_commander_seeks_enemy_medic_when_repositioning(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        commander = _make_player(
            "red_commander", "red", "commander", cell_row=0, cell_col=0
        )
        enemy_medic = _make_player(
            "blue_medic", "blue", "medic", cell_row=5, cell_col=5
        )
        # MECH-07: the Commander still hunts the Medic, now as an Attack goal: a
        # cell that can see the Medic's remembered cell, not the Medic's own cell.
        commander.player_memory["blue_medic"] = {
            "cell": (5, 5),
            "timestamp": 0,
            "role": "medic",
        }
        ctx = _base_ctx(spawn_cells=spawn_cells, sight_data={"5,5": frozenset({"5,4"})})

        goal = choose_goal_cell(commander, [commander, enemy_medic], spawn_cells, ctx)
        assert goal == (5, 4)

    def test_commander_does_not_seek_an_enemy_medic_it_never_saw(self):
        # MECH-07: the true cell is not information the Commander has.
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        commander = _make_player(
            "red_commander", "red", "commander", cell_row=0, cell_col=0
        )
        enemy_medic = _make_player(
            "blue_medic", "blue", "medic", cell_row=5, cell_col=5
        )
        ctx = _base_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(commander, [commander, enemy_medic], spawn_cells, ctx)
        assert goal == (9, 9)

    def test_commander_falls_back_to_enemy_base_when_no_enemy_medic(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        commander = _make_player(
            "red_commander", "red", "commander", cell_row=0, cell_col=0
        )
        ctx = _base_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(commander, [commander], spawn_cells, ctx)
        assert goal == (9, 9)

    def test_action_tag_moves_toward_nearest_enemy(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        enemy_near = _make_player("blue_scout", "blue", "scout", cell_row=2, cell_col=2)
        enemy_far = _make_player("blue_heavy", "blue", "heavy", cell_row=8, cell_col=8)
        ctx = _base_ctx(spawn_cells=spawn_cells)
        # MECH-17: tag goals read memory. No sight data here, so the goal is the target's
        # cell. The invariant is unchanged: the nearest of them is the target.
        scout.player_memory["blue_scout"] = {
            "cell": (2, 2),
            "timestamp": 0,
            "role": "scout",
        }
        scout.player_memory["blue_heavy"] = {
            "cell": (8, 8),
            "timestamp": 0,
            "role": "heavy",
        }

        goal = choose_goal_cell(
            scout,
            [scout, enemy_near, enemy_far],
            spawn_cells,
            ctx,
            intended_action="tag_player",
        )
        assert goal == (2, 2)

    def test_action_tag_commander_seeks_enemy_medic_first(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        commander = _make_player(
            "red_commander", "red", "commander", cell_row=0, cell_col=0
        )
        enemy_near = _make_player("blue_heavy", "blue", "heavy", cell_row=1, cell_col=1)
        enemy_medic = _make_player(
            "blue_medic", "blue", "medic", cell_row=7, cell_col=7
        )
        ctx = _base_ctx(spawn_cells=spawn_cells)
        # MECH-07: the invariant holds for a Medic the Commander knows about. In
        # Attack, the Medic-first step reads memory instead of the true cell.
        commander.player_memory["blue_medic"] = {
            "cell": (7, 7),
            "timestamp": 0,
            "role": "medic",
        }

        goal = choose_goal_cell(
            commander,
            [commander, enemy_near, enemy_medic],
            spawn_cells,
            ctx,
            intended_action="tag_player",
        )
        # A remembered Medic comes first regardless of distance
        assert goal == (7, 7)

    def test_action_tag_commander_not_knowing_the_medic_takes_the_nearest_enemy(self):
        # MECH-07: the Medic-first step reads memory only; nobody walks to a cell
        # they never learned.
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        commander = _make_player(
            "red_commander", "red", "commander", cell_row=0, cell_col=0
        )
        enemy_near = _make_player("blue_heavy", "blue", "heavy", cell_row=1, cell_col=1)
        enemy_medic = _make_player(
            "blue_medic", "blue", "medic", cell_row=7, cell_col=7
        )
        ctx = _base_ctx(spawn_cells=spawn_cells)
        # MECH-17: the nearest enemy is read from memory too, so the Heavy is remembered.
        # No sight data here, so the goal is its cell. The Medic stays unknown.
        commander.player_memory["blue_heavy"] = {
            "cell": (1, 1),
            "timestamp": 0,
            "role": "heavy",
        }

        goal = choose_goal_cell(
            commander,
            [commander, enemy_near, enemy_medic],
            spawn_cells,
            ctx,
            intended_action="tag_player",
        )
        assert goal == (1, 1)

    def test_action_resupply_medic_goes_to_neediest_by_lives(self, switches_off):
        # MECH-07c: the ally is at 10% of the Medic's resource, below the 75% Defense
        # needs before it walks over (test_modes.TestDefenseResupplyGoal). With the
        # support-survival switches on (the default) the Medic holds its position
        # and never walks: test_support_survival.
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        medic = _make_player("red_medic", "red", "medic", cell_row=0, cell_col=0)
        ally_low = _make_player(
            "red_scout", "red", "scout", lives=3, cell_row=2, cell_col=2
        )
        ally_high = _make_player(
            "red_heavy", "red", "heavy", lives=18, cell_row=5, cell_col=5
        )
        ctx = _base_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(
            medic,
            [medic, ally_low, ally_high],
            spawn_cells,
            ctx,
            intended_action="resupply_ally",
        )
        # ally_low has 3/30 = 10% lives; ally_high has 18/20 = 90%
        assert goal == (2, 2)

    def test_action_resupply_ammo_goes_to_neediest_by_shots(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        ammo = _make_player("red_ammo", "red", "ammo", cell_row=0, cell_col=0)
        ally_low_shots = _make_player(
            "red_scout", "red", "scout", shots=2, cell_row=3, cell_col=3
        )
        ally_ok_shots = _make_player(
            "red_commander", "red", "commander", shots=50, cell_row=7, cell_col=7
        )
        ctx = _base_ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(
            ammo,
            [ammo, ally_low_shots, ally_ok_shots],
            spawn_cells,
            ctx,
            intended_action="resupply_ally",
        )
        # ally_low_shots has 2/60 shots; ally_ok_shots has 50/60
        assert goal == (3, 3)

    def test_action_hide_moves_to_safest_adjacent_cell(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=2, cell_col=2)
        adj = {(2, 2): [(2, 1), (2, 3)]}
        cell_los_counts = {"2,1": 1, "2,3": 10}
        ctx = _base_ctx(
            adj=adj, cell_los_counts=cell_los_counts, spawn_cells=spawn_cells
        )

        goal = choose_goal_cell(
            scout, [scout], spawn_cells, ctx, intended_action="hide"
        )
        assert goal == (2, 1)

    def test_critical_lives_overrides_action_for_non_support(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        # Scout with ≤ 30% of 30 max lives = 9 lives threshold; give 5
        scout = _make_player(
            "red_scout", "red", "scout", lives=5, cell_row=0, cell_col=0
        )
        medic = _make_player("red_medic", "red", "medic", cell_row=4, cell_col=4)
        enemy = _make_player("blue_heavy", "blue", "heavy", cell_row=1, cell_col=1)
        ctx = _base_ctx(spawn_cells=spawn_cells)
        # CAL-03c (#591): the Resupply visit a critical player starts.
        scout.visit = Visit(phase="go", started=0, goal=(4, 4))

        # Even with tag_player action, critical lives → seek medic
        goal = choose_goal_cell(
            scout,
            [scout, medic, enemy],
            spawn_cells,
            ctx,
            intended_action="tag_player",
        )
        assert goal == (4, 4)

    def test_default_enemy_base_when_no_ctx(self):
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)

        goal = choose_goal_cell(scout, [scout], spawn_cells)
        assert goal == (9, 9)


# ---------------------------------------------------------------------------
# MAP-05 DB integration tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMap05DBIntegration:
    """DB-backed tests for _resolve_map_data and _build_movement_ctx with MAP-05 configs."""

    def _make_full_map(self, name="MAP05Test"):
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapZoneConfig,
            MapBaseConfig,
            MapCellRankingConfig,
            HeavyStrongSpotsConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines, compute_high_los_ranking

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
        sight_data = compute_sight_lines(zone_data)
        SightLineConfig.objects.create(
            arena_map=arena_map, zone_size=100, sight_data=sight_data
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=100, visible_cells=[[0, 0]]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=100, visible_cells=[[2, 2]]
        )

        ranked = compute_high_los_ranking(sight_data)
        MapCellRankingConfig.objects.create(
            arena_map=arena_map, zone_size=100, ranked_cells=ranked
        )
        top_n = max(1, len(ranked) // 4)
        HeavyStrongSpotsConfig.objects.create(
            arena_map=arena_map, zone_size=100, cells=ranked[:top_n]
        )
        return arena_map

    def _make_map_without_map05_configs(self, name="MAP05Absent"):
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapZoneConfig,
            MapBaseConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines

        zone_data = [[1, 1], [1, 1]]
        arena_map = ArenaMap.objects.create(name=name, img_width=200, img_height=200)
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
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=100, visible_cells=[[0, 0]]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=100, visible_cells=[[1, 1]]
        )
        return arena_map

    def test_resolve_map_data_returns_cell_ranking(self):
        """resolve_map_data cell_ranking field is the ranked cell list."""
        arena_map = self._make_full_map("ResolveCellRank")
        cell_ranking = resolve_map_data(arena_map).cell_ranking

        assert isinstance(cell_ranking, list)
        assert len(cell_ranking) > 0
        # Each entry is [row, col]
        assert len(cell_ranking[0]) == 2

    def test_resolve_map_data_returns_strong_spots(self):
        """resolve_map_data strong_spots field is the heavy strong spots list."""
        arena_map = self._make_full_map("ResolveStrongSpots")
        strong_spots = resolve_map_data(arena_map).strong_spots

        assert isinstance(strong_spots, list)
        assert len(strong_spots) > 0

    def test_resolve_map_data_returns_empty_lists_when_configs_absent(self):
        """resolve_map_data returns [] for cell_ranking and strong_spots when configs missing."""
        arena_map = self._make_map_without_map05_configs("AbsentConfigs")
        md = resolve_map_data(arena_map)

        assert md.cell_ranking == []
        assert md.strong_spots == []

    def test_build_movement_ctx_populates_map05_keys(self):
        """load_map_context returns a MapContext with cell_los_counts, high_los_cells,
        and strong_spots populated. SIM-09: load_map_context replaces the former
        two-step resolve+build pipeline, deriving high_los_cells from cell_ranking inline.
        """
        arena_map = self._make_full_map("BuildCtxMAP05")
        ctx, _zone_size = load_map_context(arena_map)

        assert "cell_los_counts" in ctx
        assert "high_los_cells" in ctx
        assert "strong_spots" in ctx

        assert isinstance(ctx["cell_los_counts"], dict)
        assert isinstance(ctx["high_los_cells"], list)
        assert isinstance(ctx["strong_spots"], list)

        # strong_spots should be tuples
        for spot in ctx["strong_spots"]:
            assert isinstance(spot, tuple)
