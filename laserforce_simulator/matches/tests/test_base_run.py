"""MECH-26 (#623, ticket #624): the Base run by the supports. When the
Commander fires a nuke, one team roll; a support waits out the fuse at a
staging cell, crosses out of sight to every Base it has not taken (the farther
first), captures, then stays as a Board flip or walks back to its Heavy."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from matches.sim_helpers import base_run, combat, pathfinding
from matches.sim_helpers.base_run import (
    NUKE_RUN_CHANCE,
    RETURN_ARRIVE_TICKS,
    RUN_LEG_ARRIVE_TICKS,
    BaseRun,
    base_run_goal,
    end_base_run,
    end_on_down,
    run_order,
    squares_per_tick,
    start_on_nuke_fire,
    tick_base_run,
)
from matches.sim_helpers.board_flip import MAX_FLIPS_PER_ROUND, Flip
from matches.sim_helpers.combat import plan_action
from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.pathfinding import choose_goal_cell
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.round_context import RoundContext
from matches.simulation import BatchSimulator
from matches.tests.test_downed_option import _map, _ps, _remember

Cell = tuple[int, int]
T = 200

# 5 rows x 60 columns of open floor (1 ft a square); a cell sees its own row
# and column. Blue's targets: neutral_1 in range from column 20, the red base
# from (2, 50).
NEUTRAL_RANGE = frozenset({"2,20", "3,20"})
RED_RANGE = frozenset({"2,50"})


def _bases(**ranges: frozenset[str]) -> MapContext:
    mc = _map(["." * 60] * 5)
    mc.base_sight_data = ranges or {"neutral_1": NEUTRAL_RANGE, "red": RED_RANGE}
    return mc


def _support(
    role: str = "medic", cell: Cell = (2, 0), team: str = "blue"
) -> PlayerState:
    return _ps(f"{team}_{role}", team, cell, role=role)


class TestRunOrder:
    """``run_order``: the Bases not destroyed that have a range, farther first."""

    def test_farther_first(self) -> None:
        assert run_order(_support(), _bases(), None) == ["red", "neutral_1"]

    def test_from_a_start_cell(self) -> None:
        # From (2, 59) the neutral range is 39 away, the red one 9.
        assert run_order(_support(), _bases(), None, start=(2, 59)) == [
            "neutral_1",
            "red",
        ]

    def test_the_nearer_first_when_the_farther_is_out_of_the_window(self) -> None:
        medic = _support()
        medic.speed = 0  # 5 ft/s: 2.5 squares a tick; the red range is 50 away
        assert squares_per_tick(medic, _bases()) == 2.5
        assert run_order(medic, _bases(), 19) == ["neutral_1", "red"]

    def test_still_farther_first_when_exactly_in_reach(self) -> None:
        medic = _support()
        medic.speed = 0
        assert run_order(medic, _bases(), 20) == ["red", "neutral_1"]

    def test_a_destroyed_neutral_is_left_out(self) -> None:
        medic = _support()
        medic.neutral_base_destroyed = True
        assert run_order(medic, _bases(), None) == ["red"]

    def test_a_destroyed_opposing_base_is_left_out(self) -> None:
        medic = _support()
        medic.opposing_base_destroyed = True
        assert run_order(medic, _bases(), None) == ["neutral_1"]

    def test_the_first_neutral_with_a_range(self) -> None:
        mc = _bases(neutral_1=frozenset(), neutral_2=NEUTRAL_RANGE, red=RED_RANGE)
        assert run_order(_support(), mc, None) == ["red", "neutral_2"]

    def test_only_one_neutral(self) -> None:
        mc = _bases(
            neutral_1=NEUTRAL_RANGE, neutral_2=frozenset({"0,40"}), red=RED_RANGE
        )
        assert run_order(_support(), mc, None) == ["red", "neutral_1"]

    def test_a_base_with_no_range_is_left_out(self) -> None:
        assert run_order(_support(), _bases(neutral_1=NEUTRAL_RANGE), None) == [
            "neutral_1"
        ]

    def test_reds_opposing_base_is_blue(self) -> None:
        red = _support(team="red")
        mc = _bases(neutral_1=NEUTRAL_RANGE, red=RED_RANGE, blue=frozenset({"0,5"}))
        assert run_order(red, mc, None) == ["neutral_1", "blue"]

    @pytest.mark.parametrize(
        "shots, expected",
        [(3, ["red"]), (5, ["red"]), (6, ["red", "neutral_1"])],
    )
    def test_a_medic_plans_one_base_per_3_shots(
        self, shots: int, expected: list[str]
    ) -> None:
        medic = _support()
        medic.final_shots = shots
        assert run_order(medic, _bases(), None) == expected

    def test_empty_for_a_medic_with_2_shots(self) -> None:
        medic = _support()
        medic.final_shots = 2
        assert run_order(medic, _bases(), None) == []

    def test_an_ammo_with_no_shots_still_plans_both(self) -> None:
        ammo = _support("ammo")
        ammo.final_shots = 0
        assert run_order(ammo, _bases(), None) == ["red", "neutral_1"]

    def test_empty_when_unplaced(self) -> None:
        medic = _support()
        medic.cell_row = medic.cell_col = None
        assert run_order(medic, _bases(), None) == []
        assert run_order(medic, _bases(), None, start=(2, 0)) == []

    def test_empty_with_no_base_sight_data(self) -> None:
        mc = _bases()
        mc.base_sight_data = {}
        assert run_order(_support(), mc, None) == []

    def test_empty_when_both_are_destroyed(self) -> None:
        medic = _support()
        medic.neutral_base_destroyed = medic.opposing_base_destroyed = True
        assert run_order(medic, _bases(), None) == []


class TestBaseRunEvent:
    def test_the_start_row(self) -> None:
        medic = _support()
        events = EventLog()
        events.base_run(medic, T, "start", "nuke", ["red", "neutral_1"])
        (row,) = events.entries
        assert row["event_type"] == "base_run"
        assert row["actor_id"] == medic.player_id
        assert row["target_id"] is None
        assert row["timestamp"] == T
        assert row["points_awarded"] == 0
        meta = row["metadata"]
        assert meta["actor_role"] == "medic"
        assert (meta["phase"], meta["trigger"], meta["bases"]) == (
            "start",
            "nuke",
            ["red", "neutral_1"],
        )
        assert "reason" not in meta and "taken" not in meta

    def test_the_end_row(self) -> None:
        events = EventLog()
        events.base_run(
            _support(), T, "end", "nuke", ["red"], reason="returned", taken=["red"]
        )
        meta = events.entries[0]["metadata"]
        assert (meta["phase"], meta["reason"], meta["taken"]) == (
            "end",
            "returned",
            ["red"],
        )

    def test_the_null_log_writes_nothing(self) -> None:
        events = EventLog(persist=False)
        events.base_run(_support(), T, "start", "nuke", ["red"])
        assert len(events) == 0

    def test_it_is_a_game_event_choice(self) -> None:
        from matches.models import GameEvent

        assert ("base_run", "Base Run") in GameEvent.EVENT_TYPES


def _draws(monkeypatch: pytest.MonkeyPatch, *values: float) -> list[float]:
    """Pin ``random.random`` in ``base_run`` to *values*; a further draw fails."""
    left = list(values)

    def draw() -> float:
        assert left, "an unexpected draw"
        return left.pop(0)

    monkeypatch.setattr(base_run.random, "random", draw)
    return left


def _team(medic_cell: Cell = (2, 0), ammo_cell: Cell = (4, 0)) -> list[PlayerState]:
    """Blue: Commander, Medic, Ammo, Heavy (the Heavy remembered by both)."""
    cmd = _ps("blue_commander", "blue", (0, 0), role="commander")
    cmd.special_active_until = T + 10
    medic = _support("medic", medic_cell)
    ammo = _support("ammo", ammo_cell)
    heavy = _ps("blue_heavy", "blue", (4, 59), role="heavy")
    for support in (medic, ammo):
        _remember(support, heavy, T - 5)
    return [cmd, medic, ammo, heavy]


def _rctx(mc: MapContext | None = None) -> RoundContext:
    return RoundContext(events=EventLog(), movement_ctx=mc or _bases())


def _fire(team: list[PlayerState], ctx: RoundContext, fuse: int = 10) -> None:
    start_on_nuke_fire(team[0], team, T, T + fuse, ctx)


def _rows(events: EventLog) -> list[dict]:
    return [e for e in events.entries if e["event_type"] == "base_run"]


class TestNukeRunEligibility:
    """One fixture per condition on the Medic, each failing it alone: the
    Medic starts no run and the Ammo, still eligible, rolls and goes."""

    def _medic_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
        knock_out: Callable[[PlayerState], None],
    ) -> list[PlayerState]:
        team = _team()
        knock_out(team[1])
        _draws(monkeypatch, 0.0, 0.0)
        _fire(team, _rctx())
        return team

    def _assert_only_the_ammo_runs(self, team: list[PlayerState]) -> None:
        medic, ammo = team[1], team[2]
        assert ammo.base_run is not None and ammo.base_run.trigger == "nuke"
        assert medic.base_run is None or medic.base_run.trigger != "nuke"

    def test_no_lives(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def out(p: PlayerState) -> None:
            p.final_lives = 0

        self._assert_only_the_ammo_runs(self._medic_out(monkeypatch, out))

    def test_unplaced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def out(p: PlayerState) -> None:
            p.cell_row = p.cell_col = None

        self._assert_only_the_ammo_runs(self._medic_out(monkeypatch, out))

    def test_already_running(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def out(p: PlayerState) -> None:
            p.base_run = BaseRun(trigger="streak", bases=["red"], started=T - 9)

        team = self._medic_out(monkeypatch, out)
        self._assert_only_the_ammo_runs(team)
        assert team[1].base_run is not None and team[1].base_run.trigger == "streak"

    def test_on_a_started_flip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def out(p: PlayerState) -> None:
            p.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T - 5)

        self._assert_only_the_ammo_runs(self._medic_out(monkeypatch, out))

    def test_a_pending_flip_is_eligible(self, monkeypatch: pytest.MonkeyPatch) -> None:
        team = _team()
        team[1].flip = Flip(from_cell=(2, 0))
        _draws(monkeypatch, 0.0, 0.0)
        _fire(team, _rctx())
        assert team[1].base_run is not None

    def test_a_medic_with_2_shots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def out(p: PlayerState) -> None:
            p.final_shots = 2

        self._assert_only_the_ammo_runs(self._medic_out(monkeypatch, out))

    def test_both_bases_destroyed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def out(p: PlayerState) -> None:
            p.neutral_base_destroyed = p.opposing_base_destroyed = True

        self._assert_only_the_ammo_runs(self._medic_out(monkeypatch, out))

    def test_no_base_sight_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        team = _team()
        mc = _bases()
        mc.base_sight_data = {}
        _draws(monkeypatch)  # no draw at all
        _fire(team, _rctx(mc))
        assert all(p.base_run is None for p in team)

    def test_a_downed_support_is_eligible(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        team = _team()
        team[1].last_downed_time = T - 2
        _draws(monkeypatch, 0.0, 0.0)
        _fire(team, _rctx())
        assert team[1].base_run is not None

    def test_the_leader_is_the_medic_and_goes_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        team = _team()
        _draws(monkeypatch, 0.0, 0.0)
        _fire(team, _rctx())
        assert team[1].base_run is not None
        assert team[2].base_run is None


class TestTeamRoll:
    def test_no_eligible_support_makes_no_draw(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        team = _team()
        team[1].final_lives = team[2].final_lives = 0
        _draws(monkeypatch)
        _fire(team, _rctx())
        assert all(p.base_run is None for p in team)

    @pytest.mark.parametrize("awareness, runs", [(50, True), (20, False)])
    def test_the_chance_scales_with_game_awareness(
        self, monkeypatch: pytest.MonkeyPatch, awareness: int, runs: bool
    ) -> None:
        team = _team()
        team[1].game_awareness = team[2].game_awareness = awareness
        left = _draws(monkeypatch, 0.30, 0.0)
        _fire(team, _rctx())
        assert (team[1].base_run is not None) is runs
        assert left == ([] if runs else [0.0])  # a miss draws nothing more

    def test_the_mean_of_the_eligible_supports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Mean (50 + 20) / 2 / 50 = 0.7: 0.39 x 0.7 = 0.273.
        team = _team()
        team[2].game_awareness = 20
        _draws(monkeypatch, 0.27, 0.0)
        _fire(team, _rctx())
        assert team[1].base_run is not None
        team = _team()
        team[2].game_awareness = 20
        _draws(monkeypatch, 0.28)
        _fire(team, _rctx())
        assert team[1].base_run is None

    def test_an_ineligible_supports_awareness_does_not_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        team = _team()
        team[2].final_lives = 0
        team[2].game_awareness = 0
        _draws(monkeypatch, NUKE_RUN_CHANCE - 0.01, 0.0)
        _fire(team, _rctx())
        assert team[1].base_run is not None

    @pytest.mark.parametrize("draw, stays", [(0.49, True), (0.5, False)])
    def test_the_stay_roll(
        self, monkeypatch: pytest.MonkeyPatch, draw: float, stays: bool
    ) -> None:
        team = _team()
        _draws(monkeypatch, 0.0, draw)
        _fire(team, _rctx())
        assert team[1].base_run is not None and team[1].base_run.stays is stays

    def test_a_lone_ammo_never_stays(self, monkeypatch: pytest.MonkeyPatch) -> None:
        team = _team()
        team[1].final_lives = 0
        _draws(monkeypatch, 0.0, 0.0)
        _fire(team, _rctx())
        assert team[2].base_run is not None and team[2].base_run.stays is False


class TestNukeRunStart:
    def test_the_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        team = _team()
        _draws(monkeypatch, 0.0, 0.9)
        _fire(team, _rctx(), fuse=4)
        run = team[1].base_run
        assert run is not None
        assert (run.trigger, run.started, run.detonation) == ("nuke", T, T + 4)
        assert run.nuker is team[0]
        assert run.leg_started == T + 4  # the legs start at detonation
        assert run.bases == ["red", "neutral_1"]

    def test_starting_clears_hide_hold_and_commitment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        team = _team()
        medic = team[1]
        medic.is_hiding = medic.is_holding = True
        medic.hold_until = T + 20
        medic._committed_goal = ((0, 0), False, 0)
        _draws(monkeypatch, 0.0, 0.9)
        _fire(team, _rctx())
        assert (medic.is_hiding, medic.is_holding) == (False, False)
        assert medic.hold_until is None and medic._committed_goal is None

    def test_the_start_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        team = _team()
        ctx = _rctx()
        _draws(monkeypatch, 0.0, 0.9)
        _fire(team, ctx, fuse=4)
        (row,) = _rows(ctx.events)
        assert row["actor_id"] == team[1].player_id
        assert row["timestamp"] == T
        assert row["metadata"]["phase"] == "start"
        assert row["metadata"]["trigger"] == "nuke"
        assert row["metadata"]["bases"] == ["red", "neutral_1"]

    def test_a_miss_writes_no_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx = _rctx()
        _draws(monkeypatch, 0.99)
        _fire(_team(), ctx)
        assert _rows(ctx.events) == []


class TestStaging:
    """The leader walks only to a staging cell in the fuse: reachable by
    detonation, unseen by the remembered enemies, nearest the first range."""

    def test_an_unseen_cell_nearest_the_first_range(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fuse 4 ticks at 3.75 squares a tick: 15 squares. With no enemy
        # remembered, (2, 15) is nearest the red range (2, 50).
        team = _team()
        _draws(monkeypatch, 0.0, 0.9)
        _fire(team, _rctx(), fuse=4)
        assert team[1].base_run is not None
        assert team[1].base_run.staging == (2, 15)

    def test_a_seen_cell_is_left_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # (2, 30) sees row 2: (1, 14) and (3, 14) are 37 from (2, 50).
        team = _team()
        _remember(team[1], _ps("red_scout", "red", (2, 30)), T)
        _draws(monkeypatch, 0.0, 0.9)
        _fire(team, _rctx(), fuse=4)
        assert team[1].base_run is not None
        assert team[1].base_run.staging == (1, 14)

    def test_its_own_cell_when_every_cell_is_seen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        line = _map(["." * 60])
        line.base_sight_data = {"red": frozenset({"0,50"})}
        team = _team(medic_cell=(0, 0), ammo_cell=(0, 1))
        _remember(team[1], _ps("red_scout", "red", (0, 30)), T)
        _draws(monkeypatch, 0.0, 0.9)
        _fire(team, _rctx(line), fuse=4)
        assert team[1].base_run is not None
        assert team[1].base_run.staging == (0, 0)

    def test_the_window_check_measures_from_the_staging_cell(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 2.5 squares a tick; window 4 + 16 = 20 ticks, 50 squares. From
        # (2, 0) the red range (2, 55) is out of reach, from the staging cell
        # (2, 10) it is in reach: the farther goes first.
        mc = _bases(neutral_1=NEUTRAL_RANGE, red=frozenset({"2,55"}))
        team = _team()
        team[1].speed = 0
        _draws(monkeypatch, 0.0, 0.9)
        _fire(team, _rctx(mc), fuse=4)
        run = team[1].base_run
        assert run is not None
        assert (run.bases, run.staging) == (["red", "neutral_1"], (2, 10))


def _running(
    player: PlayerState,
    nuker: PlayerState,
    bases: list[str] | None = None,
    **fields: object,
) -> BaseRun:
    """A nuke run fired at T - 50 that detonated at T - 40."""
    run = BaseRun(
        trigger="nuke",
        bases=bases or ["red", "neutral_1"],
        started=T - 50,
        leg_started=T,
        nuker=nuker,
        detonation=T - 40,
        events=EventLog(),
    )
    for name, value in fields.items():
        setattr(run, name, value)
    player.base_run = run
    return run


@pytest.fixture
def ends(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every ``end_base_run`` reason, in order."""
    seen: list[str] = []
    real = base_run.end_base_run

    def end(player: PlayerState, tick: int, reason: str) -> None:
        seen.append(reason)
        real(player, tick, reason)  # type: ignore[arg-type]

    monkeypatch.setattr(base_run, "end_base_run", end)
    return seen


