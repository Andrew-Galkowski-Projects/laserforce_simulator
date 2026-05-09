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
