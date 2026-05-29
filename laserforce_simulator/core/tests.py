import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image as PILImage

from .views import _get_image_local_path, _seed_defaults


class GetImageLocalPathTests(TestCase):
    def test_returns_path_for_local_storage(self) -> None:
        mock_field = MagicMock()
        mock_field.path = "/some/local/path.png"
        self.assertEqual(_get_image_local_path(mock_field), "/some/local/path.png")

    def test_downloads_to_cache_for_remote_storage(self) -> None:
        test_content = b"fake image bytes"
        mock_field = MagicMock()
        type(mock_field).path = PropertyMock(side_effect=NotImplementedError)
        mock_field.name = "maps/test_image.png"

        mock_file = MagicMock()
        mock_file.read.return_value = test_content
        mock_field.open.return_value.__enter__ = lambda s: mock_file
        mock_field.open.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.settings(MEDIA_ROOT=Path(tmpdir)):
                path = _get_image_local_path(mock_field)
                self.assertTrue(Path(path).exists())
                self.assertEqual(Path(path).read_bytes(), test_content)

    def test_reuses_cached_file_on_second_call(self) -> None:
        test_content = b"cached image bytes"
        mock_field = MagicMock()
        type(mock_field).path = PropertyMock(side_effect=NotImplementedError)
        mock_field.name = "maps/cached_image.png"

        mock_file = MagicMock()
        mock_file.read.return_value = test_content
        mock_field.open.return_value.__enter__ = lambda s: mock_file
        mock_field.open.return_value.__exit__ = MagicMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.settings(MEDIA_ROOT=Path(tmpdir)):
                _get_image_local_path(mock_field)
                _get_image_local_path(mock_field)
                mock_field.open.assert_called_once()


class SeedDefaultsTests(TestCase):
    @patch("django.core.files.storage.default_storage", new=MagicMock())
    def test_skips_seeding_for_remote_storage(self) -> None:
        from .models import ArenaMap

        _seed_defaults()
        self.assertEqual(ArenaMap.objects.count(), 0)