def _tick(player: PlayerState, team: list[PlayerState], tick: int, **kw) -> EventLog:
    events = EventLog()
    tick_base_run(player, tick, kw.get("mc") or _bases(), team, events)
    return events


class TestEndings:
    """Every run ends in exactly one ``end_base_run``."""

    def test_a_down(self, ends: list[str]) -> None:
        team = _team()
        run = _running(team[1], team[0])
        end_on_down(team[1], T)
        assert team[1].base_run is None
        assert ends == ["down"]
        (row,) = _rows(run.events)
        assert row["metadata"]["phase"] == "end"
        assert row["metadata"]["reason"] == "down"
        assert row["metadata"]["taken"] == []

    def test_an_elimination(self, ends: list[str]) -> None:
        team = _team()
        _running(team[1], team[0])
        team[1].final_lives = 0
        end_on_down(team[1], T)
        assert ends == ["eliminated"]

    def test_no_run_no_end(self, ends: list[str]) -> None:
        end_on_down(_support(), T)
        assert ends == []

    def test_a_down_at_the_start_of_a_run_started_while_downed(
        self, ends: list[str]
    ) -> None:
        team = _team()
        medic = team[1]
        medic.last_downed_time = T - 3
        _running(medic, team[0], started=T)
        end_on_down(medic, T)
        assert medic.base_run is not None
        end_on_down(medic, T + 20)
        assert ends == ["down"]

    def test_not_arrived_on_a_leg_after_60_ticks(self, ends: list[str]) -> None:
        team = _team()
        _running(team[1], team[0])
        _tick(team[1], team, T + RUN_LEG_ARRIVE_TICKS)
        assert team[1].base_run is not None
        _tick(team[1], team, T + RUN_LEG_ARRIVE_TICKS + 1)
        assert ends == ["not_arrived"]

    def test_not_arrived_on_the_return_after_120_ticks(self, ends: list[str]) -> None:
        team = _team()
        _running(team[1], team[0], returning_to=(4, 59), return_started=T)
        _tick(team[1], team, T + RETURN_ARRIVE_TICKS)
        assert team[1].base_run is not None
        _tick(team[1], team, T + RETURN_ARRIVE_TICKS + 1)
        assert ends == ["not_arrived"]

    def test_a_cancelled_run_out_of_time_is_nuke_cancelled(
        self, ends: list[str]
    ) -> None:
        team = _team()
        _running(
            team[1], team[0], returning_to=(4, 59), return_started=T, cancelled=True
        )
        _tick(team[1], team, T + RETURN_ARRIVE_TICKS + 1)
        assert ends == ["nuke_cancelled"]

    def _in_fuse(self) -> tuple[list[PlayerState], BaseRun]:
        team = _team()
        run = _running(team[1], team[0], started=T - 5, detonation=T + 5)
        run.leg_started = T + 5
        return team, run

    def test_the_nuker_eliminated_in_the_fuse_starts_the_return(
        self, ends: list[str]
    ) -> None:
        team, run = self._in_fuse()
        team[0].final_lives = 0
        _tick(team[1], team, T)
        assert run.cancelled and run.returning_to == (4, 59)
        assert run.return_started == T
        assert ends == []

    def test_the_nuke_no_longer_pending_starts_the_return(
        self, ends: list[str]
    ) -> None:
        team, run = self._in_fuse()
        team[0].special_active_until = 0
        _tick(team[1], team, T)
        assert run.cancelled and run.returning_to == (4, 59)

    def test_the_nuker_eliminated_after_detonation_cancels_nothing(
        self, ends: list[str]
    ) -> None:
        team = _team()
        run = _running(team[1], team[0])  # detonated at T - 40
        team[0].final_lives = 0
        _tick(team[1], team, T)
        assert not run.cancelled and run.returning_to is None

    def test_a_pending_nuke_keeps_the_run(self, ends: list[str]) -> None:
        team, run = self._in_fuse()
        _tick(team[1], team, T)
        assert not run.cancelled and run.returning_to is None

    def test_reaching_the_return_cell_of_a_cancelled_run(self, ends: list[str]) -> None:
        team, run = self._in_fuse()
        team[0].final_lives = 0
        _tick(team[1], team, T)
        team[1].cell_row, team[1].cell_col = (4, 56)
        _tick(team[1], team, T + 1)
        assert ends == ["nuke_cancelled"]

    def test_a_cancelled_run_with_no_heavy_memory(self, ends: list[str]) -> None:
        team, run = self._in_fuse()
        team[1].player_memory.clear()
        team[0].final_lives = 0
        _tick(team[1], team, T)
        assert ends == ["nuke_cancelled"]

    def test_returned_within_3_squares(self, ends: list[str]) -> None:
        team = _team()
        _running(team[1], team[0], returning_to=(4, 59), return_started=T)
        team[1].cell_row, team[1].cell_col = (3, 57)  # 3 away
        _tick(team[1], team, T + 5)
        assert ends == ["returned"]

    def test_4_squares_is_not_back_yet(self, ends: list[str]) -> None:
        team = _team()
        _running(team[1], team[0], returning_to=(4, 59), return_started=T)
        team[1].cell_row, team[1].cell_col = (2, 57)
        _tick(team[1], team, T + 5)
        assert ends == []

    def test_the_last_capture_starts_the_return(self) -> None:
        team = _team()
        medic = team[1]
        run = _running(medic, team[0], bases=["red"])
        medic.opposing_base_destroyed = True
        _tick(medic, team, T + 5)
        assert run.taken == ["red"]
        assert (run.returning_to, run.return_started) == ((4, 59), T + 5)
        assert run.return_draw

    def test_returned_with_no_heavy_memory(self, ends: list[str]) -> None:
        team = _team()
        medic = team[1]
        medic.player_memory.clear()
        _running(medic, team[0], bases=["red"])
        medic.opposing_base_destroyed = True
        _tick(medic, team, T + 5)
        assert ends == ["returned"]

    def test_the_heavys_memory_is_used_however_stale(self) -> None:
        team = _team()
        medic = team[1]
        medic.player_memory["blue_heavy"]["timestamp"] = 0
        run = _running(medic, team[0], bases=["red"])
        medic.opposing_base_destroyed = True
        _tick(medic, team, T + 5)
        assert run.returning_to == (4, 59)

    def test_stayed_hands_over_to_a_started_flip(self, ends: list[str]) -> None:
        team = _team(medic_cell=(2, 50))
        medic = team[1]
        _running(medic, team[0], bases=["red"], stays=True)
        medic.opposing_base_destroyed = True
        events = _tick(medic, team, T + 5)
        assert ends == ["stayed"]
        assert medic.flip is not None and medic.flip.started == T + 5
        assert medic.flip.target is not None and medic.flip.to_target
        assert medic.flips_started == 1
        flips = [e for e in events.entries if e["event_type"] == "board_flip"]
        assert len(flips) == 1

    def test_a_stays_medic_with_no_flips_left_returns(self, ends: list[str]) -> None:
        team = _team(medic_cell=(2, 50))
        medic = team[1]
        medic.flips_started = MAX_FLIPS_PER_ROUND
        run = _running(medic, team[0], bases=["red"], stays=True)
        medic.opposing_base_destroyed = True
        _tick(medic, team, T + 5)
        assert ends == [] and run.returning_to == (4, 59)
        assert medic.flip is None

    def test_a_stays_medic_with_no_flip_cell_returns(self, ends: list[str]) -> None:
        # Nothing on a 5 x 30 floor is 40 squares' walk from its middle.
        small = _map(["." * 30] * 5)
        small.base_sight_data = {"red": frozenset({"2,15"})}
        team = _team(medic_cell=(2, 15))
        medic = team[1]
        run = _running(medic, team[0], bases=["red"], stays=True)
        medic.opposing_base_destroyed = True
        _tick(medic, team, T + 5, mc=small)
        assert ends == [] and run.returning_to == (4, 59)

    def test_a_stays_medic_not_active_returns(self, ends: list[str]) -> None:
        team = _team(medic_cell=(2, 50))
        medic = team[1]
        medic.last_downed_time = T
        run = _running(medic, team[0], bases=["red"], stays=True)
        medic.opposing_base_destroyed = True
        _tick(medic, team, T + 5)
        assert ends == [] and run.returning_to == (4, 59)

    def test_a_stays_ammo_never_flips(self, ends: list[str]) -> None:
        team = _team(ammo_cell=(2, 50))
        ammo = team[2]
        run = _running(ammo, team[0], bases=["red"], stays=True)
        ammo.opposing_base_destroyed = True
        _tick(ammo, team, T + 5)
        assert ammo.flip is None and run.returning_to == (4, 59)

    def test_an_unplaced_player_is_not_ticked(self, ends: list[str]) -> None:
        team = _team()
        _running(team[1], team[0])
        team[1].cell_row = team[1].cell_col = None
        _tick(team[1], team, T + RUN_LEG_ARRIVE_TICKS + 1)
        assert team[1].base_run is not None and ends == []

    def test_end_base_run_refuses_an_unknown_reason(self) -> None:
        team = _team()
        _running(team[1], team[0])
        with pytest.raises(ValueError):
            end_base_run(team[1], T, "bored")  # type: ignore[arg-type]
        assert team[1].base_run is not None

    def test_the_end_row_lists_the_taken_bases(self) -> None:
        team = _team()
        run = _running(team[1], team[0], taken=["red"])
        end_base_run(team[1], T, "down")
        (row,) = _rows(run.events)
        assert row["metadata"]["taken"] == ["red"]
        assert row["metadata"]["bases"] == ["red", "neutral_1"]


