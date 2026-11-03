"""LFS-01e: the ``lfstats_calibrate`` management command.

Tiny synthetic Training / Test files and the synthetic map fixture from
``test_lfstats_calibration``: CI never asserts a real LFstats number.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import ArenaMap
from matches import lfstats_calibration as cal
from matches.management.commands import lfstats_calibrate
from matches.sim_helpers.map_context import MapContext
from matches.tests.test_lfstats_calibration import (
    _fixture_dict,
    _row,
    _three_game_set,
    _train,
)
from teams.lfstats_formulas import DEFAULT_CONSTANTS
from teams.models import Player, Team


def _write(path: Path, data: dict) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.fixture
def files(tmp_path: Path) -> dict[str, str]:
    train, test = _three_game_set()
    train["rows"]["all"]["h1|heavy"] = _row("heavy", games=30)
    return {
        "train": _write(tmp_path / "train.json", train),
        "test": _write(tmp_path / "test.json", test),
        "map_fixture": _write(tmp_path / "map.json", _fixture_dict()),
    }


def _counts() -> tuple[int, int, int]:
    return ArenaMap.objects.count(), Team.objects.count(), Player.objects.count()


def _run(*args: str, **options: object) -> str:
    out = StringIO()
    call_command("lfstats_calibrate", *args, stdout=out, **options)
    return out.getvalue()


@pytest.mark.django_db
class TestTestSplit:
    def test_prints_coverage_and_a_table_and_leaves_no_rows(
        self, files: dict[str, str]
    ) -> None:
        before = _counts()
        out = _run("--split", "test", "--rounds", "1", **files)
        assert "Coverage: 1 of 3 games used, 1 skipped (lineup), 1 skipped" in out
        assert "Match level: 1 games" in out
        assert "Metric" in out and "Sim" in out and "Real" in out and "Pass" in out
        assert "commander (n=2)" in out
        assert _counts() == before

    def test_output_is_ascii(self, files: dict[str, str]) -> None:
        out = _run("--split", "test", "--rounds", "1", **files)
        out.encode("ascii")


@pytest.mark.django_db
class TestTrainSplit:
    def test_prints_the_table_and_objective(
        self, files: dict[str, str], tmp_path: Path
    ) -> None:
        one = _write(tmp_path / "one.json", _train({"h1|heavy": _row("heavy")}))
        before = _counts()
        out = _run("--split", "train", "--rounds", "1", **dict(files, train=one))
        assert "heavy (n=1)" in out
        assert "commander" not in out
        assert "Objective: " in out
        assert "Coverage" not in out
        assert _counts() == before

    def test_constant_flags_and_options_reach_the_service(
        self, files: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = []

        def stub(
            train: dict, movement_ctx: object, **kwargs: object
        ) -> cal.TrainReport:
            calls.append((train, movement_ctx, kwargs))
            return cal.TrainReport(cells=[], objective=0.25)

        monkeypatch.setattr(cal, "run_train", stub)
        _run(
            "--split",
            "train",
            "--rounds",
            "2",
            "--seed",
            "9",
            "--workers",
            "3",
            "--accuracy-offset",
            "-20",
            "--preferred-role-boost",
            "1.1",
            **files,
        )
        [(train, movement_ctx, kwargs)] = calls
        assert "h1|heavy" in train["rows"]["all"]
        assert isinstance(movement_ctx, MapContext)
        assert movement_ctx.spawn_cells
        assert kwargs == {
            "constants": cal.ImportConstants(
                accuracy_offset=-20.0, preferred_role_boost=1.1
            ),
            "rounds": 2,
            "seed": 9,
            "workers": 3,
        }


@pytest.mark.django_db
class TestSearchSplit:
    @pytest.fixture(autouse=True)
    def _no_real_search(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A real search takes hours: a mutant that slips past a guard must hit
        # this stub, not the simulator (LFS-01 retro). Tests override it.
        def stub(
            train: dict, movement_ctx: object, **kwargs: object
        ) -> cal.TrainReport:
            return cal.TrainReport(cells=[], objective=1.0)

        monkeypatch.setattr(cal, "run_train", stub)

    def test_searches_the_default_grid_and_prints_the_result(
        self, files: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = []

        def stub(
            train: dict,
            movement_ctx: object,
            *,
            constants: cal.ImportConstants,
            rounds: int,
            seed: int,
            workers: int,
        ) -> cal.TrainReport:
            seen.append(constants)
            score = abs(constants.accuracy_offset + 10)
            return cal.TrainReport(cells=[], objective=score)

        monkeypatch.setattr(cal, "run_train", stub)
        out = _run("--split", "search", "--rounds", "1", "--log-scale", "35", **files)
        assert seen[0] == cal.ImportConstants(log_scale=35.0)
        for constants in seen:
            for name, values in lfstats_calibrate.DEFAULT_GRID.items():
                assert getattr(constants, name) in values
        # The chosen constants are re-run for the table, then printed.
        assert seen[-1] == cal.ImportConstants(log_scale=35.0, accuracy_offset=-10.0)
        assert "accuracy_offset" in out and "Chosen" in out
        assert "Objective: 0.0000" in out
        assert "(start)" in out and "Kept" in out
        # Progress lines stream while the search runs (decision: a Syracuse
        # search takes hours), one per scored candidate.
        assert "step pass 0 start objective 10.0000" in out
        assert "step pass 1 accuracy_offset=-10 objective 0.0000 kept" in out

    def test_search_fields_narrows_the_grid(
        self, files: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[cal.ImportConstants] = []

        def stub(
            train: dict,
            movement_ctx: object,
            *,
            constants: cal.ImportConstants,
            **kwargs: object,
        ) -> cal.TrainReport:
            seen.append(constants)
            return cal.TrainReport(cells=[], objective=1.0)

        monkeypatch.setattr(cal, "run_train", stub)
        _run(
            "--split",
            "search",
            "--rounds",
            "1",
            "--search-fields",
            "accuracy_offset,preferred_role_boost",
            **files,
        )
        for constants in seen:
            assert constants.log_scale == DEFAULT_CONSTANTS.log_scale
            assert constants.accuracy_scale == DEFAULT_CONSTANTS.accuracy_scale
        assert {c.accuracy_offset for c in seen} == {-20.0, -10.0, 0.0}
        assert {c.preferred_role_boost for c in seen} == {1.0, 1.1, 1.2}

    def test_unknown_search_field_is_a_command_error(
        self, files: dict[str, str]
    ) -> None:
        with pytest.raises(CommandError, match="bogus"):
            _run("--split", "search", "--search-fields", "bogus", **files)

    def test_default_grid_is_three_values_around_each_default(self) -> None:
        assert lfstats_calibrate.DEFAULT_GRID == {
            "log_scale": (15.0, 25.0, 35.0),
            "accuracy_offset": (-20.0, -10.0, 0.0),
            "accuracy_scale": (80.0, 100.0, 120.0),
            "survival_times_hit_weight": (0.25, 0.5, 0.75),
            "preferred_role_boost": (1.0, 1.1, 1.2),
        }
        for name, values in lfstats_calibrate.DEFAULT_GRID.items():
            assert getattr(DEFAULT_CONSTANTS, name) in values


@pytest.mark.django_db
class TestErrors:
    def test_missing_split_is_a_command_error(self, files: dict[str, str]) -> None:
        with pytest.raises(CommandError, match="--split"):
            _run(**files)

    def test_missing_fixture_names_the_path(
        self, files: dict[str, str], tmp_path: Path
    ) -> None:
        missing = str(tmp_path / "nowhere" / "syracuse.json")
        with pytest.raises(CommandError, match="nowhere"):
            _run("--split", "test", "--rounds", "1", **dict(files, map_fixture=missing))

    def test_default_fixture_is_syracuse(self) -> None:
        assert lfstats_calibrate.DEFAULT_MAP_FIXTURE.parts[-3:] == (
            "data",
            "maps",
            "syracuse.json",
        )
        assert lfstats_calibrate.DEFAULT_TRAIN.name == "lfstats_train_2026-10-21.json"

    def test_missing_train_file_names_the_path(
        self, files: dict[str, str], tmp_path: Path
    ) -> None:
        missing = str(tmp_path / "absent_train.json")
        with pytest.raises(CommandError, match="absent_train"):
            _run("--split", "train", **dict(files, train=missing))

    def test_broken_fixture_is_a_command_error_and_rolls_back(
        self, files: dict[str, str], tmp_path: Path
    ) -> None:
        data = _fixture_dict()
        del data["sight_data"]
        broken = _write(tmp_path / "broken.json", data)
        before = _counts()
        with pytest.raises(CommandError, match="sight_data"):
            _run("--split", "test", "--rounds", "1", **dict(files, map_fixture=broken))
        assert _counts() == before
