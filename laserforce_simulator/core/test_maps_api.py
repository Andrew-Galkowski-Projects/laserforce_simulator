"""ANG-01a — the read side of ``/api/maps/`` (seam contract §5.12).

The ANG-01c writes (upload, delete, the map-editor detail actions) are tested
in ``core/test_maps_api_writes.py``; this module keeps list / detail / filters.

``ArenaMapViewSet`` over ``ArenaMapSerializer`` with ``ArenaMapFilterSet``
(``is_default``, ``has_confirmed_config``). ArenaMap is **shared reference
data** (ADR-0038): every authenticated Account sees every map; it is NOT
ownership-filtered.

``core`` keeps a single ``core/tests.py``, so this lives in a sibling
``test_*.py`` (collected by ``pytest.ini``'s ``python_files``).
"""

from __future__ import annotations

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image as PILImage
from rest_framework.test import APIClient

from conftest import get_shared_manager
from core.map_test_fixtures import TempMediaMixin, make_bare_map, make_image_map
from core.models import ArenaMap, MapZoneConfig

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
    "confirmed_zone_size",
}


class _MapsTestCase(TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.api = APIClient()
        self.api.force_login(get_shared_manager())
        self.alpha = ArenaMap.objects.create(
            name="Alpha Arena",
            image="maps/alpha.png",
            is_default=True,
            img_width=800,
            img_height=600,
        )
        self.bravo = ArenaMap.objects.create(name="Bravo Arena", image="maps/bravo.png")
        self.charlie = ArenaMap.objects.create(name="Charlie Arena", image="")
        MapZoneConfig.objects.create(
            arena_map=self.alpha, zone_size=20, zone_data={}, confirmed=True
        )
        # An UNconfirmed config does not count.
        MapZoneConfig.objects.create(
            arena_map=self.bravo, zone_size=20, zone_data={}, confirmed=False
        )

    def names(self, params: dict | None = None) -> list[str]:
        response = self.api.get("/api/maps/", params or {})
        self.assertEqual(response.status_code, 200, response.content)
        return [row["name"] for row in response.json()["data"]]


class TestMapsList(_MapsTestCase):
    def test_route_name(self) -> None:
        self.assertEqual(reverse("arenamap-list"), "/api/maps/")
        self.assertEqual(reverse("arenamap-detail", args=[5]), "/api/maps/5/")

    def test_list_is_paginated_envelope(self) -> None:
        body = self.api.get("/api/maps/").json()
        self.assertEqual(set(body), {"data", "meta"})
        self.assertEqual(
            set(body["meta"]), {"page", "page_size", "count", "total_pages"}
        )
        self.assertEqual(body["meta"]["count"], ArenaMap.objects.count())

    def test_default_ordering_by_name(self) -> None:
        names = self.names()
        self.assertEqual(names, sorted(names))

    def test_ordering_param(self) -> None:
        names = self.names({"ordering": "-name"})
        self.assertEqual(names, sorted(names, reverse=True))

    def test_list_row_fields_exact(self) -> None:
        row = self.api.get("/api/maps/").json()["data"][0]
        self.assertEqual(set(row), _MAP_FIELDS)


class TestMapsDetail(_MapsTestCase):
    def test_detail_envelope_and_fields(self) -> None:
        body = self.api.get(f"/api/maps/{self.alpha.pk}/").json()
        self.assertEqual(body["meta"], {})
        data = body["data"]
        self.assertEqual(set(data), _MAP_FIELDS)
        self.assertEqual(data["name"], "Alpha Arena")
        self.assertTrue(data["is_default"])
        self.assertEqual(data["img_width"], 800)
        self.assertEqual(data["img_height"], 600)

    def test_image_is_absolute_url(self) -> None:
        image = self.api.get(f"/api/maps/{self.alpha.pk}/").json()["data"]["image"]
        self.assertTrue(image.startswith("http://testserver"), image)
        self.assertTrue(image.endswith("maps/alpha.png"), image)

    def test_missing_image_is_null(self) -> None:
        image = self.api.get(f"/api/maps/{self.charlie.pk}/").json()["data"]["image"]
        self.assertIsNone(image)

    def test_has_confirmed_config_true_and_false(self) -> None:
        self.assertTrue(
            self.api.get(f"/api/maps/{self.alpha.pk}/").json()["data"][
                "has_confirmed_config"
            ]
        )
        self.assertFalse(
            self.api.get(f"/api/maps/{self.bravo.pk}/").json()["data"][
                "has_confirmed_config"
            ]
        )
        self.assertFalse(
            self.api.get(f"/api/maps/{self.charlie.pk}/").json()["data"][
                "has_confirmed_config"
            ]
        )

    def test_confirmed_zone_size_null_without_confirmed_config(self) -> None:
        # bravo has only an UNconfirmed config, charlie has none.
        for arena_map in (self.bravo, self.charlie):
            with self.subTest(name=arena_map.name):
                data = self.api.get(f"/api/maps/{arena_map.pk}/").json()["data"]
                self.assertIsNone(data["confirmed_zone_size"])

    def test_confirmed_zone_size_is_latest_confirmed(self) -> None:
        data = self.api.get(f"/api/maps/{self.alpha.pk}/").json()["data"]
        self.assertEqual(data["confirmed_zone_size"], 20)
        MapZoneConfig.objects.create(
            arena_map=self.alpha, zone_size=40, zone_data={}, confirmed=True
        )
        # A later UNconfirmed config does not count.
        MapZoneConfig.objects.create(
            arena_map=self.alpha, zone_size=80, zone_data={}, confirmed=False
        )
        data = self.api.get(f"/api/maps/{self.alpha.pk}/").json()["data"]
        self.assertEqual(data["confirmed_zone_size"], 40)

    def test_confirmed_zone_size_on_list_rows(self) -> None:
        rows = {
            row["name"]: row["confirmed_zone_size"]
            for row in self.api.get("/api/maps/").json()["data"]
        }
        self.assertEqual(rows["Alpha Arena"], 20)
        self.assertIsNone(rows["Bravo Arena"])

    def test_missing_map_is_404_envelope(self) -> None:
        response = self.api.get("/api/maps/999999/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["errors"][0]["code"], "not_found")

    def test_detail_ignores_filters(self) -> None:
        response = self.api.get(f"/api/maps/{self.bravo.pk}/", {"is_default": "true"})
        self.assertEqual(response.status_code, 200)


class TestMapsFilters(_MapsTestCase):
    def test_is_default_true(self) -> None:
        self.assertEqual(self.names({"is_default": "true"}), ["Alpha Arena"])

    def test_is_default_false(self) -> None:
        names = self.names({"is_default": "false"})
        self.assertNotIn("Alpha Arena", names)
        self.assertIn("Bravo Arena", names)

    def test_has_confirmed_config_true(self) -> None:
        self.assertEqual(self.names({"has_confirmed_config": "true"}), ["Alpha Arena"])

    def test_has_confirmed_config_false(self) -> None:
        names = self.names({"has_confirmed_config": "false"})
        self.assertNotIn("Alpha Arena", names)
        self.assertIn("Bravo Arena", names)
        self.assertIn("Charlie Arena", names)

    def test_unparseable_boolean_is_ignored(self) -> None:
        """BooleanWidget treats junk as absent — never a 400 on booleans."""
        names = self.names({"is_default": "maybe"})
        self.assertIn("Alpha Arena", names)
        self.assertIn("Bravo Arena", names)


class TestMapsSharedAndAuth(_MapsTestCase):
    def test_visible_to_every_account(self) -> None:
        other = User.objects.create_user(
            email="maps-other@example.com", password="Str0ng-Passphrase-9"
        )
        other_api = APIClient()
        other_api.force_login(other)
        mine = set(self.names())
        theirs = {row["name"] for row in other_api.get("/api/maps/").json()["data"]}
        self.assertEqual(mine, theirs)
        self.assertEqual(other_api.get(f"/api/maps/{self.alpha.pk}/").status_code, 200)

    def test_anonymous_is_401_not_authenticated(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        response = APIClient().get("/api/maps/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["errors"][0]["code"], "not_authenticated")

    def test_detail_put_and_patch_are_405(self) -> None:
        """ANG-01c: there is no update / partial_update. Upload (POST) and
        delete (DELETE) are covered in ``core/test_maps_api_writes.py``."""
        for method in ("put", "patch"):
            with self.subTest(method=method):
                response = getattr(self.api, method)(
                    f"/api/maps/{self.alpha.pk}/", {}, format="json"
                )
                self.assertEqual(response.status_code, 405)
                self.assertEqual(
                    response.json()["errors"][0]["code"], "method_not_allowed"
                )
        self.assertTrue(ArenaMap.objects.filter(pk=self.alpha.pk).exists())


class TestMapsOriginalImage(TempMediaMixin, TestCase):
    """ANG-06h -- ``GET /api/maps/<id>/image/``: the original uploaded image,
    raw and un-enveloped (the JWT SPA cannot pass the ``/media/`` login gate)."""

    def setUp(self) -> None:
        super().setUp()
        self.api = APIClient()
        self.api.force_login(get_shared_manager())

    def get_image(self, pk: int, api: APIClient | None = None):
        return (api or self.api).get(
            reverse("arenamap-image", args=[pk]), HTTP_ACCEPT="*/*"
        )

    def test_route_name(self) -> None:
        self.assertEqual(reverse("arenamap-image", args=[7]), "/api/maps/7/image/")

    def test_png_is_returned_raw(self) -> None:
        arena_map = make_image_map()
        response = self.get_image(arena_map.pk)
        # Read to exhaustion: the test client then closes the file (CI-01).
        body = b"".join(response.streaming_content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(body, (self.media_root / arena_map.image.name).read_bytes())

    def test_jpeg_content_type_is_guessed(self) -> None:
        buf = io.BytesIO()
        PILImage.new("RGB", (30, 20), color=(10, 20, 30)).save(buf, format="JPEG")
        arena_map = ArenaMap.objects.create(
            name="Jpeg Arena",
            image=SimpleUploadedFile("arena.jpg", buf.getvalue()),
            img_width=30,
            img_height=20,
        )
        response = self.get_image(arena_map.pk)
        body = b"".join(response.streaming_content)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(body, buf.getvalue())

    def test_unknown_map_is_404(self) -> None:
        response = self.get_image(999_999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["errors"][0]["code"], "not_found")

    def test_map_without_image_field_is_404(self) -> None:
        response = self.get_image(make_bare_map().pk)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["errors"][0]["code"], "not_found")

    def test_map_whose_image_file_is_gone_is_404(self) -> None:
        arena_map = ArenaMap.objects.create(name="Ghost", image="maps/ghost.png")
        response = self.get_image(arena_map.pk)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["errors"][0]["code"], "not_found")

    def test_anonymous_is_401(self) -> None:
        response = self.get_image(make_image_map().pk, api=APIClient())
        self.assertEqual(response.status_code, 401)


class TestMapsListSeedsDefaults(TempMediaMixin, TestCase):
    """ANG-06h -- a fresh database lists the default maps through the API,
    without first visiting the HTML ``/maps/`` list."""

    def test_empty_db_list_returns_the_defaults(self) -> None:
        api = APIClient()
        api.force_login(get_shared_manager())
        body = api.get("/api/maps/").json()
        self.assertEqual(
            {row["name"] for row in body["data"]},
            {"Syracuse Laser Tag", "San Marcos Laser Tag"},
        )
        self.assertTrue(all(row["is_default"] for row in body["data"]))
        self.assertEqual(body["meta"]["count"], 2)

    def test_html_list_on_empty_db_still_seeds(self) -> None:
        self.client.force_login(get_shared_manager())
        self.client.get("/maps/")
        self.assertEqual(ArenaMap.objects.filter(is_default=True).count(), 2)

    def test_anonymous_list_seeds_nothing(self) -> None:
        response = APIClient().get("/api/maps/")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(ArenaMap.objects.exists())