class TestLegs:
    def test_a_capture_mid_route_counts(self) -> None:
        team = _team()
        medic = team[1]
        run = _running(medic, team[0])
        medic.neutral_base_destroyed = True  # the second leg's base, on the way
        _tick(medic, team, T + 5)
        assert (run.leg, run.taken) == (0, ["neutral_1"])
        medic.opposing_base_destroyed = True
        _tick(medic, team, T + 6)
        assert run.taken == ["neutral_1", "red"]
        assert run.returning_to == (4, 59)

    def test_the_next_leg_restarts_the_clock(self, ends: list[str]) -> None:
        team = _team()
        medic = team[1]
        run = _running(medic, team[0])
        medic.opposing_base_destroyed = True
        _tick(medic, team, T + 30)
        assert (run.leg, run.leg_started) == (1, T + 30)
        _tick(medic, team, T + 30 + RUN_LEG_ARRIVE_TICKS)
        assert ends == []
        _tick(medic, team, T + 31 + RUN_LEG_ARRIVE_TICKS)
        assert ends == ["not_arrived"]

    def test_no_leg_limit_in_the_fuse(self, ends: list[str]) -> None:
        team = _team()
        _running(team[1], team[0], started=T - 5, detonation=T + 5, leg_started=T + 5)
        _tick(team[1], team, T + 4)
        assert ends == []


