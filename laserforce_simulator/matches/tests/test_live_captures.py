"""MECH-17c (spec #550, Live Base captures): a player stops for a capture only when one
can happen, a Commander goes for its Bases, and Heavies and Scouts in Control are drawn
to neutral ones. Hand-built ``PlayerState`` and ``MapContext`` at the ``plan_action`` and
``choose_goal_cell`` seams, plus seeded Syracuse rounds.
"""

from __future__ import annotations

import random
from typing import Any, Callable

import pytest

from matches.round_probe import run_probe
from matches.sim_helpers import combat, modes, pathfinding
from matches.sim_helpers.combat import award_bases, capture_base, plan_action
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.modes import (
    CONTROL_BONUS,
    CONTROL_VALUE_CAP,
    Mode,
    base_goal,
    mode_goal,
)
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.round_context import RoundContext
from matches.sim_helpers.time_constants import (
    GOAL_RECOMPUTE_PERIOD_TICKS,
    HELD_GOAL_EXPIRY,
)
from matches.simulation import BatchSimulator, entrypoints
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_batch_sim import _move01_make_map
from matches.tests.test_modes import (
    _choose,
    _enemy,
    _grid_map,
    _placed,
    _player,
    _remember,
    _rosters,
    _syracuse,
)

Cell = tuple[int, int]

NOW = 100
ENEMY_RANGE: list[Cell] = [(5, 5), (5, 6)]
NEUTRAL_RANGE: list[Cell] = [(2, 2)]
OWN_RANGE: list[Cell] = [(1, 1)]


def _key(cell: Cell) -> str:
    return f"{cell[0]},{cell[1]}"


def _bases(**fields: Any) -> MapContext:
    """Red's Bases to capture: blue's and a neutral one; ``red`` is its own."""
    return _grid_map(
        base_sight={
            "blue": set(map(_key, ENEMY_RANGE)),
            "neutral_1": set(map(_key, NEUTRAL_RANGE)),
            "red": set(map(_key, OWN_RANGE)),
        },
        **fields,
    )


@pytest.fixture
def best_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    """Roll 0 always takes the best-scored candidate."""
    monkeypatch.setattr("random.random", lambda: 0.0)


@pytest.fixture
def draws(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list, Callable]]:
    """Every ``(candidates, value_of)`` ``modes`` hands to ``draw_goal``."""
    calls: list[tuple[list, Callable]] = []
    real = modes.draw_goal

    def spy(player: PlayerState, candidates: list, value_of: Callable, *rest: Any):
        calls.append((list(candidates), value_of))
        return real(player, candidates, value_of, *rest)

    monkeypatch.setattr(modes, "draw_goal", spy)
    return calls


@pytest.fixture
def rolled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, int]]:
    """The weights of every Action roll ``plan_action`` makes."""
    seen: list[dict[str, int]] = []
    real = combat.random.choices

    def spy(population: list, weights: list, *rest: Any, **kwargs: Any) -> list:
        if "capture_base" in population:
            seen.append(dict(zip(population, weights)))
        return real(population, weights, *rest, **kwargs)

    monkeypatch.setattr(combat.random, "choices", spy)
    return seen


