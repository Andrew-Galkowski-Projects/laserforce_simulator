"""Tests for the ``score_averages`` management command ``--map`` option.

The command previously always ran on the 3-zone fallback. ``--map <name>``
resolves an ``ArenaMap`` by name, builds the movement context, and runs the
batch simulation with cell-aware pathfinding instead.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from matches.tests.conftest import make_team_with_slots


def _make_arena_map(name="TestArena"):
    """A minimally-complete, confirmed map (zones, both bases, sight lines)."""
    from core.map_processing import compute_sight_lines
    from core.models import (
        ArenaMap,
        BaseSightLineConfig,
        MapBaseConfig,
        MapZoneConfig,
        SightLineConfig,
    )

    arena_map = ArenaMap.objects.create(name=name, img_width=200, img_height=200)
    zone_data = [
        [0, 2, 1, 0],
        [2, 2, 1, 3],
        [0, 1, 1, 3],
        [0, 1, 3, 3],
    ]
    MapZoneConfig.objects.create(
        arena_map=arena_map, zone_size=50, zone_data=zone_data, confirmed=True
    )
    MapBaseConfig.objects.create(arena_map=arena_map, base_type="red", x_px=25, y_px=75)
    MapBaseConfig.objects.create(
        arena_map=arena_map, base_type="blue", x_px=175, y_px=125
    )
    SightLineConfig.objects.create(
        arena_map=arena_map,
        zone_size=50,
        sight_data=compute_sight_lines(zone_data),
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="red", zone_size=50, visible_cells=[]
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="blue", zone_size=50, visible_cells=[]
    )
    return arena_map


@pytest.mark.django_db
class TestScoreAveragesMapOption:
    def _teams(self):
        red, _ = make_team_with_slots("SAMapRed")
        blue, _ = make_team_with_slots("SAMapBlue")
        return red, blue

    def test_map_flag_runs_on_named_map_and_prints_table(self):
        """Happy path: a configured map name runs and the banner names the map."""
        red, blue = self._teams()
        _make_arena_map("TestArena")
        out = StringIO()

        call_command(
            "score_averages",
            "--rounds=2",
            "--seed=1",
            f"--team-red={red.name}",
            f"--team-blue={blue.name}",
            "--map=TestArena",
            stdout=out,
        )

        output = out.getvalue()
        assert "on map 'TestArena'" in output
        assert "Role" in output
        assert "commander" in output

    def test_no_map_flag_keeps_three_zone_fallback(self):
        """Omitting --map preserves the original fallback banner (no regression)."""
        red, blue = self._teams()
        out = StringIO()

        call_command(
            "score_averages",
            "--rounds=2",
            "--seed=1",
            f"--team-red={red.name}",
            f"--team-blue={blue.name}",
            stdout=out,
        )

        output = out.getvalue()
        assert "on map" not in output
        assert "commander" in output

    def test_unknown_map_name_raises_command_error(self):
        """Failure mode: a name with no matching ArenaMap is a clean error."""
        red, blue = self._teams()

        with pytest.raises(CommandError, match="Nonexistent"):
            call_command(
                "score_averages",
                "--rounds=2",
                f"--team-red={red.name}",
                f"--team-blue={blue.name}",
                "--map=Nonexistent",
                stdout=StringIO(),
            )

    def test_map_missing_confirmed_config_raises_command_error(self):
        """Failure mode: an ArenaMap with no confirmed config errors cleanly."""
        from core.models import ArenaMap

        red, blue = self._teams()
        ArenaMap.objects.create(name="Unconfigured", img_width=200, img_height=200)

        with pytest.raises(CommandError, match="zone configuration"):
            call_command(
                "score_averages",
                "--rounds=2",
                f"--team-red={red.name}",
                f"--team-blue={blue.name}",
                "--map=Unconfigured",
                stdout=StringIO(),
            )
