"""MECH-17b (spec #550, Action goals): players walk to where they have a shot or can
Resupply, act on what they remember, and search toward the enemy base with nothing
remembered. Hand-built ``PlayerState`` and ``MapContext`` at the ``choose_goal_cell``
and ``plan_action`` seams, plus seeded Syracuse rounds for the engagement guard.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from matches.round_probe import run_probe
from matches.sim_helpers import combat, pathfinding
from matches.sim_helpers.combat import plan_action
from matches.sim_helpers.follow_rule import BandCell, Chase, Trail
from matches.sim_helpers.goal_scoring import enemy_base_cells
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.modes import (
    TRAIL_COST,
    Mode,
    attack_tag_goal,
    mode_goal,
    trail_costs,
)
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.time_constants import (
    GOAL_RECOMPUTE_PERIOD_TICKS,
    HELD_GOAL_EXPIRY,
    RESPAWN_TICKS,
)
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_batch_sim import _move01_make_map
from matches.tests.test_modes import (
    _choose,
    _enemy,
    _grid_map,
    _placed,
    _player,
    _remember,
    _syracuse,
)

Cell = tuple[int, int]

TAG_ACTIONS = ["tag_player", "missile_player"]
TARGET: Cell = (5, 5)
VIEW: list[Cell] = [(5, 2), (5, 8)]


@pytest.fixture
def best_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    """Roll 0 always takes the best-scored candidate."""
    monkeypatch.setattr("random.random", lambda: 0.0)


@pytest.fixture
def draws(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list, Callable]]:
    """Every ``(candidates, value_of)`` ``pathfinding`` hands to ``draw_goal``."""
    calls: list[tuple[list, Callable]] = []
    real = pathfinding.draw_goal

    def spy(player: PlayerState, candidates: list, value_of: Callable, *rest: Any):
        calls.append((list(candidates), value_of))
        return real(player, candidates, value_of, *rest)

    monkeypatch.setattr(pathfinding, "draw_goal", spy)
    return calls


def _sees_target(**fields: Any) -> MapContext:
    return _grid_map(sight={"5,5": set(map(_key, VIEW))}, **fields)


def _key(cell: Cell) -> str:
    return f"{cell[0]},{cell[1]}"


def _set_an_action_goal(player: PlayerState) -> bool:
    committed = player._committed_goal
    return committed is not None and committed[1]


def _assert_a_full_value_draw_over(draws: list, candidates: list[Cell]) -> None:
    assert len(draws) == 1
    drawn, value_of = draws[0]
    assert drawn == candidates
    assert {value_of(cell) for cell in candidates} == {1.0}


@pytest.mark.usefixtures("best_draw")
class TestTagGoalSeesItsTarget:
    @pytest.mark.parametrize("action", TAG_ACTIONS)
    def test_the_nearest_remembered_enemy_is_seen_not_stood_on(
        self, action: str, draws: list
    ) -> None:
        cmdr = _placed("commander", mode=Mode.CONTROL)
        _remember(cmdr, "blue_scout", TARGET, 100, "scout")
        goal = _choose(cmdr, [cmdr, _enemy("scout")], _sees_target(), action=action)
        assert goal == (5, 2)
        _assert_a_full_value_draw_over(draws, VIEW)

    def test_the_attack_target_is_seen_not_stood_on(self, draws: list) -> None:
        cmdr = _placed("commander", mode=Mode.ATTACK)
        _remember(cmdr, "blue_medic", TARGET, 100, "medic")
        _remember(cmdr, "blue_scout", (1, 1), 100, "scout")
        everyone = [cmdr, _enemy("medic"), _enemy("scout")]
        goal = _choose(cmdr, everyone, _sees_target(), action="tag_player")
        assert goal == (5, 2)
        _assert_a_full_value_draw_over(draws, VIEW)

    def test_it_is_an_action_goal_on_the_cadence(self) -> None:
        cmdr = _placed("commander", mode=Mode.CONTROL)
        _remember(cmdr, "blue_scout", TARGET, 100, "scout")
        goal = _choose(
            cmdr, [cmdr, _enemy("scout")], _sees_target(), action="tag_player"
        )
        expiry = 100 + GOAL_RECOMPUTE_PERIOD_TICKS
        assert cmdr._committed_goal == (goal, True, expiry)

    def test_with_no_sight_data_the_goal_is_the_targets_cell(self) -> None:
        cmdr = _placed("commander", mode=Mode.CONTROL)
        _remember(cmdr, "blue_scout", TARGET, 100, "scout")
        ctx = _grid_map()
        assert _choose(cmdr, [cmdr, _enemy("scout")], ctx, action="tag_player") == (
            TARGET
        )

    def test_with_no_reachable_cell_the_goal_is_the_targets_cell(self) -> None:
        cmdr = _placed("commander", mode=Mode.CONTROL)
        _remember(cmdr, "blue_scout", TARGET, 100, "scout")
        ctx = _grid_map(sight={"5,5": {"50,50"}})
        assert _choose(cmdr, [cmdr, _enemy("scout")], ctx, action="tag_player") == (
            TARGET
        )


@pytest.mark.usefixtures("best_draw")
class TestResupplyGoalSeesItsTarget:
    def test_the_neediest_ally_is_seen_not_stood_on(self, draws: list) -> None:
        medic = _placed("medic", mode=Mode.CONTROL)
        ally = _placed("scout", TARGET, final_lives=1)
        goal = _choose(medic, [medic, ally], _sees_target(), action="resupply_ally")
        assert goal == (5, 2)
        _assert_a_full_value_draw_over(draws, VIEW)

    def test_with_no_reachable_cell_the_goal_is_the_allys_cell(self) -> None:
        medic = _placed("medic", mode=Mode.CONTROL)
        ally = _placed("scout", TARGET, final_lives=1)
        ctx = _grid_map(sight={"5,5": {"50,50"}})
        assert _choose(medic, [medic, ally], ctx, action="resupply_ally") == TARGET


@pytest.mark.usefixtures("best_draw")
class TestSearchWithNoRememberedEnemy:
    @pytest.mark.parametrize("action", TAG_ACTIONS)
    def test_it_draws_over_the_enemy_base_as_an_action_goal(
        self, action: str, draws: list
    ) -> None:
        cmdr = _placed("commander", mode=Mode.CONTROL)
        ctx = _grid_map()
        goal = _choose(cmdr, [cmdr, _enemy("scout")], ctx, action=action)
        base = enemy_base_cells("blue", ctx)
        assert goal in base
        _assert_a_full_value_draw_over(draws, base)
        expiry = 100 + GOAL_RECOMPUTE_PERIOD_TICKS
        assert cmdr._committed_goal == (goal, True, expiry)

    def test_a_held_positioning_goal_yields_to_the_search(self) -> None:
        cmdr = _placed(
            "commander",
            mode=Mode.CONTROL,
            _committed_goal=((1, 1), False, HELD_GOAL_EXPIRY),
        )
        ctx = _grid_map()
        goal = _choose(cmdr, [cmdr, _enemy("scout")], ctx, action="tag_player")
        assert goal in enemy_base_cells("blue", ctx)

    @pytest.mark.parametrize("cell", [(1, 1), (6, 6), (10, 0)])
    def test_moving_an_unremembered_enemy_does_not_change_the_goal(
        self, cell: Cell
    ) -> None:
        cmdr = _placed("commander", mode=Mode.CONTROL)
        enemy = _enemy("scout")
        enemy.cell_row, enemy.cell_col = cell
        ctx = _grid_map()
        assert _choose(cmdr, [cmdr, enemy], ctx, action="tag_player") == _choose(
            _placed("commander", mode=Mode.CONTROL),
            [_enemy("scout")],
            ctx,
            action="tag_player",
        )

    def test_a_chased_enemy_alone_in_memory_means_a_search(self) -> None:
        enemy = _enemy("scout")
        enemy.last_downed_time = 95
        enemy._trail = Trail(start_tick=95)
        cmdr = _placed("commander", mode=Mode.CONTROL)
        cmdr._chases[enemy.tag_id_key] = Chase(trail_start=95, banned=True)
        _remember(cmdr, enemy.tag_id_key, TARGET, 100, "scout")
        ctx = _sees_target()
        goal = _choose(cmdr, [cmdr, enemy], ctx, action="tag_player")
        assert goal in enemy_base_cells("blue", ctx)
        # An action goal, not the enemy-base default below positioning.
        assert _set_an_action_goal(cmdr)

    def test_with_no_enemy_base_the_positioning_rule_runs(self) -> None:
        cmdr = _placed("commander", mode=Mode.CONTROL)
        ctx = _grid_map(high_los=[(2, 2)])
        ctx.spawn_cells = {}
        goal = _choose(cmdr, [cmdr, _enemy("scout")], ctx, action="tag_player")
        assert goal == (2, 2)
        assert cmdr._committed_goal == ((2, 2), False, HELD_GOAL_EXPIRY)


class TestDownWindow:
    """A remembered Down only counts for as long as a Down lasts."""

    NOW = 100

    def _cmdr(self, age: int, status: str) -> tuple[PlayerState, list[PlayerState]]:
        cmdr = _placed("commander")
        _remember(cmdr, "blue_medic", TARGET, self.NOW - age, "medic", status=status)
        return cmdr, [cmdr, _enemy("medic")]

    def test_a_down_inside_the_window_is_not_an_attack_target(self) -> None:
        cmdr, everyone = self._cmdr(RESPAWN_TICKS, "downed")
        assert attack_tag_goal(cmdr, everyone, "blue", self.NOW) is None

    def test_a_down_past_the_window_is_an_attack_target_again(self) -> None:
        cmdr, everyone = self._cmdr(RESPAWN_TICKS + 1, "downed")
        assert attack_tag_goal(cmdr, everyone, "blue", self.NOW) == TARGET

    def test_the_attack_positioning_goal_reads_the_window_too(self) -> None:
        cmdr, everyone = self._cmdr(RESPAWN_TICKS + 1, "downed")
        ctx = _sees_target(high_los=[(9, 9)])
        assert mode_goal(cmdr, everyone, "blue", ctx, self.NOW) in VIEW

    def _trail_scene(self, age: int, status: str) -> tuple[PlayerState, PlayerState]:
        cmdr = _placed("commander")
        enemy = _enemy("medic")
        # A Resupply reopened the Trail after the Down the player remembers.
        enemy.last_downed_time = self.NOW - 1
        enemy._trail = Trail(start_tick=self.NOW - 1, band={(0, 2): BandCell(0, 0)})
        _remember(
            cmdr, enemy.tag_id_key, (0, 2), self.NOW - age, "medic", status=status
        )
        return cmdr, enemy

    @pytest.mark.parametrize("status", ["downed", "reset_window"])
    def test_a_trail_is_charged_inside_the_window(self, status: str) -> None:
        cmdr, enemy = self._trail_scene(RESPAWN_TICKS, status)
        assert trail_costs(cmdr, [cmdr, enemy], self.NOW) == {(0, 2): TRAIL_COST}

    @pytest.mark.parametrize("status", ["downed", "reset_window"])
    def test_a_trail_is_free_past_the_window(self, status: str) -> None:
        cmdr, enemy = self._trail_scene(RESPAWN_TICKS + 1, status)
        assert trail_costs(cmdr, [cmdr, enemy], self.NOW) == {}


class TestResupplyNeedsLineOfSight:
    """On a map round a Resupply needs line of sight; map-less rounds keep the zone."""

    def _ctx(self) -> MapContext:
        return _grid_map(sight={"0,0": {"3,3"}})

    def _scene(
        self, ally_cell: Cell, ally_zone: int
    ) -> tuple[PlayerState, PlayerState]:
        medic = _placed("medic", current_zone=1)
        ally = _placed("scout", ally_cell, current_zone=ally_zone, final_lives=1)
        return medic, ally

    def _plan_resupply(
        self,
        monkeypatch: pytest.MonkeyPatch,
        medic: PlayerState,
        ally: PlayerState,
        ctx: MapContext | None,
    ) -> list:
        real = combat.random.choices
        monkeypatch.setattr(
            combat.random,
            "choices",
            lambda population, *rest: (
                ["resupply_ally"]
                if "resupply_ally" in population
                else real(population, *rest)
            ),
        )
        return plan_action(medic, [medic, ally], 100, ctx, time_domain="ticks")

    def test_an_ally_in_the_zone_behind_a_wall_is_no_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        medic, ally = self._scene((7, 7), ally_zone=1)
        assert self._plan_resupply(monkeypatch, medic, ally, self._ctx()) == []

    def test_an_ally_in_sight_in_another_zone_is_a_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        medic, ally = self._scene((3, 3), ally_zone=2)
        plans = self._plan_resupply(monkeypatch, medic, ally, self._ctx())
        assert [plan["target"] for plan in plans] == [ally]

    def test_a_map_less_round_keeps_the_zone_rule(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        medic, ally = self._scene((3, 3), ally_zone=1)
        plans = self._plan_resupply(monkeypatch, medic, ally, None)
        assert [plan["target"] for plan in plans] == [ally]

    def test_a_map_less_round_ignores_sight_for_another_zone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        medic, ally = self._scene((3, 3), ally_zone=2)
        assert self._plan_resupply(monkeypatch, medic, ally, None) == []


class TestEmptyActionsKeepSettingGoals:
    """Decision 11: a roll that finds no target stays a roll and still sets a goal.
    Both prototypes that took this away collapsed the game (spec #550)."""

    def _roll(self, monkeypatch: pytest.MonkeyPatch, action: str) -> None:
        monkeypatch.setattr(combat.random, "choices", lambda *_a, **_k: [action])

    @pytest.mark.usefixtures("best_draw")
    def test_an_empty_tag_roll_sets_an_action_goal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cmdr = _placed("commander", mode=Mode.CONTROL)
        enemy = _enemy("scout")
        _remember(cmdr, enemy.tag_id_key, TARGET, 100, "scout")
        ctx = _sees_target()
        self._roll(monkeypatch, "tag_player")
        # The enemy at (11, 11) is out of sight, so the Shot has no target.
        assert plan_action(cmdr, [cmdr, enemy], 100, ctx, time_domain="ticks") == []
        goal = _choose(cmdr, [cmdr, enemy], ctx, action=cmdr.last_chosen_action)
        assert goal in VIEW
        assert _set_an_action_goal(cmdr)

    @pytest.mark.usefixtures("best_draw")
    def test_an_empty_resupply_roll_sets_an_action_goal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        medic = _placed("medic", mode=Mode.CONTROL)
        ally = _placed("scout", TARGET, final_lives=1)
        ctx = _sees_target()
        self._roll(monkeypatch, "resupply_ally")
        assert plan_action(medic, [medic, ally], 100, ctx, time_domain="ticks") == []
        goal = _choose(medic, [medic, ally], ctx, action=medic.last_chosen_action)
        assert goal in VIEW
        assert _set_an_action_goal(medic)


@pytest.mark.django_db
class TestSearchOnSyracuse:
    """The search must work on the map it was specified for, whose base cells are in
    the wall and whose red base opens on a pocket cut off from the main floor."""

    @pytest.mark.parametrize("enemy, team", [("red", "blue"), ("blue", "red")])
    def test_a_player_with_no_memory_gets_a_goal_near_the_enemy_base(
        self, enemy: str, team: str
    ) -> None:
        ctx = _syracuse()
        base = ctx.spawn_cells[enemy]
        start = ctx.team_spawn_pools[team][0]
        # MECH-17c: a Commander with a Base left goes for that first, and searches once
        # its Bases are taken. The search itself is the same for every Role.
        player = _player(
            "commander",
            team,
            cell_row=start[0],
            cell_col=start[1],
            neutral_base_destroyed=True,
            opposing_base_destroyed=True,
        )
        goal = _choose(player, [player], ctx, action="tag_player")
        assert goal in ctx.walk_distances(start)
        assert abs(goal[0] - base[0]) + abs(goal[1] - base[1]) <= abs(
            start[0] - base[0]
        ) + abs(start[1] - base[1])
        assert _set_an_action_goal(player)


@pytest.mark.django_db
class TestEngagementGuard:
    """A goal change must not quietly stop the game. The "empty rolls set no goal"
    prototype scored 35 planned Shots; the lowest seed before this task was 88.

    Seeds 12, 16, 21 and 22 because they tell the two apart: 125.6 as built, 65.8 when
    an empty tag roll sets no goal. They are picked for that. Since MECH-17c a
    Commander's Base goal also walks it to the enemy, so over seeds 1-24 the bug no
    longer lowers Commander Shots (95.2 as built, 97.9 with it), while Heavy (88.0 to
    54.6) and Scout (83.8 to 45.7) fall. Re-run the mutation after any RNG change.
    """

    def test_commanders_keep_shooting_on_syracuse(self) -> None:
        result = run_probe(_syracuse(), [12, 16, 21, 22])
        assert result.roles["commander"].tags_per_round >= 70


# Walls, so a goal that sees a target differs from the target's own cell.
_WALLED: list[list[int]] = [
    [1 if c != 6 or r in (2, 9) else 0 for c in range(12)] for r in range(12)
]


@pytest.mark.django_db
class TestSerialEqualsParallelOnActionGoals:
    """The action goals add draws on map rounds, over a sight set whose frozenset order
    differs per process, so serial == parallel is pinned on a map with walls."""

    def test_one_seed_matches_across_workers(self) -> None:
        red, _ = make_team_with_slots("AGRed")
        blue, _ = make_team_with_slots("AGBlue")
        arena = _move01_make_map("ActionGoalArena", zone_data=_WALLED)
        sim = BatchSimulator()
        serial = sim.run(red, blue, n=2, master_seed=321, arena_map=arena)
        parallel = sim.run(red, blue, n=2, master_seed=321, arena_map=arena, workers=2)
        assert serial == parallel
