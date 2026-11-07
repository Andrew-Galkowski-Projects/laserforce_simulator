"""LFS-01f: the Syracuse map fixture and the script that exports it.

``scripts/lfstats_import/export_map_fixture.py`` writes a configured map in
the format ``matches.lfstats_calibration.load_map_fixture`` reads; the
committed ``data/maps/syracuse.json`` is its output for the owner-approved
20px Syracuse configuration at 1 ft a square (PLAN.md LFS-01 decision 30, revised
2026-11-05 from the 30px / 5 ft export).
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from core.models import (
    BaseSightLineConfig,
    HeavyStrongSpotsConfig,
    MapBaseConfig,
    MapCellRankingConfig,
    MapZoneConfig,
    SightLineConfig,
)
from matches.lfstats_calibration import load_map_fixture
from matches.sim_helpers.map_loader import load_map_context
from matches.tests.test_lfstats_calibration import _fixture_dict

_REPO = Path(__file__).resolve().parents[3]
SYRACUSE = _REPO / "data" / "maps" / "syracuse.json"


def _load_script() -> ModuleType:
    path = _REPO / "scripts" / "lfstats_import" / "export_map_fixture.py"
    spec = importlib.util.spec_from_file_location("lfstats_export_map", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


export = _load_script()


def _configs(arena_map, zone_size: int) -> dict:
    """Every config row of ``arena_map`` as plain data, for comparison."""
    return {
        "zone_data": MapZoneConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size, confirmed=True
        ).zone_data,
        "bases": sorted(
            MapBaseConfig.objects.filter(arena_map=arena_map).values_list(
                "base_type", "x_px", "y_px"
            )
        ),
        "sight": SightLineConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size
        ).sight_data,
        "base_sights": sorted(
            (b.base_type, b.visible_cells)
            for b in BaseSightLineConfig.objects.filter(
                arena_map=arena_map, zone_size=zone_size
            )
        ),
        "ranking": MapCellRankingConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size
        ).ranked_cells,
        "strong": HeavyStrongSpotsConfig.objects.get(
            arena_map=arena_map, zone_size=zone_size
        ).cells,
    }


@pytest.mark.django_db
def test_export_round_trips_through_load_map_fixture(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps(_fixture_dict()), encoding="utf-8")
    original = load_map_fixture(source)
    zone_size = _fixture_dict()["zone_size"]

    dumped = tmp_path / "dumped.json"
    dumped.write_text(
        json.dumps(export.dump_map_fixture(original, zone_size)), encoding="utf-8"
    )
    copy = load_map_fixture(dumped)

    assert copy.pk != original.pk
    assert _configs(copy, zone_size) == _configs(original, zone_size)


@pytest.mark.django_db
def test_export_reads_only_the_requested_zone_size(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps(_fixture_dict()), encoding="utf-8")
    original = load_map_fixture(source)
    zone_size = _fixture_dict()["zone_size"]
    SightLineConfig.objects.create(
        arena_map=original, zone_size=zone_size + 10, sight_data={"0,0": []}
    )

    dumped = export.dump_map_fixture(original, zone_size)

    assert dumped["zone_size"] == zone_size
    assert dumped["sight_data"] == _fixture_dict()["sight_data"]


@pytest.mark.django_db
class TestFixtureCarriesTheMapScale:
    """The fixture carries ``ArenaMap.feet_per_pixel``: a map's drawing scale is as
    much a part of its configuration as its grid (Syracuse is 1 ft per 20 px, not the
    1/6 default), and the Calibration harness reads the fixture, not the database."""

    def test_export_writes_feet_per_pixel(self, tmp_path: Path) -> None:
        source = tmp_path / "source.json"
        source.write_text(json.dumps(_fixture_dict()), encoding="utf-8")
        original = load_map_fixture(source)
        original.feet_per_pixel = 0.05
        original.save()

        dumped = export.dump_map_fixture(original, _fixture_dict()["zone_size"])

        assert dumped["feet_per_pixel"] == pytest.approx(0.05)

    def test_load_reads_feet_per_pixel(self, tmp_path: Path) -> None:
        source = tmp_path / "source.json"
        source.write_text(
            json.dumps({**_fixture_dict(), "feet_per_pixel": 0.05}), encoding="utf-8"
        )

        arena_map = load_map_fixture(source)
        ctx, zone_size = load_map_context(arena_map)

        assert arena_map.feet_per_pixel == pytest.approx(0.05)
        assert ctx is not None and zone_size is not None
        assert ctx.feet_per_square == pytest.approx(zone_size * 0.05)

    def test_load_without_the_key_keeps_the_model_default(self, tmp_path: Path) -> None:
        from core.models import DEFAULT_FEET_PER_PIXEL

        source = tmp_path / "source.json"
        source.write_text(json.dumps(_fixture_dict()), encoding="utf-8")

        arena_map = load_map_fixture(source)

        assert arena_map.feet_per_pixel == pytest.approx(DEFAULT_FEET_PER_PIXEL)


@pytest.mark.django_db
def test_committed_syracuse_fixture_resolves_to_a_map_context() -> None:
    arena_map = load_map_fixture(SYRACUSE)

    ctx, zone_size = load_map_context(arena_map)

    # The owner-configured 20 px grid at 1 ft a square (20 px = 1 ft, owner-measured).
    assert zone_size == 20
    assert arena_map.feet_per_pixel == pytest.approx(0.05)
    assert arena_map.name == "Syracuse Laser Tag"
    assert ctx is not None
    assert ctx.feet_per_square == pytest.approx(1.0)
    pools = ctx.get_team_spawn_pools()
    assert pools["red"] and pools["blue"]