class TestSameTick:
    def test_a_run_started_at_t_is_first_ticked_at_t_plus_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        team = _team()
        _draws(monkeypatch, 0.0, 0.9)
        _fire(team, _rctx())
        run = team[1].base_run
        assert run is not None
        team[0].final_lives = 0
        _tick(team[1], team, T)
        assert not run.cancelled
        _tick(team[1], team, T + 1)
        assert run.cancelled


class TestGoal:
    def test_in_the_fuse_the_staging_cell(self) -> None:
        team = _team()
        _running(team[1], team[0], started=T - 5, detonation=T + 5, staging=(2, 15))
        assert base_run_goal(team[1], _bases(), T + 4) == (2, 15)

    def test_outbound_the_nearest_range_cell(self) -> None:
        team = _team()
        _running(team[1], team[0], staging=(2, 15))
        assert base_run_goal(team[1], _bases(), T) == (2, 50)
        team[1].base_run.leg = 1  # type: ignore[union-attr]
        team[1].base_run.leg_target = None  # type: ignore[union-attr]
        assert base_run_goal(team[1], _bases(), T) == (2, 20)

    def test_returning_the_return_cell(self) -> None:
        team = _team()
        _running(team[1], team[0], returning_to=(4, 59), return_started=T)
        assert base_run_goal(team[1], _bases(), T) == (4, 59)

    def test_the_crossing_steps_to_the_unseen_cell_nearest_the_target(self) -> None:
        # (2, 30) sees row 2 and column 30: within 15 squares of (2, 0),
        # (1, 14) and (3, 14) are 37 from (2, 50); the cell breaks the tie.
        team = _team()
        _running(team[1], team[0])
        _remember(team[1], _ps("red_scout", "red", (2, 30)), T)
        assert base_run_goal(team[1], _bases(), T) == (1, 14)

    def test_straight_with_no_remembered_enemy(self) -> None:
        team = _team()
        _running(team[1], team[0])
        assert base_run_goal(team[1], _bases(), T) == (2, 50)

    def test_straight_when_its_own_cell_is_the_best(self) -> None:
        # (2, 30) sees row 2, the target's: from (1, 50) no unseen cell is
        # nearer (2, 50) than its own.
        team = _team(medic_cell=(1, 50))
        _running(team[1], team[0], bases=["red"])
        _remember(team[1], _ps("red_scout", "red", (2, 30)), T)
        assert base_run_goal(team[1], _bases(), T) == (2, 50)

    def test_straight_when_every_near_cell_is_seen(self) -> None:
        line = _map(["." * 60])
        line.base_sight_data = {"red": frozenset({"0,50"})}
        team = _team(medic_cell=(0, 0))
        _running(team[1], team[0], bases=["red"])
        _remember(team[1], _ps("red_scout", "red", (0, 30)), T)
        assert base_run_goal(team[1], line, T) == (0, 50)


