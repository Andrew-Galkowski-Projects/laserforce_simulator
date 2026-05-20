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
    resolve_map_data,
    zone_from_cell,
)

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