def _roll(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    monkeypatch.setattr(combat.random, "choices", lambda *_a, **_k: [action])


def _plan(player: PlayerState, ctx: MapContext | None, *others: PlayerState) -> list:
    return plan_action(player, [player, *others], NOW, ctx, time_domain="ticks")


class TestBaseInReach:
    """``plan_action`` says on a map round whether a capture can happen right now."""

    def test_on_a_range_cell_with_shots_it_is_true(self) -> None:
        cmdr = _placed("commander", ENEMY_RANGE[0])
        _plan(cmdr, _bases())
        assert cmdr.base_in_reach is True

    def test_on_a_neutral_range_cell_it_is_true(self) -> None:
        cmdr = _placed("commander", NEUTRAL_RANGE[0])
        _plan(cmdr, _bases())
        assert cmdr.base_in_reach is True

    def test_off_every_range_cell_it_is_false(self) -> None:
        cmdr = _placed("commander", (9, 0))
        _plan(cmdr, _bases())
        assert cmdr.base_in_reach is False

    def test_the_own_base_is_out_of_reach(self) -> None:
        cmdr = _placed("commander", OWN_RANGE[0])
        _plan(cmdr, _bases())
        assert cmdr.base_in_reach is False

    def test_a_captured_base_is_out_of_reach(self) -> None:
        cmdr = _placed("commander", ENEMY_RANGE[0], opposing_base_destroyed=True)
        _plan(cmdr, _bases())
        assert cmdr.base_in_reach is False

    def test_a_player_not_active_cannot_reach_it(self) -> None:
        cmdr = _placed("commander", ENEMY_RANGE[0], last_downed_time=NOW - 1)
        _plan(cmdr, _bases())
        assert cmdr.base_in_reach is False

    @pytest.mark.parametrize("shots, reach", [(2, False), (3, True)])
    def test_it_takes_three_shots(self, shots: int, reach: bool) -> None:
        cmdr = _placed("commander", ENEMY_RANGE[0], final_shots=shots)
        _plan(cmdr, _bases())
        assert cmdr.base_in_reach is reach

    def test_an_ammo_pays_no_shots(self) -> None:
        ammo = _placed("ammo", ENEMY_RANGE[0], final_shots=0)
        _plan(ammo, _bases())
        assert ammo.base_in_reach is True

    def test_a_map_less_round_leaves_it_unset(self) -> None:
        cmdr = _placed("commander", ENEMY_RANGE[0])
        _plan(cmdr, None)
        assert cmdr.base_in_reach is None


class TestCanCapture:
    """``can_capture_base_in_current_zone`` reads ``base_in_reach`` when it is set."""

    def test_true_beats_a_zone_with_no_base(self) -> None:
        player = _player("scout", current_zone=0, base_in_reach=True)
        assert player.can_capture_base_in_current_zone is True

    def test_false_beats_a_zone_with_a_base(self) -> None:
        player = _player("scout", current_zone=1, base_in_reach=False)
        assert player.can_capture_base_in_current_zone is False

    @pytest.mark.parametrize("zone, expected", [(0, False), (1, True), (2, True)])
    def test_unset_it_is_the_zone_logic(self, zone: int, expected: bool) -> None:
        player = _player("scout", current_zone=zone)
        assert player.base_in_reach is None
        assert player.can_capture_base_in_current_zone is expected


class TestCaptureWeight:
    @pytest.mark.parametrize("role", ["commander", "heavy", "scout", "medic", "ammo"])
    def test_on_a_map_out_of_reach_it_is_zero_in_any_zone(
        self, role: str, rolled: list
    ) -> None:
        player = _placed(role, (9, 0), current_zone=1)
        _plan(player, _bases())
        assert rolled[0]["capture_base"] == 0

    @pytest.mark.parametrize("role", ["commander", "heavy", "scout", "medic", "ammo"])
    def test_on_a_map_in_reach_it_is_there_in_any_zone(
        self, role: str, rolled: list
    ) -> None:
        player = _placed(role, ENEMY_RANGE[0], current_zone=0)
        _plan(player, _bases())
        assert rolled[0]["capture_base"] > 0

    # Pinned from the commit before this ticket: a map-less round replays as it did.
    @pytest.mark.parametrize(
        "role, zone, weight",
        [
            ("commander", 1, 105),
            ("commander", 0, 0),
            ("heavy", 1, 20),
            ("scout", 1, 13),
            ("medic", 1, 3),
            ("ammo", 1, 3),
        ],
    )
    def test_a_map_less_round_keeps_the_zone_rule(
        self, role: str, zone: int, weight: int, rolled: list
    ) -> None:
        player = _placed(role, current_zone=zone)
        _plan(player, None)
        assert rolled[0]["capture_base"] == weight


class TestCaptureRoll:
    def test_in_reach_the_roll_plans_the_capture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cmdr = _placed("commander", NEUTRAL_RANGE[0])
        _roll(monkeypatch, "capture_base")
        plans = _plan(cmdr, _bases())
        assert [plan["base_id"] for plan in plans] == [15]


class TestBaseGoal:
    def test_it_draws_over_the_uncaptured_bases_at_full_value(
        self, draws: list
    ) -> None:
        cmdr = _placed("commander")
        goal = base_goal(cmdr, [cmdr], "blue", _bases(), NOW)
        assert len(draws) == 1
        candidates, value_of = draws[0]
        assert candidates == sorted(ENEMY_RANGE + NEUTRAL_RANGE)
        assert {value_of(cell) for cell in candidates} == {1.0}
        assert goal in candidates

    def test_a_captured_neutral_base_is_left_out(self, draws: list) -> None:
        cmdr = _placed("commander", neutral_base_destroyed=True)
        assert base_goal(cmdr, [cmdr], "blue", _bases(), NOW) in ENEMY_RANGE
        assert draws[0][0] == ENEMY_RANGE

    def test_a_captured_enemy_base_is_left_out(self, draws: list) -> None:
        cmdr = _placed("commander", opposing_base_destroyed=True)
        assert base_goal(cmdr, [cmdr], "blue", _bases(), NOW) in NEUTRAL_RANGE
        assert draws[0][0] == NEUTRAL_RANGE

    def test_with_no_base_left_there_is_none(self, draws: list) -> None:
        cmdr = _placed(
            "commander", neutral_base_destroyed=True, opposing_base_destroyed=True
        )
        assert base_goal(cmdr, [cmdr], "blue", _bases(), NOW) is None
        assert draws == []

    @pytest.mark.parametrize("shots, has_goal", [(2, False), (3, True)])
    def test_it_takes_three_shots(self, shots: int, has_goal: bool) -> None:
        cmdr = _placed("commander", final_shots=shots)
        assert (base_goal(cmdr, [cmdr], "blue", _bases(), NOW) is not None) is has_goal

    @pytest.mark.parametrize("role", ["heavy", "scout", "medic", "ammo"])
    def test_no_other_role_gets_one(self, role: str) -> None:
        player = _placed(role)
        assert base_goal(player, [player], "blue", _bases(), NOW) is None

    def test_with_no_reachable_cell_there_is_none(self) -> None:
        cmdr = _placed("commander")
        ctx = _grid_map(base_sight={"blue": {"50,50"}})
        assert base_goal(cmdr, [cmdr], "blue", ctx, NOW) is None

    def test_a_map_with_no_base_data_has_none(self) -> None:
        cmdr = _placed("commander")
        assert base_goal(cmdr, [cmdr], "blue", _grid_map(), NOW) is None


@pytest.mark.usefixtures("best_draw")
class TestChooseGoalCellGoesForBases:
    def test_it_beats_an_action_goal(self) -> None:
        cmdr = _placed("commander", mode=Mode.CONTROL)
        _remember(cmdr, "blue_scout", (9, 9), NOW, "scout")
        goal = _choose(cmdr, [cmdr, _enemy("scout")], _bases(), action="tag_player")
        assert goal == NEUTRAL_RANGE[0]
        assert cmdr._committed_goal == (goal, False, HELD_GOAL_EXPIRY)

    def test_it_replaces_an_action_goal_already_committed(self) -> None:
        expiry = NOW + GOAL_RECOMPUTE_PERIOD_TICKS
        cmdr = _placed("commander", _committed_goal=((9, 9), True, expiry))
        assert _choose(cmdr, [cmdr], _bases()) == NEUTRAL_RANGE[0]

    def test_it_is_held_until_reached(self, draws: list) -> None:
        cmdr = _placed("commander")
        ctx = _bases()
        goal = _choose(cmdr, [cmdr], ctx)
        later = NOW + 10 * GOAL_RECOMPUTE_PERIOD_TICKS
        assert _choose(cmdr, [cmdr], ctx, tick=later, action="tag_player") == goal
        assert len(draws) == 1

    def test_a_goal_on_a_base_captured_on_the_way_is_dropped(self) -> None:
        cmdr = _placed("commander")
        ctx = _bases()
        assert _choose(cmdr, [cmdr], ctx) == NEUTRAL_RANGE[0]
        cmdr.neutral_base_destroyed = True
        assert _choose(cmdr, [cmdr], ctx, tick=NOW + 1) in ENEMY_RANGE

    def test_a_held_goal_off_the_bases_gives_way(self) -> None:
        cmdr = _placed("commander", _committed_goal=((9, 9), False, HELD_GOAL_EXPIRY))
        assert _choose(cmdr, [cmdr], _bases()) == NEUTRAL_RANGE[0]

    def test_a_nuke_reaction_comes_first(self) -> None:
        cmdr = _placed("commander", reacting_to_nuke=True)
        _remember(cmdr, "blue_commander", (9, 9), NOW, "commander")
        assert _choose(cmdr, [cmdr], _bases()) == (9, 9)

    def test_low_lives_come_first(self) -> None:
        cmdr = _placed("commander", final_lives=1)
        medic = _placed("medic", (7, 7))
        assert _choose(cmdr, [cmdr, medic], _bases()) == (7, 7)

    def test_low_shots_come_first(self) -> None:
        cmdr = _placed("commander", final_shots=3)
        ammo = _placed("ammo", (7, 7))
        assert _choose(cmdr, [cmdr, ammo], _bases()) == (7, 7)

    def test_a_downed_option_comes_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pathfinding, "downed_goal", lambda *_a: (8, 8))
        cmdr = _placed("commander")
        assert _choose(cmdr, [cmdr], _bases()) == (8, 8)

    def test_a_heavy_keeps_its_action_goal(self) -> None:
        heavy = _placed("heavy")
        _remember(heavy, "blue_scout", (9, 9), NOW, "scout")
        everyone = [heavy, _enemy("scout")]
        assert _choose(heavy, everyone, _bases(), action="tag_player") == (9, 9)
        assert heavy._committed_goal == (
            (9, 9),
            True,
            NOW + GOAL_RECOMPUTE_PERIOD_TICKS,
        )

    def test_with_every_base_taken_the_action_goal_is_back(self) -> None:
        cmdr = _placed(
            "commander", neutral_base_destroyed=True, opposing_base_destroyed=True
        )
        _remember(cmdr, "blue_scout", (9, 9), NOW, "scout")
        everyone = [cmdr, _enemy("scout")]
        assert _choose(cmdr, everyone, _bases(), action="tag_player") == (9, 9)