def _goal(player: PlayerState, everyone: list, mc: MapContext) -> Cell | None:
    return choose_goal_cell(player, everyone, {}, mc, "", T, time_domain="ticks")


class TestGoalOrder:
    """``choose_goal_cell``: the nuke reaction, then a Downed or reset runner's
    run goal ahead of its Downed option; an Active runner's right after the
    flip, ahead of the link return; a runner skips the Ammo leash."""

    def test_a_downed_runner_goes_ahead_of_its_downed_option(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pathfinding, "downed_goal", lambda *a: (1, 1))
        team = _team()
        medic = team[1]
        medic.last_downed_time = T - 2
        _running(medic, team[0])
        medic._committed_goal = ((0, 0), False, 0)
        assert _goal(medic, team, _bases()) == (2, 50)
        assert medic._committed_goal is None
        medic.base_run = None
        assert _goal(medic, team, _bases()) == (1, 1)

    def test_an_active_runner_with_a_live_downed_plan_goes_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pathfinding, "downed_goal", lambda *a: (1, 1))
        monkeypatch.setattr(pathfinding, "plan_is_live", lambda *a: True)
        team = _team()
        _running(team[1], team[0])
        assert _goal(team[1], team, _bases()) == (2, 50)

    def test_a_started_flip_beats_an_active_runner(self) -> None:
        team = _team()
        medic = team[1]
        medic.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T, arrived=T)
        _running(medic, team[0])
        assert _goal(medic, team, _bases()) == (4, 59)

    def test_an_active_runner_beats_the_link_return(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pathfinding, "_link_return_goal", lambda *a: (0, 0))
        team = _team()
        medic = team[1]
        _running(medic, team[0])
        medic._committed_goal = ((0, 0), False, 0)
        assert _goal(medic, team, _bases()) == (2, 50)
        assert medic._committed_goal is None
        medic.base_run = None
        assert _goal(medic, team, _bases()) == (0, 0)

    def test_the_enemy_nuke_reaction_beats_it(self) -> None:
        team = _team()
        medic = team[1]
        medic.reacting_to_nuke = True
        team[3].final_lives = 1  # the neediest ally: the Heavy on (4, 59)
        _running(medic, team[0])
        assert _goal(medic, team, _bases()) == (4, 59)

    def test_a_runner_skips_the_leash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pathfinding, "_leash_goal", lambda *a: (0, 0))
        team = _team(ammo_cell=(2, 0))
        ammo = team[2]
        _running(ammo, team[0])
        assert _goal(ammo, team, _bases()) == (2, 50)
        ammo.base_run = None
        assert _goal(ammo, team, _bases()) == (0, 0)


