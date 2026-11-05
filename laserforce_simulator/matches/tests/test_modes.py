"""MECH-07: Player Modes (spec #542).

MECH-07a: Mode state, switching and the weight nudge. Hand-built ``PlayerState``
and ``MapContext`` at the ``sim_helpers.modes`` seam, plus seeded Syracuse rounds
for the tick-loop wiring.
"""

from __future__ import annotations

import pathlib
import random
import textwrap
from typing import Any, Callable
from unittest import mock

import pytest

from matches.sim_helpers import combat, modes, pathfinding
from matches.sim_helpers.goal_scoring import enemy_base_cells
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.modes import (
    Mode,
    apply_mode_weights,
    attack_tag_goal,
    default_mode,
    mode_goal,
    update_mode,
)
from matches.sim_helpers.pathfinding import choose_goal_cell
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.time_constants import HELD_GOAL_EXPIRY, _stale_table
from matches.simulation import BatchSimulator, entrypoints


def _player(role: str, team: str = "red", **fields: object) -> PlayerState:
    player = PlayerState(
        tag_id=f"{team}_{role}",
        name=role,
        team_color=team,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=10,
        starting_shots=20,
        final_lives=0,
        final_shots=0,
    )
    player.final_lives = player.max_lives
    player.final_shots = player.max_shots
    for name, value in fields.items():
        setattr(player, name, value)
    return player


_DEFAULTS = {
    "commander": Mode.ATTACK,
    "scout": Mode.ATTACK,
    "heavy": Mode.CONTROL,
    "medic": Mode.DEFENSE,
    "ammo": Mode.DEFENSE,
}


class TestDefaultMode:
    @pytest.mark.parametrize("role, mode", _DEFAULTS.items())
    def test_each_role(self, role: str, mode: Mode) -> None:
        assert default_mode(role) is mode

    def test_an_unknown_role_attacks(self) -> None:
        # PlayerState tolerates an unknown role (its max_lives falls back too).
        assert default_mode("goalie") is Mode.ATTACK

    @pytest.mark.parametrize("role, mode", _DEFAULTS.items())
    def test_a_fresh_player_starts_in_it(self, role: str, mode: Mode) -> None:
        player = _player(role)
        assert player.mode is mode
        assert player.mode_since == 0
        assert player.mode_next_check == 0
        assert player.mode_ticks == {}
        assert player.mode_hold_until is None


def _low_commander(**fields: object) -> PlayerState:
    """A Commander whose lives trigger row 1 and who always wins the roll."""
    player = _player("commander", game_awareness=100)
    player.final_lives = player.max_lives // 2
    for name, value in fields.items():
        setattr(player, name, value)
    return player


class TestDwellAndCheck:
    def test_mode_ticks_adds_one_per_call_to_the_current_mode(self) -> None:
        player = _player("commander")
        for tick in range(5):
            update_mode(player, tick)
        assert player.mode_ticks == {Mode.ATTACK: 5}

    def test_no_switch_inside_the_dwell(self) -> None:
        player = _low_commander()
        update_mode(player, 59)
        assert player.mode is Mode.ATTACK

    def test_switches_when_the_dwell_ends(self) -> None:
        player = _low_commander()
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL
        assert player.mode_since == 60

    def test_the_dwell_counts_from_the_last_switch(self) -> None:
        player = _low_commander(mode_since=100)
        update_mode(player, 159)
        assert player.mode is Mode.ATTACK
        update_mode(player, 160)
        assert player.mode is Mode.CONTROL

    def test_checks_only_every_ten_ticks(self) -> None:
        player = _player("commander", game_awareness=100)
        update_mode(player, 60)  # nothing wrong: a check that finds no reason
        assert player.mode_next_check == 70
        player.final_lives = player.max_lives // 2
        update_mode(player, 69)
        assert player.mode is Mode.ATTACK
        update_mode(player, 70)
        assert player.mode is Mode.CONTROL
        assert player.mode_next_check == 80

    def test_mode_ticks_credits_the_mode_held_when_the_tick_began(self) -> None:
        player = _low_commander()
        for tick in range(58, 63):
            update_mode(player, tick)
        # 58, 59 and 60 count as Attack (the switch lands during 60), 61 and 62
        # as Control: the total is one per tick.
        assert player.mode_ticks == {Mode.ATTACK: 3, Mode.CONTROL: 2}


class TestRowOneLowResources:
    @pytest.mark.parametrize("role", ["commander", "scout"])
    def test_lives_at_half_wants_control(self, role: str) -> None:
        player = _player(role, game_awareness=100, max_lives=20, final_lives=10)
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL

    @pytest.mark.parametrize("role", ["commander", "scout"])
    def test_shots_at_half_wants_control(self, role: str) -> None:
        player = _player(role, game_awareness=100, max_shots=20, final_shots=10)
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL

    @pytest.mark.parametrize("role", ["commander", "scout"])
    def test_just_above_half_keeps_the_default(self, role: str) -> None:
        player = _player(
            role,
            game_awareness=100,
            max_lives=20,
            final_lives=11,
            max_shots=20,
            final_shots=11,
        )
        update_mode(player, 60)
        assert player.mode is Mode.ATTACK

    @pytest.mark.parametrize(
        "role, mode",
        [
            ("heavy", Mode.CONTROL),
            ("medic", Mode.DEFENSE),
            ("ammo", Mode.DEFENSE),
        ],
    )
    def test_other_roles_ignore_it(self, role: str, mode: Mode) -> None:
        player = _player(role, game_awareness=100, final_lives=1, final_shots=1)
        update_mode(player, 60)
        assert player.mode is mode

    def test_recovered_resources_return_to_the_default(self) -> None:
        player = _player("commander", game_awareness=100, mode=Mode.CONTROL)
        update_mode(player, 60)
        assert player.mode is Mode.ATTACK


def _heavy_entry(tick: int, **fields: object) -> dict:
    return {"cell": (3, 3), "timestamp": tick, "role": "heavy", **fields}


