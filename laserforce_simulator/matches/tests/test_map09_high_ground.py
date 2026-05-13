"""
Tests for MAP-09 · High Ground.

Covers:
  1. Elevation data round-trips through MapZoneConfig.zone_data
  2. Wall height stored/retrieved from wall_meta
  3. Shoot-over formula: attacker_elevation - wall_base_elevation > wall_height * 0.5
  4. LOS extended by elevation (high-ground attacker shoots over wall that was previously blocking)
  5. Hit-chance uphill modifier: target 1 elev above → multiplier 0.9
  6. Hit-chance large diff: target 6 elev above → clamps to 0.5
  7. Hit-chance downhill or level: no modifier (multiplier 1.0)
  8. Backwards compat: MapZoneConfig with no 'elevation' key defaults to all-zeros (no crash)
"""

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_player(tag_id, team_color, role="scout", cell_row=0, cell_col=0):
    """Create a lightweight in-memory PlayerState for unit tests."""
    from matches.sim_helpers.player_state import PlayerState

    return PlayerState(
        tag_id=tag_id,
        name=tag_id,
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=10,
        starting_shots=30,
        final_lives=10,
        final_shots=30,
        current_zone=1,
        cell_row=cell_row,
        cell_col=cell_col,
    )


# ---------------------------------------------------------------------------
# TestMap09ElevationStorage — zone_data round-trips (DB-backed)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMap09ElevationStorage:
    """Elevation array and wall height persisted correctly through MapZoneConfig."""

    def test_elevation_array_round_trips_in_zone_data(self):
        """Storing elevation as 2D float array in zone_data and reading it back."""
        from core.models import ArenaMap, MapZoneConfig

        arena_map = ArenaMap.objects.create(
            name="ElevRoundTrip", img_width=300, img_height=300
        )
        elevation = [[0.0, 1.5, 2.0], [0.5, 0.0, 3.0], [1.0, 2.5, 0.0]]
        zone_data = {
            "zones": [[1, 1, 1], [1, 1, 1], [1, 1, 1]],
            "blocked_edges": {},
            "elevation": elevation,
        }
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            zone_data=zone_data,
            confirmed=True,
        )

        config = arena_map.latest_confirmed_config()
        stored = config.zone_data
        assert isinstance(stored, dict), "zone_data must be a dict"
        assert "elevation" in stored, "elevation key must be present"
        assert (
            stored["elevation"] == elevation
        ), "elevation array must round-trip unchanged"

    def test_elevation_values_are_floats(self):
        """Individual elevation values survive JSON serialisation as numbers."""
        from core.models import ArenaMap, MapZoneConfig

        arena_map = ArenaMap.objects.create(
            name="ElevFloats", img_width=200, img_height=200
        )
        elevation = [[2.5, 0.0], [1.75, 3.0]]
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            zone_data={
                "zones": [[1, 1], [1, 1]],
                "blocked_edges": {},
                "elevation": elevation,
            },
            confirmed=True,
        )

        stored = arena_map.latest_confirmed_config().zone_data["elevation"]
        assert stored[0][0] == 2.5
        assert stored[1][0] == 1.75

    def test_elevation_missing_key_defaults_to_zeros_no_crash(self):
        """MapZoneConfig without 'elevation' key doesn't crash; callers must default to 0."""
        from core.models import ArenaMap, MapZoneConfig

        arena_map = ArenaMap.objects.create(
            name="ElevMissing", img_width=200, img_height=200
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            zone_data={"zones": [[1, 1], [1, 1]], "blocked_edges": {}},
            confirmed=True,
        )

        stored = arena_map.latest_confirmed_config().zone_data
        # Must not raise
        elevation = stored.get("elevation", None)
        # If key is absent, the production helper must return 0 for any cell
        from matches.sim_helpers.pathfinding import _elevation_at

        elev_data = None  # no elevation data at all
        assert _elevation_at(0, 0, elev_data) == 0.0
        assert _elevation_at(1, 1, elev_data) == 0.0

    def test_elevation_at_reads_from_dict(self):
        """_elevation_at returns the stored float for a given (row, col) key."""
        from matches.sim_helpers.pathfinding import _elevation_at

        elev_data = {(0, 0): 0.0, (0, 1): 1.5, (1, 0): 2.0}
        assert _elevation_at(0, 0, elev_data) == 0.0
        assert _elevation_at(0, 1, elev_data) == 1.5
        assert _elevation_at(1, 0, elev_data) == 2.0

    def test_elevation_at_missing_cell_returns_zero(self):
        """_elevation_at returns 0 for a cell not in the elevation dict."""
        from matches.sim_helpers.pathfinding import _elevation_at

        elev_data = {(0, 0): 3.0}
        assert _elevation_at(5, 5, elev_data) == 0.0

    def test_zone_data_without_elevation_still_loads_in_resolve_map_data(self):
        """_resolve_map_data does not crash when zone_data has no elevation key."""
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines
        from matches.simulation import ResourceBasedSimulator

        zone_data = [[1, 1], [1, 1]]
        arena_map = ArenaMap.objects.create(
            name="NoElevResolve", img_width=200, img_height=200
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            zone_data=zone_data,
            confirmed=True,
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
            arena_map=arena_map, base_type="red", zone_size=100, visible_cells=[]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=100, visible_cells=[]
        )

        # Must not raise
        result = ResourceBasedSimulator._resolve_map_data(arena_map)
        assert result is not None