@pytest.mark.usefixtures("best_draw")
class TestControlDrawnToNeutralBases:
    NEUTRAL: Cell = (3, 3)
    PLAIN: Cell = (3, 4)
    ENEMY: Cell = (4, 4)

    def _ctx(self) -> MapContext:
        return _grid_map(
            high_los=[self.NEUTRAL, self.PLAIN, self.ENEMY],
            base_sight={"neutral_2": {_key(self.NEUTRAL)}, "blue": {_key(self.ENEMY)}},
        )

    def _value_of(self, role: str, draws: list, **fields: Any) -> Callable:
        player = _placed(role, mode=Mode.CONTROL, **fields)
        mode_goal(player, [player], "blue", self._ctx(), NOW)
        return draws[0][1]

    @pytest.mark.parametrize("role", ["heavy", "scout"])
    def test_a_neutral_range_cell_is_worth_the_bonus(
        self, role: str, draws: list
    ) -> None:
        value_of = self._value_of(role, draws)
        assert value_of(self.NEUTRAL) == value_of(self.PLAIN) + CONTROL_BONUS

    def test_a_neutral_base_the_player_captured_is_not(self, draws: list) -> None:
        value_of = self._value_of("heavy", draws, neutral_base_destroyed=True)
        assert value_of(self.NEUTRAL) == value_of(self.PLAIN)

    def test_it_stops_at_the_cap(self, draws: list) -> None:
        ctx = self._ctx()
        ctx.cell_los_counts = {_key(self.NEUTRAL): 9, _key(self.PLAIN): 0, "9,9": 1}
        heavy = _placed("heavy", mode=Mode.CONTROL)
        mode_goal(heavy, [heavy], "blue", ctx, NOW)
        assert draws[0][1](self.NEUTRAL) == CONTROL_VALUE_CAP

    def test_the_enemy_base_bonus_stays(self, draws: list) -> None:
        value_of = self._value_of("heavy", draws)
        assert value_of(self.ENEMY) == value_of(self.PLAIN) + CONTROL_BONUS

    def test_a_captured_enemy_base_does_not_end_the_neutral_bonus(
        self, draws: list
    ) -> None:
        value_of = self._value_of("heavy", draws, opposing_base_destroyed=True)
        assert value_of(self.NEUTRAL) == value_of(self.PLAIN) + CONTROL_BONUS
        assert value_of(self.ENEMY) == value_of(self.PLAIN)


