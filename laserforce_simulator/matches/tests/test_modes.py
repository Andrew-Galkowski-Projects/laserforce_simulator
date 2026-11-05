"""MECH-07: Player Modes (spec #542).

MECH-07a: Mode state, switching and the weight nudge. Hand-built ``PlayerState``
and ``MapContext`` at the ``sim_helpers.modes`` seam, plus seeded Syracuse rounds
for the tick-loop wiring.
"""

from __future__ import annotations

import pathlib
import random
import textwrap
from unittest import mock

import pytest

from matches.sim_helpers import combat
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.modes import (
    Mode,
    apply_mode_weights,
    default_mode,
    update_mode,
)
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.time_constants import _stale_table
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