# ---------------------------------------------------------------------------
# TestMap09WallHeight — wall height stored and retrieved
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMap09WallHeight:
    """Wall height stored in wall_meta and retrieved correctly."""

    def test_wall_height_round_trips_in_wall_meta(self):
        """wall_meta with height field persists correctly through MapZoneConfig.zone_data."""
        from core.models import ArenaMap, MapZoneConfig

        arena_map = ArenaMap.objects.create(
            name="WallHeightRoundTrip", img_width=300, img_height=100
        )
        wall_meta = {
            "0,1": {"facing": "N", "height": 2.0},
            "0,2": {"facing": "E", "height": 1.0},
        }
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            zone_data={
                "zones": [[1, 0, 0]],
                "blocked_edges": {},
                "wall_meta": wall_meta,
            },
            confirmed=True,
        )

        stored = arena_map.latest_confirmed_config().zone_data
        assert stored["wall_meta"]["0,1"]["height"] == 2.0
        assert stored["wall_meta"]["0,2"]["height"] == 1.0

    def test_wall_height_missing_key_defaults_gracefully(self):
        """A wall entry in wall_meta without 'height' must not crash the shoot-over check.

        Production code must treat missing height as infinity (or a very large value),
        meaning the wall cannot be shot over.
        """
        from core.models import ArenaMap, MapZoneConfig

        arena_map = ArenaMap.objects.create(
            name="WallNoHeight", img_width=200, img_height=100
        )
        # wall_meta entry has facing but no height
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            zone_data={
                "zones": [[1, 0, 1]],
                "blocked_edges": {},
                "wall_meta": {"0,1": {"facing": "N"}},  # no height key
            },
            confirmed=True,
        )
        stored = arena_map.latest_confirmed_config().zone_data
        height = stored["wall_meta"]["0,1"].get("height", None)
        # Height is not present — production code must not crash when reading this
        assert height is None  # confirms the key is absent; callers must default


# ---------------------------------------------------------------------------
# TestMap09ShootOver — shoot-over formula (pure unit tests, no DB)
# ---------------------------------------------------------------------------


