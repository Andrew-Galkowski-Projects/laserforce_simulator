"""ANG-01c -- the HTML map views after delegating to ``core/services.py``
(seam contract section 7).

Valid editor payloads keep byte-identical success bodies; malformed payloads
(now caught by the shared strict Forms) get each view's existing error-body
shape with a 400; a non-object JSON body gets the view's parse-error body; the
delete guard refuses an in-use map with a flash message; the heatmap is scoped
to the requesting Account.
"""

from __future__ import annotations

import json
import re

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.http import JsonResponse
from django.test import TestCase
from django.urls import reverse

from conftest import get_shared_manager
from core.map_processing import compute_sight_lines, detect_zones
from core.map_test_fixtures import (
    BLUE_BASE,
    GRID,
    RED_BASE,
    SIGHT,
    TempMediaMixin,
    make_heatmap_round,
    make_image_map,
    make_season,
    png_upload,
)
from core.models import (
    ArenaMap,
    HeavyStrongSpotsConfig,
    MapBaseConfig,
    MapZoneConfig,
    SightLineConfig,
)

User = get_user_model()

OK_BODY = b'{"status": "ok"}'
ZONES_MSG = "zones must be a non-empty list of equal-length rows of cell values 0-5."
BASES_MSG = (
    'bases must be a list of {"type", "x_px", "y_px"} objects with a valid base '
    "type and integer pixel coordinates."
)
BASES_REPEAT_MSG = "bases must not repeat a base type."
SIGHT_DATA_MSG = 'sight_data must map "r,c" keys to lists of "r,c" strings.'
BASE_SIGHTS_MSG = "base_sights must map base types to lists of [int, int] pairs."
CELLS_MSG = "cells must be a list of [int, int] pairs"


class _HtmlMapTestCase(TempMediaMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_a = get_shared_manager()
        self.client.force_login(self.user_a)
        self.map = make_image_map()

    def post_json(self, url_name: str, body, pk: int | None = None):
        payload = body if isinstance(body, (str, bytes)) else json.dumps(body)
        return self.client.post(
            reverse(url_name, args=[pk or self.map.pk]),
            data=payload,
            content_type="application/json",
        )

    def assert_error_body(self, response, expected: dict) -> None:
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json(), expected)


# ---------------------------------------------------------------------------
# delete_map -- the in-use guard
# ---------------------------------------------------------------------------


