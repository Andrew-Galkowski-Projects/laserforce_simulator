"""LFS-01d: the Calibration harness's Test side (``matches.lfstats_calibration``).

Synthetic Train / Test dicts and a tiny fixture map only: CI never asserts a
real LFstats number (spec #514, Testing Decisions).
"""

from __future__ import annotations

import inspect
import json
import random
from collections.abc import Callable
from pathlib import Path

import pytest

from core.map_processing import compute_high_los_ranking, compute_sight_lines
from core.models import ArenaMap
from matches import lfstats_calibration as cal
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.map_loader import load_map_context
from matches.sim_helpers.parallel_worker import calibration_round_worker
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.role_constants import MAX_LIVES, MAX_SHOTS
from matches.simulation.entrypoints import _SIMULATION_STATS, _PlayerData
from teams.lfstats_formulas import DEFAULT_CONSTANTS, ImportConstants

ALL_POSITIONS = ("commander", "heavy", "scout", "ammo", "medic")
BASE_METRICS = {
    "avg_accuracy",
    "avg_hit_diff",
    "avg_times_hit",
    "avg_mvp",
    "avg_uptime_pct",
    "elimination_rate",
}
# A round that ran its full length with nobody eliminated.
NO_WIPE = {"red_eliminated": False, "blue_eliminated": False, "eliminated_at": 1801}


def _ps(role: str, team_color: str = "red", **fields: object) -> PlayerState:
    """A hand-built end-of-round PlayerState; counter names go on ``counters``."""
    state = PlayerState(
        tag_id=f"{team_color}_{role}",
        name=f"{team_color} {role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=MAX_LIVES[role],
        starting_shots=MAX_SHOTS[role],
        final_lives=MAX_LIVES[role],
        final_shots=MAX_SHOTS[role],
    )
    for name, value in fields.items():
        if hasattr(state.counters, name):
            setattr(state.counters, name, value)
        else:
            setattr(state, name, value)
    return state


class TestMetrics:
    def test_metrics_table_matches_the_spec(self) -> None:
        assert cal.METRICS == (
            ("avg_accuracy", "fraction", ALL_POSITIONS),
            ("avg_hit_diff", "count", ALL_POSITIONS),
            ("avg_times_hit", "count", ALL_POSITIONS),
            ("avg_mvp", "count", ALL_POSITIONS),
            ("avg_uptime_pct", "fraction", ALL_POSITIONS),
            ("elimination_rate", "fraction", ALL_POSITIONS),
            ("avg_resupplies_given", "count", ("ammo", "medic")),
            ("avg_nukes_detonated", "count", ("commander",)),
            ("avg_missiles_hit", "count", ("commander", "heavy")),
            ("avg_rapid_fire", "count", ("scout",)),
        )


class TestSimMetrics:
    def test_accuracy_is_tags_over_shots(self) -> None:
        m = cal.sim_metrics(_ps("heavy", tags_made=30, shots_missed=10), NO_WIPE)
        assert m["avg_accuracy"] == 0.75

    def test_zero_shot_accuracy_is_zero(self) -> None:
        m = cal.sim_metrics(_ps("heavy"), NO_WIPE)
        assert m["avg_accuracy"] == 0.0

    def test_hit_diff_is_tags_over_times_tagged(self) -> None:
        m = cal.sim_metrics(_ps("scout", tags_made=30, times_tagged=20), NO_WIPE)
        assert m["avg_hit_diff"] == 1.5

    def test_hit_diff_never_tagged_divides_by_one(self) -> None:
        m = cal.sim_metrics(_ps("scout", tags_made=7, times_tagged=0), NO_WIPE)
        assert m["avg_hit_diff"] == 7.0

    def test_times_hit_is_times_tagged(self) -> None:
        m = cal.sim_metrics(_ps("medic", times_tagged=13), NO_WIPE)
        assert m["avg_times_hit"] == 13.0

    def test_uptime_excludes_dead_time(self) -> None:
        state = _ps(
            "ammo",
            ticks_active=900,
            ticks_reset_window=300,
            ticks_not_targetable=300,
            was_eliminated_at=1500,
            final_lives=0,
        )
        assert cal.sim_metrics(state, NO_WIPE)["avg_uptime_pct"] == 0.6

    def test_uptime_with_no_ticks_is_zero(self) -> None:
        assert cal.sim_metrics(_ps("ammo"), NO_WIPE)["avg_uptime_pct"] == 0.0

    def test_eliminated_player_elimination_is_one(self) -> None:
        state = _ps("scout", was_eliminated_at=1500, final_lives=0)
        assert cal.sim_metrics(state, NO_WIPE)["elimination_rate"] == 1.0

    def test_survivor_elimination_is_zero(self) -> None:
        assert cal.sim_metrics(_ps("scout"), NO_WIPE)["elimination_rate"] == 0.0

    def test_resupplies_given(self) -> None:
        m = cal.sim_metrics(_ps("ammo", resupplies_given=12), NO_WIPE)
        assert m["avg_resupplies_given"] == 12.0

    def test_nukes_detonated_subtracts_cancels(self) -> None:
        state = _ps("commander", specials_used=3, own_specials_cancelled=1)
        assert cal.sim_metrics(state, NO_WIPE)["avg_nukes_detonated"] == 2.0

    def test_missiles_hit_is_missiles_landed(self) -> None:
        m = cal.sim_metrics(_ps("commander", missiles_landed=4), NO_WIPE)
        assert m["avg_missiles_hit"] == 4.0
        m = cal.sim_metrics(_ps("heavy", missiles_landed=5), NO_WIPE)
        assert m["avg_missiles_hit"] == 5.0

    def test_rapid_fire_is_scout_specials_used(self) -> None:
        m = cal.sim_metrics(_ps("scout", specials_used=6), NO_WIPE)
        assert m["avg_rapid_fire"] == 6.0

    @pytest.mark.parametrize("role", ALL_POSITIONS)
    def test_only_the_roles_metrics_are_present(self, role: str) -> None:
        expected = {mid for mid, _kind, positions in cal.METRICS if role in positions}
        assert set(cal.sim_metrics(_ps(role), NO_WIPE)) == expected

    def test_role_limited_metrics_absent_for_other_roles(self) -> None:
        assert set(cal.sim_metrics(_ps("scout"), NO_WIPE)) == BASE_METRICS | {
            "avg_rapid_fire"
        }
        assert set(cal.sim_metrics(_ps("heavy"), NO_WIPE)) == BASE_METRICS | {
            "avg_missiles_hit"
        }
        assert set(cal.sim_metrics(_ps("commander"), NO_WIPE)) == BASE_METRICS | {
            "avg_nukes_detonated",
            "avg_missiles_hit",
        }
        for role in ("ammo", "medic"):
            assert set(cal.sim_metrics(_ps(role), NO_WIPE)) == BASE_METRICS | {
                "avg_resupplies_given"
            }

    def _heavy(self, team_color: str) -> PlayerState:
        return _ps(
            "heavy",
            team_color,
            tags_made=30,
            shots_missed=10,
            missiles_landed=2,
            points_scored=9000,
            medic_hits=3,
            times_missiled=1,
            final_lives=0,
            was_eliminated_at=1100,
        )

    def test_mvp_without_a_wipe(self) -> None:
        # accuracy 75 -> +7.5, 2 missiles -> +4, 9000 pts -> +2, 3 medic hits
        # -> +3, missiled once -> -1, eliminated -> -1.
        assert cal.sim_metrics(self._heavy("red"), NO_WIPE)["avg_mvp"] == 14.5

    def test_mvp_reads_the_wipe_from_the_round_result(self) -> None:
        # Blue wiped at tick 1200: 600 ticks left, 240 past the 3-minute mark
        # -> 4 + 2 for every red player.
        wipe = {"red_eliminated": False, "blue_eliminated": True, "eliminated_at": 1200}
        assert cal.sim_metrics(self._heavy("red"), wipe)["avg_mvp"] == 20.5
        assert cal.sim_metrics(self._heavy("blue"), wipe)["avg_mvp"] == 14.5