class TestMap09ShootOver:
    """Unit tests for the shoot-over formula.

    Shoot-over: attacker_elev - wall_base_elev > wall_height * 0.5

    These tests are pure-formula tests.  The function under test is expected to
    live in core.map_processing (extending _has_los) or as a standalone helper
    that _has_los will call.  Tests import it directly so they act as a spec
    for the production API.
    """

    def test_shoot_over_true_when_attacker_high_enough(self):
        """Attacker at elev 2.0, wall at base_elev 0.0, wall height 2.0: can shoot over.

        2.0 - 0.0 = 2.0 > 2.0 * 0.5 = 1.0  →  True
        """
        from core.map_processing import can_shoot_over_wall

        assert (
            can_shoot_over_wall(attacker_elev=2.0, wall_base_elev=0.0, wall_height=2.0)
            is True
        )

    def test_shoot_over_false_when_attacker_too_low(self):
        """Attacker at elev 0.5, same wall: cannot shoot over.

        0.5 - 0.0 = 0.5 > 1.0  →  False
        """
        from core.map_processing import can_shoot_over_wall

        assert (
            can_shoot_over_wall(attacker_elev=0.5, wall_base_elev=0.0, wall_height=2.0)
            is False
        )

    def test_shoot_over_exactly_at_threshold_is_false(self):
        """Boundary: attacker_elev - wall_base_elev == wall_height * 0.5 is NOT enough.

        The formula uses strict greater-than, so equality must return False.
        1.0 - 0.0 = 1.0 > 2.0 * 0.5 = 1.0  →  False (not strictly greater)
        """
        from core.map_processing import can_shoot_over_wall

        assert (
            can_shoot_over_wall(attacker_elev=1.0, wall_base_elev=0.0, wall_height=2.0)
            is False
        )

    def test_shoot_over_elevated_wall_base(self):
        """Wall sitting on elevated terrain raises the effective threshold.

        attacker_elev=3.0, wall_base_elev=2.0, height=2.0:
        3.0 - 2.0 = 1.0 > 2.0 * 0.5 = 1.0  →  False
        """
        from core.map_processing import can_shoot_over_wall

        assert (
            can_shoot_over_wall(attacker_elev=3.0, wall_base_elev=2.0, wall_height=2.0)
            is False
        )

    def test_shoot_over_elevated_wall_base_high_attacker(self):
        """With attacker above the threshold.

        attacker_elev=4.0, wall_base_elev=2.0, height=2.0:
        4.0 - 2.0 = 2.0 > 1.0  →  True
        """
        from core.map_processing import can_shoot_over_wall

        assert (
            can_shoot_over_wall(attacker_elev=4.0, wall_base_elev=2.0, wall_height=2.0)
            is True
        )

    def test_shoot_over_short_wall_always_passable(self):
        """Very short wall (height 0.1): any attacker above base_elev can shoot over."""
        from core.map_processing import can_shoot_over_wall

        # attacker_elev - base_elev = 0.1 > 0.05 → True
        assert (
            can_shoot_over_wall(attacker_elev=0.1, wall_base_elev=0.0, wall_height=0.1)
            is True
        )

    def test_shoot_over_zero_height_wall(self):
        """Wall with height 0: any positive attacker elevation difference shoots over."""
        from core.map_processing import can_shoot_over_wall

        # 0.01 > 0.0 * 0.5 = 0.0  →  True
        assert (
            can_shoot_over_wall(attacker_elev=0.01, wall_base_elev=0.0, wall_height=0.0)
            is True
        )

    def test_shoot_over_no_elevation_advantage_fails(self):
        """Attacker at same elevation as wall base and nonzero wall height cannot shoot over."""
        from core.map_processing import can_shoot_over_wall

        # 0.0 - 0.0 = 0.0, not > 0.5
        assert (
            can_shoot_over_wall(attacker_elev=0.0, wall_base_elev=0.0, wall_height=1.0)
            is False
        )


# ---------------------------------------------------------------------------
# TestMap09LOSWithElevation — LOS extended by elevation (no DB)
# ---------------------------------------------------------------------------