class TestRowTwoHeavyDowned:
    @pytest.mark.parametrize(
        "role, wanted",
        [
            ("commander", Mode.CONTROL),
            ("medic", Mode.CONTROL),
            ("ammo", Mode.CONTROL),
        ],
    )
    def test_a_heavy_seen_downed(self, role: str, wanted: Mode) -> None:
        player = _player(role, game_awareness=100)
        player.player_memory["red_heavy"] = _heavy_entry(55, status="downed")
        update_mode(player, 60)
        assert player.mode is wanted

    def test_a_scout_wants_attack(self) -> None:
        player = _player("scout", game_awareness=100, mode=Mode.CONTROL)
        player.player_memory["red_heavy"] = _heavy_entry(55, status="downed")
        update_mode(player, 60)
        assert player.mode is Mode.ATTACK

    def test_the_reset_window_counts_as_downed(self) -> None:
        player = _player("medic", game_awareness=100)
        player.player_memory["red_heavy"] = _heavy_entry(55, status="reset_window")
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL

    def test_an_active_heavy_does_not_trigger(self) -> None:
        player = _player("medic", game_awareness=100)
        player.player_memory["red_heavy"] = _heavy_entry(55, status="active")
        update_mode(player, 60)
        assert player.mode is Mode.DEFENSE

    def test_a_missing_status_counts_as_active(self) -> None:
        player = _player("medic", game_awareness=100)
        player.player_memory["red_heavy"] = _heavy_entry(55)
        update_mode(player, 60)
        assert player.mode is Mode.DEFENSE

    def test_an_unseen_downed_heavy_does_not_trigger(self) -> None:
        # The real Heavy may well be Downed: memory is all a player can read.
        player = _player("medic", game_awareness=100)
        update_mode(player, 60)
        assert player.mode is Mode.DEFENSE

    def test_the_entry_at_the_staleness_limit_still_triggers(self) -> None:
        player = _player("medic", game_awareness=100)
        player.player_memory["red_heavy"] = _heavy_entry(80, status="downed")
        update_mode(player, 200)  # age 120 ticks, the Heavy limit
        assert player.mode is Mode.CONTROL

    def test_a_stale_entry_stops_triggering(self) -> None:
        player = _player("medic", game_awareness=100)
        player.player_memory["red_heavy"] = _heavy_entry(79, status="downed")
        update_mode(player, 200)  # age 121
        assert player.mode is Mode.DEFENSE

    def test_the_enemy_heavy_does_not_trigger(self) -> None:
        player = _player("medic", game_awareness=100)
        player.player_memory["blue_heavy"] = _heavy_entry(55, status="downed")
        update_mode(player, 60)
        assert player.mode is Mode.DEFENSE

    def test_a_blue_player_reads_the_blue_heavy(self) -> None:
        player = _player("medic", team="blue", game_awareness=100)
        player.player_memory["blue_heavy"] = _heavy_entry(55, status="downed")
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL

    def test_a_heavy_has_no_row_two(self) -> None:
        player = _player("heavy", game_awareness=100, mode=Mode.ATTACK)
        player.player_memory["red_heavy"] = _heavy_entry(55, status="downed")
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL

    def test_row_one_wins_over_row_two(self) -> None:
        # Only a Scout tells them apart: row 1 wants Control, row 2 Attack.
        player = _player("scout", game_awareness=100, final_lives=1)
        player.player_memory["red_heavy"] = _heavy_entry(55, status="downed")
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL


class TestRowThreeHeavyLosing:
    def test_a_heavy_on_a_losing_team_wants_attack(self) -> None:
        player = _player(
            "heavy",
            game_awareness=100,
            score_broadcast_state={"winning_team": "blue", "timestamp": 360},
        )
        update_mode(player, 400)
        assert player.mode is Mode.ATTACK

    @pytest.mark.parametrize(
        "broadcast",
        [
            {"winning_team": "red", "timestamp": 360},
            {"winning_team": "tied", "timestamp": 360},
            {},
        ],
        ids=["winning", "tied", "no-broadcast"],
    )
    def test_not_losing_keeps_control(self, broadcast: dict) -> None:
        player = _player("heavy", game_awareness=100, score_broadcast_state=broadcast)
        update_mode(player, 400)
        assert player.mode is Mode.CONTROL

    @pytest.mark.parametrize(
        "role, mode",
        [
            ("commander", Mode.ATTACK),
            ("scout", Mode.ATTACK),
            ("medic", Mode.DEFENSE),
            ("ammo", Mode.DEFENSE),
        ],
    )
    def test_other_roles_ignore_the_score(self, role: str, mode: Mode) -> None:
        player = _player(
            role,
            game_awareness=100,
            score_broadcast_state={"winning_team": "blue", "timestamp": 360},
        )
        update_mode(player, 400)
        assert player.mode is mode

    def test_low_resources_do_not_stop_a_losing_heavy(self) -> None:
        # Row 1 is for Commanders and Scouts, so it cannot hold a Heavy in Control.
        player = _player(
            "heavy",
            game_awareness=100,
            final_lives=1,
            score_broadcast_state={"winning_team": "blue", "timestamp": 360},
        )
        update_mode(player, 400)
        assert player.mode is Mode.ATTACK

    def test_blue_is_losing_when_red_leads(self) -> None:
        player = _player(
            "heavy",
            team="blue",
            game_awareness=100,
            score_broadcast_state={"winning_team": "red", "timestamp": 360},
        )
        update_mode(player, 400)
        assert player.mode is Mode.ATTACK


class TestTheRoll:
    @staticmethod
    def _wants_control(game_awareness: int) -> PlayerState:
        player = _low_commander()
        player.game_awareness = game_awareness
        return player

    def test_awareness_zero_never_switches(self) -> None:
        player = self._wants_control(0)
        with mock.patch("random.random", return_value=0.0):
            update_mode(player, 60)
        assert player.mode is Mode.ATTACK

    def test_awareness_one_hundred_always_switches(self) -> None:
        player = self._wants_control(100)
        with mock.patch("random.random", return_value=0.999999):
            update_mode(player, 60)
        assert player.mode is Mode.CONTROL

    def test_a_roll_below_the_share_switches(self) -> None:
        player = self._wants_control(50)
        with mock.patch("random.random", return_value=0.49):
            update_mode(player, 60)
        assert player.mode is Mode.CONTROL

    def test_a_roll_at_the_share_does_not(self) -> None:
        player = self._wants_control(50)
        with mock.patch("random.random", return_value=0.5):
            update_mode(player, 60)
        assert player.mode is Mode.ATTACK
        assert player.mode_since == 0

    def test_a_failed_roll_waits_for_the_next_check(self) -> None:
        player = self._wants_control(50)
        with mock.patch("random.random", side_effect=[0.9, 0.1]) as roll:
            update_mode(player, 60)
            update_mode(player, 65)
            assert roll.call_count == 1
            update_mode(player, 70)
        assert player.mode is Mode.CONTROL

    def test_a_mismatch_draws_exactly_once(self) -> None:
        player = self._wants_control(50)
        with mock.patch("random.random", return_value=0.9) as roll:
            update_mode(player, 60)
        assert roll.call_count == 1

    def test_no_draw_when_the_wanted_mode_matches(self) -> None:
        player = _player("commander", game_awareness=100)
        state = random.getstate()
        update_mode(player, 60)
        assert random.getstate() == state

    def test_no_draw_inside_the_dwell(self) -> None:
        player = _low_commander()
        state = random.getstate()
        update_mode(player, 59)
        assert random.getstate() == state


class TestSwitchClearsCommitment:
    def test_a_positioning_commitment_clears(self) -> None:
        player = _low_commander(_committed_goal=((4, 4), False, 1801))
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL
        assert player._committed_goal is None

    def test_an_action_commitment_stays(self) -> None:
        committed = ((4, 4), True, 64)
        player = _low_commander(_committed_goal=committed)
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL
        assert player._committed_goal == committed

    def test_no_commitment_is_fine(self) -> None:
        player = _low_commander()
        update_mode(player, 60)
        assert player._committed_goal is None

    def test_no_switch_keeps_a_positioning_commitment(self) -> None:
        committed = ((4, 4), False, 1801)
        player = _player("commander", game_awareness=100, _committed_goal=committed)
        update_mode(player, 60)
        assert player._committed_goal == committed

    def test_a_failed_roll_keeps_a_positioning_commitment(self) -> None:
        committed = ((4, 4), False, 1801)
        player = _low_commander(_committed_goal=committed, game_awareness=0)
        update_mode(player, 60)
        assert player._committed_goal == committed


def _idx(action: str) -> int:
    return combat._ACTION_IDX[action]


