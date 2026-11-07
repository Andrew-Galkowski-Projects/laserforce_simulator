"""Export a configured map as an LFS-01 map fixture (see docs/lfstats-import.md).

Usage:
    python scripts/lfstats_import/export_map_fixture.py <map name> <zone size> <out.json>

Writes the map's confirmed zone config, bases, sight lines, base sight lines,
cell ranking and Heavy strong spots at one zone size, in the format
``matches.lfstats_calibration.load_map_fixture`` reads. The Calibration harness
replays the Test set on ``data/maps/syracuse.json``, written by this script from
the owner-approved Syracuse configuration, so the result reproduces on any
database. Runs against whichever database ``DATABASE_URL`` selects.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def dump_map_fixture(arena_map: Any, zone_size: int) -> dict[str, Any]:
    """``arena_map``'s configs at ``zone_size`` as a fixture dict."""
    from core.models import (
        BaseSightLineConfig,
        HeavyStrongSpotsConfig,
        MapBaseConfig,
        MapCellRankingConfig,
        MapZoneConfig,
        SightLineConfig,
    )

    zone = (
        MapZoneConfig.objects.filter(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    if zone is None:
        raise ValueError(f"{arena_map.name} has no confirmed {zone_size}px config")
    fixture: dict[str, Any] = {
        "name": arena_map.name,
        "image": arena_map.image.name if arena_map.image else "",
        "img_width": arena_map.img_width,
        "img_height": arena_map.img_height,
        # MOVE-08: the drawing scale; a square is zone_size x feet_per_pixel feet.
        "feet_per_pixel": arena_map.feet_per_pixel,
        "zone_size": zone_size,
        "zone_data": zone.zone_data,
        "bases": [
            {"base_type": b.base_type, "x_px": b.x_px, "y_px": b.y_px}
            for b in MapBaseConfig.objects.filter(arena_map=arena_map).order_by(
                "base_type"
            )
        ],
        "sight_data": SightLineConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size
        ).sight_data,
        "base_sight_lines": [
            {"base_type": b.base_type, "visible_cells": b.visible_cells}
            for b in BaseSightLineConfig.objects.filter(
                arena_map=arena_map, zone_size=zone_size
            ).order_by("base_type")
        ],
    }
    ranking = MapCellRankingConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()
    if ranking is not None:
        fixture["cell_ranking"] = ranking.ranked_cells
    spots = HeavyStrongSpotsConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()
    if spots is not None:
        fixture["strong_spots"] = spots.cells
    return fixture


def main(argv: list[str]) -> None:
    sys.path.insert(
        0, str(Path(__file__).resolve().parents[2] / "laserforce_simulator")
    )
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "laserforce_simulator.settings")
    import django

    django.setup()
    from core.models import ArenaMap

    fixture = dump_map_fixture(ArenaMap.objects.get(name=argv[1]), int(argv[2]))
    with open(argv[3], "w", encoding="utf-8") as f:
        json.dump(fixture, f, sort_keys=True)
        f.write("\n")
    print(
        f"{fixture['name']}: zone size {fixture['zone_size']}, "
        f"{len(fixture['bases'])} bases, {len(fixture['sight_data'])} sight-line cells"
    )


if __name__ == "__main__":
    main(sys.argv)