class TestMap09LOSWithElevation:
    """Tests that elevation data feeds into the LOS computation (_has_los).

    _has_los must accept an optional elevation_data dict (and optional wall_meta)
    so that shoot-over can open LOS paths that were previously blocked by walls.
    """

    def test_high_wall_blocks_los_without_elevation(self):
        """Baseline: a high wall in the middle of a 1×3 grid blocks LOS (value 0)."""
        from core.map_processing import _has_los

        zone_data = [[1, 0, 1]]
        assert _has_los(zone_data, 0, 0, 0, 2) is False

    def test_high_wall_blocked_even_with_flat_elevation(self):
        """All-zero elevation gives no shoot-over advantage."""
        from core.map_processing import _has_los

        zone_data = [[1, 0, 1]]
        wall_meta = {"0,1": {"height": 2.0}}
        elevation_grid = [[0.0, 0.0, 0.0]]
        # 0.0 - 0.0 = 0.0, not > 1.0 → still blocked
        assert (
            _has_los(
                zone_data,
                0,
                0,
                0,
                2,
                wall_meta=wall_meta,
                elevation_grid=elevation_grid,
            )
            is False
        )

    def test_elevated_attacker_can_shoot_over_wall(self):
        """Attacker at elevation 2.0 can shoot over a wall with height 2.0 at base 0.0.

        shoot-over: 2.0 - 0.0 = 2.0 > 2.0 * 0.5 = 1.0  →  True → LOS opens.
        """
        from core.map_processing import _has_los

        zone_data = [[1, 0, 1]]
        wall_meta = {"0,1": {"height": 2.0}}
        # Attacker at (0,0) is elevated, wall at (0,1) is at ground level
        elevation_grid = [[2.0, 0.0, 0.0]]
        assert (
            _has_los(
                zone_data,
                0,
                0,
                0,
                2,
                wall_meta=wall_meta,
                elevation_grid=elevation_grid,
            )
            is True
        )

    def test_insufficient_elevation_still_blocked(self):
        """Attacker at 0.5 elevation cannot shoot over a wall with height 2.0.

        0.5 - 0.0 = 0.5, not > 1.0 → still blocked.
        """
        from core.map_processing import _has_los

        zone_data = [[1, 0, 1]]
        wall_meta = {"0,1": {"height": 2.0}}
        elevation_grid = [[0.5, 0.0, 0.0]]
        assert (
            _has_los(
                zone_data,
                0,
                0,
                0,
                2,
                wall_meta=wall_meta,
                elevation_grid=elevation_grid,
            )
            is False
        )

    def test_elevation_does_not_override_windowed_wall(self):
        """Windowed wall (5) already allows LOS through aperture; elevation is not needed."""
        from core.map_processing import _has_los

        zone_data = [[1, 5, 1]]
        # Windowed wall should remain transparent regardless of elevation_data
        assert _has_los(zone_data, 0, 0, 0, 2) is True

    def test_elevation_does_not_break_existing_los(self):
        """_has_los with elevation_grid still returns True when no wall is blocking."""
        from core.map_processing import _has_los

        zone_data = [[1, 1, 1]]
        elevation_grid = [[0.0, 1.0, 2.0]]
        assert _has_los(zone_data, 0, 0, 0, 2, elevation_grid=elevation_grid) is True

    def test_wall_without_height_in_meta_is_not_shoot_overable(self):
        """If a wall cell in the path has no 'height' in wall_meta, treat as impassable.

        Production code must default to blocking when height is unknown.
        """
        from core.map_processing import _has_los

        zone_data = [[1, 0, 1]]
        # wall_meta has facing but no height
        wall_meta = {"0,1": {"facing": "N"}}
        elevation_grid = [[5.0, 0.0, 0.0]]
        # Without height we cannot compute shoot-over → must remain blocked
        assert (
            _has_los(
                zone_data,
                0,
                0,
                0,
                2,
                wall_meta=wall_meta,
                elevation_grid=elevation_grid,
            )
            is False
        )

    def test_wall_not_in_meta_is_not_shoot_overable(self):
        """A high-wall cell with no entry in wall_meta is not shoot-overable."""
        from core.map_processing import _has_los

        zone_data = [[1, 0, 1]]
        wall_meta = {}  # no entry for (0,1)
        elevation_grid = [[5.0, 0.0, 0.0]]
        assert (
            _has_los(
                zone_data,
                0,
                0,
                0,
                2,
                wall_meta=wall_meta,
                elevation_grid=elevation_grid,
            )
            is False
        )

    # --- Low wall + elevation tests (MAP-09) ------------------------------------

    def test_low_wall_transparent_when_elevations_equal(self):
        """Low wall (4) is transparent when attacker and target are at the same elevation."""
        from core.map_processing import _has_los

        zone_data = [[1, 4, 1]]
        elevation_grid = [[0.0, 0.0, 0.0]]
        assert _has_los(zone_data, 0, 0, 0, 2, elevation_grid=elevation_grid) is True

    def test_low_wall_transparent_when_elevation_diff_at_threshold(self):
        """Low wall stays transparent when target is exactly 1.0 above attacker (strict >)."""
        from core.map_processing import _has_los

        zone_data = [[1, 4, 1]]
        elevation_grid = [[0.0, 0.0, 1.0]]
        assert _has_los(zone_data, 0, 0, 0, 2, elevation_grid=elevation_grid) is True

    def test_low_wall_blocks_when_wall_elevated_and_attacker_below(self):
        """Low wall blocks LOS when the wall cell itself is at elevation >= 0.5 and attacker is below it."""
        from core.map_processing import _has_los

        zone_data = [[1, 4, 1]]
        # Wall at elevation 1.0; attacker at 0.0 is below the wall → blocked.
        elevation_grid = [[0.0, 1.0, 0.0]]
        assert _has_los(zone_data, 0, 0, 0, 2, elevation_grid=elevation_grid) is False

    def test_low_wall_does_not_block_downhill_shot(self):
        """High-ground attacker shooting down through a low wall is never blocked."""
        from core.map_processing import _has_los

        zone_data = [[1, 4, 1]]
        elevation_grid = [[1.5, 0.0, 0.0]]
        # attacker at 1.5, target at 0.0 — target_elev - attacker_elev = -1.5 → not > 1
        assert _has_los(zone_data, 0, 0, 0, 2, elevation_grid=elevation_grid) is True

    def test_low_wall_blocks_asymmetry_in_compute_sight_lines(self):
        """compute_sight_lines reflects elevated low-wall asymmetry: below-wall blocked, at-wall-level open."""
        from core.map_processing import compute_sight_lines

        # Wall at elev 1.0 (>= 0.5 threshold): cell 0 is low ground (0.0), cell 2 is high ground (1.0).
        # Low ground → elevated wall → high ground: blocked.
        # High ground → elevated wall → low ground: open (attacker_elev >= wall_elev).
        zone_data = {
            "zones": [[1, 4, 1]],
            "blocked_edges_grid": None,
            "elevation": [[0.0, 1.0, 1.0]],
        }
        sight = compute_sight_lines(zone_data, use_quadtree=False)

        assert "0,2" not in sight.get("0,0", []), "Below-wall attacker must NOT see through elevated low wall"
        assert "0,0" in sight.get("0,2", []), "At-wall-elevation attacker MUST see low-ground target through elevated low wall"

    def test_low_wall_transparent_without_elevation_grid(self):
        """Low wall stays transparent when no elevation data is provided (backwards compat)."""
        from core.map_processing import _has_los

        zone_data = [[1, 4, 1]]
        assert _has_los(zone_data, 0, 0, 0, 2) is True

    def test_asymmetric_los_compute_sight_lines(self):
        """Elevated A can see B over a wall, but B cannot see A.

        Map: [A(elev=2), Wall(height=2), B(elev=0)]
        A→B: 2.0 - 0.0 = 2.0 > 1.0 → shoots over → LOS True
        B→A: 0.0 - 0.0 = 0.0, not > 1.0 → blocked  → LOS False

        compute_sight_lines must reflect this asymmetry:
          "0,0" has "0,2" in its visible list, but "0,2" does NOT have "0,0".
        """
        from core.map_processing import compute_sight_lines

        zone_data = {
            "zones": [[1, 0, 1]],
            "blocked_edges_grid": None,
            "elevation": [[2.0, 0.0, 0.0]],
            "wall_meta": {"0,1": {"height": 2.0}},
        }
        sight = compute_sight_lines(zone_data, use_quadtree=False)

        assert "0,2" in sight.get("0,0", []), "Elevated A must see B"
        assert "0,0" not in sight.get(
            "0,2", []
        ), "Ground-level B must NOT see elevated A"