class TestCaptureRulesDoNotChange:
    def test_a_capture_costs_three_shots_and_scores_1001(self) -> None:
        cmdr = _placed("commander", ENEMY_RANGE[0])
        shots = cmdr.final_shots
        assert capture_base(cmdr, 14, NOW, _bases(), ctx=RoundContext())
        assert cmdr.final_shots == shots - 3
        assert cmdr.counters.points_scored == 1001
        assert cmdr.opposing_base_destroyed

    def test_an_ammo_pays_no_shots(self) -> None:
        ammo = _placed("ammo", ENEMY_RANGE[0], final_shots=5)
        assert capture_base(ammo, 14, NOW, _bases(), ctx=RoundContext())
        assert ammo.final_shots == 5

    def test_an_ammo_captures_with_no_shots(self) -> None:
        ammo = _placed("ammo", ENEMY_RANGE[0], final_shots=0)
        assert capture_base(ammo, 14, NOW, _bases(), ctx=RoundContext())

    def test_it_is_per_player(self) -> None:
        ctx = _bases()
        first = _placed("commander", ENEMY_RANGE[0])
        second = _placed("scout", ENEMY_RANGE[0])
        assert capture_base(first, 14, NOW, ctx, ctx=RoundContext())
        assert not second.opposing_base_destroyed
        assert capture_base(second, 14, NOW, ctx, ctx=RoundContext())

    def test_it_is_per_base(self) -> None:
        cmdr = _placed("commander", ENEMY_RANGE[0])
        assert capture_base(cmdr, 14, NOW, _bases(), ctx=RoundContext())
        assert not cmdr.neutral_base_destroyed

    def test_the_round_end_award_still_runs_for_what_is_left(self) -> None:
        cmdr = _placed("commander", ENEMY_RANGE[0])
        capture_base(cmdr, 14, NOW, _bases(), ctx=RoundContext())
        award_bases(cmdr, NOW, ctx=RoundContext())
        assert cmdr.neutral_base_destroyed
        assert cmdr.counters.points_scored == 2002


