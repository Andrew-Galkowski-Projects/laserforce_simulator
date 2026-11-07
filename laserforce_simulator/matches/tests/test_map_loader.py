"""SIM-09 — Pin the public API of ``matches.sim_helpers.map_loader``.

This is the new home of the static map-loading helpers that previously lived
as ``ResourceBasedSimulator._zone_from_cell`` / ``_resolve_map_data`` /
``_build_movement_ctx`` / ``_build_spawn_assignments`` / ``_load_map_context``.

The deeper mechanic behaviour of each helper is covered by ``test_map.py``
and ``test_spawn_assigner.py``. The point of *this* file is to pin the new
public free-function names + basic shape so a future refactor that renames or
silently changes a return type fails loudly here.
"""

from __future__ import annotations

import pytest

from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.map_loader import (
    build_movement_ctx,
    build_spawn_assignments,
    load_map_context,
    reachable_base_sights,
    reachable_spawn_pools,
    resolve_map_data,
    zone_from_cell,
)
from matches.sim_helpers.pathfinding import build_movement_adjacency

# ---------------------------------------------------------------------------
# load_map_context — primary public entry point
# ---------------------------------------------------------------------------


class TestLoadMapContextNone:
    """``load_map_context(None)`` is the 3-zone fallback path: returns the
    sentinel ``(None, None)`` pair so callers can use ``ctx is None`` to
    branch into the no-map code path.
    """

    def test_none_arena_map_returns_pair_of_nones(self):
        ctx, zone_size = load_map_context(None)
        assert ctx is None
        assert zone_size is None