ZONE_SIZE = 20
MAP_ROWS, MAP_COLS = 10, 16
RED_CELL, BLUE_CELL = (5, 2), (4, 13)


def _grid() -> list[list[int]]:
    """An open room: 1 = floor, 0 = the high-wall border and one centre pillar."""
    grid = [
        [
            0 if r in (0, MAP_ROWS - 1) or c in (0, MAP_COLS - 1) else 1
            for c in range(MAP_COLS)
        ]
        for r in range(MAP_ROWS)
    ]
    for r in (4, 5):
        for c in (7, 8):
            grid[r][c] = 0
    return grid


def _fixture_dict() -> dict:
    grid = _grid()
    sight_data = compute_sight_lines(grid)
    near = {
        color: [
            [r, c]
            for r, row in enumerate(grid)
            for c, v in enumerate(row)
            if v == 1 and abs(r - cell[0]) + abs(c - cell[1]) <= 2
        ]
        for color, cell in (("red", RED_CELL), ("blue", BLUE_CELL))
    }
    ranking = compute_high_los_ranking(sight_data)
    return {
        "name": "Calibration Test Arena",
        "img_width": MAP_COLS * ZONE_SIZE,
        "img_height": MAP_ROWS * ZONE_SIZE,
        "zone_size": ZONE_SIZE,
        "zone_data": {"zones": grid, "wall_meta": {}, "elevation": None},
        "bases": [
            {
                "base_type": color,
                "x_px": cell[1] * ZONE_SIZE + ZONE_SIZE // 2,
                "y_px": cell[0] * ZONE_SIZE + ZONE_SIZE // 2,
            }
            for color, cell in (("red", RED_CELL), ("blue", BLUE_CELL))
        ],
        "sight_data": sight_data,
        "base_sight_lines": [
            {"base_type": color, "visible_cells": cells}
            for color, cells in near.items()
        ],
        "cell_ranking": ranking,
        "strong_spots": ranking[:3],
    }


@pytest.fixture(scope="module")
def fixture_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("maps") / "calibration_test_arena.json"
    path.write_text(json.dumps(_fixture_dict()), encoding="utf-8")
    return path