# ---------------------------------------------------------------------------
# TestMap09HitChanceModifier — hit-chance elevation modifier (pure unit)
# ---------------------------------------------------------------------------


class TestMap09HitChanceModifier:
    """Unit tests for the hit-chance uphill modifier.

    Formula: hit_chance *= max(0.5, 1 - 0.1 * elevation_diff)
    where elevation_diff = max(0, target_elevation - attacker_elevation)

    The function under test is expected to be importable as:
        from matches.simulation import elevation_hit_modifier
    """

    def test_downhill_no_modifier(self):
        """Attacker higher than target → elevation_diff=0 → multiplier=1.0."""
        from matches.simulation import elevation_hit_modifier

        # attacker at 2.0, target at 0.0 → diff = max(0, -2.0) = 0 → 1.0
        assert elevation_hit_modifier(attacker_elev=2.0, target_elev=0.0) == 1.0

    def test_level_no_modifier(self):
        """Same elevation → elevation_diff=0 → multiplier=1.0."""
        from matches.simulation import elevation_hit_modifier

        assert elevation_hit_modifier(attacker_elev=1.5, target_elev=1.5) == 1.0

    def test_uphill_one_unit_modifier(self):
        """Target 1 unit above attacker → multiplier = max(0.5, 1 - 0.1 * 1) = 0.9."""
        from matches.simulation import elevation_hit_modifier

        result = elevation_hit_modifier(attacker_elev=0.0, target_elev=1.0)
        assert abs(result - 0.9) < 1e-9, f"Expected 0.9, got {result}"

    def test_uphill_three_units_modifier(self):
        """Target 3 units above → multiplier = max(0.5, 1 - 0.3) = 0.7."""
        from matches.simulation import elevation_hit_modifier

        result = elevation_hit_modifier(attacker_elev=0.0, target_elev=3.0)
        assert abs(result - 0.7) < 1e-9, f"Expected 0.7, got {result}"

    def test_uphill_five_units_clamps_to_half(self):
        """Target 5 units above → max(0.5, 1 - 0.5) = 0.5 (at boundary, not yet clamped)."""
        from matches.simulation import elevation_hit_modifier

        result = elevation_hit_modifier(attacker_elev=0.0, target_elev=5.0)
        assert abs(result - 0.5) < 1e-9, f"Expected 0.5, got {result}"

    def test_uphill_six_units_clamps_to_half(self):
        """Target 6 units above → max(0.5, 1 - 0.6) = max(0.5, 0.4) = 0.5 (clamped)."""
        from matches.simulation import elevation_hit_modifier

        result = elevation_hit_modifier(attacker_elev=0.0, target_elev=6.0)
        assert abs(result - 0.5) < 1e-9, f"Expected 0.5 (clamped), got {result}"

    def test_uphill_large_diff_always_clamps_to_half(self):
        """Very large uphill difference must always produce exactly 0.5."""
        from matches.simulation import elevation_hit_modifier

        result = elevation_hit_modifier(attacker_elev=0.0, target_elev=100.0)
        assert abs(result - 0.5) < 1e-9, f"Expected 0.5, got {result}"

    def test_uphill_fractional_diff(self):
        """Target 2.5 units above → max(0.5, 1 - 0.25) = 0.75."""
        from matches.simulation import elevation_hit_modifier

        result = elevation_hit_modifier(attacker_elev=1.0, target_elev=3.5)
        assert abs(result - 0.75) < 1e-9, f"Expected 0.75, got {result}"

    def test_modifier_applied_to_hit_chance_reduces_it(self):
        """When a modifier <1.0 is applied multiplicatively, hit_chance decreases.

        This tests the contract between elevation_hit_modifier and the caller:
        new_hit_chance = original_hit_chance * elevation_hit_modifier(...)
        """
        from matches.simulation import elevation_hit_modifier

        original_hit_chance = 80
        modifier = elevation_hit_modifier(attacker_elev=0.0, target_elev=1.0)  # 0.9
        new_hit_chance = original_hit_chance * modifier
        assert new_hit_chance == pytest.approx(72.0, abs=1e-6)

    def test_modifier_does_not_affect_downhill_hit_chance(self):
        """Downhill modifier is 1.0, so hit_chance is unchanged."""
        from matches.simulation import elevation_hit_modifier

        original_hit_chance = 75
        modifier = elevation_hit_modifier(attacker_elev=3.0, target_elev=1.0)
        assert modifier == 1.0
        assert original_hit_chance * modifier == 75.0