class TestApplyModeWeights:
    @pytest.mark.parametrize(
        "mode, boosted",
        [
            (Mode.ATTACK, {"tag_player"}),
            (Mode.DEFENSE, {"resupply_ally"}),
            (Mode.CONTROL, {"hold", "capture_base"}),
        ],
    )
    def test_each_mode_boosts_only_its_actions(
        self, mode: Mode, boosted: set[str]
    ) -> None:
        weights = [20] * len(combat._ACTION_IDX)
        apply_mode_weights(_player("scout", mode=mode), weights)
        for action, idx in combat._ACTION_IDX.items():
            assert weights[idx] == (25 if action in boosted else 20), action

    def test_rounds_to_an_int(self) -> None:
        weights = [0] * len(combat._ACTION_IDX)
        weights[_idx("tag_player")] = 30  # 37.5
        apply_mode_weights(_player("scout", mode=Mode.ATTACK), weights)
        assert weights[_idx("tag_player")] == 38
        assert isinstance(weights[_idx("tag_player")], int)

    def test_zero_stays_zero(self) -> None:
        weights = [0] * len(combat._ACTION_IDX)
        apply_mode_weights(_player("scout", mode=Mode.CONTROL), weights)
        assert weights == [0] * len(combat._ACTION_IDX)

    def test_a_negative_slot_is_left_alone(self) -> None:
        # Raw role output may be negative (a Scout out of shots); finalize clamps it.
        weights = [0] * len(combat._ACTION_IDX)
        weights[_idx("tag_player")] = -10
        apply_mode_weights(_player("scout", mode=Mode.ATTACK), weights)
        assert weights[_idx("tag_player")] == -10

    def test_draws_no_rng(self) -> None:
        state = random.getstate()
        apply_mode_weights(_player("scout"), [20] * len(combat._ACTION_IDX))
        assert random.getstate() == state


def _final_weights(player: PlayerState, movement_ctx: MapContext | None) -> list[int]:
    """The vector ``plan_action`` hands to ``random.choices``."""
    with mock.patch("random.choices", return_value=["only_move"]) as choices:
        combat.plan_action(player, [player], 100, movement_ctx, time_domain="ticks")
    return choices.call_args.args[1]


class TestPlanActionWiring:
    def test_a_map_round_gets_the_nudge(self) -> None:
        no_map = _final_weights(_player("commander", decision_making=0), None)
        on_map = _final_weights(
            _player("commander", decision_making=0), _sight_map(None)
        )
        assert no_map[_idx("tag_player")] == 80
        assert on_map[_idx("tag_player")] == 100
        no_map[_idx("tag_player")] = on_map[_idx("tag_player")]
        assert on_map == no_map

    def test_a_no_map_round_never_calls_it(self) -> None:
        with mock.patch.object(combat, "apply_mode_weights") as spy:
            _final_weights(_player("commander"), None)
        spy.assert_not_called()

    def test_runs_after_the_role_weights_and_before_cooldown_zeroing(self) -> None:
        calls: list[str] = []
        real_role = combat._get_commander_weights

        def role_spy(*args, **kwargs):
            calls.append("role")
            return real_role(*args, **kwargs)

        def cooldown_spy(*_args: object) -> float:
            calls.append("cooldown")
            return 0.0

        with (
            mock.patch.object(combat, "_get_commander_weights", role_spy),
            mock.patch.object(
                combat, "apply_mode_weights", lambda *a: calls.append("mode")
            ),
            mock.patch.object(combat, "shot_cooldown", cooldown_spy),
        ):
            _final_weights(_player("commander"), _sight_map(None))
        assert calls == ["role", "mode", "cooldown"]

    def test_runs_before_the_stamina_penalty(self) -> None:
        # The penalty scales only_move, so the Mode sees it unscaled.
        seen: list[int] = []
        player = _player("scout", decision_making=0)
        player.stamina_penalty_count = 5
        with mock.patch.object(
            combat,
            "apply_mode_weights",
            lambda _player, weights: seen.append(weights[_idx("only_move")]),
        ):
            final = _final_weights(player, _sight_map(None))
        assert seen[0] > final[_idx("only_move")]

    def test_a_shot_cooldown_still_zeroes_the_boosted_tag(self) -> None:
        player = _player("commander", decision_making=0, last_shot_time=100.0)
        weights = _final_weights(player, _sight_map(None))
        assert weights[_idx("tag_player")] == 0


def _rosters(prefix: str) -> tuple[list, list]:
    from matches.tests.conftest import make_team_with_slots

    red, _ = make_team_with_slots(f"{prefix}Red")
    blue, _ = make_team_with_slots(f"{prefix}Blue")
    return list(red.active_roster), list(blue.active_roster)


def _syracuse() -> MapContext:
    from matches.lfstats_calibration import load_map_fixture
    from matches.sim_helpers.map_loader import load_map_context

    fixture = pathlib.Path(__file__).resolve().parents[3] / "data/maps/syracuse.json"
    ctx = load_map_context(load_map_fixture(fixture))[0]
    assert ctx is not None
    return ctx