class UploadMapViewTests(TestCase):
    def _make_png(self, width: int = 20, height: int = 15) -> bytes:
        buf = io.BytesIO()
        PILImage.new("RGB", (width, height), color=(0, 128, 255)).save(
            buf, format="PNG"
        )
        return buf.getvalue()

    def test_upload_stores_image_dimensions(self) -> None:
        from .models import ArenaMap

        png_bytes = self._make_png(width=120, height=90)
        response = self.client.post(
            "/maps/upload/",
            {
                "name": "Test Map",
                "image": SimpleUploadedFile(
                    "test.png", png_bytes, content_type="image/png"
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        arena_map = ArenaMap.objects.filter(name="Test Map").first()
        self.assertIsNotNone(arena_map)
        self.assertEqual(arena_map.img_width, 120)
        self.assertEqual(arena_map.img_height, 90)

    def test_upload_rejects_invalid_image(self) -> None:
        from .models import ArenaMap

        response = self.client.post(
            "/maps/upload/",
            {
                "name": "Bad Map",
                "image": SimpleUploadedFile(
                    "bad.png", b"not an image", content_type="image/png"
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ArenaMap.objects.filter(name="Bad Map").exists())


class DeleteMapViewTests(TestCase):
    def _make_map(self, name="ToDelete"):
        from .models import ArenaMap

        png = io.BytesIO()
        PILImage.new("RGB", (20, 15), color=(0, 128, 255)).save(png, format="PNG")
        return ArenaMap.objects.create(
            name=name,
            image=SimpleUploadedFile(
                "del.png", png.getvalue(), content_type="image/png"
            ),
            img_width=20,
            img_height=15,
        )

    def test_post_deletes_map_and_redirects_to_list(self) -> None:
        from .models import ArenaMap

        arena_map = self._make_map()
        response = self.client.post(f"/maps/{arena_map.pk}/delete/")
        self.assertRedirects(response, "/maps/")
        self.assertFalse(ArenaMap.objects.filter(pk=arena_map.pk).exists())

    def test_delete_cascades_related_configs(self) -> None:
        from .models import ArenaMap, MapBaseConfig, MapZoneConfig

        arena_map = self._make_map("WithConfig")
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=50,
            zone_data=[[1, 1], [1, 1]],
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type="red", x_px=10, y_px=10
        )

        self.client.post(f"/maps/{arena_map.pk}/delete/")

        self.assertFalse(ArenaMap.objects.filter(pk=arena_map.pk).exists())
        self.assertFalse(
            MapZoneConfig.objects.filter(arena_map_id=arena_map.pk).exists()
        )
        self.assertFalse(
            MapBaseConfig.objects.filter(arena_map_id=arena_map.pk).exists()
        )

    def test_get_is_not_allowed(self) -> None:
        from .models import ArenaMap

        arena_map = self._make_map()
        response = self.client.get(f"/maps/{arena_map.pk}/delete/")
        self.assertEqual(response.status_code, 405)
        self.assertTrue(ArenaMap.objects.filter(pk=arena_map.pk).exists())

    def test_delete_nonexistent_map_returns_404(self) -> None:
        response = self.client.post("/maps/999999/delete/")
        self.assertEqual(response.status_code, 404)

    def test_list_page_has_delete_button(self) -> None:
        arena_map = self._make_map("Listed")
        response = self.client.get("/maps/")
        self.assertContains(response, f"/maps/{arena_map.pk}/delete/")


class MapListUploadFormTests(TestCase):
    """MP-1: the upload form's text input must carry an autocomplete attribute
    so the page does not trip the "element doesn't have an autocomplete
    attribute" a11y issue.
    """

    def test_map_name_input_has_autocomplete_attribute(self) -> None:
        response = self.client.get("/maps/")
        self.assertContains(response, 'id="map-name"')
        # The text input must declare autocomplete (a map name is not
        # autofillable user data, so "off" is the right value).
        self.assertContains(response, 'autocomplete="off"')


# ---------------------------------------------------------------------------
# LG-01a — Landing view tests (mode picker + in-progress Leagues)
# ---------------------------------------------------------------------------

import re
from datetime import date

from django.urls import reverse


class TestLandingView(TestCase):
    """LG-01a — ``core.views.landing`` mode-picker landing page.

    Pinned by the LG-01a seam contract; DOM ids and link semantics are
    locked. The view is read-only and renders one of two states:
    (a) empty in-progress section (no active Leagues), or
    (b) one card per active League sorted ``-id``.
    """

    def _make_active_league(self, name: str = "Active League"):
        from matches.models import League

        return League.objects.create(name=name, state="active")

    def _make_archived_league(self, name: str = "Archived League"):
        from matches.models import League

        return League.objects.create(name=name, state="archived")

    def _attach_season(self, league, name: str = "Season 1"):
        from matches.models import Season

        return Season.objects.create(
            league=league, name=name, start_date=date(2026, 1, 1)
        )

    def test_landing_get_returns_200_with_default_context(self) -> None:
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("in_progress_leagues", response.context)
        # Iterable — list / queryset both work for the template loop.
        iter(response.context["in_progress_leagues"])

    def test_landing_renders_three_mode_card_dom_ids(self) -> None:
        response = self.client.get(reverse("landing"))
        self.assertContains(response, 'id="mode-card-sandbox"')
        self.assertContains(response, 'id="mode-card-league"')
        self.assertContains(response, 'id="mode-card-multiplayer"')

    def test_landing_sandbox_card_links_to_team_list(self) -> None:
        response = self.client.get(reverse("landing"))
        body = response.content.decode()
        match = re.search(r'<a[^>]*id="mode-card-sandbox"[^>]*href="([^"]+)"', body)
        # Fallback: href may come before id attribute.
        if match is None:
            match = re.search(r'<a[^>]*href="([^"]+)"[^>]*id="mode-card-sandbox"', body)
        self.assertIsNotNone(
            match, "mode-card-sandbox anchor with href not found in body"
        )
        self.assertEqual(match.group(1), reverse("team_list"))

    def test_landing_league_card_links_to_league_list(self) -> None:
        response = self.client.get(reverse("landing"))
        body = response.content.decode()
        match = re.search(r'<a[^>]*id="mode-card-league"[^>]*href="([^"]+)"', body)
        if match is None:
            match = re.search(r'<a[^>]*href="([^"]+)"[^>]*id="mode-card-league"', body)
        self.assertIsNotNone(
            match, "mode-card-league anchor with href not found in body"
        )
        self.assertEqual(match.group(1), reverse("league_list"))

    def test_landing_multiplayer_card_is_non_anchor_with_coming_soon_badge(
        self,
    ) -> None:
        response = self.client.get(reverse("landing"))
        body = response.content.decode()
        # (a) DOM id present.
        self.assertIn('id="mode-card-multiplayer"', body)
        # (b) NOT wrapped in <a> — no <a ... id="mode-card-multiplayer">.
        self.assertIsNone(
            re.search(r'<a[^>]*id="mode-card-multiplayer"', body),
            "mode-card-multiplayer must not be an anchor",
        )
        # (c) Coming soon badge text present.
        self.assertIn("Coming soon", body)
        # (d) aria-disabled="true" on the multiplayer card.
        self.assertIn('aria-disabled="true"', body)

    def test_landing_omits_in_progress_section_when_no_active_leagues(
        self,
    ) -> None:
        response = self.client.get(reverse("landing"))
        self.assertNotContains(response, 'id="in-progress-leagues"')

    def test_landing_lists_active_leagues_as_cards_sorted_by_id_desc(
        self,
    ) -> None:
        l1 = self._make_active_league("League One")
        l2 = self._make_active_league("League Two")
        response = self.client.get(reverse("landing"))
        body = response.content.decode()
        id1 = f'id="in-progress-league-card-{l1.id}"'
        id2 = f'id="in-progress-league-card-{l2.id}"'
        self.assertIn(id1, body)
        self.assertIn(id2, body)
        # Sorted by -id: L2 appears before L1.
        self.assertLess(body.index(id2), body.index(id1))

    def test_landing_in_progress_card_links_to_deferred_league_detail_url(
        self,
    ) -> None:
        league = self._make_active_league("Linked League")
        response = self.client.get(reverse("landing"))
        # Deferred broken link to LG-01c — raw URL string.
        self.assertContains(response, f'href="/leagues/{league.id}/"')

    def test_landing_in_progress_card_shows_active_season_name_when_present(
        self,
    ) -> None:
        league = self._make_active_league("With Season")
        self._attach_season(league, name="Season 1")
        response = self.client.get(reverse("landing"))
        self.assertContains(response, "Season: Season 1")

    def test_landing_in_progress_card_shows_no_active_season_subtitle_when_absent(
        self,
    ) -> None:
        self._make_active_league("Seasonless")
        response = self.client.get(reverse("landing"))
        self.assertContains(response, "No active season")

    def test_landing_excludes_archived_leagues_from_in_progress_section(
        self,
    ) -> None:
        la = self._make_active_league("Active LA")
        lz = self._make_archived_league("Archived LZ")
        response = self.client.get(reverse("landing"))
        body = response.content.decode()
        self.assertIn(f'id="in-progress-league-card-{la.id}"', body)
        self.assertNotIn(f'id="in-progress-league-card-{lz.id}"', body)

    def test_root_url_reverses_to_landing_view(self) -> None:
        self.assertEqual(reverse("landing"), "/")

    def test_base_html_navbar_brand_links_to_landing(
        self,
    ) -> None:
        """Navbar regression — brand href must be ``/`` on any view
        extending ``base.html``. LG-01k retired ``leagues-nav-link``
        (replaced by ``league-nav-link`` in league mode only); the
        landing page is start mode and renders neither.
        """
        response = self.client.get(reverse("landing"))
        body = response.content.decode()
        # Brand href is "/" — parse the navbar-brand anchor.
        match = re.search(r'class="navbar-brand"[^>]*href="([^"]+)"', body)
        if match is None:
            # Fallback: href may come before class attribute.
            match = re.search(r'href="([^"]+)"[^>]*class="navbar-brand"', body)
        self.assertIsNotNone(match, "navbar-brand anchor with href not found in body")
        self.assertEqual(match.group(1), "/")