@pytest.mark.django_db
class TestMapLessRoundsNeverReachIt:
    def test_a_seeded_map_less_round_leaves_it_all_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_args: object) -> None:
            raise AssertionError("a map-less round reached a Base goal")

        monkeypatch.setattr(pathfinding, "base_goal", fail)
        red, blue = _rosters("LCNoMap")
        random.seed(7)
        _, red_players, blue_players = BatchSimulator()._simulate_round(red, blue)
        assert all(p.base_in_reach is None for p in red_players + blue_players)


@pytest.mark.django_db
class TestLiveCapturesOnSyracuse:
    def test_both_teams_capture_the_enemy_base_during_the_round(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: set[tuple[str, int]] = set()
        real = entrypoints._capture_base_shared

        def spy(player: PlayerState, base_id: int, *rest: Any, **kwargs: Any) -> bool:
            ok = real(player, base_id, *rest, **kwargs)
            if ok:
                captured.add((player.team_color, base_id))
            return ok

        monkeypatch.setattr(entrypoints, "_capture_base_shared", spy)
        run_probe(_syracuse(), [1, 2])
        # Blue on red's Base needs the loader repair: that range starts in a pocket.
        assert ("blue", 13) in captured, captured
        assert ("red", 14) in captured, captured


_FLOOR: list[list[int]] = [[1] * 12 for _ in range(12)]


@pytest.mark.django_db
class TestSerialEqualsParallelOnBaseGoals:
    """A Base goal adds a draw over cells that come from a frozenset, whose order
    differs per process, so serial == parallel is pinned on a map with Bases in range.
    """

    def test_one_seed_matches_across_workers(self) -> None:
        from core.models import BaseSightLineConfig

        red, _ = make_team_with_slots("LCRed")
        blue, _ = make_team_with_slots("LCBlue")
        arena = _move01_make_map("BaseGoalArena", zone_data=_FLOOR)
        ranges = {"red": [[1, 1], [1, 2]], "blue": [[10, 10], [10, 9]]}
        for base_type, cells in ranges.items():
            BaseSightLineConfig.objects.filter(
                arena_map=arena, base_type=base_type
            ).update(visible_cells=cells)
        BaseSightLineConfig.objects.create(
            arena_map=arena,
            base_type="neutral_1",
            zone_size=100,
            visible_cells=[[6, 6], [5, 6], [6, 5]],
        )
        sim = BatchSimulator()
        serial = sim.run(red, blue, n=2, master_seed=321, arena_map=arena)
        parallel = sim.run(red, blue, n=2, master_seed=321, arena_map=arena, workers=2)
        assert serial == parallel