# ---------------------------------------------------------------------------
# TestMap09BackwardsCompat — no elevation key → all zeros, no crash
# ---------------------------------------------------------------------------


class TestMap09BackwardsCompat:
    """Old zone_data (list format or dict without 'elevation') must not crash anything."""

    def test_elevation_at_with_none_returns_zero(self):
        """_elevation_at(r, c, None) always returns 0."""
        from matches.sim_helpers.pathfinding import _elevation_at

        assert _elevation_at(0, 0, None) == 0.0
        assert _elevation_at(99, 99, None) == 0.0

    def test_elevation_at_with_empty_dict_returns_zero(self):
        """_elevation_at with an empty dict returns 0 for all cells."""
        from matches.sim_helpers.pathfinding import _elevation_at

        assert _elevation_at(2, 3, {}) == 0.0

    def test_hit_modifier_with_zero_elevation_is_one(self):
        """When both elevations are 0 (legacy default), modifier is 1.0 (no change)."""
        from matches.simulation import elevation_hit_modifier

        assert elevation_hit_modifier(attacker_elev=0.0, target_elev=0.0) == 1.0

    def test_can_shoot_over_with_no_height_is_blocked(self):
        """can_shoot_over_wall with height=None (absent key) returns False (safe default)."""
        from core.map_processing import can_shoot_over_wall

        # Production code must handle None height gracefully
        assert (
            can_shoot_over_wall(
                attacker_elev=10.0, wall_base_elev=0.0, wall_height=None
            )
            is False
        )

    def test_has_los_without_elevation_kwargs_unchanged(self):
        """_has_los called without elevation_data/wall_meta kwargs behaves exactly as before."""
        from core.map_processing import _has_los

        zone_data = [[1, 1, 1]]
        # Basic LOS with no new kwargs — must still work
        assert _has_los(zone_data, 0, 0, 0, 2) is True

        zone_data_wall = [[1, 0, 1]]
        assert _has_los(zone_data_wall, 0, 0, 0, 2) is False


