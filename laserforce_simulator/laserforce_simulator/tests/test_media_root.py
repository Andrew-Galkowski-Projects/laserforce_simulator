"""TEST-01: no test writes into the real ``MEDIA_ROOT``.

The root conftest's ``pytest_configure`` points ``MEDIA_ROOT`` at a fresh temp
dir per xdist worker, so a test that uploads a map without ``TempMediaMixin``
still leaves ``laserforce_simulator/media/`` untouched.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import SimpleTestCase

_REAL_MEDIA_ROOT = (Path(settings.BASE_DIR) / "media").resolve()


class TestSessionMediaRoot(SimpleTestCase):
    def test_media_root_is_a_temp_path_not_the_real_media_dir(self) -> None:
        media_root = settings.MEDIA_ROOT
        self.assertIsInstance(media_root, Path)
        self.assertNotEqual(media_root.resolve(), _REAL_MEDIA_ROOT)
        self.assertTrue(
            media_root.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()),
            media_root,
        )

    def test_default_storage_writes_under_the_temp_media_root(self) -> None:
        name = default_storage.save("maps/test01-guard.png", ContentFile(b"png"))
        try:
            saved = Path(default_storage.path(name)).resolve()
            self.assertTrue(saved.is_relative_to(settings.MEDIA_ROOT.resolve()))
            self.assertFalse(saved.is_relative_to(_REAL_MEDIA_ROOT), saved)
        finally:
            default_storage.delete(name)
