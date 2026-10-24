"""ANG-01c -- shared fixtures for the map editor tests (seam contract section 8).

Test-support only (not collected: the name matches no ``python_files`` glob).
Imports nothing from ``core.services`` / ``core.forms`` so it loads even before
those modules exist.

The fixture image is a tiny real PNG -- 200x200 white with a 6px black border.
OpenCV runs for real: at ``zone_size`` 50 it detects ``GRID`` (a ring of high
walls around a 2x2 floor), whose all-pairs sight lines are ``SIGHT``.
"""

from __future__ import annotations

import io
import shutil
import tempfile
from datetime import date
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image as PILImage
from PIL import ImageDraw

from core.models import ArenaMap

ZONE_SIZE = 50

GRID: list[list[int]] = [
    [0, 0, 0, 0],
    [0, 1, 1, 0],
    [0, 1, 1, 0],
    [0, 0, 0, 0],
]

SIGHT: dict[str, list[str]] = {
    "1,1": ["1,2", "2,1", "2,2"],
    "1,2": ["1,1", "2,1", "2,2"],
    "2,1": ["1,1", "1,2", "2,2"],
    "2,2": ["1,1", "1,2", "2,1"],
}

# Base pixel positions that land on floor cells (1,1) and (2,2) at zone 50.
RED_BASE = {"type": "red", "x_px": 75, "y_px": 75}
BLUE_BASE = {"type": "blue", "x_px": 125, "y_px": 125}

ZONE_CONFIG_KEYS = {
    "zone_size",
    "zones",
    "wall_meta",
    "elevation",
    "red_spawn",
    "blue_spawn",
    "spawn_user_edited",
    "bases",
}

DETECT_KEYS = {
    "rows",
    "cols",
    "cell_size",
    "img_width",
    "img_height",
    "zones",
    "blocked_edges",
    "blocked_edges_grid",
}

HEATMAP_KEYS = ["cell_occupancy", "zone_size", "rows", "cols", "round_count"]


def png_bytes(size: int = 200, border: int = 6) -> bytes:
    """A real PNG: white square with a black border."""
    img = PILImage.new("RGB", (size, size), "white")
    ImageDraw.Draw(img).rectangle(
        [0, 0, size - 1, size - 1], outline="black", width=border
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_upload(filename: str = "arena.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(filename, png_bytes(), content_type="image/png")


def corrupt_upload(filename: str = "bad.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(filename, b"not an image", content_type="image/png")


class _MediaRootPaths:
    media_root: Path

    def processed_cache_path(self, arena_map: ArenaMap) -> Path:
        return self.media_root / "maps" / f"processed_{arena_map.pk}.png"

    def write_processed_cache(self, arena_map: ArenaMap) -> Path:
        path = self.processed_cache_path(arena_map)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes(size=10, border=1))
        return path


class TempMediaMixin(_MediaRootPaths):
    """Point ``MEDIA_ROOT`` at a fresh temp dir (a ``Path``, as the code does
    ``MEDIA_ROOT / "maps"``) for the duration of one test."""

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        self.media_root = Path(tempfile.mkdtemp(prefix="ang01c-media-"))
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(shutil.rmtree, self.media_root, True)  # type: ignore[attr-defined]
        self.addCleanup(override.disable)  # type: ignore[attr-defined]


class ClassTempMediaMixin(_MediaRootPaths):
    """``TempMediaMixin`` for a whole test class (PERF-02): the temp
    ``MEDIA_ROOT`` is set before ``setUpTestData`` runs and lasts until the
    class ends, so files a class-level fixture writes survive for every test."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.media_root = Path(tempfile.mkdtemp(prefix="perf02-media-"))
        override = override_settings(MEDIA_ROOT=cls.media_root)
        override.enable()
        cls.addClassCleanup(shutil.rmtree, cls.media_root, True)  # type: ignore[attr-defined]
        cls.addClassCleanup(override.disable)  # type: ignore[attr-defined]
        super().setUpClass()  # type: ignore[misc]


def make_image_map(name: str = "Probe Arena") -> ArenaMap:
    """An ArenaMap whose image file really exists under MEDIA_ROOT."""
    return ArenaMap.objects.create(
        name=name, image=png_upload(), img_width=200, img_height=200
    )


def make_bare_map(name: str = "Bare Arena") -> ArenaMap:
    """An ArenaMap with NO image file (service paths that never touch it)."""
    return ArenaMap.objects.create(name=name, img_width=200, img_height=200)


def make_season(state: str, *, manager=None, **fields):
    """A Season in its own League (so the one-live-Season rule never bites)."""
    from matches.models import League, Season

    league = League.objects.create(name=f"League {state}", manager=manager)
    return Season.objects.create(
        league=league,
        name=f"Season {state}",
        start_date=date(2026, 1, 1),
        state=state,
        **fields,
    )


def make_teams(prefix: str):
    from teams.models import Team

    return (
        Team.objects.create(name=f"{prefix} Red"),
        Team.objects.create(name=f"{prefix} Blue"),
    )


def make_heatmap_round(
    arena_map: ArenaMap,
    occupancy: dict,
    *,
    manager=None,
    match=None,
    prefix: str = "Heat",
    zone_size: int = ZONE_SIZE,
):
    """A GameRound with a populated ``cell_occupancy_json`` on ``arena_map``."""
    from matches.models import GameRound

    red, blue = (match.team_red, match.team_blue) if match else make_teams(prefix)
    return GameRound.objects.create(
        round_number=1,
        team_red=red,
        team_blue=blue,
        match=match,
        manager=manager,
        arena_map=arena_map,
        zone_size=zone_size,
        cell_occupancy_json=occupancy,
    )


def make_match(prefix: str, *, manager=None, season=None):
    from matches.models import Match

    red, blue = make_teams(prefix)
    return Match.objects.create(
        team_red=red, team_blue=blue, manager=manager, season=season
    )
