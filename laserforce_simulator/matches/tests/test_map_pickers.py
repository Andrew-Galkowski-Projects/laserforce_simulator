"""UXP-10 — simulation map pickers offer only simulation-ready maps.

A map is simulation-ready when it has a confirmed ``MapZoneConfig`` AND both a
red and a blue ``MapBaseConfig`` (``load_map_context`` refuses anything else at
simulate time). Every form that feeds a simulation draws its map choices from
``_maps_with_confirmed_config``.
"""

import pytest

from core.models import ArenaMap, MapBaseConfig, MapZoneConfig
from matches.forms import (
    BatchSimulateForm,
    CreateLeagueForm,
    MatchSetupForm,
    SingleRoundSetupForm,
    _maps_with_confirmed_config,
)


def _make_map(name: str, base_types: tuple[str, ...]) -> ArenaMap:
    arena_map = ArenaMap.objects.create(name=name, img_width=100, img_height=100)
    MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=50,
        zone_data={"zones": [[1, 1], [1, 1]]},
        confirmed=True,
    )
    for base_type in base_types:
        MapBaseConfig.objects.create(
            arena_map=arena_map, base_type=base_type, x_px=25, y_px=25
        )
    return arena_map


@pytest.mark.django_db
class TestSimulationReadyMaps:
    def test_single_round_picker_excludes_map_without_bases(self) -> None:
        no_bases = _make_map("No Bases", ())
        ready = _make_map("Ready", ("red", "blue"))

        choices = list(SingleRoundSetupForm().fields["arena_map"].queryset)

        assert ready in choices
        assert no_bases not in choices

    def test_map_with_only_one_team_base_is_excluded(self) -> None:
        red_only = _make_map("Red Only", ("red", "neutral_1"))
        blue_only = _make_map("Blue Only", ("blue",))

        ready_ids = set(_maps_with_confirmed_config().values_list("id", flat=True))

        assert red_only.id not in ready_ids
        assert blue_only.id not in ready_ids

    def test_map_with_bases_but_no_confirmed_config_is_excluded(self) -> None:
        arena_map = ArenaMap.objects.create(name="Draft", img_width=10, img_height=10)
        for base_type in ("red", "blue"):
            MapBaseConfig.objects.create(
                arena_map=arena_map, base_type=base_type, x_px=5, y_px=5
            )

        assert arena_map not in _maps_with_confirmed_config()

    def test_ready_map_listed_once(self) -> None:
        ready = _make_map("Ready", ("red", "blue", "neutral_1"))
        MapZoneConfig.objects.create(
            arena_map=ready,
            zone_size=25,
            zone_data={"zones": [[1]]},
            confirmed=True,
        )

        assert list(_maps_with_confirmed_config()) == [ready]

    def test_every_simulation_picker_excludes_map_without_bases(self) -> None:
        no_bases = _make_map("No Bases", ())
        ready = _make_map("Ready", ("red", "blue"))

        querysets = [
            MatchSetupForm().fields["arena_map"].queryset,
            BatchSimulateForm().fields["arena_map"].queryset,
            CreateLeagueForm().fields["map_pool"].queryset,
        ]

        for queryset in querysets:
            assert ready in queryset
            assert no_bases not in queryset