def _plan(
    player: PlayerState,
    team: list[PlayerState],
    monkeypatch: pytest.MonkeyPatch,
    choice: str = "hide",
) -> list[dict]:
    """``plan_action`` with the Action draw pinned to *choice*."""
    monkeypatch.setattr(combat.random, "choices", lambda *a, **k: [choice])
    return plan_action(player, team, T, _bases(), time_domain="ticks")


class TestForcedCapture:
    """One fixture per condition, each failing it alone, against the case
    where all hold: an Active runner on a range cell with 3 Shots (or as the
    Ammo) plans ``capture_base``."""

    def _on_range(self, role: str = "medic") -> tuple[PlayerState, list]:
        team = _team(medic_cell=(2, 50), ammo_cell=(2, 50))
        player = team[1] if role == "medic" else team[2]
        _running(player, team[0])
        return player, team

    def test_all_together_capture(self, monkeypatch: pytest.MonkeyPatch) -> None:
        medic, team = self._on_range()
        plans = _plan(medic, team, monkeypatch)
        assert [p["type"] for p in plans] == ["capture_base"]
        assert plans[0]["base_id"] == 13  # blue takes the red base

    def test_not_a_runner(self, monkeypatch: pytest.MonkeyPatch) -> None:
        medic, team = self._on_range()
        medic.base_run = None
        _plan(medic, team, monkeypatch)
        assert medic.last_chosen_action == "hide"

    def test_not_active(self, monkeypatch: pytest.MonkeyPatch) -> None:
        medic, team = self._on_range()
        medic.last_downed_time = T - 2
        _plan(medic, team, monkeypatch)
        assert medic.last_chosen_action == "hide"

    def test_not_on_a_range_cell(self, monkeypatch: pytest.MonkeyPatch) -> None:
        medic, team = self._on_range()
        medic.cell_col = 40
        _plan(medic, team, monkeypatch)
        assert medic.last_chosen_action == "hide"

    def test_a_medic_with_2_shots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        medic, team = self._on_range()
        medic.final_shots = 2
        _plan(medic, team, monkeypatch)
        assert medic.last_chosen_action == "hide"

    def test_an_ammo_with_no_shots_captures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ammo, team = self._on_range("ammo")
        ammo.final_shots = 0
        _plan(ammo, team, monkeypatch)
        assert ammo.last_chosen_action == "capture_base"