# ---------------------------------------------------------------------------
# TestMap09ResolvesElevationFromZoneData — _resolve_map_data returns elevation (DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMap09ResolvesElevationFromZoneData:
    """_resolve_map_data should expose elevation_data when present in zone_data."""

    def _make_elevated_map(self, name: str):
        """2×2 all-floor map with elevation=[0.0, 2.0; 0.0, 0.0] and one tall wall."""
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines

        # Simple 2-column map with no walls; elevation gives high-ground to (0,1)
        zone_data_grid = [[1, 1], [1, 1]]
        elevation = [[0.0, 2.0], [0.0, 0.0]]
        arena_map = ArenaMap.objects.create(name=name, img_width=200, img_height=200)
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            zone_data={
                "zones": zone_data_grid,
                "blocked_edges": {},
                "elevation": elevation,
            },
            confirmed=True,
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
            sight_data=compute_sight_lines(zone_data_grid),
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=100, visible_cells=[]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=100, visible_cells=[]
        )
        return arena_map

    def test_resolve_map_data_returns_elevation_data(self):
        """_resolve_map_data's return tuple includes elevation_data when present.

        MAP-09 will add elevation_data as the 10th return value. This test
        specifies that contract so production code knows what to implement.
        """
        from matches.simulation import ResourceBasedSimulator

        arena_map = self._make_elevated_map("ElevResolve")
        result = ResourceBasedSimulator._resolve_map_data(arena_map)

        # The 10th element (index 9) should be the elevation_data dict.
        # Until implemented, this position may not exist — the test will fail
        # as designed (TDD red phase).
        assert (
            len(result) >= 10
        ), "_resolve_map_data must return at least 10 elements (index 9 = elevation_data)"
        elevation_data = result[9]
        assert (
            elevation_data is not None
        ), "elevation_data must not be None when zone_data has 'elevation'"
        # elevation_data is a 2D list of floats matching the zones grid shape
        # Cell (0,1) has elevation 2.0 in the fixture
        assert (
            elevation_data[0][1] == 2.0
        ), "elevation_data[row][col] must return the float elevation for that cell"
        assert elevation_data[0][0] == 0.0

    def test_resolve_map_data_elevation_none_when_absent(self):
        """_resolve_map_data returns None (or empty) elevation_data when zone_data has no elevation key."""
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines
        from matches.simulation import ResourceBasedSimulator

        zone_data_grid = [[1, 1], [1, 1]]
        arena_map = ArenaMap.objects.create(
            name="ElevNoneResolve", img_width=200, img_height=200
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=100,
            zone_data={"zones": zone_data_grid, "blocked_edges": {}},
            confirmed=True,
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
            sight_data=compute_sight_lines(zone_data_grid),
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=100, visible_cells=[]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=100, visible_cells=[]
        )

        result = ResourceBasedSimulator._resolve_map_data(arena_map)
        assert len(result) >= 10, "_resolve_map_data must return at least 10 elements"
        elevation_data = result[9]
        # When absent, elevation_data should be None or an empty dict — either is acceptable
        assert (
            elevation_data is None or elevation_data == {}
        ), "elevation_data should be None or empty dict when zone_data has no 'elevation' key"