@pytest.mark.django_db
class TestLoadMapContextDB:
    """``load_map_context(arena_map)`` resolves the ORM-backed map config,
    constructs a ``MapContext``, and returns ``(ctx, zone_size)``.
    """

    def _make_minimal_arena_map(self, name="MapLoaderHappy"):
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines

        zone_size = 50
        zone_data = [[1] * 4 for _ in range(4)]
        arena_map = ArenaMap.objects.create(
            name=name, img_width=4 * zone_size, img_height=4 * zone_size
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
            x_px=4 * zone_size - zone_size // 2,
            y_px=4 * zone_size - zone_size // 2,
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            sight_data=compute_sight_lines(zone_data),
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            zone_size=zone_size,
            visible_cells=[],
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            zone_size=zone_size,
            visible_cells=[],
        )
        return arena_map, zone_size

    def test_happy_path_returns_map_context_and_zone_size(self):
        arena_map, expected_zone_size = self._make_minimal_arena_map(
            "MapLoaderHappyPath"
        )
        ctx, zone_size = load_map_context(arena_map)
        assert isinstance(ctx, MapContext)
        assert zone_size == expected_zone_size

    def test_missing_zone_config_raises_value_error(self):
        from core.models import ArenaMap

        arena_map = ArenaMap.objects.create(
            name="NoZoneConfig", img_width=100, img_height=100
        )
        with pytest.raises(ValueError):
            load_map_context(arena_map)

    def test_missing_red_base_raises_value_error(self):
        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig

        arena_map = ArenaMap.objects.create(
            name="NoRedBaseLoader", img_width=100, img_height=100
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
        with pytest.raises(ValueError):
            load_map_context(arena_map)

    def test_missing_sight_lines_raises_value_error(self):
        from core.models import ArenaMap, MapBaseConfig, MapZoneConfig

        arena_map = ArenaMap.objects.create(
            name="NoSightLinesLoader", img_width=100, img_height=100
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
        with pytest.raises(ValueError):
            load_map_context(arena_map)


# ---------------------------------------------------------------------------
# reachable_spawn_pools — LFS-01: no spawn in a pocket cut off from the floor
# ---------------------------------------------------------------------------

# (0, 0) is a one-cell pocket; every other floor cell is one connected area.
_POCKET_GRID = [
    [1, 0, 1, 1],
    [0, 0, 1, 1],
    [1, 1, 1, 1],
]


class TestReachableSpawnPools:
    """A spawn cell outside the main floor becomes the nearest main-floor
    cell to that team's base not already in the pool; the pool keeps its
    size and order. (LFS-01: Syracuse at 30px cut red's base corridor off,
    and four red players never left it.)
    """

    def test_pocketed_cell_is_replaced_by_nearest_main_floor_cell(self):
        adj = build_movement_adjacency(_POCKET_GRID)
        pools = reachable_spawn_pools(
            {"red": [(0, 0), (2, 0)], "blue": [(2, 3)]},
            {"red": (0, 0), "blue": (2, 3)},
            adj,
        )
        # (2, 0) is already in the pool; (0, 2) is the next nearest (distance 2).
        assert pools == {"red": [(0, 2), (2, 0)], "blue": [(2, 3)]}

    def test_replacement_skips_cells_already_in_the_pool(self):
        adj = build_movement_adjacency(_POCKET_GRID)
        # (0, 2) is the nearest main-floor cell to the base, but red already
        # spawns there, so the pocketed cell takes (2, 0) instead.
        pools = reachable_spawn_pools({"red": [(0, 0), (0, 2)]}, {"red": (0, 0)}, adj)
        assert pools == {"red": [(2, 0), (0, 2)]}

    def test_reachable_pools_come_back_unchanged(self):
        adj = build_movement_adjacency(_POCKET_GRID)
        pools = {"red": [(2, 0), (2, 1)], "blue": [(0, 3), (1, 3)]}
        assert (
            reachable_spawn_pools(pools, {"red": (2, 0), "blue": (0, 3)}, adj) == pools
        )

    def test_no_floor_leaves_pools_alone(self):
        pools = {"red": [(0, 0)]}
        assert reachable_spawn_pools(pools, {"red": (0, 0)}, {}) == pools


class TestReachableBaseSights:
    """A Base whose range cells are all cut off gets as many main-floor cells, the
    nearest to its range cells (Manhattan to the nearest, then row, then column)."""

    def test_a_cut_off_type_moves_to_the_nearest_main_floor_cells(self):
        adj = build_movement_adjacency(_POCKET_GRID)
        # (0, 2) is 1 from (0, 1). Row breaks the tie among those 2 away.
        assert reachable_base_sights({"red": ["0,0", "0,1"]}, adj) == {
            "red": frozenset({"0,2", "0,3"})
        }

    def test_the_tie_goes_to_the_lower_row(self):
        adj = build_movement_adjacency(_POCKET_GRID)
        # (0, 2) and (2, 0) are both 2 from (0, 0).
        assert reachable_base_sights({"red": ["0,0"]}, adj) == {
            "red": frozenset({"0,2"})
        }

    def test_the_column_breaks_a_tie_within_a_row(self):
        adj = build_movement_adjacency(
            [[1, 0, 1, 0, 1], [0, 0, 0, 0, 0], [1, 1, 1, 1, 1]]
        )
        # (2, 2) is nearest. (2, 1) and (2, 3) tie after it, and the column decides.
        assert reachable_base_sights({"red": ["0,2", "1,2"]}, adj) == {
            "red": frozenset({"2,1", "2,2"})
        }

    def test_a_type_with_any_main_floor_cell_is_unchanged(self):
        adj = build_movement_adjacency(_POCKET_GRID)
        assert reachable_base_sights({"red": ["0,0", "2,0"]}, adj) == {
            "red": frozenset({"0,0", "2,0"})
        }

    def test_an_empty_type_is_unchanged(self):
        adj = build_movement_adjacency(_POCKET_GRID)
        assert reachable_base_sights({"red": []}, adj) == {"red": frozenset()}

    def test_no_floor_leaves_it_alone(self):
        assert reachable_base_sights({"red": ["0,0"]}, {}) == {
            "red": frozenset({"0,0"})
        }

    def test_each_type_is_read_on_its_own(self):
        adj = build_movement_adjacency(_POCKET_GRID)
        assert reachable_base_sights({"red": ["0,0"], "blue": ["2,0"]}, adj) == {
            "red": frozenset({"0,2"}),
            "blue": frozenset({"2,0"}),
        }

    def test_the_order_of_the_range_cells_does_not_matter(self):
        adj = build_movement_adjacency(_POCKET_GRID)
        assert reachable_base_sights(
            {"red": ["0,1", "0,0"]}, adj
        ) == reachable_base_sights({"red": ["0,0", "0,1"]}, adj)

    def test_a_pocket_free_map_is_untouched(self):
        adj = build_movement_adjacency([[1, 1], [1, 1]])
        sights = {"red": frozenset({"0,0"}), "neutral_1": frozenset({"1,1"})}
        assert reachable_base_sights(sights, adj) == sights


@pytest.mark.django_db
class TestSyracuseBaseRanges:
    """At 30px red's 6 range cells sat in a pocket cut off from the main floor
    (MECH-17); the owner's 20px configuration reaches every base from the floor,
    so the repair must leave all three ranges exactly as configured."""

    def _load(self):
        from pathlib import Path

        from core.models import ArenaMap
        from matches.lfstats_calibration import load_map_fixture
        from matches.sim_helpers.map_context import _main_floor

        fixture = Path(__file__).resolve().parents[3] / "data/maps/syracuse.json"
        arena_map: ArenaMap = load_map_fixture(fixture)
        ctx = load_map_context(arena_map)[0]
        return ctx, resolve_map_data(arena_map).base_sight_data, _main_floor(ctx.adj)

    def test_every_range_is_on_the_main_floor(self):
        _, raw, floor = self._load()
        keys = {f"{r},{c}" for r, c in floor}
        # The three placed Bases have a range; the unplaced neutral 2-4 rows are empty.
        for base_type in ("red", "blue", "neutral_1"):
            assert raw[base_type], base_type
        for base_type, cells in raw.items():
            assert cells <= keys, base_type

    def test_the_repair_leaves_every_range_unchanged(self):
        ctx, raw, _ = self._load()
        assert ctx.base_sight_data == raw


@pytest.mark.django_db
class TestLoadMapContextSpawnPools:
    def test_load_map_context_keeps_spawns_on_the_main_floor(self):
        from core.map_processing import compute_sight_lines
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )

        zone_size = 50
        arena_map = ArenaMap.objects.create(
            name="PocketSpawn", img_width=4 * zone_size, img_height=3 * zone_size
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data={
                "zones": _POCKET_GRID,
                "red_spawn": [[0, 0], [2, 0]],
                "blue_spawn": [[2, 3]],
            },
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=25, y_px=25
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="blue", x_px=175, y_px=125
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            sight_data=compute_sight_lines(_POCKET_GRID),
        )
        for base_type in ("red", "blue"):
            BaseSightLineConfig.objects.create(
                arena_map=arena_map,
                base_type=base_type,
                zone_size=zone_size,
                visible_cells=[],
            )
        ctx, _ = load_map_context(arena_map)
        assert ctx is not None
        assert ctx.team_spawn_pools["red"] == [(0, 2), (2, 0)]
        assert all(cell in ctx.adj for cell in ctx.team_spawn_pools["red"])


# ---------------------------------------------------------------------------
# zone_from_cell — pure helper, no DB
# ---------------------------------------------------------------------------


class TestZoneFromCell:
    """``zone_from_cell(row, col, spawn_cells)`` mirrors the parity tests
    that used to live in ``test_map.py``: it is a pure proximity-by-Manhattan
    classifier into 0=red, 1=neutral, 2=blue, with neutral as the fallback
    when ``spawn_cells`` is missing or empty.
    """

    def test_near_red_base_returns_red_zone(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 10)}
        assert zone_from_cell(0, 1, spawn_cells) == 0

    def test_near_blue_base_returns_blue_zone(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 10)}
        assert zone_from_cell(10, 9, spawn_cells) == 2

    def test_equidistant_is_neutral(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 0)}
        assert zone_from_cell(5, 0, spawn_cells) == 1

    def test_empty_spawn_cells_returns_neutral_fallback(self):
        assert zone_from_cell(5, 5, {}) == 1

    def test_none_or_missing_spawn_cells_treated_as_neutral(self):
        # No red entry → can't determine red zone → falls through to neutral.
        assert zone_from_cell(0, 0, {"blue": (10, 10)}) == 1