class TestHold:
    def test_a_runner_skips_the_hold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        team = _team()
        medic = team[1]
        _running(medic, team[0])
        medic.is_holding, medic.hold_until = True, T + 10
        plans = _plan(medic, team, monkeypatch)
        assert medic.last_chosen_action == "hide"
        assert medic.hold_until is None and plans != [{"type": "hold", "actor": medic}]

    def test_without_a_run_the_hold_stands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        team = _team()
        medic = team[1]
        medic.is_holding, medic.hold_until = True, T + 10
        assert _plan(medic, team, monkeypatch) == [{"type": "hold", "actor": medic}]


def _advance(player: PlayerState, team: list[PlayerState], tick: int = T) -> Cell:
    BatchSimulator()._advance_player(player, team, tick, _bases(), "", EventLog())
    assert player.cell_row is not None and player.cell_col is not None
    return (player.cell_row, player.cell_col)


class TestAdvance:
    def test_a_runner_neither_hides_nor_holds_and_walks(self) -> None:
        team = _team()
        medic = team[1]
        _running(medic, team[0])
        medic.is_hiding = medic.is_holding = True
        assert _advance(medic, team) != (2, 0)
        assert (medic.is_hiding, medic.is_holding) == (False, False)

    def test_the_advance_ticks_the_run(self, ends: list[str]) -> None:
        team = _team()
        _running(team[1], team[0])
        _advance(team[1], team, T + RUN_LEG_ARRIVE_TICKS + 1)
        assert ends == ["not_arrived"]