class TestHtmlDelete(_HtmlMapTestCase):
    def test_in_use_map_is_refused_with_message(self) -> None:
        season = make_season("active")
        season.map_pool.add(self.map)
        MapZoneConfig.objects.create(
            arena_map=self.map, zone_size=50, zone_data={"zones": GRID}, confirmed=True
        )
        image_file = self.media_root / self.map.image.name
        response = self.client.post(reverse("delete_map", args=[self.map.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("map_list"))
        self.assertEqual(reverse("map_list"), "/maps/")
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(
            messages,
            [
                "Cannot delete map “Probe Arena”: it is used by a Season "
                "that is not completed."
            ],
        )
        self.assertTrue(ArenaMap.objects.filter(pk=self.map.pk).exists())
        self.assertTrue(MapZoneConfig.objects.filter(arena_map=self.map).exists())
        self.assertTrue(image_file.exists())

    def test_in_use_message_is_error_level(self) -> None:
        make_season("draft", map_rotation_ids_json=[self.map.pk])
        response = self.client.post(reverse("delete_map", args=[self.map.pk]))
        levels = [m.level_tag for m in get_messages(response.wsgi_request)]
        self.assertEqual(levels, ["error"])

    def test_conference_rotation_blocks(self) -> None:
        from matches.models import Conference

        season = make_season("active")
        Conference.objects.create(
            season=season,
            name="West",
            ordinal=1,
            starting_map_rotation_ids_json=[self.map.pk],
        )
        self.client.post(reverse("delete_map", args=[self.map.pk]))
        self.assertTrue(ArenaMap.objects.filter(pk=self.map.pk).exists())

    def test_unused_map_still_deletes(self) -> None:
        pk = self.map.pk
        response = self.client.post(reverse("delete_map", args=[pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/maps/")
        self.assertFalse(ArenaMap.objects.filter(pk=pk).exists())
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(messages, ["Deleted map “Probe Arena”."])

    def test_completed_season_does_not_block(self) -> None:
        season = make_season("completed")
        season.map_pool.add(self.map)
        self.client.post(reverse("delete_map", args=[self.map.pk]))
        self.assertFalse(ArenaMap.objects.filter(pk=self.map.pk).exists())


# ---------------------------------------------------------------------------
# upload_map
# ---------------------------------------------------------------------------


class TestHtmlUpload(_HtmlMapTestCase):
    def test_valid_upload_redirects_to_editor(self) -> None:
        response = self.client.post(
            reverse("upload_map"), {"name": "Html Arena", "image": png_upload()}
        )
        row = ArenaMap.objects.get(name="Html Arena")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("map_editor", args=[row.pk]))
        self.assertEqual((row.img_width, row.img_height), (200, 200))

    def test_name_over_100_redirects_to_list_without_row(self) -> None:
        before = ArenaMap.objects.count()
        response = self.client.post(
            reverse("upload_map"), {"name": "n" * 101, "image": png_upload()}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/maps/")
        self.assertEqual(ArenaMap.objects.count(), before)

    def test_missing_image_redirects_to_list(self) -> None:
        response = self.client.post(reverse("upload_map"), {"name": "Nothing"})
        self.assertEqual(response["Location"], "/maps/")
        self.assertFalse(ArenaMap.objects.filter(name="Nothing").exists())


# ---------------------------------------------------------------------------
# save_zone_config
# ---------------------------------------------------------------------------


class TestHtmlSaveZoneConfig(_HtmlMapTestCase):
    URL = "save_zone_config"

    def test_valid_editor_payload_is_exact_ok(self) -> None:
        response = self.post_json(
            self.URL,
            {
                "zone_size": 50,
                "zones": GRID,
                "wall_meta": {"0,1": {"facing": "N"}},
                "elevation": [[0] * 4] * 4,
                "red_spawn": [[1, 1]],
                "blue_spawn": [[2, 2]],
                "bases": [RED_BASE, BLUE_BASE],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, OK_BODY)
        config = self.map.latest_confirmed_config()
        self.assertEqual(config.zone_data["zones"], GRID)
        self.assertEqual(
            set(
                MapBaseConfig.objects.filter(arena_map=self.map).values_list(
                    "base_type", flat=True
                )
            ),
            {"red", "blue"},
        )

    def test_zone_size_is_clamped_not_rejected(self) -> None:
        response = self.post_json(self.URL, {"zone_size": 5, "zones": GRID})
        self.assertEqual(response.content, OK_BODY)
        self.assertEqual(self.map.latest_confirmed_config().zone_size, 10)
        self.post_json(self.URL, {"zone_size": 999, "zones": GRID})
        self.assertEqual(self.map.latest_confirmed_config().zone_size, 200)

    def test_missing_zone_size_defaults_to_50(self) -> None:
        self.assertEqual(self.post_json(self.URL, {"zones": GRID}).content, OK_BODY)
        self.assertEqual(self.map.latest_confirmed_config().zone_size, 50)

    def test_missing_bases_defaults_to_none(self) -> None:
        MapBaseConfig.objects.create(
            arena_map=self.map, base_type="red", x_px=1, y_px=1
        )
        self.assertEqual(self.post_json(self.URL, {"zone_size": 50}).content, OK_BODY)
        self.assertFalse(MapBaseConfig.objects.filter(arena_map=self.map).exists())

    def test_existing_parse_error_body(self) -> None:
        for body in ("{not json", {"zone_size": "abc"}):
            with self.subTest(body=body):
                self.assert_error_body(
                    self.post_json(self.URL, body),
                    {"status": "error", "message": "Invalid zone_size"},
                )

    def test_non_object_body_is_parse_error_body(self) -> None:
        self.assert_error_body(
            self.post_json(self.URL, [1, 2]),
            {"status": "error", "message": "Invalid zone_size"},
        )

    def test_malformed_payloads(self) -> None:
        cases = (
            ({"zones": [[0, 1], [1]]}, ZONES_MSG),
            ({"zones": "auto"}, ZONES_MSG),
            ({"elevation": [5]}, "Invalid elevation: each row must be a list"),
            (
                {"elevation": [[0, 10.5]]},
                "Elevation value out of range [0.0, 10.0]: 10.5",
            ),
            ({"elevation": "flat"}, "Invalid elevation: must be a list of rows"),
            ({"red_spawn": [[1, "a"]]}, "red_spawn must be a list of [int, int] pairs"),
            ({"bases": [{"type": "green", "x_px": 1, "y_px": 1}]}, BASES_MSG),
            ({"bases": [{"type": "red", "x_px": "1", "y_px": 1}]}, BASES_MSG),
            ({"bases": [RED_BASE, RED_BASE]}, BASES_REPEAT_MSG),
        )
        for extra, message in cases:
            with self.subTest(extra=extra):
                self.assert_error_body(
                    self.post_json(self.URL, {"zone_size": 50, "zones": GRID, **extra}),
                    {"status": "error", "message": message},
                )
        self.assertIsNone(self.map.latest_confirmed_config())

    def test_first_error_in_field_order(self) -> None:
        response = self.post_json(
            self.URL,
            {"zone_size": 50, "zones": [[9]], "bases": "nope", "red_spawn": "nope"},
        )
        self.assert_error_body(response, {"status": "error", "message": ZONES_MSG})


# ---------------------------------------------------------------------------
# compute_sight_lines_view
# ---------------------------------------------------------------------------


class TestHtmlEditorMissingBaseWarning(_HtmlMapTestCase):
    """UXP-10 -- the save endpoint keeps its pinned ``{"status": "ok"}`` body for
    a base-less config; the editor JS warns from its own ``bases`` state."""

    def test_editor_save_handler_warns_when_a_team_base_is_missing(self) -> None:
        response = self.client.get(reverse("map_editor", args=[self.map.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'const missingTeamBases = ["red", "blue"].filter('
        )
        self.assertContains(response, "before using this map in a match.")
        self.assertContains(response, "It won't be offered in match, round or League")
        self.assertContains(response, '"alert alert-warning"')

    def test_base_less_save_still_returns_exact_ok(self) -> None:
        response = self.post_json(
            "save_zone_config", {"zone_size": 50, "zones": GRID, "bases": []}
        )
        self.assertEqual(response.content, OK_BODY)
        self.assertIsNotNone(self.map.latest_confirmed_config())


class TestHtmlComputeSightLines(_HtmlMapTestCase):
    URL = "compute_sight_lines"

    def test_valid_body_is_byte_identical(self) -> None:
        zone_data = detect_zones(self.map.image.path, 50)
        expected = JsonResponse(
            {
                "sight_data": compute_sight_lines(zone_data, use_quadtree=True),
                "zone_size": 50,
            }
        ).content
        response = self.post_json(self.URL, {"zone_size": 50})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, expected)
        self.assertEqual(response.json()["sight_data"], SIGHT)

    def test_bad_spawn_uses_error_key(self) -> None:
        self.assert_error_body(
            self.post_json(self.URL, {"zone_size": 50, "red_spawn": "x"}),
            {"error": "red_spawn must be a list of [int, int] pairs"},
        )
        self.assertFalse(SightLineConfig.objects.filter(arena_map=self.map).exists())

    def test_parse_errors(self) -> None:
        for body in ("{bad", [50], {"zone_size": "abc"}):
            with self.subTest(body=body):
                self.assert_error_body(
                    self.post_json(self.URL, body), {"error": "Invalid zone_size"}
                )


# ---------------------------------------------------------------------------
# save_sight_lines
# ---------------------------------------------------------------------------


class TestHtmlSaveSightLines(_HtmlMapTestCase):
    URL = "save_sight_lines"

    def stored(self) -> dict | None:
        row = SightLineConfig.objects.filter(arena_map=self.map, zone_size=50).first()
        return row.sight_data if row else None

    def test_replace_then_merge_batches(self) -> None:
        first = {k: SIGHT[k] for k in ("1,1", "1,2")}
        rest = {k: SIGHT[k] for k in ("2,1", "2,2")}
        r1 = self.post_json(
            self.URL,
            {
                "zone_size": 50,
                "sight_data": first,
                "base_sights": {"red": [], "blue": []},
                "replace": True,
            },
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.content, OK_BODY)
        r2 = self.post_json(
            self.URL, {"zone_size": 50, "sight_data": rest, "replace": False}
        )
        self.assertEqual(r2.content, OK_BODY)
        self.assertEqual(self.stored(), SIGHT)

    def test_replace_defaults_true(self) -> None:
        self.post_json(self.URL, {"zone_size": 50, "sight_data": SIGHT})
        self.post_json(self.URL, {"zone_size": 50, "sight_data": {"1,1": []}})
        self.assertEqual(self.stored(), {"1,1": []})

    def test_merge_onto_missing_config_keeps_batch(self) -> None:
        self.post_json(
            self.URL, {"zone_size": 50, "sight_data": SIGHT, "replace": False}
        )
        self.assertEqual(self.stored(), SIGHT)

    def test_malformed_sight_data(self) -> None:
        self.assert_error_body(
            self.post_json(self.URL, {"zone_size": 50, "sight_data": {"bad": ["1,1"]}}),
            {"status": "error", "message": SIGHT_DATA_MSG},
        )
        self.assertIsNone(self.stored())

    def test_unknown_base_type_rejected(self) -> None:
        self.assert_error_body(
            self.post_json(self.URL, {"zone_size": 50, "base_sights": {"green": []}}),
            {"status": "error", "message": BASE_SIGHTS_MSG},
        )

    def test_non_object_body_is_400_error_status(self) -> None:
        response = self.post_json(self.URL, [1])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "error")

    def test_bad_zone_size_is_400_error_status(self) -> None:
        response = self.post_json(self.URL, {"zone_size": "abc"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "error")


# ---------------------------------------------------------------------------
# save_strong_spots
# ---------------------------------------------------------------------------


class TestHtmlSaveStrongSpots(_HtmlMapTestCase):
    URL = "save_strong_spots"

    def test_valid_is_exact_ok(self) -> None:
        response = self.post_json(self.URL, {"zone_size": 50, "cells": [[1, 1]]})
        self.assertEqual(response.content, OK_BODY)
        self.assertEqual(
            HeavyStrongSpotsConfig.objects.get(arena_map=self.map, zone_size=50).cells,
            [[1, 1]],
        )

    def test_missing_cells_defaults_to_empty(self) -> None:
        response = self.post_json(self.URL, {"zone_size": 50})
        self.assertEqual(response.content, OK_BODY)
        self.assertEqual(
            HeavyStrongSpotsConfig.objects.get(arena_map=self.map, zone_size=50).cells,
            [],
        )

    def test_malformed_cells_pinned_message(self) -> None:
        for cells in ("not-a-list", [[1, True]], [[1, "2"]], [[1]]):
            with self.subTest(cells=cells):
                self.assert_error_body(
                    self.post_json(self.URL, {"zone_size": 50, "cells": cells}),
                    {"status": "error", "message": CELLS_MSG},
                )
        self.assertFalse(
            HeavyStrongSpotsConfig.objects.filter(arena_map=self.map).exists()
        )

    def test_explicit_null_cells_is_required(self) -> None:
        """Absent ``cells`` defaults to ``[]``; an explicit null is the Form's
        ``required`` error."""
        self.assert_error_body(
            self.post_json(self.URL, {"zone_size": 50, "cells": None}),
            {"status": "error", "message": "This field is required."},
        )

    def test_parse_error_bodies(self) -> None:
        for body in ("{bad", [1], {"zone_size": "abc"}):
            with self.subTest(body=body):
                self.assert_error_body(
                    self.post_json(self.URL, body),
                    {"status": "error", "message": "Invalid payload"},
                )


# ---------------------------------------------------------------------------
# GET endpoints stay byte-identical / lenient
# ---------------------------------------------------------------------------


class TestHtmlReads(_HtmlMapTestCase):
    def test_spawn_cells_exact(self) -> None:
        response = self.client.get(reverse("get_spawn_cells", args=[self.map.pk]))
        self.assertEqual(response.content, b'{"red_spawn": [], "blue_spawn": []}')

    def test_strong_spots_default_zone_size(self) -> None:
        HeavyStrongSpotsConfig.objects.create(
            arena_map=self.map, zone_size=50, cells=[[2, 2]]
        )
        response = self.client.get(reverse("get_strong_spots", args=[self.map.pk]))
        self.assertEqual(response.content, b'{"cells": [[2, 2]]}')

    def test_ranked_cells_empty_exact(self) -> None:
        response = self.client.get(
            reverse("get_ranked_cells", args=[self.map.pk]), {"zone_size": "junk"}
        )
        self.assertEqual(response.content, b'{"ranked_cells": []}')

    def test_single_cell_off_grid(self) -> None:
        response = self.client.get(
            reverse("compute_single_cell_sight", args=[self.map.pk]),
            {"zone_size": 50, "r": 99, "c": 99},
        )
        self.assertEqual(response.content, b'{"visible_cells": []}')

    def test_single_cell_invalid_params(self) -> None:
        response = self.client.get(
            reverse("compute_single_cell_sight", args=[self.map.pk]),
            {"zone_size": 50, "r": "x"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid parameters"})

    def test_process_zones_lenient(self) -> None:
        response = self.client.get(
            reverse("process_zones", args=[self.map.pk]), {"zone_size": "junk"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["zones"], GRID)

    def test_processed_image_png(self) -> None:
        response = self.client.get(reverse("processed_image", args=[self.map.pk]))
        # Read to exhaustion first: the test client then closes the response
        # (and its file). A manual close() would fire request_finished inside
        # the test transaction (CI-01).
        body = b"".join(response.streaming_content)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))


# ---------------------------------------------------------------------------
# map_heatmap_data -- now scoped to the requesting Account
# ---------------------------------------------------------------------------


class TestHtmlHeatmapScoping(_HtmlMapTestCase):
    def setUp(self) -> None:
        super().setUp()
        MapZoneConfig.objects.create(
            arena_map=self.map, zone_size=50, zone_data={"zones": GRID}, confirmed=True
        )
        make_heatmap_round(
            self.map, {"1": {"1,1": 4}}, manager=self.user_a, prefix="HA"
        )
        make_heatmap_round(self.map, {"2": {"1,1": 6}}, prefix="HU")
        self.url = reverse("map_heatmap_data", args=[self.map.pk])

    def test_foreign_round_excluded_and_count_drops(self) -> None:
        before = self.client.get(self.url, {"zone_size": 50}).json()
        self.assertEqual(before["round_count"], 2)

        user_b = User.objects.create_user(
            email="ang01c-html-heat-b@example.com", password="Str0ng-Passphrase-9"
        )
        make_heatmap_round(self.map, {"3": {"2,2": 99}}, manager=user_b, prefix="HB")

        after = self.client.get(self.url, {"zone_size": 50}).json()
        self.assertEqual(after["round_count"], 2)
        self.assertEqual(after["cell_occupancy"], {"1,1": 10})
        self.assertEqual(
            list(after), ["cell_occupancy", "zone_size", "rows", "cols", "round_count"]
        )

        other = self.client_class()
        other.force_login(user_b)
        theirs = other.get(self.url, {"zone_size": 50}).json()
        self.assertEqual(theirs["round_count"], 2)
        self.assertEqual(theirs["cell_occupancy"], {"1,1": 6, "2,2": 99})

    def test_plain_text_400s_unchanged(self) -> None:
        missing = self.client.get(self.url)
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.content, b"zone_size required")
        bad = self.client.get(self.url, {"zone_size": 50, "team_color": "green"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.content, b"invalid team_color")


# ---------------------------------------------------------------------------
# UXP-19 -- readable Floor brush, one <h1> on the map list
# ---------------------------------------------------------------------------


class TestHtmlMapPagesUxp19(_HtmlMapTestCase):
    def test_floor_brush_button_uses_readable_outline_style(self) -> None:
        body = self.client.get(reverse("map_editor", args=[self.map.pk])).content
        match = re.search(
            r'<button class="([^"]*)" data-wall="1"[^>]*>Floor</button>',
            body.decode(),
        )
        assert match is not None, "Floor wall-brush button not found"
        classes = match.group(1).split()
        self.assertIn("wall-brush-btn", classes)
        self.assertIn("btn-outline-secondary", classes)
        self.assertNotIn("btn-outline-light", classes)

    def test_map_list_has_exactly_one_h1(self) -> None:
        body = self.client.get(reverse("map_list")).content.decode()
        self.assertEqual(body.count("<h1"), 1)
        self.assertRegex(body, r"<h1[^>]*>Arena Maps</h1>")