# ---------------------------------------------------------------------------
# build_spawn_assignments — thin delegation shim, parity tested in
# test_spawn_assigner.py. Just pin the new public name + basic shape.
# ---------------------------------------------------------------------------


class TestBuildSpawnAssignmentsPublicSurface:
    def test_commander_gets_front_cell_role_priority(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 10)}
        # Sorted by ascending Manhattan distance to enemy base (10,10):
        # (0,8)=12 closest → front; (0,0)=20 farthest → back.
        pools = {"red": [(0, 0), (0, 2), (0, 4), (0, 6), (0, 8)]}
        result = build_spawn_assignments(["commander"], "red", spawn_cells, pools)
        assert result[0] == (0, 8)

    def test_returns_dict_keyed_by_roster_index(self):
        spawn_cells = {"red": (0, 0), "blue": (10, 10)}
        pools = {"red": [(0, 0), (0, 2), (0, 4)]}
        result = build_spawn_assignments(
            ["commander", "scout", "medic"], "red", spawn_cells, pools
        )
        assert set(result.keys()) == {0, 1, 2}


# ---------------------------------------------------------------------------
# resolve_map_data — legacy shim retained for back-compat. One happy path.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResolveMapDataPublicSurface:
    def test_returns_dataclass_with_expected_fields(self):
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines

        zone_data = [[2, 1], [1, 3]]
        arena_map = ArenaMap.objects.create(
            name="ResolveLoader", img_width=100, img_height=100
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            zone_data=zone_data,
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
            sight_data=compute_sight_lines(zone_data),
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            zone_size=50,
            visible_cells=[],
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            zone_size=50,
            visible_cells=[],
        )

        md = resolve_map_data(arena_map)
        # MapData dataclass fields pinned here so a future rename trips this
        # test rather than silently shifting consumers.
        assert md.zone_size == 50
        assert md.zone_data == zone_data
        assert isinstance(md.spawn_cells, dict)
        assert isinstance(md.sight_data, dict)
        assert isinstance(md.base_sight_data, dict)


# ---------------------------------------------------------------------------
# build_movement_ctx — smoke: one keyword-arg-driven call returns a MapContext.
# ---------------------------------------------------------------------------


class TestBuildMovementCtxPublicSurface:
    def test_smoke_returns_map_context(self):
        ctx = build_movement_ctx(
            zone_data=[[1, 1], [1, 1]],
            spawn_cells={"red": (0, 0), "blue": (1, 1)},
            sight_data={},
            base_sight_data={},
            cell_los_counts={},
            high_los_cells=[],
            strong_spots=[],
            wall_meta={},
            team_spawn_pools={"red": [], "blue": []},
            elevation_grid=None,
        )
        assert isinstance(ctx, MapContext)
        assert ctx.spawn_cells == {"red": (0, 0), "blue": (1, 1)}