class TestRecordDown:
    def test_a_down_ends_the_run(self, ends: list[str]) -> None:
        from matches.sim_helpers.down import record_down

        team = _team()
        _running(team[1], team[0])
        record_down(team[1], T, RoundContext())
        assert ends == ["down"]


def _syracuse_round(seed: int) -> list[dict]:
    from matches.tests.test_missile_lock import _syracuse_round as play

    return play(seed)


@pytest.mark.django_db
class TestSyracuseRounds:
    """Whole rounds on the fixture map: the dispatch hook, ``record_down``
    and the Advance are wired, and a seeded round replays its runs."""

    @pytest.mark.parametrize("seed", [3, 6])
    def test_every_nuke_run_starts_as_its_commander_fires(self, seed: int) -> None:
        log = _syracuse_round(seed)
        fired = {
            e["timestamp"]
            for e in log
            if e["event_type"] == "special" and "nuke" in e["description"]
        }
        runs = [e for e in log if e["event_type"] == "base_run"]
        starts = [e for e in runs if e["metadata"]["phase"] == "start"]
        assert starts, f"no base run in Syracuse seed {seed}"
        for row in starts:
            assert row["metadata"]["trigger"] == "nuke"
            assert row["metadata"]["actor_role"] in ("medic", "ammo")
            assert row["timestamp"] in fired
        # One end row per run at most (a run may still be live at round end).
        per_actor: dict[int, list[str]] = {}
        for row in runs:
            per_actor.setdefault(row["actor_id"], []).append(row["metadata"]["phase"])
        for phases in per_actor.values():
            assert "".join(p[0] for p in phases).replace("se", "") in ("", "s")

    def test_the_same_seed_gives_the_same_events(self) -> None:
        first = _syracuse_round(6)
        assert any(e["event_type"] == "base_run" for e in first)
        assert first == _syracuse_round(6)

    def test_serial_equals_parallel(self) -> None:
        from matches.round_probe import run_probe
        from matches.tests.test_dodge import _syracuse_ctx

        ctx = _syracuse_ctx()
        assert run_probe(ctx, [3, 6], workers=1) == run_probe(ctx, [3, 6], workers=2)