@pytest.mark.django_db
class TestTickLoopWiring:
    def test_a_no_map_round_never_touches_modes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_args: object) -> None:
            raise AssertionError("a no-map round reached the Mode layer")

        monkeypatch.setattr(entrypoints, "update_mode", fail)
        monkeypatch.setattr(combat, "apply_mode_weights", fail)
        red, blue = _rosters("NoMap")
        random.seed(7)
        _, red_players, blue_players = BatchSimulator()._simulate_round(red, blue)
        assert all(p.mode_ticks == {} for p in red_players + blue_players)

    def test_a_map_round_updates_each_alive_player_once_before_planning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        updates: list[tuple[int, str]] = []
        real_update = entrypoints.update_mode
        real_plan = BatchSimulator._plan_action
        planned: set[tuple[int, str]] = set()

        def update_spy(player: PlayerState, tick: int) -> None:
            updates.append((tick, player.tag_id))
            real_update(player, tick)

        def plan_spy(
            self: BatchSimulator,
            player: PlayerState,
            all_alive: list,
            tick: int,
            movement_ctx: MapContext | None = None,
        ) -> list:
            # Updates run in tick order, so the newest dozen hold this tick's.
            assert (tick, player.tag_id) in updates[-12:]
            planned.add((tick, player.tag_id))
            return real_plan(self, player, all_alive, tick, movement_ctx=movement_ctx)

        monkeypatch.setattr(entrypoints, "update_mode", update_spy)
        monkeypatch.setattr(BatchSimulator, "_plan_action", plan_spy)
        red, blue = _rosters("OnMap")
        random.seed(7)
        _, red_players, blue_players = BatchSimulator()._simulate_round(
            red, blue, movement_ctx=_syracuse()
        )
        assert len(updates) == len(set(updates))
        assert set(updates) == planned
        assert planned
        players = red_players + blue_players
        # mode_ticks adds up: one per update, none lost across a switch.
        for player in players:
            calls = sum(1 for _, tag in updates if tag == player.tag_id)
            assert sum(player.mode_ticks.values()) == calls
        # The wiring is live: at least one player left its default Mode.
        assert any(len(p.mode_ticks) > 1 for p in players)

    def test_control_players_hold_a_reached_goal_in_a_seeded_round(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        holds: dict[str, list[int]] = {}
        real_goal = pathfinding.mode_goal

        def goal_spy(
            player: PlayerState,
            all_alive: list,
            enemy_color: str,
            ctx: MapContext,
            tick: int,
            time_domain: str,
        ) -> tuple[int, int] | None:
            goal = real_goal(player, all_alive, enemy_color, ctx, tick, time_domain)
            if player.mode_hold_until is not None:
                assert goal == (player.cell_row, player.cell_col)
                holds.setdefault(player.tag_id, []).append(tick)
            return goal

        monkeypatch.setattr(pathfinding, "mode_goal", goal_spy)
        red, blue = _rosters("Hold")
        random.seed(7)
        BatchSimulator()._simulate_round(red, blue, movement_ctx=_syracuse())
        assert holds, "no Control player ever held a reached goal"
        for ticks in holds.values():
            run = 0
            for previous, tick in zip([None, *ticks], ticks):
                run = run + 1 if previous == tick - 1 else 1
                assert run <= 20  # the deadline tick draws again

    def test_same_seed_same_modes(self) -> None:
        red, blue = _rosters("Same")
        ctx = _syracuse()

        def run() -> tuple:
            random.seed(11)
            result, red_players, blue_players = BatchSimulator()._simulate_round(
                red, blue, movement_ctx=ctx
            )
            ticks = {p.tag_id: p.mode_ticks for p in red_players + blue_players}
            return result, ticks

        assert run() == run()


def _grid_map(
    *,
    high_los: list[tuple[int, int]] | None = None,
    strong: list[tuple[int, int]] | None = None,
    sight: dict[str, set[str]] | None = None,
    counts: dict[str, int] | None = None,
    base_sight: dict[str, set[str]] | None = None,
    size: int = 12,
) -> MapContext:
    """An open *size* x *size* floor, 5 ft a square: a step costs 0.025 of score."""
    adj = {
        (r, c): [
            (r + dr, c + dc)
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
            if 0 <= r + dr < size and 0 <= c + dc < size
        ]
        for r in range(size)
        for c in range(size)
    }
    return MapContext(
        adj=adj,
        spawn_cells={"red": (0, 0), "blue": (size - 1, size - 1)},
        zone_data=None,
        sight_data={k: frozenset(v) for k, v in (sight or {}).items()},
        base_sight_data={k: frozenset(v) for k, v in (base_sight or {}).items()},
        cell_los_counts=counts or {},
        high_los_cells=high_los or [],
        strong_spots=strong or [],
        wall_meta={},
        team_spawn_pools={},
    )


def _placed(role: str, cell: tuple[int, int] = (0, 0), **fields: Any) -> PlayerState:
    return _player(role, cell_row=cell[0], cell_col=cell[1], **fields)


def _enemy(role: str, tag_id: str | None = None) -> PlayerState:
    enemy = _player(role, team="blue", cell_row=11, cell_col=11)
    if tag_id is not None:
        enemy.tag_id = tag_id
    return enemy


def _remember(
    player: PlayerState,
    tag_id: str,
    cell: tuple[int, int],
    tick: int,
    role: str,
    **fields: object,
) -> None:
    player.player_memory[tag_id] = {
        "cell": cell,
        "timestamp": tick,
        "role": role,
        **fields,
    }


@pytest.fixture
def best_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    """Roll 0 always takes the best-scored candidate."""
    monkeypatch.setattr(modes.random, "random", lambda: 0.0)


@pytest.fixture
def draws(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list, Callable]]:
    """Every ``(candidates, value_of)`` handed to ``draw_goal``, in order."""
    calls: list[tuple[list, Callable]] = []
    real = modes.draw_goal

    def spy(player: PlayerState, candidates: list, value_of: Callable, *rest: Any):
        calls.append((list(candidates), value_of))
        return real(player, candidates, value_of, *rest)

    monkeypatch.setattr(modes, "draw_goal", spy)
    return calls


@pytest.mark.usefixtures("best_draw")
class TestAttackGoal:
    def test_no_target_draws_high_los_cells_by_rank(self) -> None:
        scout = _placed("scout")
        ctx = _grid_map(high_los=[(1, 1), (1, 2)], counts={"1,1": 1, "1,2": 10})
        assert mode_goal(scout, [scout], "blue", ctx, 100) == (1, 2)
        assert scout._goal_was_drawn is True

    def test_no_cell_at_all_returns_none(self) -> None:
        scout = _placed("scout", _goal_was_drawn=True)
        assert mode_goal(scout, [scout], "blue", _grid_map(), 100) is None
        assert scout._goal_was_drawn is False

    def test_a_cell_that_sees_the_medic_is_worth_more_than_one_that_sees_the_ammo(
        self, draws: list
    ) -> None:
        cmdr, everyone, ctx = _attack_scene()
        assert mode_goal(cmdr, everyone, "blue", ctx, 100) == (3, 2)
        candidates, value_of = draws[-1]
        assert sorted(candidates) == [(3, 2), (5, 5), (8, 7)]
        assert value_of((3, 2)) == 1.0
        assert value_of((8, 7)) == 0.7
        # Sees both: the Medic wins.
        assert value_of((5, 5)) == 1.0
        assert cmdr._goal_was_drawn is True

    def test_a_cell_seeing_both_stays_a_medic_cell_whatever_the_tag_order(
        self, draws: list
    ) -> None:
        cmdr = _placed("commander")
        everyone = [cmdr, _enemy("medic", "blue_a"), _enemy("ammo", "blue_b")]
        _remember(cmdr, "blue_a", (3, 3), 100, "medic")
        _remember(cmdr, "blue_b", (8, 8), 100, "ammo")
        ctx = _grid_map(sight={"3,3": {"5,5"}, "8,8": {"5,5"}})
        mode_goal(cmdr, everyone, "blue", ctx, 100)
        assert draws[-1][1]((5, 5)) == 1.0

    def test_a_target_with_no_reachable_candidate_falls_back_to_high_los(self) -> None:
        cmdr = _placed("commander")
        medic = _enemy("medic")
        _remember(cmdr, "blue_medic", (3, 3), 100, "medic")
        ctx = _grid_map(
            high_los=[(1, 1)],
            sight={"3,3": {"99,99"}},  # off the floor, so never reachable
            counts={"1,1": 5, "2,2": 1},
        )
        assert mode_goal(cmdr, [cmdr, medic], "blue", ctx, 100) == (1, 1)


_RANKS = {"1,1": 1, "1,2": 2, "1,3": 3, "1,4": 4, "1,5": 5}  # ranks 0 .. 1 by 0.25


