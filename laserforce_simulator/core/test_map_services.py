"""ANG-01c -- ``core/services.py`` (seam contract sections 3 and 4).

Real ORM, real OpenCV where an image is involved (``map_test_fixtures``), no
mocks. Covers the zone-config carry-forward rules, sight-line replace / merge
(including the merge-onto-missing-config regression fix), compute, single
cell, the six-reference ``map_in_use`` delete guard, ``delete_map`` and the
Account-scoped ``aggregate_map_heatmap``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase

from conftest import get_shared_manager
from core import services
from core.map_processing import (
    compute_high_los_ranking,
    compute_sight_lines,
    compute_spawn_cells,
)
from core.map_test_fixtures import (
    BLUE_BASE,
    GRID,
    HEATMAP_KEYS,
    RED_BASE,
    SIGHT,
    ZONE_CONFIG_KEYS,
    ZONE_SIZE,
    TempMediaMixin,
    make_bare_map,
    make_heatmap_round,
    make_image_map,
    make_match,
    make_season,
)
from core.models import (
    VALID_BASE_TYPES,
    ArenaMap,
    BaseSightLineConfig,
    HeavyStrongSpotsConfig,
    MapBaseConfig,
    MapCellRankingConfig,
    MapZoneConfig,
    SightLineConfig,
)

User = get_user_model()

SIGHT_A = {k: SIGHT[k] for k in ("1,1", "1,2")}
SIGHT_B = {k: SIGHT[k] for k in ("2,1", "2,2")}


def _cells(value: list) -> list[list[int]]:
    """JSON round-trips tuples to lists; compare as lists."""
    return [list(rc) for rc in value]


class _ServiceTestCase(TempMediaMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.map = make_bare_map()

    def save_zone(self, arena_map: ArenaMap | None = None, **overrides):
        kwargs = {
            "zone_size": ZONE_SIZE,
            "zones": GRID,
            "wall_meta": {},
            "elevation": None,
            "red_spawn": None,
            "blue_spawn": None,
            "bases": [],
        }
        kwargs.update(overrides)
        return services.save_zone_config(arena_map or self.map, **kwargs)

    def confirmed_data(self, arena_map: ArenaMap | None = None) -> dict:
        config = (arena_map or self.map).latest_confirmed_config()
        self.assertIsNotNone(config)
        return config.zone_data


# ---------------------------------------------------------------------------
# save_zone_config
# ---------------------------------------------------------------------------


class TestSaveZoneConfig(_ServiceTestCase):
    def test_returns_new_confirmed_config(self) -> None:
        config = self.save_zone()
        self.assertIsInstance(config, MapZoneConfig)
        self.assertTrue(config.confirmed)
        self.assertEqual(config.zone_size, ZONE_SIZE)
        self.assertEqual(config.zone_data["zones"], GRID)
        self.assertEqual(config.zone_data["blocked_edges"], {})

    def test_unconfirms_previous_config(self) -> None:
        first = self.save_zone()
        second = self.save_zone()
        first.refresh_from_db()
        self.assertFalse(first.confirmed)
        self.assertTrue(second.confirmed)
        self.assertEqual(
            MapZoneConfig.objects.filter(arena_map=self.map, confirmed=True).count(), 1
        )
        self.assertEqual(self.map.latest_confirmed_config().pk, second.pk)

    def test_minimal_payload_keys(self) -> None:
        config = self.save_zone()
        self.assertEqual(set(config.zone_data), {"zones", "blocked_edges"})

    def test_full_payload_keys(self) -> None:
        config = self.save_zone(
            wall_meta={"0,0": {"height": 1.0}},
            elevation=[[0] * 4] * 4,
            red_spawn=[[1, 1]],
            blue_spawn=[[2, 2]],
        )
        self.assertEqual(
            set(config.zone_data),
            {
                "zones",
                "blocked_edges",
                "wall_meta",
                "elevation",
                "red_spawn",
                "blue_spawn",
                "spawn_user_edited",
            },
        )

    def test_elevation_carried_forward(self) -> None:
        elevation = [[0, 1.5, 0, 0], [0] * 4, [0] * 4, [0] * 4]
        self.save_zone(elevation=elevation)
        self.save_zone(elevation=None)
        self.assertEqual(self.confirmed_data()["elevation"], elevation)

    def test_sent_elevation_replaces(self) -> None:
        self.save_zone(elevation=[[1] * 4] * 4)
        self.save_zone(elevation=[[2] * 4] * 4)
        self.assertEqual(self.confirmed_data()["elevation"], [[2] * 4] * 4)

    def test_wall_meta_not_carried_forward(self) -> None:
        self.save_zone(wall_meta={"0,1": {"facing": "N", "height": 2}})
        self.assertIn("wall_meta", self.confirmed_data())
        self.save_zone(wall_meta={})
        self.assertNotIn("wall_meta", self.confirmed_data())

    def test_spawn_sent_non_empty_sets_lock(self) -> None:
        self.save_zone(red_spawn=[[1, 1]], blue_spawn=[[2, 2]])
        data = self.confirmed_data()
        self.assertEqual(data["red_spawn"], [[1, 1]])
        self.assertEqual(data["blue_spawn"], [[2, 2]])
        self.assertIs(data["spawn_user_edited"], True)

    def test_spawn_absent_carries_cells_and_lock(self) -> None:
        self.save_zone(red_spawn=[[1, 1]], blue_spawn=[[2, 2]])
        self.save_zone(red_spawn=None, blue_spawn=None)
        data = self.confirmed_data()
        self.assertEqual(data["red_spawn"], [[1, 1]])
        self.assertEqual(data["blue_spawn"], [[2, 2]])
        self.assertIs(data["spawn_user_edited"], True)

    def test_spawn_sent_empty_clears_cells_and_lock(self) -> None:
        self.save_zone(red_spawn=[[1, 1]], blue_spawn=[[2, 2]])
        self.save_zone(red_spawn=[], blue_spawn=[])
        data = self.confirmed_data()
        self.assertNotIn("red_spawn", data)
        self.assertNotIn("blue_spawn", data)
        self.assertNotIn("spawn_user_edited", data)

    def test_one_spawn_side_sent_other_carried(self) -> None:
        self.save_zone(red_spawn=[[1, 1]], blue_spawn=[[2, 2]])
        self.save_zone(red_spawn=[[1, 2]], blue_spawn=None)
        data = self.confirmed_data()
        self.assertEqual(data["red_spawn"], [[1, 2]])
        self.assertEqual(data["blue_spawn"], [[2, 2]])
        self.assertIs(data["spawn_user_edited"], True)

    def test_bases_replaced(self) -> None:
        MapBaseConfig.objects.create(
            arena_map=self.map, base_type="neutral_1", x_px=1, y_px=1
        )
        self.save_zone(bases=[RED_BASE, BLUE_BASE])
        rows = {
            b.base_type: (b.x_px, b.y_px)
            for b in MapBaseConfig.objects.filter(arena_map=self.map)
        }
        self.assertEqual(rows, {"red": (75, 75), "blue": (125, 125)})

    def test_absent_bases_deletes_all(self) -> None:
        self.save_zone(bases=[RED_BASE, BLUE_BASE])
        self.save_zone(bases=[])
        self.assertFalse(MapBaseConfig.objects.filter(arena_map=self.map).exists())

    def test_processed_cache_deleted(self) -> None:
        cache = self.write_processed_cache(self.map)
        self.save_zone()
        self.assertFalse(cache.exists())

    def test_zones_none_auto_detects_from_image(self) -> None:
        image_map = make_image_map()
        config = self.save_zone(image_map, zones=None)
        self.assertEqual(config.zone_data["zones"], GRID)
        self.assertIsInstance(config.zone_data["blocked_edges"], dict)


# ---------------------------------------------------------------------------
# get_zone_config
# ---------------------------------------------------------------------------


class TestGetZoneConfig(_ServiceTestCase):
    def test_no_config_defaults(self) -> None:
        self.assertEqual(
            services.get_zone_config(self.map),
            {
                "zone_size": None,
                "zones": None,
                "wall_meta": {},
                "elevation": None,
                "red_spawn": [],
                "blue_spawn": [],
                "spawn_user_edited": False,
                "bases": [],
            },
        )

    def test_saved_config_shape(self) -> None:
        wall_meta = {"0,1": {"facing": "E", "height": 1.5}}
        elevation = [[0, 2, 0, 0], [0] * 4, [0] * 4, [0] * 4]
        self.save_zone(
            wall_meta=wall_meta,
            elevation=elevation,
            red_spawn=[[1, 1]],
            blue_spawn=[[2, 2]],
            bases=[RED_BASE, BLUE_BASE],
        )
        data = services.get_zone_config(self.map)
        self.assertEqual(set(data), ZONE_CONFIG_KEYS)
        self.assertEqual(data["zone_size"], ZONE_SIZE)
        self.assertEqual(data["zones"], GRID)
        self.assertEqual(data["wall_meta"], wall_meta)
        self.assertEqual(data["elevation"], elevation)
        self.assertEqual(data["red_spawn"], [[1, 1]])
        self.assertEqual(data["blue_spawn"], [[2, 2]])
        self.assertIs(data["spawn_user_edited"], True)

    def test_bases_sorted_by_type(self) -> None:
        self.save_zone(
            bases=[RED_BASE, {"type": "neutral_2", "x_px": 5, "y_px": 6}, BLUE_BASE]
        )
        self.assertEqual(
            services.get_zone_config(self.map)["bases"],
            [
                BLUE_BASE,
                {"type": "neutral_2", "x_px": 5, "y_px": 6},
                RED_BASE,
            ],
        )

    def test_bases_independent_of_config(self) -> None:
        MapBaseConfig.objects.create(
            arena_map=self.map, base_type="red", x_px=1, y_px=2
        )
        data = services.get_zone_config(self.map)
        self.assertIsNone(data["zone_size"])
        self.assertEqual(data["bases"], [{"type": "red", "x_px": 1, "y_px": 2}])

    def test_legacy_list_zone_data(self) -> None:
        MapZoneConfig.objects.create(
            arena_map=self.map, zone_size=40, zone_data=GRID, confirmed=True
        )
        data = services.get_zone_config(self.map)
        self.assertEqual(data["zone_size"], 40)
        self.assertEqual(data["zones"], GRID)
        self.assertEqual(data["wall_meta"], {})
        self.assertIsNone(data["elevation"])
        self.assertEqual(data["red_spawn"], [])
        self.assertEqual(data["blue_spawn"], [])
        self.assertIs(data["spawn_user_edited"], False)

    def test_unconfirmed_config_ignored(self) -> None:
        MapZoneConfig.objects.create(
            arena_map=self.map,
            zone_size=ZONE_SIZE,
            zone_data={"zones": GRID},
            confirmed=False,
        )
        self.assertIsNone(services.get_zone_config(self.map)["zone_size"])

    def test_shape_round_trips_through_save(self) -> None:
        self.save_zone(
            wall_meta={"0,1": {"facing": "N"}},
            elevation=[[0] * 4] * 4,
            red_spawn=[[1, 1]],
            blue_spawn=[[2, 2]],
            bases=[RED_BASE, BLUE_BASE],
        )
        before = services.get_zone_config(self.map)
        payload = {k: v for k, v in before.items() if k != "spawn_user_edited"}
        services.save_zone_config(self.map, **payload)
        self.assertEqual(services.get_zone_config(self.map), before)


# ---------------------------------------------------------------------------
# save_sight_lines
# ---------------------------------------------------------------------------


class TestSaveSightLines(_ServiceTestCase):
    def save(self, mode: str, sight_data: dict, base_sights: dict | None = None):
        return services.save_sight_lines(
            self.map,
            ZONE_SIZE,
            sight_data=sight_data,
            base_sights=base_sights or {},
            mode=mode,
        )

    def ranking(self) -> list | None:
        row = MapCellRankingConfig.objects.filter(
            arena_map=self.map, zone_size=ZONE_SIZE
        ).first()
        return row.ranked_cells if row else None

    def strong_spots(self) -> list | None:
        row = HeavyStrongSpotsConfig.objects.filter(
            arena_map=self.map, zone_size=ZONE_SIZE
        ).first()
        return row.cells if row else None

    def stored_sight(self) -> dict | None:
        row = SightLineConfig.objects.filter(
            arena_map=self.map, zone_size=ZONE_SIZE
        ).first()
        return row.sight_data if row else None

    def test_returns_none(self) -> None:
        self.assertIsNone(self.save("replace", SIGHT))

    def test_replace_ranks_and_reseeds_strong_spots(self) -> None:
        HeavyStrongSpotsConfig.objects.create(
            arena_map=self.map, zone_size=ZONE_SIZE, cells=[[8, 8]]
        )
        self.save("replace", SIGHT)
        ranked = compute_high_los_ranking(SIGHT)
        self.assertEqual(self.stored_sight(), SIGHT)
        self.assertEqual(self.ranking(), _cells(ranked))
        self.assertEqual(
            self.strong_spots(), _cells(ranked[: max(1, len(ranked) // 4)])
        )

    def test_replace_overwrites_previous_sight_data(self) -> None:
        self.save("replace", SIGHT_A)
        self.save("replace", SIGHT_B)
        self.assertEqual(self.stored_sight(), SIGHT_B)

    def test_empty_replace_leaves_ranking_and_spots_alone(self) -> None:
        MapCellRankingConfig.objects.create(
            arena_map=self.map, zone_size=ZONE_SIZE, ranked_cells=[[9, 9]]
        )
        HeavyStrongSpotsConfig.objects.create(
            arena_map=self.map, zone_size=ZONE_SIZE, cells=[[8, 8]]
        )
        self.save("replace", {})
        self.assertEqual(self.stored_sight(), {})
        self.assertEqual(self.ranking(), [[9, 9]])
        self.assertEqual(self.strong_spots(), [[8, 8]])

    def test_merge_ranks_without_reseeding_strong_spots(self) -> None:
        self.save("replace", SIGHT_A)
        HeavyStrongSpotsConfig.objects.filter(
            arena_map=self.map, zone_size=ZONE_SIZE
        ).update(cells=[[8, 8]])
        self.save("merge", SIGHT_B)
        self.assertEqual(self.stored_sight(), SIGHT)
        self.assertEqual(self.ranking(), _cells(compute_high_los_ranking(SIGHT)))
        self.assertEqual(self.strong_spots(), [[8, 8]])

    def test_merge_overlays_existing_keys(self) -> None:
        self.save("replace", SIGHT)
        self.save("merge", {"1,1": ["1,2"]})
        self.assertEqual(self.stored_sight(), {**SIGHT, "1,1": ["1,2"]})

    def test_merge_onto_missing_config_keeps_the_batch(self) -> None:
        """Regression (contract section 4 FIX): today the batch is discarded."""
        self.assertIsNone(self.stored_sight())
        self.save("merge", SIGHT)
        self.assertEqual(self.stored_sight(), SIGHT)
        self.assertEqual(self.ranking(), _cells(compute_high_los_ranking(SIGHT)))
        self.assertIsNone(self.strong_spots())

    def test_empty_merge_onto_missing_config_creates_empty(self) -> None:
        self.save("merge", {})
        self.assertEqual(self.stored_sight(), {})
        self.assertIsNone(self.ranking())

    def test_base_sights_upserted_per_type(self) -> None:
        BaseSightLineConfig.objects.create(
            arena_map=self.map,
            base_type="blue",
            zone_size=ZONE_SIZE,
            visible_cells=[[1, 1]],
        )
        BaseSightLineConfig.objects.create(
            arena_map=self.map,
            base_type="red",
            zone_size=ZONE_SIZE,
            visible_cells=[[2, 2]],
        )
        self.save("replace", {}, base_sights={"red": [[1, 2]]})
        rows = {
            b.base_type: b.visible_cells
            for b in BaseSightLineConfig.objects.filter(arena_map=self.map)
        }
        self.assertEqual(rows, {"blue": [[1, 1]], "red": [[1, 2]]})

    def test_base_sights_in_merge_mode(self) -> None:
        self.save("merge", {}, base_sights={"neutral_3": [[2, 1]]})
        row = BaseSightLineConfig.objects.get(arena_map=self.map, base_type="neutral_3")
        self.assertEqual(row.visible_cells, [[2, 1]])

    def test_spawns_auto_computed(self) -> None:
        self.save_zone(bases=[RED_BASE, BLUE_BASE])
        self.save("replace", SIGHT)
        expected = compute_spawn_cells(GRID, {"red": (1, 1), "blue": (2, 2)})
        data = self.confirmed_data()
        self.assertTrue(expected["red"])
        self.assertEqual(data["red_spawn"], _cells(expected["red"]))
        self.assertEqual(data["blue_spawn"], _cells(expected["blue"]))

    def test_spawns_not_recomputed_when_locked(self) -> None:
        self.save_zone(
            bases=[RED_BASE, BLUE_BASE], red_spawn=[[2, 2]], blue_spawn=[[1, 1]]
        )
        self.save("replace", SIGHT)
        data = self.confirmed_data()
        self.assertEqual(data["red_spawn"], [[2, 2]])
        self.assertEqual(data["blue_spawn"], [[1, 1]])


# ---------------------------------------------------------------------------
# get_* readers
# ---------------------------------------------------------------------------


class TestReaders(_ServiceTestCase):
    def test_get_sight_lines_empty(self) -> None:
        data = services.get_sight_lines(self.map, ZONE_SIZE)
        self.assertEqual(list(data), ["zone_size", "sight_data", "base_sights"])
        self.assertEqual(data["zone_size"], ZONE_SIZE)
        self.assertIsNone(data["sight_data"])
        self.assertEqual(data["base_sights"], {btype: [] for btype in VALID_BASE_TYPES})

    def test_get_sight_lines_populated(self) -> None:
        SightLineConfig.objects.create(
            arena_map=self.map, zone_size=ZONE_SIZE, sight_data=SIGHT
        )
        BaseSightLineConfig.objects.create(
            arena_map=self.map,
            base_type="red",
            zone_size=ZONE_SIZE,
            visible_cells=[[1, 1]],
        )
        data = services.get_sight_lines(self.map, ZONE_SIZE)
        self.assertEqual(data["sight_data"], SIGHT)
        self.assertEqual(data["base_sights"]["red"], [[1, 1]])

    def test_get_sight_lines_other_zone_size_is_empty(self) -> None:
        SightLineConfig.objects.create(
            arena_map=self.map, zone_size=40, sight_data=SIGHT
        )
        self.assertIsNone(services.get_sight_lines(self.map, ZONE_SIZE)["sight_data"])

    def test_ranked_cells(self) -> None:
        self.assertEqual(
            services.get_ranked_cells(self.map, ZONE_SIZE), {"ranked_cells": []}
        )
        MapCellRankingConfig.objects.create(
            arena_map=self.map, zone_size=ZONE_SIZE, ranked_cells=[[1, 1]]
        )
        self.assertEqual(
            services.get_ranked_cells(self.map, ZONE_SIZE), {"ranked_cells": [[1, 1]]}
        )

    def test_strong_spots_round_trip(self) -> None:
        self.assertEqual(services.get_strong_spots(self.map, ZONE_SIZE), {"cells": []})
        self.assertIsNone(services.save_strong_spots(self.map, ZONE_SIZE, [[1, 2]]))
        self.assertEqual(
            services.get_strong_spots(self.map, ZONE_SIZE), {"cells": [[1, 2]]}
        )
        services.save_strong_spots(self.map, ZONE_SIZE, [])
        self.assertEqual(services.get_strong_spots(self.map, ZONE_SIZE), {"cells": []})
        self.assertEqual(
            HeavyStrongSpotsConfig.objects.filter(arena_map=self.map).count(), 1
        )

    def test_spawn_cells(self) -> None:
        self.assertEqual(
            services.get_spawn_cells(self.map), {"red_spawn": [], "blue_spawn": []}
        )
        self.save_zone(red_spawn=[[1, 1]], blue_spawn=[[2, 2]])
        self.assertEqual(
            services.get_spawn_cells(self.map),
            {"red_spawn": [[1, 1]], "blue_spawn": [[2, 2]]},
        )


# ---------------------------------------------------------------------------
# compute_and_store_sight_lines / compute_single_cell
# ---------------------------------------------------------------------------


class TestComputeAndStore(_ServiceTestCase):
    def test_uses_stored_grid_and_persists(self) -> None:
        """A bare map (no image file) proves the stored grid was used."""
        zone_data = {"zones": GRID, "blocked_edges": {}}
        MapZoneConfig.objects.create(
            arena_map=self.map, zone_size=ZONE_SIZE, zone_data=zone_data, confirmed=True
        )
        result = services.compute_and_store_sight_lines(self.map, ZONE_SIZE)
        expected = compute_sight_lines(zone_data, use_quadtree=True)
        self.assertEqual(list(result), ["sight_data", "zone_size"])
        self.assertEqual(result, {"sight_data": expected, "zone_size": ZONE_SIZE})
        self.assertEqual(
            SightLineConfig.objects.get(
                arena_map=self.map, zone_size=ZONE_SIZE
            ).sight_data,
            expected,
        )
        ranked = _cells(compute_high_los_ranking(expected))
        self.assertTrue(ranked)
        self.assertEqual(
            MapCellRankingConfig.objects.get(arena_map=self.map).ranked_cells, ranked
        )
        self.assertEqual(
            HeavyStrongSpotsConfig.objects.get(arena_map=self.map).cells,
            ranked[: max(1, len(ranked) // 4)],
        )

    def test_unconfirmed_config_of_that_size_is_used(self) -> None:
        MapZoneConfig.objects.create(
            arena_map=self.map,
            zone_size=ZONE_SIZE,
            zone_data={"zones": GRID},
            confirmed=False,
        )
        result = services.compute_and_store_sight_lines(self.map, ZONE_SIZE)
        self.assertEqual(result["sight_data"], SIGHT)

    def test_legacy_list_zone_data_is_used(self) -> None:
        MapZoneConfig.objects.create(
            arena_map=self.map, zone_size=ZONE_SIZE, zone_data=GRID, confirmed=True
        )
        result = services.compute_and_store_sight_lines(self.map, ZONE_SIZE)
        self.assertEqual(result["sight_data"], SIGHT)

    def test_falls_back_to_image_detection(self) -> None:
        image_map = make_image_map()
        result = services.compute_and_store_sight_lines(image_map, ZONE_SIZE)
        self.assertEqual(result, {"sight_data": SIGHT, "zone_size": ZONE_SIZE})

    def test_spawn_override_locks(self) -> None:
        self.save_zone(bases=[RED_BASE, BLUE_BASE])
        services.compute_and_store_sight_lines(
            self.map, ZONE_SIZE, red_spawn=[[1, 2]], blue_spawn=None
        )
        data = self.confirmed_data()
        self.assertEqual(data["red_spawn"], [[1, 2]])
        self.assertIs(data["spawn_user_edited"], True)

    def test_spawn_override_empty_unlocks_and_autocomputes(self) -> None:
        self.save_zone(bases=[RED_BASE, BLUE_BASE], red_spawn=[[1, 2]])
        services.compute_and_store_sight_lines(
            self.map, ZONE_SIZE, red_spawn=[], blue_spawn=[]
        )
        data = self.confirmed_data()
        self.assertFalse(data.get("spawn_user_edited"))
        expected = compute_spawn_cells(GRID, {"red": (1, 1), "blue": (2, 2)})
        self.assertEqual(data["red_spawn"], _cells(expected["red"]))

    def test_no_spawn_args_autocomputes(self) -> None:
        self.save_zone(bases=[RED_BASE, BLUE_BASE])
        services.compute_and_store_sight_lines(self.map, ZONE_SIZE)
        expected = compute_spawn_cells(GRID, {"red": (1, 1), "blue": (2, 2)})
        self.assertEqual(self.confirmed_data()["blue_spawn"], _cells(expected["blue"]))


class TestComputeSingleCell(_ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        MapZoneConfig.objects.create(
            arena_map=self.map,
            zone_size=ZONE_SIZE,
            zone_data={"zones": GRID},
            confirmed=True,
        )

    def test_on_grid(self) -> None:
        result = services.compute_single_cell(self.map, ZONE_SIZE, 1, 1)
        self.assertEqual(list(result), ["visible_cells"])
        self.assertEqual(sorted(result["visible_cells"]), ["1,2", "2,1", "2,2"])

    def test_off_grid_is_empty(self) -> None:
        self.assertEqual(
            services.compute_single_cell(self.map, ZONE_SIZE, 99, 99),
            {"visible_cells": []},
        )

    def test_wall_cell_is_empty(self) -> None:
        self.assertEqual(
            services.compute_single_cell(self.map, ZONE_SIZE, 0, 0),
            {"visible_cells": []},
        )


# ---------------------------------------------------------------------------
# detect_zones_with_overlay / processed_image_path
# ---------------------------------------------------------------------------


class TestDetectAndProcessedImage(_ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.image_map = make_image_map()

    def test_detect_without_config(self) -> None:
        data = services.detect_zones_with_overlay(self.image_map, ZONE_SIZE)
        self.assertEqual(data["rows"], 4)
        self.assertEqual(data["cols"], 4)
        self.assertEqual(data["zones"], GRID)
        self.assertNotIn("wall_meta", data)
        self.assertNotIn("elevation", data)

    def test_confirmed_config_overlays(self) -> None:
        painted = [row[:] for row in GRID]
        painted[1][1] = 4
        self.save_zone(
            self.image_map,
            zones=painted,
            wall_meta={"1,1": {"height": 1.0}},
            elevation=[[1] * 4] * 4,
        )
        data = services.detect_zones_with_overlay(self.image_map, ZONE_SIZE)
        self.assertEqual(data["zones"], painted)
        self.assertEqual(data["wall_meta"], {"1,1": {"height": 1.0}})
        self.assertEqual(data["elevation"], [[1] * 4] * 4)

    def test_other_zone_size_config_not_overlaid(self) -> None:
        painted = [row[:] for row in GRID]
        painted[1][1] = 4
        self.save_zone(self.image_map, zone_size=40, zones=painted)
        data = services.detect_zones_with_overlay(self.image_map, ZONE_SIZE)
        self.assertEqual(data["zones"], GRID)

    def test_processed_image_path_builds_png(self) -> None:
        path = services.processed_image_path(self.image_map)
        self.assertEqual(path, self.processed_cache_path(self.image_map))
        self.assertTrue(path.exists())
        self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))


# ---------------------------------------------------------------------------
# map_in_use -- the six references (contract section 3)
# ---------------------------------------------------------------------------


class TestMapInUse(_ServiceTestCase):
    REFERENCES = (
        "season_pool",
        "season_pool_snapshot",
        "season_rotation",
        "season_rotation_snapshot",
        "conference_rotation",
        "conference_rotation_snapshot",
    )

    def reference(self, state: str, ref: str, ids: list[int] | None = None):
        """Create a Season in ``state`` that references ``self.map`` via ``ref``."""
        from matches.models import Conference

        ids = ids if ids is not None else [999_998, self.map.pk]
        season_fields = {
            "season_pool_snapshot": {"starting_map_pool_ids_json": ids},
            "season_rotation": {"map_rotation_ids_json": ids},
            "season_rotation_snapshot": {"starting_map_rotation_ids_json": ids},
        }.get(ref, {})
        season = make_season(state, **season_fields)
        if ref == "season_pool":
            season.map_pool.add(self.map)
        elif ref == "conference_rotation":
            Conference.objects.create(
                season=season, name="East", ordinal=1, map_rotation_ids_json=ids
            )
        elif ref == "conference_rotation_snapshot":
            Conference.objects.create(
                season=season,
                name="East",
                ordinal=1,
                starting_map_rotation_ids_json=ids,
            )
        return season

    def test_unreferenced_map_not_in_use(self) -> None:
        self.assertFalse(services.map_in_use(self.map))

    def test_each_reference_blocks_for_draft_and_active(self) -> None:
        for state in ("draft", "active"):
            for ref in self.REFERENCES:
                with self.subTest(state=state, ref=ref):
                    season = self.reference(state, ref)
                    self.assertTrue(services.map_in_use(self.map))
                    season.league.delete()
                    self.assertFalse(services.map_in_use(self.map))

    def test_completed_season_never_blocks(self) -> None:
        for ref in self.REFERENCES:
            with self.subTest(ref=ref):
                season = self.reference("completed", ref)
                self.assertFalse(services.map_in_use(self.map))
                season.league.delete()

    def test_completed_season_with_every_reference(self) -> None:
        from matches.models import Conference

        ids = [self.map.pk]
        season = make_season(
            "completed",
            starting_map_pool_ids_json=ids,
            map_rotation_ids_json=ids,
            starting_map_rotation_ids_json=ids,
        )
        season.map_pool.add(self.map)
        Conference.objects.create(
            season=season,
            name="East",
            ordinal=1,
            map_rotation_ids_json=ids,
            starting_map_rotation_ids_json=ids,
        )
        self.assertFalse(services.map_in_use(self.map))

    def test_other_map_ids_do_not_block(self) -> None:
        other = make_bare_map("Other Arena")
        for ref in self.REFERENCES:
            with self.subTest(ref=ref):
                season = self.reference("active", ref, ids=[other.pk])
                if ref == "season_pool":
                    season.map_pool.remove(self.map)
                    season.map_pool.add(other)
                self.assertFalse(services.map_in_use(self.map))
                self.assertTrue(services.map_in_use(other))
                season.league.delete()

    def test_null_and_non_list_json_ignored(self) -> None:
        make_season(
            "active",
            starting_map_pool_ids_json=None,
            map_rotation_ids_json={"ids": [self.map.pk]},
            starting_map_rotation_ids_json=str(self.map.pk),
        )
        self.assertFalse(services.map_in_use(self.map))

    def test_game_round_reference_alone_does_not_block(self) -> None:
        make_heatmap_round(self.map, {"1": {"1,1": 1}}, prefix="InUseRound")
        self.assertFalse(services.map_in_use(self.map))

    def test_map_mode_alone_does_not_block(self) -> None:
        make_season("active", map_mode="single")
        self.assertFalse(services.map_in_use(self.map))


# ---------------------------------------------------------------------------
# delete_map
# ---------------------------------------------------------------------------


class TestDeleteMap(_ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.image_map = make_image_map("Doomed Arena")
        self.image_file = self.media_root / self.image_map.image.name
        MapZoneConfig.objects.create(
            arena_map=self.image_map,
            zone_size=ZONE_SIZE,
            zone_data={"zones": GRID},
            confirmed=True,
        )
        SightLineConfig.objects.create(
            arena_map=self.image_map, zone_size=ZONE_SIZE, sight_data=SIGHT
        )
        self.cache = self.write_processed_cache(self.image_map)

    def test_in_use_raises_and_touches_nothing(self) -> None:
        season = make_season("active")
        season.map_pool.add(self.image_map)
        with self.assertRaises(services.MapInUseError):
            services.delete_map(self.image_map)
        self.assertTrue(ArenaMap.objects.filter(pk=self.image_map.pk).exists())
        self.assertTrue(MapZoneConfig.objects.filter(arena_map=self.image_map).exists())
        self.assertTrue(self.image_file.exists())
        self.assertTrue(self.cache.exists())

    def test_deletes_row_configs_image_and_cache(self) -> None:
        pk = self.image_map.pk
        round_ = make_heatmap_round(
            self.image_map, {"1": {"1,1": 1}}, prefix="DelRound"
        )
        self.assertTrue(self.image_file.exists())
        # Files are removed in a transaction.on_commit callback.
        with self.captureOnCommitCallbacks(execute=True):
            self.assertIsNone(services.delete_map(self.image_map))
        self.assertFalse(ArenaMap.objects.filter(pk=pk).exists())
        self.assertFalse(MapZoneConfig.objects.filter(arena_map_id=pk).exists())
        self.assertFalse(SightLineConfig.objects.filter(arena_map_id=pk).exists())
        self.assertFalse(self.image_file.exists())
        self.assertFalse(self.cache.exists())
        round_.refresh_from_db()
        self.assertIsNone(round_.arena_map_id)

    def test_files_survive_until_the_delete_commits(self) -> None:
        """Regression: file removal waits for commit, so a rollback keeps them."""
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            services.delete_map(self.image_map)
        self.assertTrue(self.image_file.exists())
        self.assertEqual(len(callbacks), 1)

    def test_completed_season_does_not_block_delete(self) -> None:
        season = make_season("completed")
        season.map_pool.add(self.image_map)
        services.delete_map(self.image_map)
        self.assertFalse(ArenaMap.objects.filter(name="Doomed Arena").exists())


# ---------------------------------------------------------------------------
# upload_map
# ---------------------------------------------------------------------------


class TestUploadMap(_ServiceTestCase):
    def test_upload_stores_dimensions_and_file(self) -> None:
        from core.map_test_fixtures import png_upload

        arena_map = services.upload_map("Fresh Arena", png_upload())
        arena_map.refresh_from_db()
        self.assertEqual(arena_map.name, "Fresh Arena")
        self.assertEqual((arena_map.img_width, arena_map.img_height), (200, 200))
        self.assertTrue((self.media_root / arena_map.image.name).exists())

    def test_corrupt_upload_raises_and_leaves_no_row(self) -> None:
        from core.map_test_fixtures import corrupt_upload

        before = ArenaMap.objects.count()
        with self.assertRaises(services.InvalidMapImage):
            services.upload_map("Broken Arena", corrupt_upload())
        self.assertEqual(ArenaMap.objects.count(), before)
        self.assertFalse(ArenaMap.objects.filter(name="Broken Arena").exists())


# ---------------------------------------------------------------------------
# aggregate_map_heatmap -- Account scoping
# ---------------------------------------------------------------------------


class TestAggregateMapHeatmap(_ServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.user_b = User.objects.create_user(
            email="ang01c-heat-b@example.com", password="Str0ng-Passphrase-9"
        )
        MapZoneConfig.objects.create(
            arena_map=self.map,
            zone_size=ZONE_SIZE,
            zone_data={"zones": GRID},
            confirmed=True,
        )
        # Visible to A: own standalone + Unmanaged standalone.
        make_heatmap_round(
            self.map, {"1": {"1,1": 10}}, manager=self.user_a, prefix="Own"
        )
        make_heatmap_round(self.map, {"2": {"1,1": 5, "1,2": 3}}, prefix="Unmanaged")
        # Foreign to A: B's standalone, B's Match, B's League Season.
        make_heatmap_round(
            self.map, {"3": {"2,2": 100}}, manager=self.user_b, prefix="FgnSolo"
        )
        make_heatmap_round(
            self.map,
            {"4": {"2,2": 100}},
            match=make_match("FgnMatch", manager=self.user_b),
        )
        season = make_season("active", manager=self.user_b)
        make_heatmap_round(
            self.map,
            {"5": {"2,2": 100}},
            match=make_match("FgnLeague", season=season),
        )

    def test_own_and_unmanaged_counted_foreign_excluded(self) -> None:
        data = services.aggregate_map_heatmap(self.map, ZONE_SIZE, None, self.user_a)
        self.assertEqual(list(data), HEATMAP_KEYS)
        self.assertEqual(
            data,
            {
                "cell_occupancy": {"1,1": 15, "1,2": 3},
                "zone_size": ZONE_SIZE,
                "rows": 4,
                "cols": 4,
                "round_count": 2,
            },
        )

    def test_other_account_sees_its_own_plus_unmanaged(self) -> None:
        data = services.aggregate_map_heatmap(self.map, ZONE_SIZE, None, self.user_b)
        self.assertEqual(data["round_count"], 4)
        self.assertEqual(data["cell_occupancy"], {"1,1": 5, "1,2": 3, "2,2": 300})

    def test_own_match_and_own_league_rounds_counted(self) -> None:
        make_heatmap_round(
            self.map,
            {"6": {"2,1": 7}},
            match=make_match("OwnMatch", manager=self.user_a),
        )
        own_season = make_season("draft", manager=self.user_a)
        make_heatmap_round(
            self.map,
            {"7": {"2,1": 1}},
            match=make_match("OwnLeague", season=own_season),
        )
        data = services.aggregate_map_heatmap(self.map, ZONE_SIZE, None, self.user_a)
        self.assertEqual(data["round_count"], 4)
        self.assertEqual(data["cell_occupancy"]["2,1"], 8)
        self.assertNotIn("2,2", data["cell_occupancy"])

    def test_team_color_filter_keeps_scoped_round_count(self) -> None:
        data = services.aggregate_map_heatmap(self.map, ZONE_SIZE, "red", self.user_a)
        # No PlayerRoundState rows => no player matches "red"; rounds still counted.
        self.assertEqual(data["cell_occupancy"], {})
        self.assertEqual(data["round_count"], 2)

    def test_other_zone_size_and_other_map_excluded(self) -> None:
        make_heatmap_round(
            self.map,
            {"8": {"0,0": 9}},
            manager=self.user_a,
            zone_size=40,
            prefix="Zs40",
        )
        other = make_bare_map("Elsewhere")
        make_heatmap_round(other, {"9": {"0,0": 9}}, manager=self.user_a, prefix="Else")
        data = services.aggregate_map_heatmap(self.map, ZONE_SIZE, None, self.user_a)
        self.assertEqual(data["round_count"], 2)
        self.assertNotIn("0,0", data["cell_occupancy"])

    def test_no_config_rows_cols_zero(self) -> None:
        data = services.aggregate_map_heatmap(self.map, 40, None, self.user_a)
        self.assertEqual((data["rows"], data["cols"], data["round_count"]), (0, 0, 0))


class TestEnsureDefaultMaps(TempMediaMixin, TestCase):
    """ANG-06h -- the HTML map list's default-map seeding, as a service."""

    DEFAULT_NAMES = {"Syracuse Laser Tag", "San Marcos Laser Tag"}

    def test_empty_db_seeds_the_defaults_with_their_images(self) -> None:
        services.ensure_default_maps()
        maps = ArenaMap.objects.all()
        self.assertEqual({m.name for m in maps}, self.DEFAULT_NAMES)
        for arena_map in maps:
            with self.subTest(name=arena_map.name):
                self.assertTrue(arena_map.is_default)
                self.assertGreater(arena_map.img_width, 0)
                self.assertTrue((self.media_root / arena_map.image.name).exists())

    def test_second_call_adds_no_rows(self) -> None:
        services.ensure_default_maps()
        services.ensure_default_maps()
        self.assertEqual(ArenaMap.objects.count(), 2)

    def test_existing_map_blocks_seeding(self) -> None:
        make_bare_map()
        services.ensure_default_maps()
        self.assertEqual(
            list(ArenaMap.objects.values_list("name", flat=True)), ["Bare Arena"]
        )

    def test_restores_a_missing_default_image_without_adding_rows(self) -> None:
        services.ensure_default_maps()
        arena_map = ArenaMap.objects.order_by("pk").first()
        (self.media_root / arena_map.image.name).unlink()
        services.ensure_default_maps()
        self.assertTrue((self.media_root / arena_map.image.name).exists())
        self.assertEqual(ArenaMap.objects.count(), 2)

    def test_skips_for_non_filesystem_storage(self) -> None:
        from unittest.mock import MagicMock, patch

        with patch("django.core.files.storage.default_storage", new=MagicMock()):
            services.ensure_default_maps()
        self.assertEqual(ArenaMap.objects.count(), 0)
