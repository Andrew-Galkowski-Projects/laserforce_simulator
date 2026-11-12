"""``seed_demo``: a demo Account with one map Round, for a live site (CAL-04 retro)."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from core.models import ArenaMap, MapZoneConfig
from matches.models import GameRound
from matches.tests.test_lfstats_calibration import _fixture_dict
from teams.models import Team

EMAIL = "demo@laserforce.demo"


@pytest.fixture
def fixture_path(tmp_path: Path) -> Path:
    path = tmp_path / "arena.json"
    path.write_text(json.dumps(_fixture_dict()), encoding="utf-8")
    return path


def _seed(path: Path) -> dict[str, str]:
    out = io.StringIO()
    call_command("seed_demo", str(path), stdout=out)
    printed = dict(re.findall(r"^(\w+): (.+)$", out.getvalue(), re.MULTILINE))
    round_id, on, map_name = printed["Round"].split(" ", 2)
    assert on == "on"
    printed.update(Round=round_id, Map=map_name)
    return printed


@pytest.mark.django_db
class TestSeedDemo:
    def test_first_run_makes_the_account_its_teams_and_a_map_round(
        self, fixture_path: Path
    ) -> None:
        printed = _seed(fixture_path)
        demo = get_user_model().objects.get(email=EMAIL)
        assert printed["Email"] == EMAIL
        assert demo.check_password(printed["Password"])
        assert Team.objects.filter(manager=demo).count() == 2
        game_round = GameRound.objects.get(pk=int(printed["Round"]))
        assert game_round.manager == demo
        assert game_round.arena_map is not None
        assert game_round.arena_map.name == _fixture_dict()["name"]

    def test_second_run_reuses_the_map_and_teams_and_resets_the_password(
        self, fixture_path: Path
    ) -> None:
        first = _seed(fixture_path)
        second = _seed(fixture_path)
        demo = get_user_model().objects.get(email=EMAIL)
        assert ArenaMap.objects.count() == 1
        assert Team.objects.filter(manager=demo).count() == 2
        assert first["Round"] != second["Round"]
        assert first["Password"] != second["Password"]
        assert demo.check_password(second["Password"])

    def test_attaches_configs_to_a_bare_map_of_the_same_name(
        self, fixture_path: Path
    ) -> None:
        bare = ArenaMap.objects.create(name=_fixture_dict()["name"])
        printed = _seed(fixture_path)
        assert ArenaMap.objects.count() == 1
        assert MapZoneConfig.objects.filter(arena_map=bare, confirmed=True).exists()
        assert GameRound.objects.get(pk=int(printed["Round"])).arena_map == bare