@pytest.mark.usefixtures("best_draw")
class TestControlGoal:
    def _value_of(self, draws: list, player: PlayerState, ctx: MapContext) -> Callable:
        mode_goal(player, [player], "blue", ctx, 100)
        return draws[-1][1]

    @pytest.mark.parametrize(
        "role, mode",
        [
            ("commander", Mode.CONTROL),
            ("scout", Mode.CONTROL),
            ("medic", Mode.CONTROL),
            ("ammo", Mode.CONTROL),
        ],
    )
    def test_candidates_are_high_los_cells(
        self, role: str, mode: Mode, draws: list
    ) -> None:
        player = _placed(role, mode=mode)
        ctx = _grid_map(high_los=[(1, 5)], strong=[(1, 1)], counts=_RANKS)
        assert mode_goal(player, [player], "blue", ctx, 100) == (1, 5)
        assert draws[-1][0] == [(1, 5)]
        assert player._goal_was_drawn is True

    def test_a_heavys_candidates_are_strong_spots(self, draws: list) -> None:
        heavy = _placed("heavy")
        ctx = _grid_map(high_los=[(1, 5)], strong=[(1, 1)], counts=_RANKS)
        assert mode_goal(heavy, [heavy], "blue", ctx, 100) == (1, 1)
        assert draws[-1][0] == [(1, 1)]

    def test_a_heavy_falls_back_to_high_los_on_a_map_with_no_spots(self) -> None:
        heavy = _placed("heavy")
        ctx = _grid_map(high_los=[(1, 5)], counts=_RANKS)
        assert mode_goal(heavy, [heavy], "blue", ctx, 100) == (1, 5)

    def test_value_is_the_sight_rank(self, draws: list) -> None:
        heavy = _placed("heavy")
        ctx = _grid_map(strong=[(1, 2)], counts=_RANKS)
        assert self._value_of(draws, heavy, ctx)((1, 2)) == 0.25

    def test_a_cell_in_range_of_the_enemy_base_gets_the_bonus(
        self, draws: list
    ) -> None:
        heavy = _placed("heavy")
        ctx = _grid_map(strong=[(1, 2)], counts=_RANKS, base_sight={"blue": {"1,2"}})
        assert self._value_of(draws, heavy, ctx)((1, 2)) == 0.5

    def test_no_base_bonus_near_the_players_own_base(self, draws: list) -> None:
        heavy = _placed("heavy")
        ctx = _grid_map(strong=[(1, 2)], counts=_RANKS, base_sight={"red": {"1,2"}})
        assert ctx.base_in_range((1, 2)) == 14  # which is why it is not used
        assert self._value_of(draws, heavy, ctx)((1, 2)) == 0.25

    def test_no_base_bonus_after_the_base_is_destroyed(self, draws: list) -> None:
        heavy = _placed("heavy", opposing_base_destroyed=True)
        ctx = _grid_map(strong=[(1, 2)], counts=_RANKS, base_sight={"blue": {"1,2"}})
        assert self._value_of(draws, heavy, ctx)((1, 2)) == 0.25

    def _seeing(self, tick_seen: int, alive: bool = True) -> tuple:
        heavy = _placed("heavy")
        enemy = _enemy("scout", "blue_scout_1")
        _remember(heavy, "blue_scout_1", (9, 9), tick_seen, "scout")
        ctx = _grid_map(strong=[(1, 2), (1, 3)], counts=_RANKS, sight={"1,2": {"9,9"}})
        return heavy, [heavy, enemy] if alive else [heavy], ctx

    def test_a_cell_that_sees_a_fresh_enemy_gets_the_bonus(self, draws: list) -> None:
        heavy, everyone, ctx = self._seeing(100)
        mode_goal(heavy, everyone, "blue", ctx, 100)
        value_of = draws[-1][1]
        assert value_of((1, 2)) == 0.5
        assert value_of((1, 3)) == 0.5  # rank 0.5, sees nobody

    def test_a_stale_enemy_gives_no_bonus(self, draws: list) -> None:
        heavy, everyone, ctx = self._seeing(100 - 31)
        mode_goal(heavy, everyone, "blue", ctx, 100)
        assert draws[-1][1]((1, 2)) == 0.25

    def test_an_eliminated_enemy_gives_no_bonus(self, draws: list) -> None:
        heavy, everyone, ctx = self._seeing(100, alive=False)
        mode_goal(heavy, everyone, "blue", ctx, 100)
        assert draws[-1][1]((1, 2)) == 0.25

    def test_value_is_capped_at_one(self, draws: list) -> None:
        heavy = _placed("heavy")
        enemy = _enemy("scout", "blue_scout_1")
        _remember(heavy, "blue_scout_1", (9, 9), 100, "scout")
        ctx = _grid_map(
            strong=[(1, 4)],
            counts=_RANKS,
            sight={"1,4": {"9,9"}},
            base_sight={"blue": {"1,4"}},
        )
        mode_goal(heavy, [heavy, enemy], "blue", ctx, 100)
        assert draws[-1][1]((1, 4)) == 1.0  # 0.75 + 0.25 + 0.25

    def test_defense_is_left_to_the_role_logic_for_now(self) -> None:
        # #545 gives Defense its own rule; until then the caller keeps today's logic.
        medic = _placed("medic", _goal_was_drawn=True)
        ctx = _grid_map(high_los=[(1, 5)], counts=_RANKS)
        assert medic.mode is Mode.DEFENSE
        assert mode_goal(medic, [medic], "blue", ctx, 100) is None
        assert medic._goal_was_drawn is False


@pytest.mark.usefixtures("best_draw")
class TestLowHealthHeavy:
    def _low_heavy(self, mode: Mode, **fields: Any) -> PlayerState:
        heavy = _placed("heavy", mode=mode, **fields)
        heavy.final_lives = heavy.max_lives // 2
        return heavy

    @pytest.mark.parametrize("mode", list(Mode))
    def test_lives_at_half_goes_to_the_remembered_medic(self, mode: Mode) -> None:
        heavy = self._low_heavy(mode, _goal_was_drawn=True)
        _remember(heavy, "red_medic", (4, 4), 100, "medic")
        _remember(heavy, "red_ammo", (5, 5), 100, "ammo")
        ctx = _grid_map(strong=[(1, 1)], high_los=[(1, 5)], counts=_RANKS)
        assert mode_goal(heavy, [heavy], "blue", ctx, 100) == (4, 4)
        # Not drawn, so it follows the support on the 4-tick cadence.
        assert heavy._goal_was_drawn is False

    @pytest.mark.parametrize("mode", list(Mode))
    def test_shots_at_half_goes_to_the_remembered_medic(self, mode: Mode) -> None:
        heavy = _placed("heavy", mode=mode)
        heavy.final_shots = heavy.max_shots // 2
        _remember(heavy, "red_medic", (4, 4), 100, "medic")
        assert mode_goal(heavy, [heavy], "blue", _grid_map(), 100) == (4, 4)

    @pytest.mark.parametrize("mode", list(Mode))
    def test_then_the_remembered_ammo(self, mode: Mode) -> None:
        heavy = self._low_heavy(mode)
        _remember(heavy, "red_ammo", (5, 5), 100, "ammo")
        assert mode_goal(heavy, [heavy], "blue", _grid_map(), 100) == (5, 5)

    def test_a_stale_medic_is_passed_over(self) -> None:
        heavy = self._low_heavy(Mode.CONTROL)
        _remember(heavy, "red_medic", (4, 4), 100 - 121, "medic")
        _remember(heavy, "red_ammo", (5, 5), 100, "ammo")
        assert mode_goal(heavy, [heavy], "blue", _grid_map(), 100) == (5, 5)

    def test_with_neither_remembered_the_mode_rule_runs(self) -> None:
        heavy = self._low_heavy(Mode.CONTROL)
        ctx = _grid_map(strong=[(1, 1)], counts=_RANKS)
        assert mode_goal(heavy, [heavy], "blue", ctx, 100) == (1, 1)

    def test_a_supports_true_cell_is_never_used(self) -> None:
        heavy = self._low_heavy(Mode.ATTACK)
        medic = _placed("medic", (4, 4))
        assert mode_goal(heavy, [heavy, medic], "blue", _grid_map(), 100) is None

    def test_above_half_the_mode_rule_runs(self) -> None:
        heavy = _placed("heavy")
        heavy.final_lives = heavy.max_lives // 2 + 1
        heavy.final_shots = heavy.max_shots // 2 + 1
        _remember(heavy, "red_medic", (4, 4), 100, "medic")
        ctx = _grid_map(strong=[(1, 1)], counts=_RANKS)
        assert mode_goal(heavy, [heavy], "blue", ctx, 100) == (1, 1)

    def test_other_roles_do_not_seek_support(self) -> None:
        scout = _placed("scout")
        scout.final_lives = 1
        _remember(scout, "red_medic", (4, 4), 100, "medic")
        assert mode_goal(scout, [scout], "blue", _grid_map(), 100) is None