@pytest.mark.django_db
class TestLoadMapFixture:
    def test_fixture_map_resolves_with_spawn_cells(self, fixture_path: Path) -> None:
        arena_map = cal.load_map_fixture(fixture_path)
        assert isinstance(arena_map, ArenaMap)
        ctx, zone_size = load_map_context(arena_map)
        assert isinstance(ctx, MapContext)
        assert zone_size == ZONE_SIZE
        assert ctx.spawn_cells == {"red": RED_CELL, "blue": BLUE_CELL}

    def test_fixture_carries_every_config(self, fixture_path: Path) -> None:
        data = _fixture_dict()
        ctx, _ = load_map_context(cal.load_map_fixture(fixture_path))
        assert ctx is not None
        assert ctx.zone_data == data["zone_data"]["zones"]
        assert ctx.sight_data == {
            k: frozenset(v) for k, v in data["sight_data"].items()
        }
        assert set(ctx.base_sight_data) == {"red", "blue"}
        assert ctx.strong_spots == [tuple(rc) for rc in data["strong_spots"]]
        top_n = max(1, len(data["cell_ranking"]) // 4)
        assert ctx.high_los_cells == [tuple(rc) for rc in data["cell_ranking"][:top_n]]

    def test_ranking_and_strong_spots_are_optional(self, tmp_path: Path) -> None:
        data = _fixture_dict()
        del data["cell_ranking"], data["strong_spots"]
        path = tmp_path / "bare.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        ctx, _ = load_map_context(cal.load_map_fixture(path))
        assert ctx is not None
        assert ctx.strong_spots == []
        assert ctx.high_los_cells == []

    def test_missing_required_key_names_it(self, tmp_path: Path) -> None:
        data = _fixture_dict()
        del data["sight_data"]
        path = tmp_path / "broken.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="sight_data"):
            cal.load_map_fixture(path)
        assert not ArenaMap.objects.filter(name=data["name"]).exists()


def _row(position: str, games: float = 30, **overrides: object) -> dict:
    """A synthetic LFstats per-position row (also used as a baseline)."""
    row = {
        "position": position,
        "games": games,
        "avg_accuracy": 0.6,
        "avg_assists": 6.0,
        "avg_eliminations": 0.3,
        "avg_hit_diff": 1.2,
        "avg_medic_hits": 1.5,
        "avg_missiles_hit": 2.0 if position in ("commander", "heavy") else None,
        "avg_mvp": 15.0,
        "avg_nukes_canceled": 0.0,
        "avg_nukes_detonated": 1.0 if position == "commander" else None,
        "avg_penalties": 0.0,
        "avg_rapid_fire": 1.5 if position == "scout" else None,
        "avg_resupplies_given": 30.0 if position in ("ammo", "medic") else None,
        "avg_shots_hit": 60.0,
        "avg_times_hit": 30.0,
        "avg_uptime_pct": 0.55,
        "elimination_rate": 0.4,
        "win_rate": 0.5,
    }
    row.update(overrides)
    return row


# The original x1.2 boost, so the boost tests still exercise the multiply and
# cap now that the calibrated default is 1.0 (no bonus).
_BOOST_1_2 = cal.ImportConstants(preferred_role_boost=1.2)


class TestPlayerData:
    def _stats(
        self, row: dict, constants: cal.ImportConstants = cal.DEFAULT_CONSTANTS
    ) -> dict:
        pos = row["position"]
        return cal.stats_for([row], [], {pos: _row(pos)}, {pos: _row(pos)}, constants)

    def _build(
        self,
        row: dict,
        role: str,
        constants: cal.ImportConstants = cal.DEFAULT_CONSTANTS,
    ):
        pos = row["position"]
        return cal.player_data(
            "#abc", "Ace", row, _row(pos), None, _row(pos), role, constants
        )

    def test_returns_a_player_data_with_every_sim_stat(self) -> None:
        pd = self._build(_row("scout"), "scout")
        assert isinstance(pd, _PlayerData)
        assert (pd.id, pd.name) == ("#abc", "Ace")
        for stat in _SIMULATION_STATS:
            assert isinstance(pd.stat_for_simulation(stat, "scout"), int)

    def test_boost_applies_at_the_rows_own_position(self) -> None:
        row = _row("heavy", avg_accuracy=0.5)
        raw = self._stats(row)
        pd = self._build(row, "heavy", _BOOST_1_2)
        for stat in _SIMULATION_STATS:
            assert pd.stat_for_simulation(stat, "heavy") == min(
                int(raw[stat] * 1.2), 100
            )
        assert pd.stat_for_simulation("accuracy", "heavy") == 60

    def test_boost_is_capped_at_100(self) -> None:
        pd = self._build(_row("heavy", avg_accuracy=0.9), "heavy", _BOOST_1_2)
        assert pd.stat_for_simulation("accuracy", "heavy") == 100

    def test_calibrated_default_leaves_own_position_stats_raw(self) -> None:
        row = _row("heavy", avg_accuracy=0.5)
        raw = self._stats(row)
        pd = self._build(row, "heavy")
        for stat in _SIMULATION_STATS:
            assert pd.stat_for_simulation(stat, "heavy") == raw[stat]

    def test_no_boost_at_another_role(self) -> None:
        row = _row("heavy", avg_accuracy=0.5)
        raw = self._stats(row)
        pd = self._build(row, "commander", _BOOST_1_2)
        for stat in _SIMULATION_STATS:
            assert pd.stat_for_simulation(stat, "commander") == raw[stat]

    def test_boost_follows_the_constants(self) -> None:
        constants = cal.ImportConstants(preferred_role_boost=1.5)
        pd = self._build(_row("heavy", avg_accuracy=0.5), "heavy", constants)
        assert pd.stat_for_simulation("accuracy", "heavy") == 75

    def test_constants_reach_the_formulas(self) -> None:
        constants = cal.ImportConstants(accuracy_offset=-20, preferred_role_boost=1.0)
        pd = self._build(_row("heavy", avg_accuracy=0.5), "heavy", constants)
        assert pd.stat_for_simulation("accuracy", "heavy") == 30

    def test_loss_row_feeds_adaptability(self) -> None:
        # adaptability is not a sim stat, so it must not break the build;
        # a loss row present or absent gives the same sim stats.
        row = _row("medic")
        with_loss = cal.player_data(
            "#m",
            "Med",
            row,
            _row("medic"),
            _row("medic"),
            _row("medic"),
            "ammo",
            cal.DEFAULT_CONSTANTS,
        )
        without = cal.player_data(
            "#m",
            "Med",
            row,
            _row("medic"),
            None,
            _row("medic"),
            "ammo",
            cal.DEFAULT_CONSTANTS,
        )
        for stat in _SIMULATION_STATS:
            assert with_loss.stat_for_simulation(stat, "ammo") == (
                without.stat_for_simulation(stat, "ammo")
            )


class TestWithinTolerance:
    """Decision 16: 15% of the real value, or an absolute floor (0.5 / 0.05)."""

    @pytest.mark.parametrize("sim", [23.0, 17.0])
    def test_count_exactly_15_percent_passes(self, sim: float) -> None:
        assert cal.within_tolerance("count", sim, 20.0)

    @pytest.mark.parametrize("sim", [23.01, 16.99])
    def test_count_just_past_15_percent_fails(self, sim: float) -> None:
        assert not cal.within_tolerance("count", sim, 20.0)

    @pytest.mark.parametrize("sim", [2.5, 1.5])
    def test_count_exactly_at_floor_passes(self, sim: float) -> None:
        # 15% of 2 is 0.3, so the 0.5 floor decides.
        assert cal.within_tolerance("count", sim, 2.0)

    @pytest.mark.parametrize("sim", [2.51, 1.49])
    def test_count_just_past_floor_fails(self, sim: float) -> None:
        assert not cal.within_tolerance("count", sim, 2.0)

    @pytest.mark.parametrize("sim", [1.15, 0.85])
    def test_fraction_exactly_15_percent_passes(self, sim: float) -> None:
        assert cal.within_tolerance("fraction", sim, 1.0)

    @pytest.mark.parametrize("sim", [1.151, 0.849])
    def test_fraction_just_past_15_percent_fails(self, sim: float) -> None:
        assert not cal.within_tolerance("fraction", sim, 1.0)

    @pytest.mark.parametrize("sim", [0.25, 0.15])
    def test_fraction_exactly_at_floor_passes(self, sim: float) -> None:
        # 15% of 0.2 is 0.03, so the 0.05 floor decides.
        assert cal.within_tolerance("fraction", sim, 0.2)

    @pytest.mark.parametrize("sim", [0.251, 0.149])
    def test_fraction_just_past_floor_fails(self, sim: float) -> None:
        assert not cal.within_tolerance("fraction", sim, 0.2)

    def test_count_floor_is_not_the_fraction_floor(self) -> None:
        # 0.3 off a real 0.1 passes as a count (floor 0.5) but fails with
        # the fraction floor (0.05), so a mixed-up kind shows here.
        assert cal.within_tolerance("count", 0.4, 0.1)
        assert not cal.within_tolerance("fraction", 0.4, 0.1)

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(ValueError):
            cal.within_tolerance("ratio", 1.0, 1.0)


class TestSpearman:
    def test_monotone_is_one_and_reversed_is_minus_one(self) -> None:
        assert cal._spearman([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]) == pytest.approx(1.0)
        assert cal._spearman([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)

    def test_ties_take_the_average_rank(self) -> None:
        # ranks x = 1, 2.5, 2.5, 4 against y = 1, 2, 3, 4.
        assert cal._spearman([1.0, 2.0, 2.0, 4.0], [1.0, 2.0, 3.0, 4.0]) == (
            pytest.approx(0.9486832980505138)
        )

    def test_fewer_than_three_players_is_none(self) -> None:
        assert cal._spearman([1.0, 2.0], [1.0, 2.0]) is None

    def test_constant_values_are_none(self) -> None:
        assert cal._spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None


# --- run_test: a tiny synthetic Test set --------------------------------------

LINEUP = ("commander", "heavy", "scout", "scout", "ammo", "medic")


def _entry(ipl_id: str | None, position: str) -> dict:
    return {
        "ipl_id": ipl_id,
        "callsign": f"cs {ipl_id}",
        "position": position,
        "score": 5000,
        "mvp": 10.0,
        "is_mercenary": False,
    }


def _side(prefix: str, positions: tuple[str, ...] = LINEUP) -> list[dict]:
    """Six distinct players, ``<prefix>-<position><n>``."""
    return [_entry(f"{prefix}-{pos}{i}", pos) for i, pos in enumerate(positions)]


def _game(slug: str, fire: list[dict], ice: list[dict], margin: int = 5000) -> dict:
    return {
        "slug": slug,
        "start_time": "2026-07-13T12:00:00",
        "round_type": "pool",
        "teams": [
            {
                "colour": "fire",
                "score": 20000 + margin,
                "result": "win",
                "roster": fire,
            },
            {"colour": "ice", "score": 20000, "result": "loss", "roster": ice},
        ],
    }


def _data(
    games: list[dict], train_games: dict[str, int] | None = None, **accuracy: float
) -> tuple[dict, dict]:
    """``(train, test)`` for ``games``.

    Every ``"<ipl>|<position>"`` has 30 Training games unless ``train_games``
    says otherwise (0 drops the row). ``accuracy`` sets an ipl's Internationals
    ``avg_accuracy`` (keys use ``_`` for ``-``).
    """
    train_games = train_games or {}
    keys = {
        f"{e['ipl_id']}|{e['position']}"
        for g in games
        for t in g["teams"]
        for e in t["roster"]
        if e["ipl_id"] is not None
    }
    rows = {}
    for key in keys:
        n = train_games.get(key, 30)
        if n:
            rows[key] = _row(key.split("|")[1], games=n)
    averages = {}
    for key in keys:
        ipl, pos = key.split("|")
        acc = accuracy.get(ipl.replace("-", "_"), 0.6)
        averages[key] = _row(pos, games=3, avg_accuracy=acc)
    train = {
        "rows": {"all": rows, "loss": {}},
        "baselines": {
            "all": {p: _row(p) for p in ALL_POSITIONS},
            "loss": {p: _row(p) for p in ALL_POSITIONS},
        },
    }
    return train, {"games": games, "averages": averages}


def _three_game_set() -> tuple[dict, dict]:
    """One used game, one two-heavy lineup, one with a thin player."""
    used = _game("used", _side("a"), _side("b"))
    two_heavy = _game(
        "two-heavy",
        _side("c", ("commander", "heavy", "heavy", "scout", "ammo", "medic")),
        _side("d"),
    )
    thin = _game("thin", _side("e"), _side("f"))
    # a-commander0 sits exactly on the rule (5 games: used); e-medic5 is one under.
    return _data(
        [used, two_heavy, thin],
        train_games={"a-commander0|commander": 5, "e-medic5|medic": 4},
    )


@pytest.fixture
def movement_ctx(db: None, fixture_path: Path) -> MapContext:
    ctx, _ = load_map_context(cal.load_map_fixture(fixture_path))
    assert ctx is not None
    return ctx


class TestRunTest:
    def test_coverage_counts_used_and_each_skip(self, movement_ctx: MapContext) -> None:
        train, test = _three_game_set()
        report = cal.run_test(train, test, movement_ctx, rounds=1, seed=7)
        assert report.coverage == cal.Coverage(3, 1, 1, 1)

    def test_cells_only_for_applicable_role_metric_pairs(
        self, movement_ctx: MapContext
    ) -> None:
        train, test = _three_game_set()
        report = cal.run_test(train, test, movement_ctx, rounds=1, seed=7)
        expected = [
            (p, mid) for p in ALL_POSITIONS for mid, _k, ps in cal.METRICS if p in ps
        ]
        assert [(c.position, c.metric) for c in report.cells] == expected

    def test_match_level(self, movement_ctx: MapContext) -> None:
        train, test = _three_game_set()
        report = cal.run_test(train, test, movement_ctx, rounds=1, seed=7)
        assert report.match.games == 1
        assert 0.0 <= report.match.winner_agreement <= 1.0
        assert report.match.mean_margin_error >= 0.0

    def test_cells_count_scorecards_and_judge_with_the_tolerance(
        self, movement_ctx: MapContext
    ) -> None:
        train, test = _three_game_set()
        report = cal.run_test(train, test, movement_ctx, rounds=1, seed=7)
        kinds = {mid: kind for mid, kind, _ps in cal.METRICS}
        for cell in report.cells:
            assert cell.n_scorecards == (4 if cell.position == "scout" else 2)
            assert cell.passed == cal.within_tolerance(
                kinds[cell.metric], cell.sim_mean, cell.real_mean
            )
            # One used game: nobody has 2 scorecards at a position.
            assert cell.spearman is None

    def test_rounds_below_one_raises(self) -> None:
        train, test = _three_game_set()
        with pytest.raises(ValueError, match="rounds"):
            cal.run_test(train, test, None, rounds=0, seed=7)

    def test_null_ipl_and_seven_player_sides_skip_as_lineup(
        self, movement_ctx: MapContext
    ) -> None:
        null_side = _side("g")
        null_side[2] = _entry(None, "scout")
        seven = _side("i") + [_entry("i-scout9", "scout")]
        train, test = _data(
            [_game("null", null_side, _side("h")), _game("seven", seven, _side("j"))]
        )
        report = cal.run_test(train, test, movement_ctx, rounds=1, seed=7)
        assert report.coverage == cal.Coverage(2, 0, 2, 0)
        assert report.cells == []
        assert report.match == cal.MatchResult(0, 0.0, 0.0)

    def test_a_player_without_a_training_row_is_thin(
        self, movement_ctx: MapContext
    ) -> None:
        train, test = _data(
            [_game("g", _side("k"), _side("l"))], train_games={"l-ammo4|ammo": 0}
        )
        report = cal.run_test(train, test, movement_ctx, rounds=1, seed=7)
        assert report.coverage == cal.Coverage(1, 0, 0, 1)

    def test_real_mean_is_scorecard_weighted(self, movement_ctx: MapContext) -> None:
        # Fire's commander m plays all three games; Ice's are n, o, o. Weighted
        # by used scorecards: (3*0.9 + 0.6 + 2*0.3) / 6 = 0.65.
        fire = _side("m")
        games = [
            _game("g1", fire, _side("n")),
            _game("g2", fire, _side("o")),
            _game("g3", fire, _side("o")),
        ]
        train, test = _data(games, m_commander0=0.9, n_commander0=0.6, o_commander0=0.3)
        report = cal.run_test(train, test, movement_ctx, rounds=1, seed=7)
        cell = next(
            c
            for c in report.cells
            if (c.position, c.metric) == ("commander", "avg_accuracy")
        )
        assert cell.n_scorecards == 6
        assert cell.real_mean == pytest.approx(0.65)

    def test_spearman_over_players_with_two_scorecards(
        self, movement_ctx: MapContext
    ) -> None:
        # Sides p, q, r each play two games, so three commanders qualify.
        p, q, r = _side("p"), _side("q"), _side("r")
        games = [_game("g1", p, q), _game("g2", q, r), _game("g3", r, p)]
        train, test = _data(games, p_commander0=0.9, q_commander0=0.6, r_commander0=0.3)
        # Training accuracy in the same order, far apart: each commander's
        # simulated accuracy follows it, so the ranks agree exactly (and a
        # mix-up of whose outputs are whose would scramble them).
        for ipl, acc in (("p", 0.95), ("q", 0.6), ("r", 0.25)):
            train["rows"]["all"][f"{ipl}-commander0|commander"]["avg_accuracy"] = acc
        report = cal.run_test(train, test, movement_ctx, rounds=2, seed=7)
        cells = {(c.position, c.metric): c for c in report.cells}
        assert cells[("commander", "avg_accuracy")].spearman == pytest.approx(1.0)
        # Every real times-hit average is equal: the rank correlation is undefined.
        assert cells[("commander", "avg_times_hit")].spearman is None

    def test_spearman_needs_three_players_with_two_scorecards(
        self, movement_ctx: MapContext
    ) -> None:
        # Only fire's players (w) have two scorecards: one player per position.
        w = _side("w")
        games = [_game("g1", w, _side("x")), _game("g2", w, _side("y"))]
        train, test = _data(games, x_commander0=0.2, y_commander0=0.8)
        report = cal.run_test(train, test, movement_ctx, rounds=1, seed=7)
        assert all(c.spearman is None for c in report.cells)

    def test_sim_mean_uses_per_round_seeds_in_game_then_round_order(
        self, movement_ctx: MapContext
    ) -> None:
        fire, ice = _side("s"), _side("t")
        games = [_game("g1", fire, ice), _game("g2", ice, fire)]
        train, test = _data(games)
        # Distinct Training stats per side, so playing Ice as red shows.
        for e in fire:
            train["rows"]["all"][f"{e['ipl_id']}|{e['position']}"]["avg_accuracy"] = 0.9
        report = cal.run_test(train, test, movement_ctx, rounds=2, seed=11)

        gen = random.Random(11)
        seeds = [gen.getrandbits(63) for _ in range(4)]
        heavy_hits = []
        rosters = [(fire, ice), (fire, ice), (ice, fire), (ice, fire)]
        for (red, blue), s in zip(rosters, seeds):
            out = calibration_round_worker(
                (_roster(train, red), _roster(train, blue), movement_ctx, s)
            )
            heavy_hits += [
                m["avg_times_hit"]
                for side in ("red", "blue")
                for m, e in zip(out[side], _ordered(red if side == "red" else blue))
                if e["position"] == "heavy"
            ]
        cell = next(
            c
            for c in report.cells
            if (c.position, c.metric) == ("heavy", "avg_times_hit")
        )
        assert cell.sim_mean == pytest.approx(sum(heavy_hits) / len(heavy_hits))

    def test_serial_equals_parallel(self, movement_ctx: MapContext) -> None:
        train, test = _three_game_set()
        serial = cal.run_test(train, test, movement_ctx, rounds=2, seed=42, workers=1)
        try:
            parallel = cal.run_test(
                train, test, movement_ctx, rounds=2, seed=42, workers=2
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Parallel worker pool unavailable: {exc!r}")
        assert serial == parallel


def _ordered(roster: list[dict]) -> list[dict]:
    order = {p: i for i, p in enumerate(ALL_POSITIONS)}
    return sorted(roster, key=lambda e: order[e["position"]])


def _roster(train: dict, roster: list[dict]) -> list:
    out = []
    for e in _ordered(roster):
        pos = e["position"]
        out.append(
            (
                pos,
                cal.player_data(
                    e["ipl_id"],
                    e["callsign"],
                    train["rows"]["all"][f"{e['ipl_id']}|{pos}"],
                    train["baselines"]["all"][pos],
                    None,
                    train["baselines"]["loss"][pos],
                    pos,
                    DEFAULT_CONSTANTS,
                ),
            )
        )
    return out


def _train(players: dict[str, dict]) -> dict:
    """A Training dict whose ``rows.all`` are ``players`` (keyed ``"<ipl>|<pos>"``)."""
    return {
        "rows": {"all": players, "loss": {}},
        "baselines": {
            "all": {p: _row(p) for p in ALL_POSITIONS},
            "loss": {p: _row(p) for p in ALL_POSITIONS},
        },
    }


def _field_roster(
    train: dict,
    player: tuple[str, object] | None = None,
    constants: ImportConstants = DEFAULT_CONSTANTS,
) -> list:
    """A field-average side in LINEUP order, ``player`` in its Role's first slot."""
    out = []
    placed = False
    for pos in LINEUP:
        if player is not None and not placed and player[0] == pos:
            out.append(player)
            placed = True
            continue
        base = dict(train["baselines"]["all"][pos], position=pos)
        out.append(
            (
                pos,
                cal.player_data(
                    f"field-{pos}",
                    f"Field {pos}",
                    base,
                    train["baselines"]["all"][pos],
                    None,
                    train["baselines"]["loss"][pos],
                    pos,
                    constants,
                ),
            )
        )
    return out


class TestRunTrain:
    def test_only_players_with_26_training_games_play(
        self, movement_ctx: MapContext
    ) -> None:
        assert cal.MIN_TRAIN_GAMES == 26
        train = _train(
            {
                "h1|heavy": _row("heavy", games=26),
                "h2|heavy": _row("heavy", games=25),
                "s1|scout": _row("scout", games=25),
            }
        )
        report = cal.run_train(train, movement_ctx, rounds=1, seed=3)
        assert {c.position for c in report.cells} == {"heavy"}
        assert all(c.n_scorecards == 1 for c in report.cells)

    def test_cells_for_applicable_pairs_and_objective(
        self, movement_ctx: MapContext
    ) -> None:
        train = _train(
            {f"p{i}|{pos}": _row(pos) for i, pos in enumerate(ALL_POSITIONS)}
        )
        report = cal.run_train(train, movement_ctx, rounds=1, seed=3)
        expected = [
            (p, mid) for p in ALL_POSITIONS for mid, _k, ps in cal.METRICS if p in ps
        ]
        assert [(c.position, c.metric) for c in report.cells] == expected
        assert report.objective == pytest.approx(cal.objective(report.cells))
        kinds = {mid: kind for mid, kind, _ps in cal.METRICS}
        for cell in report.cells:
            assert cell.passed == cal.within_tolerance(
                kinds[cell.metric], cell.sim_mean, cell.real_mean
            )

    def test_real_mean_is_the_players_training_average_unweighted(
        self, movement_ctx: MapContext
    ) -> None:
        train = _train(
            {
                "h1|heavy": _row("heavy", games=26, avg_accuracy=0.4),
                "h2|heavy": _row("heavy", games=100, avg_accuracy=0.8),
            }
        )
        report = cal.run_train(train, movement_ctx, rounds=1, seed=3)
        cell = next(c for c in report.cells if c.metric == "avg_accuracy")
        assert cell.real_mean == pytest.approx(0.6)
        assert cell.n_scorecards == 2

    def test_player_plays_their_role_in_a_field_average_team(
        self, movement_ctx: MapContext
    ) -> None:
        # Baselines without a "position" key still build field players.
        train = _train({"h1|heavy": _row("heavy", avg_accuracy=0.9)})
        for base in train["baselines"]["all"].values():
            del base["position"]
        # Non-default constants, so the player and the field must both get them.
        constants = cal.ImportConstants(accuracy_offset=-30, preferred_role_boost=1.0)
        report = cal.run_train(
            train, movement_ctx, constants=constants, rounds=2, seed=5
        )

        row = train["rows"]["all"]["h1|heavy"]
        me = (
            "heavy",
            cal.player_data(
                "h1",
                "h1",
                row,
                train["baselines"]["all"]["heavy"],
                None,
                train["baselines"]["loss"]["heavy"],
                "heavy",
                constants,
            ),
        )
        gen = random.Random(5)
        rounds = []
        for _ in range(2):
            out = calibration_round_worker(
                (
                    _field_roster(train, me, constants),
                    _field_roster(train, constants=constants),
                    movement_ctx,
                    gen.getrandbits(63),
                )
            )
            rounds.append(out["red"][1])  # the heavy is LINEUP's second slot
        assert {c.metric: c.sim_mean for c in report.cells} == pytest.approx(
            {m: sum(r[m] for r in rounds) / len(rounds) for m in rounds[0]}
        )

    def test_constants_reach_the_players(self, movement_ctx: MapContext) -> None:
        train = _train({"h1|heavy": _row("heavy", avg_accuracy=0.6)})
        low = cal.run_train(
            train,
            movement_ctx,
            constants=cal.ImportConstants(accuracy_offset=-60),
            rounds=1,
            seed=3,
        )
        high = cal.run_train(train, movement_ctx, rounds=1, seed=3)
        assert low.cells != high.cells

    def _sim_returns(
        self, monkeypatch: pytest.MonkeyPatch, *rounds: dict[str, float]
    ) -> None:
        """Every task's red / blue sides play the given rounds' metrics, in turn."""
        outputs = iter(rounds)
        monkeypatch.setattr(
            cal,
            "_run_rounds",
            lambda tasks, workers: [
                {"red": [m] * 6, "blue": [m] * 6} for m, _ in zip(outputs, tasks)
            ],
        )

    def test_a_role_that_never_tags_or_is_tagged_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # LFS-01: red's spawns sat in pockets cut off from the floor, four Roles
        # recorded nothing, and two searches ran on it before anyone noticed.
        dead = {metric: 0.0 for metric, _kind, _ps in cal.METRICS}
        self._sim_returns(monkeypatch, dead, dead)
        train = _train({"h1|heavy": _row("heavy")})
        with pytest.raises(ValueError, match="No heavy player tagged or was tagged"):
            cal.run_train(train, None, rounds=2, seed=3)

    def test_one_engaged_round_is_enough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        dead = {metric: 0.0 for metric, _kind, _ps in cal.METRICS}
        self._sim_returns(monkeypatch, dead, dict(dead, avg_times_hit=1.0))
        train = _train({"h1|heavy": _row("heavy")})
        report = cal.run_train(train, None, rounds=2, seed=3)
        assert {c.position for c in report.cells} == {"heavy"}

    def test_no_qualifying_player_raises(self) -> None:
        train = _train({"h1|heavy": _row("heavy", games=25)})
        with pytest.raises(ValueError, match="26"):
            cal.run_train(train, None, rounds=1, seed=3)

    def test_rounds_below_one_raises(self) -> None:
        train = _train({"h1|heavy": _row("heavy")})
        with pytest.raises(ValueError, match="rounds"):
            cal.run_train(train, None, rounds=0, seed=3)

    def test_serial_equals_parallel(self, movement_ctx: MapContext) -> None:
        train = _train(
            {
                "h1|heavy": _row("heavy"),
                "s1|scout": _row("scout", avg_accuracy=0.7),
                "m1|medic": _row("medic"),
            }
        )
        serial = cal.run_train(train, movement_ctx, rounds=2, seed=42, workers=1)
        try:
            parallel = cal.run_train(train, movement_ctx, rounds=2, seed=42, workers=2)
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Parallel worker pool unavailable: {exc!r}")
        assert serial == parallel


class _StubTrain:
    """Stands in for ``run_train``: scores constants with ``score``, runs no rounds."""

    def __init__(self, score: Callable[[ImportConstants], float]) -> None:
        self.score = score
        self.calls: list[dict] = []

    def __call__(
        self,
        train: dict,
        movement_ctx: object,
        *,
        constants: ImportConstants,
        **kwargs: object,
    ) -> cal.TrainReport:
        kwargs["constants"] = constants
        self.calls.append(dict(kwargs, train=train, movement_ctx=movement_ctx))
        return cal.TrainReport(cells=[], objective=self.score(constants))


GRID: dict[str, tuple[float, ...]] = {
    "accuracy_offset": (-20.0, -10.0, 0.0),
    "log_scale": (15.0, 25.0, 35.0),
}


def _search(
    monkeypatch: pytest.MonkeyPatch,
    score: Callable[[ImportConstants], float],
    grid: dict[str, tuple[float, ...]] = GRID,
    train: dict | None = None,
) -> tuple[cal.SearchResult, _StubTrain]:
    stub = _StubTrain(score)
    monkeypatch.setattr(cal, "run_train", stub)
    result = cal.search(
        train if train is not None else {"rows": {}},
        "ctx",
        start=DEFAULT_CONSTANTS,
        grid=grid,
        rounds=2,
        seed=17,
        workers=3,
    )
    return result, stub


def _bowl(c: ImportConstants) -> float:
    """Separable: best at accuracy_offset -10, log_scale 35."""
    return (c.accuracy_offset + 10) ** 2 + (c.log_scale - 35) ** 2


class TestSearch:
    def test_keeps_only_improving_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result, _stub = _search(monkeypatch, _bowl)
        assert result.constants == cal.ImportConstants(
            accuracy_offset=-10.0, log_scale=35.0
        )
        assert result.objective == _bowl(result.constants) == 0.0
        # Start (0, 25) scores 200. Pass 1, log_scale first: 15 -> 500 (no),
        # 35 -> 100 (kept); accuracy_offset: -20 -> 100 (a tie: no), -10 -> 0
        # (kept), 0 -> 100 (no). The current value is never re-tried.
        steps = [
            (s.pass_index, s.field, s.value, s.objective, s.kept)
            for s in result.history
        ]
        assert steps[:6] == [
            (0, None, None, 200.0, True),
            (1, "log_scale", 15.0, 500.0, False),
            (1, "log_scale", 35.0, 100.0, True),
            (1, "accuracy_offset", -20.0, 100.0, False),
            (1, "accuracy_offset", -10.0, 0.0, True),
            (1, "accuracy_offset", 0.0, 100.0, False),
        ]

    def test_a_tie_is_not_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result, _stub = _search(monkeypatch, lambda c: 1.0)
        assert result.constants == DEFAULT_CONSTANTS
        assert not any(s.kept for s in result.history[1:])

    def test_stops_after_a_pass_with_no_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result, _stub = _search(monkeypatch, _bowl)
        # Pass 1 reaches the optimum, pass 2 changes nothing, so no pass 3.
        assert max(s.pass_index for s in result.history) == 2

    def test_stops_after_three_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Every new evaluation scores lower than all before it, so every pass
        # changes something.
        counter = iter(range(1000, 0, -1))
        result, _stub = _search(monkeypatch, lambda c: float(next(counter)))
        assert max(s.pass_index for s in result.history) == 3
        assert [s.pass_index for s in result.history].count(3) > 0

    def test_fields_run_in_the_dataclass_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # GRID lists accuracy_offset first, against the dataclass order.
        assert list(GRID) == ["accuracy_offset", "log_scale"]
        result, _stub = _search(monkeypatch, lambda c: 1.0, grid=GRID)
        pass_one = [s.field for s in result.history if s.pass_index == 1]
        # ImportConstants declares log_scale before accuracy_offset.
        assert pass_one == [
            "log_scale",
            "log_scale",
            "accuracy_offset",
            "accuracy_offset",
        ]

    def test_every_evaluation_uses_the_same_seed_and_only_train(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        train = {"rows": {"all": {}}, "marker": True}
        _result, stub = _search(monkeypatch, _bowl, train=train)
        assert len(stub.calls) > 1
        for call in stub.calls:
            assert call["train"] is train
            assert call["movement_ctx"] == "ctx"
            assert (call["seed"], call["rounds"], call["workers"]) == (17, 2, 3)
        assert "test" not in inspect.signature(cal.search).parameters

    def test_each_constants_is_evaluated_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _result, stub = _search(monkeypatch, _bowl)
        seen = [call["constants"] for call in stub.calls]
        assert len(seen) == len(set(seen))

    def test_each_step_is_reported_as_it_happens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A long search (hours on the Syracuse map) must show progress, so a
        # crash late in the run does not lose every evaluation.
        stub = _StubTrain(_bowl)
        monkeypatch.setattr(cal, "run_train", stub)
        seen: list[tuple[cal.SearchStep, int]] = []
        result = cal.search(
            {"rows": {}},
            "ctx",
            start=DEFAULT_CONSTANTS,
            grid=GRID,
            rounds=2,
            seed=17,
            workers=3,
            on_step=lambda step: seen.append((step, len(stub.calls))),
        )
        assert [step for step, _ in seen] == result.history
        # Reported right after its own evaluation, not batched at the end.
        assert seen[0][1] == 1
        assert seen[2][1] == 3

    def test_unknown_grid_field_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError, match="nope"):
            _search(monkeypatch, _bowl, grid={"nope": (1.0,)})


def _cell(
    metric: str, sim: float, real: float, position: str = "heavy"
) -> cal.CellResult:
    return cal.CellResult(position, metric, sim, real, 1, True, None)


class TestObjective:
    """Decision 25: mean of (err / max(|real|, floor)) ** 2, floor by kind."""

    def test_count_under_its_floor_divides_by_half(self) -> None:
        # |real| 0.2 < 0.5: err 0.5 / 0.5 = 1.0.
        assert cal.objective([_cell("avg_times_hit", 0.7, 0.2)]) == pytest.approx(1.0)

    def test_fraction_under_its_floor_divides_by_005(self) -> None:
        # |real| 0.02 < 0.05: err 0.1 / 0.05 = 2.0 -> 4.0.
        assert cal.objective([_cell("avg_accuracy", 0.12, 0.02)]) == pytest.approx(4.0)

    def test_mean_of_squares_over_cells(self) -> None:
        # count above floor: (12 - 10) / 10 = 0.2 -> 0.04; fraction above
        # floor: (0.3 - 0.6) / 0.6 = -0.5 -> 0.25. Mean = 0.145.
        cells = [_cell("avg_times_hit", 12.0, 10.0), _cell("avg_accuracy", 0.3, 0.6)]
        assert cal.objective(cells) == pytest.approx(0.145)

    def test_no_cells_raises(self) -> None:
        with pytest.raises(ValueError):
            cal.objective([])


class TestCalibrationRoundWorker:
    def test_returns_metrics_per_player_and_the_result(
        self, movement_ctx: MapContext
    ) -> None:
        train, _test = _data([_game("g", _side("u"), _side("v"))])
        red = _roster(train, _side("u"))
        # Blue in reverse order, so each side's list must follow its own roster.
        blue = list(reversed(_roster(train, _side("v"))))
        out = calibration_round_worker((red, blue, movement_ctx, 5))
        assert set(out) == {"red", "blue", "winner", "red_score", "blue_score"}
        for side, roster in (("red", red), ("blue", blue)):
            assert [set(m) for m in out[side]] == [
                set(cal.sim_metrics(_ps(role), NO_WIPE)) for role, _pd in roster
            ]

    def test_winner_follows_the_points(self, movement_ctx: MapContext) -> None:
        train, _test = _data([_game("g", _side("u"), _side("v"))])
        for e in _side("u"):
            train["rows"]["all"][f"{e['ipl_id']}|{e['position']}"][
                "avg_accuracy"
            ] = 0.95
        for e in _side("v"):
            train["rows"]["all"][f"{e['ipl_id']}|{e['position']}"]["avg_accuracy"] = 0.2
        strong, weak = _roster(train, _side("u")), _roster(train, _side("v"))
        red_win = calibration_round_worker((strong, weak, movement_ctx, 5))
        blue_win = calibration_round_worker((weak, strong, movement_ctx, 5))
        assert red_win["red_score"] > red_win["blue_score"]
        assert red_win["winner"] == "red"
        assert blue_win["blue_score"] > blue_win["red_score"]
        assert blue_win["winner"] == "blue"

    def test_same_seed_same_round(self, movement_ctx: MapContext) -> None:
        train, _test = _data([_game("g", _side("u"), _side("v"))])
        args = (_roster(train, _side("u")), _roster(train, _side("v")), movement_ctx, 9)
        assert calibration_round_worker(args) == calibration_round_worker(args)
