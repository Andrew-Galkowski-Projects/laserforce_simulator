"""ANG-01c -- the writable ``/api/maps/`` (seam contract section 6).

Upload (multipart, 201), delete (204 / 409 ``map_in_use``), the ten detail
actions (zones, zone-config GET/PUT, sight-lines GET/PUT/PATCH, compute,
single cell, ranked cells, strong spots GET/PUT, spawn cells, scoped heatmap,
raw processed image) and the error table. Maps are shared reference data: any
authenticated Account may write any map.

Real OpenCV on a tiny in-memory PNG (``core/map_test_fixtures.py``) under a
temp ``MEDIA_ROOT``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from conftest import get_shared_manager
from core.map_processing import compute_high_los_ranking
from core.map_test_fixtures import (
    BLUE_BASE,
    DETECT_KEYS,
    GRID,
    HEATMAP_KEYS,
    RED_BASE,
    SIGHT,
    ZONE_CONFIG_KEYS,
    ZONE_SIZE,
    TempMediaMixin,
    corrupt_upload,
    make_heatmap_round,
    make_image_map,
    make_season,
    png_upload,
)
from core.models import (
    VALID_BASE_TYPES,
    ArenaMap,
    MapBaseConfig,
    MapZoneConfig,
    SightLineConfig,
)

User = get_user_model()

_MAP_FIELDS = {
    "id",
    "name",
    "image",
    "is_default",
    "img_width",
    "img_height",
    "created_at",
    "has_confirmed_config",
}

# url_name -> url_path for the ten detail actions (contract section 6 table).
_ACTIONS = {
    "processed-image": "processed-image",
    "zones": "zones",
    "zone-config": "zone-config",
    "sight-lines": "sight-lines",
    "sight-lines-compute": "sight-lines/compute",
    "sight-lines-cell": "sight-lines/cell",
    "ranked-cells": "ranked-cells",
    "strong-spots": "strong-spots",
    "spawn-cells": "spawn-cells",
    "heatmap": "heatmap",
}

ZONES_MSG = "zones must be a non-empty list of equal-length rows of cell values 0-5."
CELLS_MSG = "cells must be a list of [int, int] pairs"
SIGHT_DATA_MSG = 'sight_data must map "r,c" keys to lists of "r,c" strings.'
BASE_SIGHTS_MSG = "base_sights must map base types to lists of [int, int] pairs."
BASES_REPEAT_MSG = "bases must not repeat a base type."
MAP_IN_USE_MSG = "This map is used by a Season that is not completed."

SIGHT_A = {k: SIGHT[k] for k in ("1,1", "1,2")}
SIGHT_B = {k: SIGHT[k] for k in ("2,1", "2,2")}


class _MapsWriteTestCase(TempMediaMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.api = APIClient()
        self.api.force_login(self.user_a)
        self.map = make_image_map()

    # --- helpers ---------------------------------------------------------

    def url(self, url_name: str | None = None, pk: int | None = None) -> str:
        pk = self.map.pk if pk is None else pk
        if url_name is None:
            return reverse("arenamap-detail", args=[pk])
        return reverse(f"arenamap-{url_name}", args=[pk])

    def ok(self, response, status: int = 200) -> dict:
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"data", "meta"})
        return body["data"]

    def error(self, response, status: int, code: str, field: str | None = None):
        self.assertEqual(response.status_code, status, response.content)
        body = response.json()
        self.assertEqual(set(body), {"errors", "meta"})
        matching = [
            e for e in body["errors"] if e["code"] == code and e["field"] == field
        ]
        self.assertTrue(matching, body["errors"])
        return matching[0]

    def get(self, url_name: str, params: dict | None = None, api=None):
        return (api or self.api).get(self.url(url_name), params or {})

    def put_json(self, url_name: str, body, api=None):
        return (api or self.api).put(self.url(url_name), body, format="json")

    def patch_json(self, url_name: str, body):
        return self.api.patch(self.url(url_name), body, format="json")

    def post_json(self, url_name: str, body):
        return self.api.post(self.url(url_name), body, format="json")

    def upload(self, data: dict, api=None):
        return (api or self.api).post(
            reverse("arenamap-list"), data, format="multipart"
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestRoutes(_MapsWriteTestCase):
    def test_action_route_names(self) -> None:
        for url_name, url_path in _ACTIONS.items():
            with self.subTest(url_name=url_name):
                self.assertEqual(
                    reverse(f"arenamap-{url_name}", args=[7]),
                    f"/api/maps/7/{url_path}/",
                )

    def test_unknown_map_is_404_on_every_action(self) -> None:
        for url_name in _ACTIONS:
            with self.subTest(url_name=url_name):
                if url_name == "sight-lines-compute":
                    response = self.api.post(
                        self.url(url_name, pk=999_999),
                        {"zone_size": 50},
                        format="json",
                    )
                else:
                    response = self.api.get(
                        self.url(url_name, pk=999_999),
                        {"zone_size": 50, "r": 1, "c": 1},
                        HTTP_ACCEPT="*/*",
                    )
                self.error(response, 404, "not_found")

    def test_unknown_map_delete_is_404(self) -> None:
        self.error(self.api.delete(self.url(pk=999_999)), 404, "not_found")


# ---------------------------------------------------------------------------
# POST /api/maps/ -- upload
# ---------------------------------------------------------------------------


class TestUpload(_MapsWriteTestCase):
    def test_upload_201(self) -> None:
        before = ArenaMap.objects.count()
        data = self.ok(
            self.upload({"name": "Uploaded Arena", "image": png_upload()}), 201
        )
        self.assertEqual(set(data), _MAP_FIELDS)
        self.assertEqual(data["name"], "Uploaded Arena")
        self.assertEqual((data["img_width"], data["img_height"]), (200, 200))
        self.assertFalse(data["has_confirmed_config"])
        self.assertFalse(data["is_default"])
        self.assertTrue(data["image"].startswith("http://testserver"), data["image"])
        self.assertEqual(ArenaMap.objects.count(), before + 1)
        row = ArenaMap.objects.get(pk=data["id"])
        self.assertTrue((self.media_root / row.image.name).exists())

    def test_upload_then_detail_matches(self) -> None:
        data = self.ok(self.upload({"name": "Twin", "image": png_upload()}), 201)
        detail = self.ok(self.api.get(self.url(pk=data["id"])))
        self.assertEqual(detail, data)

    def test_missing_name(self) -> None:
        self.error(self.upload({"image": png_upload()}), 400, "required", "name")

    def test_missing_image(self) -> None:
        self.error(self.upload({"name": "No Image"}), 400, "required", "image")
        self.assertFalse(ArenaMap.objects.filter(name="No Image").exists())

    def test_name_too_long(self) -> None:
        self.error(
            self.upload({"name": "x" * 101, "image": png_upload()}),
            400,
            "max_length",
            "name",
        )
        self.assertFalse(ArenaMap.objects.filter(name="x" * 101).exists())

    def test_corrupt_image(self) -> None:
        before = ArenaMap.objects.count()
        self.error(
            self.upload({"name": "Corrupt", "image": corrupt_upload()}),
            400,
            "invalid_image",
            "image",
        )
        self.assertEqual(ArenaMap.objects.count(), before)

    def test_json_body_is_415(self) -> None:
        response = self.api.post(
            reverse("arenamap-list"), {"name": "Json"}, format="json"
        )
        self.error(response, 415, "unsupported_media_type")
        self.assertFalse(ArenaMap.objects.filter(name="Json").exists())


# ---------------------------------------------------------------------------
# DELETE /api/maps/<pk>/
# ---------------------------------------------------------------------------


class TestDelete(_MapsWriteTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.image_file = self.media_root / self.map.image.name
        MapZoneConfig.objects.create(
            arena_map=self.map,
            zone_size=ZONE_SIZE,
            zone_data={"zones": GRID},
            confirmed=True,
        )
        SightLineConfig.objects.create(
            arena_map=self.map, zone_size=ZONE_SIZE, sight_data=SIGHT
        )
        self.cache = self.write_processed_cache(self.map)

    def test_delete_204_cascades_and_removes_files(self) -> None:
        pk = self.map.pk
        # File removal is deferred to transaction.on_commit (a rolled-back
        # delete must keep the image), so run the commit callbacks here.
        with self.captureOnCommitCallbacks(execute=True):
            response = self.api.delete(self.url())
        self.assertEqual(response.status_code, 204, response.content)
        self.assertEqual(response.content, b"")
        self.assertFalse(ArenaMap.objects.filter(pk=pk).exists())
        self.assertFalse(MapZoneConfig.objects.filter(arena_map_id=pk).exists())
        self.assertFalse(SightLineConfig.objects.filter(arena_map_id=pk).exists())
        self.assertFalse(self.image_file.exists())
        self.assertFalse(self.cache.exists())

    def test_in_use_is_409_and_map_survives(self) -> None:
        season = make_season("draft")
        season.map_pool.add(self.map)
        error = self.error(self.api.delete(self.url()), 409, "map_in_use")
        self.assertEqual(error["message"], MAP_IN_USE_MSG)
        self.assertTrue(ArenaMap.objects.filter(pk=self.map.pk).exists())
        self.assertTrue(MapZoneConfig.objects.filter(arena_map=self.map).exists())
        self.assertTrue(self.image_file.exists())
        self.assertTrue(self.cache.exists())

    def test_in_use_via_rotation_snapshot_is_409(self) -> None:
        make_season("active", starting_map_rotation_ids_json=[self.map.pk])
        self.error(self.api.delete(self.url()), 409, "map_in_use")

    def test_completed_season_does_not_block(self) -> None:
        season = make_season("completed", map_rotation_ids_json=[self.map.pk])
        season.map_pool.add(self.map)
        self.assertEqual(self.api.delete(self.url()).status_code, 204)

    def test_put_and_patch_detail_are_405(self) -> None:
        for method in ("put", "patch"):
            with self.subTest(method=method):
                response = getattr(self.api, method)(
                    self.url(), {"name": "Renamed"}, format="json"
                )
                self.error(response, 405, "method_not_allowed")
        self.map.refresh_from_db()
        self.assertEqual(self.map.name, "Probe Arena")

    def test_wrong_method_on_actions_is_405(self) -> None:
        self.error(
            self.api.post(self.url("zones"), {}, format="json"),
            405,
            "method_not_allowed",
        )
        self.error(
            self.api.post(self.url("spawn-cells"), {}, format="json"),
            405,
            "method_not_allowed",
        )
        self.error(self.api.delete(self.url("zone-config")), 405, "method_not_allowed")
        self.error(
            self.api.get(self.url("sight-lines-compute")), 405, "method_not_allowed"
        )


# ---------------------------------------------------------------------------
# GET actions -- data key sets
# ---------------------------------------------------------------------------


class TestReadActions(_MapsWriteTestCase):
    def test_zones(self) -> None:
        data = self.ok(self.get("zones", {"zone_size": 50}))
        self.assertEqual(set(data), DETECT_KEYS)
        self.assertEqual((data["rows"], data["cols"]), (4, 4))
        self.assertEqual(data["zones"], GRID)

    def test_zones_overlay_confirmed_config(self) -> None:
        self.ok(
            self.put_json(
                "zone-config",
                {"zone_size": 50, "zones": GRID, "elevation": [[1] * 4] * 4},
            )
        )
        data = self.ok(self.get("zones", {"zone_size": 50}))
        self.assertEqual(set(data), DETECT_KEYS | {"elevation"})
        self.assertEqual(data["elevation"], [[1] * 4] * 4)

    def test_zone_config_empty(self) -> None:
        data = self.ok(self.get("zone-config"))
        self.assertEqual(set(data), ZONE_CONFIG_KEYS)
        self.assertIsNone(data["zone_size"])
        self.assertEqual(data["bases"], [])

    def test_sight_lines_empty(self) -> None:
        data = self.ok(self.get("sight-lines", {"zone_size": 50}))
        self.assertEqual(set(data), {"zone_size", "sight_data", "base_sights"})
        self.assertEqual(data["zone_size"], 50)
        self.assertIsNone(data["sight_data"])
        self.assertEqual(data["base_sights"], {b: [] for b in VALID_BASE_TYPES})

    def test_ranked_cells_empty(self) -> None:
        self.assertEqual(
            self.ok(self.get("ranked-cells", {"zone_size": 50})), {"ranked_cells": []}
        )

    def test_strong_spots_empty(self) -> None:
        self.assertEqual(
            self.ok(self.get("strong-spots", {"zone_size": 50})), {"cells": []}
        )

    def test_spawn_cells_empty(self) -> None:
        self.assertEqual(
            self.ok(self.get("spawn-cells")), {"red_spawn": [], "blue_spawn": []}
        )

    def test_heatmap_empty(self) -> None:
        data = self.ok(self.get("heatmap", {"zone_size": 50}))
        self.assertEqual(list(data), HEATMAP_KEYS)
        self.assertEqual(data["round_count"], 0)

    def test_cell_from_detected_grid(self) -> None:
        data = self.ok(self.get("sight-lines-cell", {"zone_size": 50, "r": 1, "c": 1}))
        self.assertEqual(set(data), {"visible_cells"})
        self.assertEqual(sorted(data["visible_cells"]), ["1,2", "2,1", "2,2"])

    def test_cell_off_grid_is_empty_200(self) -> None:
        data = self.ok(
            self.get("sight-lines-cell", {"zone_size": 50, "r": 99, "c": 99})
        )
        self.assertEqual(data, {"visible_cells": []})

    def test_processed_image_is_raw_png(self) -> None:
        response = self.api.get(self.url("processed-image"), HTTP_ACCEPT="*/*")
        # Read to exhaustion first: the test client then closes the response
        # (and its file). A manual close() would fire request_finished inside
        # the test transaction (CI-01).
        body = b"".join(response.streaming_content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertTrue(self.processed_cache_path(self.map).exists())


# ---------------------------------------------------------------------------
# zone_size / r / c param errors
# ---------------------------------------------------------------------------


class TestParamErrors(_MapsWriteTestCase):
    GET_WITH_ZONE_SIZE = (
        "zones",
        "sight-lines",
        "sight-lines-cell",
        "ranked-cells",
        "strong-spots",
        "heatmap",
    )

    def test_missing_zone_size_on_gets(self) -> None:
        for url_name in self.GET_WITH_ZONE_SIZE:
            with self.subTest(url_name=url_name):
                self.error(
                    self.get(url_name, {"r": 1, "c": 1}), 400, "required", "zone_size"
                )

    def test_missing_zone_size_on_writes(self) -> None:
        cases = (
            ("put", "zone-config", {"zones": GRID}),
            ("put", "sight-lines", {"sight_data": {}}),
            ("patch", "sight-lines", {"sight_data": {}}),
            ("post", "sight-lines-compute", {}),
            ("put", "strong-spots", {"cells": []}),
        )
        for method, url_name, body in cases:
            with self.subTest(method=method, url_name=url_name):
                response = getattr(self.api, method)(
                    self.url(url_name), body, format="json"
                )
                self.error(response, 400, "required", "zone_size")

    def test_zone_size_bounds_and_type(self) -> None:
        for value, code in (("abc", "invalid"), (9, "min_value"), (201, "max_value")):
            for url_name in self.GET_WITH_ZONE_SIZE:
                with self.subTest(value=value, url_name=url_name):
                    self.error(
                        self.get(url_name, {"zone_size": value, "r": 1, "c": 1}),
                        400,
                        code,
                        "zone_size",
                    )
            with self.subTest(value=value, url_name="strong-spots PUT"):
                self.error(
                    self.put_json("strong-spots", {"zone_size": value, "cells": []}),
                    400,
                    code,
                    "zone_size",
                )

    def test_cell_r_and_c_errors(self) -> None:
        for field in ("r", "c"):
            other = "c" if field == "r" else "r"
            for value, code in (
                (None, "required"),
                ("x", "invalid"),
                (-1, "min_value"),
            ):
                with self.subTest(field=field, value=value):
                    params = {"zone_size": 50, other: 1}
                    if value is not None:
                        params[field] = value
                    self.error(self.get("sight-lines-cell", params), 400, code, field)

    def test_heatmap_bad_team_color(self) -> None:
        self.error(
            self.get("heatmap", {"zone_size": 50, "team_color": "green"}),
            400,
            "invalid_choice",
            "team_color",
        )


# ---------------------------------------------------------------------------
# zone-config PUT
# ---------------------------------------------------------------------------


class TestZoneConfigPut(_MapsWriteTestCase):
    FULL = {
        "zone_size": 50,
        "zones": GRID,
        "wall_meta": {"0,1": {"facing": "N", "height": 1.5}},
        "elevation": [[0, 2.5, 0, 0], [0] * 4, [0] * 4, [0] * 4],
        "red_spawn": [[1, 1]],
        "blue_spawn": [[2, 2]],
        "bases": [RED_BASE, BLUE_BASE],
    }

    def test_put_returns_saved_state_equal_to_get(self) -> None:
        data = self.ok(self.put_json("zone-config", self.FULL))
        self.assertEqual(set(data), ZONE_CONFIG_KEYS)
        self.assertEqual(data, self.ok(self.get("zone-config")))
        self.assertEqual(data["zone_size"], 50)
        self.assertEqual(data["zones"], GRID)
        self.assertEqual(data["wall_meta"], self.FULL["wall_meta"])
        self.assertEqual(data["elevation"], self.FULL["elevation"])
        self.assertIs(data["spawn_user_edited"], True)
        self.assertEqual(data["bases"], [BLUE_BASE, RED_BASE])

    def test_put_marks_map_configured(self) -> None:
        self.ok(self.put_json("zone-config", self.FULL))
        detail = self.ok(self.api.get(self.url()))
        self.assertTrue(detail["has_confirmed_config"])

    def test_get_shape_round_trips(self) -> None:
        first = self.ok(self.put_json("zone-config", self.FULL))
        payload = {k: v for k, v in first.items() if k != "spawn_user_edited"}
        self.assertEqual(self.ok(self.put_json("zone-config", payload)), first)

    def test_absent_zones_auto_detects(self) -> None:
        data = self.ok(self.put_json("zone-config", {"zone_size": 50}))
        self.assertEqual(data["zones"], GRID)

    def test_empty_zones_auto_detects(self) -> None:
        data = self.ok(self.put_json("zone-config", {"zone_size": 50, "zones": []}))
        self.assertEqual(data["zones"], GRID)

    def test_absent_bases_deletes_all(self) -> None:
        self.ok(self.put_json("zone-config", self.FULL))
        data = self.ok(self.put_json("zone-config", {"zone_size": 50, "zones": GRID}))
        self.assertEqual(data["bases"], [])
        self.assertFalse(MapBaseConfig.objects.filter(arena_map=self.map).exists())

    def test_absent_elevation_and_spawns_carried_absent_wall_meta_dropped(self) -> None:
        self.ok(self.put_json("zone-config", self.FULL))
        data = self.ok(self.put_json("zone-config", {"zone_size": 50, "zones": GRID}))
        self.assertEqual(data["elevation"], self.FULL["elevation"])
        self.assertEqual(data["red_spawn"], [[1, 1]])
        self.assertEqual(data["blue_spawn"], [[2, 2]])
        self.assertIs(data["spawn_user_edited"], True)
        self.assertEqual(data["wall_meta"], {})

    def test_empty_spawns_clear_lock(self) -> None:
        self.ok(self.put_json("zone-config", self.FULL))
        data = self.ok(
            self.put_json(
                "zone-config",
                {"zone_size": 50, "zones": GRID, "red_spawn": [], "blue_spawn": []},
            )
        )
        self.assertEqual((data["red_spawn"], data["blue_spawn"]), ([], []))
        self.assertIs(data["spawn_user_edited"], False)

    def test_malformed_fields(self) -> None:
        cases = (
            ("zones", [[0, 1], [1]], ZONES_MSG),
            ("elevation", [[0, 11]], "Elevation value out of range [0.0, 10.0]: 11"),
            ("elevation", [5], "Invalid elevation: each row must be a list"),
            ("red_spawn", [[1]], "red_spawn must be a list of [int, int] pairs"),
            ("bases", [RED_BASE, RED_BASE], BASES_REPEAT_MSG),
        )
        for field, value, message in cases:
            with self.subTest(field=field, value=value):
                body = {"zone_size": 50, "zones": GRID, field: value}
                error = self.error(
                    self.put_json("zone-config", body), 400, "invalid", field
                )
                self.assertEqual(error["message"], message)
        self.assertIsNone(self.map.latest_confirmed_config())

    def test_wall_meta_malformed(self) -> None:
        self.error(
            self.put_json(
                "zone-config",
                {"zone_size": 50, "zones": GRID, "wall_meta": {"0,1": {"facing": "Q"}}},
            ),
            400,
            "invalid",
            "wall_meta",
        )

    def test_multipart_put_is_415(self) -> None:
        response = self.api.put(
            self.url("zone-config"), {"zone_size": "50"}, format="multipart"
        )
        self.error(response, 415, "unsupported_media_type")

    def test_array_body_is_parse_error(self) -> None:
        self.error(self.put_json("zone-config", [1, 2]), 400, "parse_error")

    def test_invalid_json_is_parse_error(self) -> None:
        response = self.api.put(
            self.url("zone-config"), b"{not json", content_type="application/json"
        )
        self.error(response, 400, "parse_error")


# ---------------------------------------------------------------------------
# sight-lines PUT / PATCH / compute
# ---------------------------------------------------------------------------


class TestSightLinesWrites(_MapsWriteTestCase):
    def test_put_then_patch_merges(self) -> None:
        data = self.ok(
            self.put_json(
                "sight-lines",
                {
                    "zone_size": 50,
                    "sight_data": SIGHT_A,
                    "base_sights": {"red": [[1, 1]]},
                },
            )
        )
        self.assertEqual(set(data), {"zone_size", "sight_data", "base_sights"})
        self.assertEqual(data["sight_data"], SIGHT_A)
        self.assertEqual(data["base_sights"]["red"], [[1, 1]])

        data = self.ok(
            self.patch_json("sight-lines", {"zone_size": 50, "sight_data": SIGHT_B})
        )
        self.assertEqual(data["sight_data"], SIGHT)
        self.assertEqual(data["base_sights"]["red"], [[1, 1]])
        self.assertEqual(data, self.ok(self.get("sight-lines", {"zone_size": 50})))
        ranked = self.ok(self.get("ranked-cells", {"zone_size": 50}))["ranked_cells"]
        self.assertEqual(ranked, [list(rc) for rc in compute_high_los_ranking(SIGHT)])

    def test_put_replaces(self) -> None:
        self.ok(self.put_json("sight-lines", {"zone_size": 50, "sight_data": SIGHT}))
        data = self.ok(
            self.put_json("sight-lines", {"zone_size": 50, "sight_data": SIGHT_B})
        )
        self.assertEqual(data["sight_data"], SIGHT_B)

    def test_patch_onto_missing_config_keeps_the_batch(self) -> None:
        data = self.ok(
            self.patch_json("sight-lines", {"zone_size": 50, "sight_data": SIGHT})
        )
        self.assertEqual(data["sight_data"], SIGHT)

    def test_bad_sight_data(self) -> None:
        error = self.error(
            self.put_json(
                "sight-lines", {"zone_size": 50, "sight_data": {"x": ["1,1"]}}
            ),
            400,
            "invalid",
            "sight_data",
        )
        self.assertEqual(error["message"], SIGHT_DATA_MSG)
        self.assertFalse(SightLineConfig.objects.filter(arena_map=self.map).exists())

    def test_bad_base_sights(self) -> None:
        error = self.error(
            self.patch_json(
                "sight-lines", {"zone_size": 50, "base_sights": {"green": [[1, 1]]}}
            ),
            400,
            "invalid",
            "base_sights",
        )
        self.assertEqual(error["message"], BASE_SIGHTS_MSG)

    def test_multipart_put_and_patch_are_415(self) -> None:
        for method in ("put", "patch"):
            with self.subTest(method=method):
                response = getattr(self.api, method)(
                    self.url("sight-lines"), {"zone_size": "50"}, format="multipart"
                )
                self.error(response, 415, "unsupported_media_type")

    def test_compute_real_opencv(self) -> None:
        data = self.ok(self.post_json("sight-lines-compute", {"zone_size": 50}))
        self.assertEqual(list(data), ["sight_data", "zone_size"])
        self.assertEqual(data, {"sight_data": SIGHT, "zone_size": 50})
        self.assertTrue(
            self.ok(self.get("ranked-cells", {"zone_size": 50}))["ranked_cells"]
        )
        self.assertTrue(self.ok(self.get("strong-spots", {"zone_size": 50}))["cells"])
        stored = self.ok(self.get("sight-lines", {"zone_size": 50}))["sight_data"]
        self.assertEqual(stored, SIGHT)

    def test_compute_spawn_override_is_locked(self) -> None:
        self.ok(
            self.put_json(
                "zone-config",
                {"zone_size": 50, "zones": GRID, "bases": [RED_BASE, BLUE_BASE]},
            )
        )
        self.ok(
            self.post_json(
                "sight-lines-compute", {"zone_size": 50, "red_spawn": [[1, 2]]}
            )
        )
        self.assertEqual(self.ok(self.get("spawn-cells"))["red_spawn"], [[1, 2]])
        self.assertIs(self.ok(self.get("zone-config"))["spawn_user_edited"], True)

    def test_compute_auto_spawns_from_bases(self) -> None:
        self.ok(
            self.put_json(
                "zone-config",
                {"zone_size": 50, "zones": GRID, "bases": [RED_BASE, BLUE_BASE]},
            )
        )
        self.ok(self.post_json("sight-lines-compute", {"zone_size": 50}))
        spawns = self.ok(self.get("spawn-cells"))
        self.assertTrue(spawns["red_spawn"])
        self.assertTrue(spawns["blue_spawn"])

    def test_compute_bad_spawn(self) -> None:
        error = self.error(
            self.post_json("sight-lines-compute", {"zone_size": 50, "blue_spawn": "x"}),
            400,
            "invalid",
            "blue_spawn",
        )
        self.assertEqual(
            error["message"], "blue_spawn must be a list of [int, int] pairs"
        )

    def test_compute_multipart_is_415(self) -> None:
        response = self.api.post(
            self.url("sight-lines-compute"), {"zone_size": "50"}, format="multipart"
        )
        self.error(response, 415, "unsupported_media_type")

    def test_compute_array_body_is_parse_error(self) -> None:
        self.error(self.post_json("sight-lines-compute", [50]), 400, "parse_error")


# ---------------------------------------------------------------------------
# strong-spots PUT
# ---------------------------------------------------------------------------


class TestStrongSpotsPut(_MapsWriteTestCase):
    def test_round_trip(self) -> None:
        data = self.ok(
            self.put_json("strong-spots", {"zone_size": 50, "cells": [[1, 1], [2, 2]]})
        )
        self.assertEqual(data, {"cells": [[1, 1], [2, 2]]})
        self.assertEqual(self.ok(self.get("strong-spots", {"zone_size": 50})), data)

    def test_empty_cells_clears(self) -> None:
        self.ok(self.put_json("strong-spots", {"zone_size": 50, "cells": [[1, 1]]}))
        self.assertEqual(
            self.ok(self.put_json("strong-spots", {"zone_size": 50, "cells": []})),
            {"cells": []},
        )

    def test_absent_and_null_cells_required(self) -> None:
        for body in ({"zone_size": 50}, {"zone_size": 50, "cells": None}):
            with self.subTest(body=body):
                self.error(
                    self.put_json("strong-spots", body), 400, "required", "cells"
                )

    def test_bad_cells_pinned_message(self) -> None:
        for cells in ("not-a-list", [[1, True]], [[1]]):
            with self.subTest(cells=cells):
                error = self.error(
                    self.put_json("strong-spots", {"zone_size": 50, "cells": cells}),
                    400,
                    "invalid",
                    "cells",
                )
                self.assertEqual(error["message"], CELLS_MSG)

    def test_multipart_is_415(self) -> None:
        response = self.api.put(
            self.url("strong-spots"), {"zone_size": "50"}, format="multipart"
        )
        self.error(response, 415, "unsupported_media_type")


# ---------------------------------------------------------------------------
# heatmap -- scoped to the requester
# ---------------------------------------------------------------------------


class TestHeatmapScoping(_MapsWriteTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_b = User.objects.create_user(
            email="ang01c-api-heat-b@example.com", password="Str0ng-Passphrase-9"
        )
        self.ok(self.put_json("zone-config", {"zone_size": 50, "zones": GRID}))
        make_heatmap_round(
            self.map, {"1": {"1,1": 10}}, manager=self.user_a, prefix="A"
        )
        make_heatmap_round(self.map, {"2": {"1,1": 1}}, prefix="Unmanaged")
        make_heatmap_round(
            self.map, {"3": {"2,2": 50}}, manager=self.user_b, prefix="B"
        )

    def test_foreign_round_excluded(self) -> None:
        data = self.ok(self.get("heatmap", {"zone_size": 50}))
        self.assertEqual(
            data,
            {
                "cell_occupancy": {"1,1": 11},
                "zone_size": 50,
                "rows": 4,
                "cols": 4,
                "round_count": 2,
            },
        )

    def test_other_account_sees_its_own(self) -> None:
        api_b = APIClient()
        api_b.force_login(self.user_b)
        data = self.ok(self.get("heatmap", {"zone_size": 50}, api=api_b))
        self.assertEqual(data["round_count"], 2)
        self.assertEqual(data["cell_occupancy"], {"1,1": 1, "2,2": 50})

    def test_team_color_blank_means_both(self) -> None:
        data = self.ok(self.get("heatmap", {"zone_size": 50, "team_color": ""}))
        self.assertEqual(data["round_count"], 2)
        self.assertEqual(data["cell_occupancy"], {"1,1": 11})


# ---------------------------------------------------------------------------
# Shared posture + anonymous
# ---------------------------------------------------------------------------


class TestSharedAndAnonymous(_MapsWriteTestCase):
    def test_second_account_can_edit_and_delete_first_accounts_map(self) -> None:
        created = self.ok(
            self.upload({"name": "A's Arena", "image": png_upload()}), 201
        )
        pk = created["id"]
        user_b = User.objects.create_user(
            email="ang01c-shared-b@example.com", password="Str0ng-Passphrase-9"
        )
        api_b = APIClient()
        api_b.force_login(user_b)

        response = api_b.put(
            reverse("arenamap-strong-spots", args=[pk]),
            {"zone_size": 50, "cells": [[1, 1]]},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        response = api_b.put(
            reverse("arenamap-zone-config", args=[pk]),
            {"zone_size": 50, "zones": GRID},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            api_b.delete(reverse("arenamap-detail", args=[pk])).status_code, 204
        )
        self.assertFalse(ArenaMap.objects.filter(pk=pk).exists())

    def test_second_account_can_upload(self) -> None:
        user_b = User.objects.create_user(
            email="ang01c-upload-b@example.com", password="Str0ng-Passphrase-9"
        )
        api_b = APIClient()
        api_b.force_login(user_b)
        data = self.ok(
            self.upload({"name": "B's Arena", "image": png_upload()}, api=api_b), 201
        )
        # ... and the first Account sees it (shared reference data).
        self.ok(self.api.get(self.url(pk=data["id"])))

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        anon = APIClient()
        cases = (
            ("post", reverse("arenamap-list"), {"name": "Anon"}, "multipart"),
            ("delete", self.url(), None, None),
            ("get", self.url("zone-config"), None, None),
            ("put", self.url("zone-config"), {"zone_size": 50}, "json"),
            ("put", self.url("strong-spots"), {"zone_size": 50, "cells": []}, "json"),
            ("post", self.url("sight-lines-compute"), {"zone_size": 50}, "json"),
        )
        for method, path, body, fmt in cases:
            with self.subTest(method=method, path=path):
                kwargs = {"format": fmt} if fmt else {}
                args = (path, body) if body is not None else (path,)
                response = getattr(anon, method)(*args, **kwargs)
                self.error(response, 401, "not_authenticated")
        self.assertTrue(ArenaMap.objects.filter(pk=self.map.pk).exists())
        self.assertIsNone(self.map.latest_confirmed_config())
        self.assertFalse(ArenaMap.objects.filter(name="Anon").exists())