class TestAttackTagGoal:
    def test_the_nearest_target_of_either_role(self) -> None:
        cmdr, everyone, _ = _attack_scene()
        assert attack_tag_goal(cmdr, everyone, "blue", 100) == (3, 3)
        cmdr.cell_row, cmdr.cell_col = 9, 9
        assert attack_tag_goal(cmdr, everyone, "blue", 100) == (8, 8)

    def test_no_target_returns_none(self) -> None:
        scout = _placed("scout")
        assert attack_tag_goal(scout, [scout, _enemy("medic")], "blue", 100) is None

    def test_exclude_drops_a_target(self) -> None:
        cmdr, everyone, _ = _attack_scene()
        goal = attack_tag_goal(cmdr, everyone, "blue", 100, exclude={"blue_medic"})
        assert goal == (8, 8)

    def test_downed_is_out_and_reset_window_is_in(self) -> None:
        cmdr, everyone, _ = _attack_scene()
        cmdr.player_memory["blue_medic"]["status"] = "reset_window"
        assert attack_tag_goal(cmdr, everyone, "blue", 100) == (3, 3)
        cmdr.player_memory["blue_medic"]["status"] = "downed"
        assert attack_tag_goal(cmdr, everyone, "blue", 100) == (8, 8)

    def test_an_eliminated_target_is_dropped(self) -> None:
        cmdr, everyone, _ = _attack_scene()
        alive = [p for p in everyone if p.tag_id != "blue_medic"]
        assert attack_tag_goal(cmdr, alive, "blue", 100) == (8, 8)

    def test_staleness_follows_the_time_domain(self) -> None:
        cmdr, everyone, _ = _attack_scene()
        del cmdr.player_memory["blue_ammo"]
        cmdr.player_memory["blue_medic"]["timestamp"] = 100 - 61
        assert attack_tag_goal(cmdr, everyone, "blue", 100) == (3, 3)
        assert attack_tag_goal(cmdr, everyone, "blue", 100, "seconds") is None

    def test_equal_distance_breaks_on_the_cell(self) -> None:
        cmdr = _placed("commander", (5, 5))
        everyone = [cmdr, _enemy("medic"), _enemy("ammo")]
        _remember(cmdr, "blue_ammo", (4, 5), 100, "ammo")
        _remember(cmdr, "blue_medic", (5, 6), 100, "medic")
        assert attack_tag_goal(cmdr, everyone, "blue", 100) == (4, 5)
        cmdr.player_memory["blue_ammo"]["cell"] = (5, 6)
        cmdr.player_memory["blue_medic"]["cell"] = (4, 5)
        assert attack_tag_goal(cmdr, everyone, "blue", 100) == (4, 5)


@pytest.mark.usefixtures("best_draw")
class TestHolding:
    """Decision 17: a Control player stays 20 ticks on a reached drawn goal."""

    def _arrived(self, **fields: object) -> tuple[PlayerState, MapContext]:
        heavy = _placed("heavy", (1, 1), **fields)
        heavy._committed_goal = ((1, 1), False, HELD_GOAL_EXPIRY)
        ctx = _grid_map(strong=[(1, 1), (1, 5)], counts=_RANKS)
        return heavy, ctx

    def test_reaching_a_drawn_goal_starts_a_hold_on_the_current_cell(self) -> None:
        heavy, ctx = self._arrived()
        heavy.cell_row, heavy.cell_col = 1, 1
        assert mode_goal(heavy, [heavy], "blue", ctx, 100) == (1, 1)
        assert heavy.mode_hold_until == 120
        # Not a new draw: the deadline, not the commitment, keeps the player here.
        assert heavy._goal_was_drawn is False

    def test_stays_until_the_deadline_without_drawing(self, draws: list) -> None:
        heavy, ctx = self._arrived()
        mode_goal(heavy, [heavy], "blue", ctx, 100)
        # choose_goal_cell recommits the returned cell on the 4-tick cadence.
        heavy._committed_goal = ((1, 1), False, 104)
        assert mode_goal(heavy, [heavy], "blue", ctx, 119) == (1, 1)
        assert draws == []

    def test_draws_again_at_the_deadline(self, draws: list) -> None:
        heavy, ctx = self._arrived()
        mode_goal(heavy, [heavy], "blue", ctx, 100)
        heavy._committed_goal = ((1, 1), False, 104)
        assert mode_goal(heavy, [heavy], "blue", ctx, 120) == (1, 5)
        assert len(draws) == 1
        assert heavy.mode_hold_until is None
        assert heavy._goal_was_drawn is True

    def test_a_cadence_goal_reached_does_not_start_a_hold(self, draws: list) -> None:
        # Only a drawn goal is held: this commitment expires on the 4-tick cadence.
        heavy, ctx = self._arrived()
        heavy._committed_goal = ((1, 1), False, 104)
        assert mode_goal(heavy, [heavy], "blue", ctx, 100) == (1, 5)
        assert heavy.mode_hold_until is None

    def test_a_reached_action_goal_does_not_start_a_hold(self) -> None:
        heavy, ctx = self._arrived()
        heavy._committed_goal = ((1, 1), True, HELD_GOAL_EXPIRY)
        assert mode_goal(heavy, [heavy], "blue", ctx, 100) == (1, 5)
        assert heavy.mode_hold_until is None

    def test_no_hold_without_a_commitment(self) -> None:
        heavy, ctx = self._arrived()
        heavy._committed_goal = None
        assert mode_goal(heavy, [heavy], "blue", ctx, 100) == (1, 5)

    def test_an_attack_player_does_not_hold(self) -> None:
        scout = _placed("scout", (1, 1))
        scout._committed_goal = ((1, 1), False, HELD_GOAL_EXPIRY)
        ctx = _grid_map(high_los=[(1, 5)], counts=_RANKS)
        assert mode_goal(scout, [scout], "blue", ctx, 100) == (1, 5)
        assert scout.mode_hold_until is None

    def test_a_hold_broken_by_an_action_goal_is_dropped(self) -> None:
        # The action walked the player off the held cell; do not freeze them where
        # the action goal happened to end.
        heavy, ctx = self._arrived()
        mode_goal(heavy, [heavy], "blue", ctx, 100)
        heavy.cell_row, heavy.cell_col = 3, 3
        heavy._committed_goal = ((3, 3), True, 110)
        assert mode_goal(heavy, [heavy], "blue", ctx, 110) == (1, 5)
        assert heavy.mode_hold_until is None

    def test_a_hold_with_the_commitment_cleared_is_dropped(self) -> None:
        # A reactive override or a Stationary exit clears the commitment.
        heavy, ctx = self._arrived()
        mode_goal(heavy, [heavy], "blue", ctx, 100)
        heavy._committed_goal = None
        assert mode_goal(heavy, [heavy], "blue", ctx, 110) == (1, 5)
        assert heavy.mode_hold_until is None

    def test_a_low_health_heavy_leaves_the_hold_for_its_support(self) -> None:
        heavy, ctx = self._arrived()
        mode_goal(heavy, [heavy], "blue", ctx, 100)
        heavy.final_lives = 1
        _remember(heavy, "red_medic", (4, 4), 105, "medic")
        assert mode_goal(heavy, [heavy], "blue", ctx, 105) == (4, 4)
        assert heavy.mode_hold_until is None

    def test_a_mode_switch_ends_the_hold(self) -> None:
        player = _low_commander(mode=Mode.ATTACK, mode_hold_until=70)
        update_mode(player, 60)
        assert player.mode is Mode.CONTROL
        assert player.mode_hold_until is None

    def test_no_switch_keeps_the_hold(self) -> None:
        player = _player("heavy", mode_hold_until=70)
        update_mode(player, 60)
        assert player.mode_hold_until == 70


