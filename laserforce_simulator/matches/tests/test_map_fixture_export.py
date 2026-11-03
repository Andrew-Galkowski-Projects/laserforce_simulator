"""LFS-01f: the Syracuse map fixture and the script that exports it.

``scripts/lfstats_import/export_map_fixture.py`` writes a configured map in
the format ``matches.lfstats_calibration.load_map_fixture`` reads; the
committed ``data/maps/syracuse.json`` is its output for the owner-approved
30px Syracuse configuration (PLAN.md LFS-01 decision 30).
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
def test_committed_syracuse_fixture_resolves_to_a_map_context() -> None:
    arena_map = load_map_fixture(SYRACUSE)

    ctx, zone_size = load_map_context(arena_map)

    assert zone_size == 30
    assert arena_map.name == "Syracuse Laser Tag"
    pools = ctx.get_team_spawn_pools()
    assert pools["red"] and pools["blue"]
