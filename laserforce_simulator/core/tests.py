import io
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image as PILImage

from .services import _get_image_local_path, _seed_defaults


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


# ---------------------------------------------------------------------------
# INFRA-01 / ADR-0025 — SQLite/Postgres parity hardening
# ---------------------------------------------------------------------------

from .db_pragmas import set_sqlite_pragmas


class Infra01SqlitePragmaGuardTests(TestCase):
    """INFRA-01 / ADR-0025 — vendor guard on the SQLite PRAGMA receiver.

    Postgres is the canonical backend; the ``connection_created`` receiver
    that issues WAL/synchronous PRAGMAs must early-return on any non-SQLite
    connection without ever opening a cursor (a PRAGMA against Postgres would
    error). This is a pure unit test — it never touches the database and runs
    identically on whichever backend the suite is configured for.
    """

    def test_sqlite_pragmas_skipped_on_non_sqlite_connection(self) -> None:
        fake_connection = MagicMock()
        fake_connection.vendor = "postgresql"

        set_sqlite_pragmas(sender=None, connection=fake_connection)

        # The vendor guard must short-circuit before any cursor is opened.
        fake_connection.cursor.assert_not_called()


class Infra01JsonFieldParityTests(TestCase):
    """INFRA-01 / ADR-0025 — JSONField round-trip parity across backends.

    SQLite stores ``JSONField`` as text while Postgres uses native ``jsonb``;
    this DB test proves a nested ``MapZoneConfig.zone_data`` payload survives a
    save + ``refresh_from_db()`` deep-equal on whichever backend is active, so
    the SQLite-canonical fixtures and the Postgres-canonical production schema
    cannot silently diverge.
    """

    def _make_map(self, name: str = "ParityMap"):
        from .models import ArenaMap

        png = io.BytesIO()
        PILImage.new("RGB", (20, 15), color=(0, 128, 255)).save(png, format="PNG")
        return ArenaMap.objects.create(
            name=name,
            image=SimpleUploadedFile(
                "parity.png", png.getvalue(), content_type="image/png"
            ),
            img_width=20,
            img_height=15,
        )

    def test_jsonfield_roundtrips_through_active_backend(self) -> None:
        from .models import MapZoneConfig

        zone_data = {
            "zones": [[1, 0, 1], [4, 1, 5]],
            "wall_meta": {"0,1": {"facing": "N", "height": 2.0}},
            "elevation": [[0.0, 1.5, 0.0], [2.25, 0.0, 1.0]],
        }

        arena_map = self._make_map()
        config = MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=20,
            zone_data=zone_data,
        )

        config.refresh_from_db()

        self.assertEqual(
            config.zone_data,
            {
                "zones": [[1, 0, 1], [4, 1, 5]],
                "wall_meta": {"0,1": {"facing": "N", "height": 2.0}},
                "elevation": [[0.0, 1.5, 0.0], [2.25, 0.0, 1.0]],
            },
        )


class ServeMediaTests(TestCase):
    """UXP-02 — with DEBUG off and no R2, nothing served ``/media/``, so every
    map image 404'd on the self-hosted stack. ``SERVE_MEDIA`` opts Django in."""

    def setUp(self) -> None:
        self.media_root = Path(tempfile.mkdtemp())
        (self.media_root / "maps").mkdir()
        (self.media_root / "maps" / "arena.png").write_bytes(b"\x89PNG fake")

    def test_serves_media_file_when_enabled(self) -> None:
        from django.test import override_settings

        with override_settings(
            DEBUG=False, SERVE_MEDIA=True, MEDIA_ROOT=self.media_root
        ):
            response = self.client.get("/media/maps/arena.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"\x89PNG fake")

    def test_debug_serves_media_without_serve_media(self) -> None:
        from django.test import override_settings

        with override_settings(
            DEBUG=True, SERVE_MEDIA=False, MEDIA_ROOT=self.media_root
        ):
            response = self.client.get("/media/maps/arena.png")
        self.assertEqual(response.status_code, 200)

    def test_404_when_disabled(self) -> None:
        from django.test import override_settings

        with override_settings(
            DEBUG=False, SERVE_MEDIA=False, MEDIA_ROOT=self.media_root
        ):
            response = self.client.get("/media/maps/arena.png")
        self.assertEqual(response.status_code, 404)

    def test_no_path_traversal_outside_media_root(self) -> None:
        from django.test import override_settings

        (self.media_root / "secret.txt").write_text("nope")
        with override_settings(
            DEBUG=False, SERVE_MEDIA=True, MEDIA_ROOT=self.media_root / "maps"
        ):
            response = self.client.get("/media/%2E%2E/secret.txt")
        self.assertIn(response.status_code, (400, 404))

    def test_anonymous_request_redirects_to_admin_login(self) -> None:
        """ANG-08 (ADR-0047) - the login gate's ``LOGIN_URL`` is the Admin
        login, the only Django login page that survives the template views."""
        self.client.logout()
        response = self.client.get("/media/x")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/login/?next=/media/x")