def _choose(
    player: PlayerState,
    everyone: list,
    ctx: MapContext,
    tick: int = 100,
    action: str = "",
) -> tuple[int, int] | None:
    return choose_goal_cell(
        player, everyone, ctx.get_spawn_cells(), ctx, action, tick, "ticks"
    )


@pytest.mark.usefixtures("best_draw")
class TestChooseGoalCellUsesModes:
    def test_positioning_comes_from_the_mode_rule(self) -> None:
        cmdr, everyone, ctx = _attack_scene()
        assert _choose(cmdr, everyone, ctx) == (3, 2)
        assert cmdr._committed_goal == ((3, 2), False, HELD_GOAL_EXPIRY)

    def test_the_enemy_base_default_stays_below_it(self) -> None:
        scout = _placed("scout")
        ctx = _grid_map()
        assert _choose(scout, [scout], ctx) in enemy_base_cells("blue", ctx)

    def test_a_critical_override_stays_above_it(self) -> None:
        cmdr, everyone, ctx = _attack_scene()
        cmdr.final_lives = 1
        medic = _placed("medic", (7, 7))
        assert _choose(cmdr, [*everyone, medic], ctx) == (7, 7)

    def test_an_attack_goal_is_not_teamwork_biased(self) -> None:
        cmdr, everyone, ctx = _attack_scene()
        cmdr.teamwork = 100
        ally = _placed("scout", (10, 0))  # too far to crowd any candidate
        # The bias would take (1, 1).
        ctx.sight_data = {**(ctx.sight_data or {}), "10,0": frozenset({"1,1"})}
        assert _choose(cmdr, [*everyone, ally], ctx) == (3, 2)

    def test_the_enemy_medic_goal_is_gone(self) -> None:
        assert not hasattr(pathfinding, "_enemy_medic_goal")


@pytest.mark.usefixtures("best_draw")
class TestDefenseKeepsTheRoleLogicUntil545:
    def _scene(self, role: str) -> tuple[PlayerState, list, MapContext]:
        support = _placed(role, teamwork=0)
        # No memory of the Heavy: today's logic reads its true cell.
        heavy = _placed("heavy", (2, 2))
        ctx = _grid_map(
            sight={"2,2": {"2,1", "2,3"}},
            counts={"2,1": 1, "2,3": 10},
            high_los=[(9, 9)],
        )
        return support, [support, heavy], ctx

    def test_a_medic_takes_a_hidden_cell_the_heavy_sees(self) -> None:
        medic, everyone, ctx = self._scene("medic")
        assert _choose(medic, everyone, ctx) == (2, 1)
        assert medic._committed_goal == ((2, 1), False, HELD_GOAL_EXPIRY)

    def test_an_ammo_takes_an_open_cell_the_heavy_sees(self) -> None:
        ammo, everyone, ctx = self._scene("ammo")
        assert _choose(ammo, everyone, ctx) == (2, 3)

    def test_the_teamwork_bias_still_applies(self) -> None:
        medic, everyone, ctx = self._scene("medic")
        with mock.patch.object(
            pathfinding, "_apply_teamwork_bias", return_value=(9, 9)
        ) as bias:
            assert _choose(medic, everyone, ctx) == (9, 9)
        assert bias.called
        # Not drawn, so it keeps the 4-tick cadence.
        assert medic._committed_goal == ((9, 9), False, 100 + 4)

    @pytest.mark.parametrize("role", ["medic", "ammo"])
    def test_in_control_a_support_uses_the_control_rule(self, role: str) -> None:
        support, everyone, ctx = self._scene(role)
        support.mode = Mode.CONTROL
        assert _choose(support, everyone, ctx) == (9, 9)


class TestTagActionGoal:
    def _scene(self) -> tuple[PlayerState, list, MapContext]:
        """A near Heavy and a far Medic, both remembered."""
        cmdr = _placed("commander")
        heavy, medic = _enemy("heavy"), _enemy("medic")
        _remember(cmdr, "blue_heavy", (1, 1), 100, "heavy")
        _remember(cmdr, "blue_medic", (7, 7), 100, "medic")
        return cmdr, [cmdr, heavy, medic], _grid_map()

    @pytest.mark.parametrize("action", ["tag_player", "missile_player"])
    def test_attack_tries_the_nearest_target_first(self, action: str) -> None:
        cmdr, everyone, ctx = self._scene()
        assert _choose(cmdr, everyone, ctx, action=action) == (7, 7)
        assert cmdr._committed_goal == ((7, 7), True, 100 + 4)

    @pytest.mark.parametrize("role", ["commander", "scout", "heavy"])
    def test_any_attacking_role_does(self, role: str) -> None:
        _, everyone, ctx = self._scene()
        player = _placed(role, mode=Mode.ATTACK)
        player.player_memory = everyone[0].player_memory
        assert _choose(player, everyone, ctx, action="tag_player") == (7, 7)

    def test_a_chased_target_is_skipped_for_the_nearest_enemy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cmdr, everyone, ctx = self._scene()
        monkeypatch.setattr(pathfinding, "chased_tag_ids", lambda *args: {"blue_medic"})
        assert _choose(cmdr, everyone, ctx, action="tag_player") == (1, 1)

    def test_a_downed_target_falls_to_the_nearest_enemy(self) -> None:
        cmdr, everyone, ctx = self._scene()
        cmdr.player_memory["blue_medic"]["status"] = "downed"
        assert _choose(cmdr, everyone, ctx, action="tag_player") == (1, 1)

    def test_a_commander_in_control_uses_the_nearest_enemy_path(self) -> None:
        cmdr, everyone, ctx = self._scene()
        cmdr.mode = Mode.CONTROL
        assert _choose(cmdr, everyone, ctx, action="tag_player") == (1, 1)

    def test_with_no_memory_the_perfect_knowledge_fallback_stays(self) -> None:
        # Decision 23: it stays for MECH-13.
        cmdr, everyone, ctx = self._scene()
        cmdr.player_memory.clear()
        assert _choose(cmdr, everyone, ctx, action="tag_player") == (11, 11)


@pytest.mark.usefixtures("best_draw")
class TestHoldingThroughChooseGoalCell:
    def _at_goal(self) -> tuple[PlayerState, MapContext]:
        heavy = _placed("heavy", (1, 5))
        ctx = _grid_map(strong=[(1, 5), (1, 1)], counts=_RANKS)
        heavy._committed_goal = ((1, 5), False, HELD_GOAL_EXPIRY)
        return heavy, ctx

    def test_stays_twenty_ticks_then_draws_with_no_full_map_draw_between(
        self, draws: list
    ) -> None:
        heavy, ctx = self._at_goal()
        for tick in range(100, 120):
            assert _choose(heavy, [heavy], ctx, tick) == (1, 5)
        assert draws == []
        _choose(heavy, [heavy], ctx, 120)
        assert len(draws) == 1

    def test_an_action_goal_interrupts(self) -> None:
        heavy, ctx = self._at_goal()
        enemy = _enemy("scout", "blue_scout_1")
        _remember(heavy, "blue_scout_1", (3, 3), 105, "scout")
        assert _choose(heavy, [heavy, enemy], ctx, 105) == (1, 5)
        assert _choose(heavy, [heavy, enemy], ctx, 106, action="tag_player") == (3, 3)
        # Back from the chase: the hold is over, so no standing still on the way.
        heavy.cell_row, heavy.cell_col = 3, 3
        assert _choose(heavy, [heavy, enemy], ctx, 110) in ctx.get_strong_spots()
        assert heavy.mode_hold_until is None

    def test_a_reactive_override_interrupts(self) -> None:
        heavy, ctx = self._at_goal()
        assert _choose(heavy, [heavy], ctx, 105) == (1, 5)
        heavy.final_lives = 1
        medic = _placed("medic", (7, 7))
        assert _choose(heavy, [heavy, medic], ctx, 106) == (7, 7)


def _attack_scene() -> tuple[PlayerState, list[PlayerState], MapContext]:
    """A Commander who just saw the enemy Medic at (3, 3) and Ammo at (8, 8)."""
    cmdr = _placed("commander")
    everyone = [cmdr, _enemy("medic"), _enemy("ammo")]
    _remember(cmdr, "blue_medic", (3, 3), 100, "medic")
    _remember(cmdr, "blue_ammo", (8, 8), 100, "ammo")
    ctx = _grid_map(
        high_los=[(1, 1)],
        sight={"3,3": {"3,2", "5,5"}, "8,8": {"8,7", "5,5"}},
        counts={"1,1": 5, "2,2": 1},
    )
    return cmdr, everyone, ctx


MEDIC_VIEW = [(3, 2), (5, 5)]
AMMO_VIEW = [(5, 5), (8, 7)]


@pytest.mark.usefixtures("best_draw")
class TestAttackTargets:
    def _candidates(self, cmdr: PlayerState, everyone: list, ctx: MapContext) -> list:
        calls: list[list] = []
        real = modes.draw_goal

        def spy(p: PlayerState, cands: list, *rest: Any):
            calls.append(sorted(cands))
            return real(p, cands, *rest)

        with mock.patch.object(modes, "draw_goal", spy):
            mode_goal(cmdr, everyone, "blue", ctx, 100)
        return calls[0]

    def test_a_downed_medic_is_not_hunted(self) -> None:
        cmdr, everyone, ctx = _attack_scene()
        cmdr.player_memory["blue_medic"]["status"] = "downed"
        assert self._candidates(cmdr, everyone, ctx) == AMMO_VIEW

    @pytest.mark.parametrize("status", ["reset_window", "active"])
    def test_a_medic_in_the_reset_window_is_still_hunted(self, status: str) -> None:
        cmdr, everyone, ctx = _attack_scene()
        del cmdr.player_memory["blue_ammo"]
        cmdr.player_memory["blue_medic"]["status"] = status
        assert self._candidates(cmdr, everyone, ctx) == MEDIC_VIEW

    def test_a_target_not_in_all_alive_is_dropped(self) -> None:
        cmdr, everyone, ctx = _attack_scene()
        alive = [p for p in everyone if p.tag_id != "blue_medic"]
        assert self._candidates(cmdr, alive, ctx) == AMMO_VIEW

    def test_a_target_eliminated_earlier_this_tick_is_dropped(self) -> None:
        cmdr, everyone, ctx = _attack_scene()
        next(p for p in everyone if p.tag_id == "blue_medic").final_lives = 0
        assert self._candidates(cmdr, everyone, ctx) == AMMO_VIEW

    def test_a_stale_entry_is_dropped(self) -> None:
        cmdr, everyone, ctx = _attack_scene()
        cmdr.player_memory["blue_medic"]["timestamp"] = 100 - 121
        assert self._candidates(cmdr, everyone, ctx) == AMMO_VIEW

    def test_an_entry_at_the_staleness_limit_is_kept(self) -> None:
        cmdr, everyone, ctx = _attack_scene()
        del cmdr.player_memory["blue_ammo"]
        cmdr.player_memory["blue_medic"]["timestamp"] = 100 - 120
        assert self._candidates(cmdr, everyone, ctx) == MEDIC_VIEW

    def test_a_chased_target_is_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmdr, everyone, ctx = _attack_scene()
        seen: list = []

        def chased(attacker: PlayerState, alive: list, tick: float) -> set[str]:
            seen.append((attacker, alive, tick))
            return {"blue_medic"}

        monkeypatch.setattr(modes, "chased_tag_ids", chased)
        assert self._candidates(cmdr, everyone, ctx) == AMMO_VIEW
        assert seen == [(cmdr, everyone, 100)]

    def test_other_roles_are_not_targets(self) -> None:
        scout = _placed("scout")
        enemy = _enemy("scout", "blue_scout_1")
        _remember(scout, "blue_scout_1", (3, 3), 100, "scout")
        ctx = _grid_map(high_los=[(1, 1)], sight={"3,3": {"3,2"}})
        assert self._candidates(scout, [scout, enemy], ctx) == [(1, 1)]

    def test_a_remembered_teammate_is_not_a_target(self) -> None:
        scout = _placed("scout")
        mate = _placed("medic")
        _remember(scout, "red_medic", (3, 3), 100, "medic")
        ctx = _grid_map(high_los=[(1, 1)], sight={"3,3": {"3,2"}})
        assert self._candidates(scout, [scout, mate], ctx) == [(1, 1)]

    def test_an_unknown_enemy_is_not_hunted_from_true_state(self) -> None:
        # Decision 7: the enemy's true cell is not information the player has.
        scout = _placed("scout")
        enemy = _enemy("medic")
        ctx = _grid_map(high_los=[(1, 1)], sight={"11,11": {"10,11"}})
        assert self._candidates(scout, [scout, enemy], ctx) == [(1, 1)]


def _sight_map(sight_data: dict[str, frozenset[str]] | None) -> MapContext:
    return MapContext(
        adj={},
        spawn_cells={},
        zone_data=None,
        sight_data=sight_data,
        base_sight_data={},
        cell_los_counts={},
        high_los_cells=[],
        strong_spots=[],
        wall_meta={},
        team_spawn_pools={},
    )


class TestPrefactor:
    """``modes`` never imports ``pathfinding``: the staleness table lives in
    ``time_constants`` and a cell's sight set is a ``MapContext`` method."""

    def test_stale_table_per_time_domain(self) -> None:
        ticks = _stale_table("ticks")
        seconds = _stale_table("seconds")
        assert (ticks["heavy"], ticks["medic"], ticks["ammo"]) == (120, 120, 120)
        assert (ticks["scout"], ticks["commander"]) == (30, 30)
        assert (seconds["heavy"], seconds["medic"], seconds["ammo"]) == (60, 60, 60)
        assert (seconds["scout"], seconds["commander"]) == (15, 15)

    def test_cells_visible_from_is_sorted_cells(self) -> None:
        # Sorted, because a frozenset's order is PYTHONHASHSEED-randomised and
        # the list decides a draw (serial == parallel).
        keys = frozenset(f"{r},{c}" for r in range(6) for c in range(6))
        ctx = _sight_map({"9,9": keys})
        assert ctx.cells_visible_from((9, 9)) == sorted(
            (r, c) for r in range(6) for c in range(6)
        )

    def test_cells_visible_from_a_cell_without_sight_lines(self) -> None:
        assert _sight_map({"1,1": frozenset({"2,2"})}).cells_visible_from((3, 3)) == []

    def test_cells_visible_from_without_sight_data(self) -> None:
        assert _sight_map(None).cells_visible_from((3, 3)) == []

    def test_modes_is_django_free_and_never_imports_pathfinding(self) -> None:
        from laserforce_simulator.testing import run_project_python

        project_root = next(
            parent
            for parent in pathlib.Path(__file__).resolve().parents
            if (parent / "manage.py").exists()
        )
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.sim_helpers.modes  # noqa: F401

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.sim_helpers.pathfinding"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            """)
        result = run_project_python(script, timeout=30)
        assert result.returncode == 0, f"{result.stdout!r} {result.stderr!r}"
