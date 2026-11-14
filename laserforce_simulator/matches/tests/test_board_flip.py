"""MECH-25 (#617): the Board flip. A Tag streak (three Tags in a row on the
Medic while it is Active, each within 60 ticks of the one before) makes a flip
pending; the Medic crosses out of sight to a far cell, stays, then returns."""

from __future__ import annotations

import random
from collections.abc import Callable

import pytest

from matches.sim_helpers import board_flip
from matches.sim_helpers.board_flip import (
    FLIP_ARRIVE_TICKS,
    FLIP_STAY_TICKS,
    MAX_FLIPS_PER_ROUND,
    TAG_STREAK_GAP_TICKS,
    Flip,
    end_flip,
    end_on_down,
    flip_cell,
    flip_goal,
    note_tag,
    tick_flip,
)
from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.pathfinding import choose_goal_cell
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.support_survival import Visit
from matches.sim_helpers.time_constants import RESPAWN_TICKS
from matches.simulation import BatchSimulator
from matches.tests.test_downed_option import _map, _ps, _remember

Cell = tuple[int, int]
T = 200


def _medic(cell: Cell = (7, 3), role: str = "medic") -> PlayerState:
    return _ps(f"blue_{role}", "blue", cell, role=role)


def _tags(player: PlayerState, *ticks: int) -> None:
    for tick in ticks:
        note_tag(player, tick)


def _flip(player: PlayerState) -> Flip:
    assert player.flip is not None
    return player.flip


def _cell(player: PlayerState) -> Cell:
    assert player.cell_row is not None and player.cell_col is not None
    return (player.cell_row, player.cell_col)


def _recording(reasons: list[str], real: Callable[..., None]) -> Callable[..., None]:
    """``end_flip`` that also records each reason."""

    def end(player: PlayerState, tick: int, reason: str) -> None:
        reasons.append(reason)
        real(player, tick, reason)

    return end


class TestTagStreak:
    """``note_tag``: one fixture per condition, each failing that one alone."""

    def test_the_third_tag_in_a_row_makes_a_flip_pending(self) -> None:
        medic = _medic()
        _tags(medic, T, T + 30)
        assert medic.flip is None
        note_tag(medic, T + 60)
        assert medic.flip == Flip(from_cell=(7, 3))
        assert _flip(medic).target is None
        # Kept until the flip starts, so a flip with no cell can retry.
        assert medic.tag_streak == [T, T + 30, T + 60]

    def test_a_gap_of_60_ticks_continues_the_streak(self) -> None:
        medic = _medic()
        _tags(medic, T, T + TAG_STREAK_GAP_TICKS, T + 2 * TAG_STREAK_GAP_TICKS)
        assert medic.flip is not None

    def test_a_gap_of_61_ticks_restarts_it(self) -> None:
        medic = _medic()
        _tags(medic, T, T + 30, T + 30 + TAG_STREAK_GAP_TICKS + 1)
        assert medic.flip is None
        assert medic.tag_streak == [T + 30 + TAG_STREAK_GAP_TICKS + 1]

    def test_a_tag_while_not_active_is_ignored(self) -> None:
        medic = _medic()
        _tags(medic, T, T + 30)
        medic.last_downed_time = T + 40  # in the Respawn cooldown at T + 50
        note_tag(medic, T + 50)
        assert medic.flip is None
        assert medic.tag_streak == [T, T + 30]

    @pytest.mark.parametrize("role", ["ammo", "commander", "heavy", "scout"])
    def test_a_tag_on_another_role_is_ignored(self, role: str) -> None:
        player = _medic(role=role)
        _tags(player, T, T + 30, T + 60)
        assert player.flip is None
        assert player.tag_streak == []

    def test_no_streak_counts_once_the_round_cap_is_reached(self) -> None:
        medic = _medic()
        medic.flips_started = MAX_FLIPS_PER_ROUND
        _tags(medic, T, T + 30, T + 60)
        assert medic.flip is None
        assert medic.tag_streak == []

    def test_a_pending_flip_ignores_further_tags(self) -> None:
        medic = _medic()
        medic.flip = Flip(from_cell=(1, 1))
        medic.tag_streak = [T - 60, T - 30, T - 1]
        _tags(medic, T, T + 30, T + 60)
        assert medic.flip == Flip(from_cell=(1, 1))
        assert medic.tag_streak == [T - 60, T - 30, T - 1]

    def test_a_hiding_or_visit_pinned_medic_counts(self) -> None:
        medic = _medic()
        medic.is_hiding = True
        medic.visit = Visit(phase="at", started=T)
        _tags(medic, T, T + 30, T + 60)
        assert medic.flip is not None

    def test_a_third_tag_never_replaces_a_started_flip(self) -> None:
        # Only a Down ends a started flip (through ``end_flip``); a streak
        # completed during it waits for it to end.
        medic = _medic()
        started = Flip(from_cell=(1, 1), target=(9, 9), started=T - 50)
        medic.flip = started
        _tags(medic, T, T + 30, T + 60)
        assert medic.flip is started
        assert medic.tag_streak == [T, T + 30, T + 60]

    def test_a_tag_on_a_started_flip_counts_toward_the_next(self) -> None:
        medic = _medic()
        medic.flip = Flip(from_cell=(1, 1), target=(9, 9), started=T)
        note_tag(medic, T + 5)
        assert medic.tag_streak == [T + 5]


class TestEnds:
    def test_a_down_ends_a_started_flip(self) -> None:
        medic = _medic()
        medic.flip = Flip(from_cell=(1, 1), target=(4, 59), started=T)
        end_on_down(medic, T + 5)
        assert medic.flip is None

    def test_a_down_leaves_a_pending_flip(self) -> None:
        medic = _medic()
        medic.flip = Flip(from_cell=(1, 1))
        end_on_down(medic, T + 5)
        assert medic.flip == Flip(from_cell=(1, 1))

    @pytest.mark.parametrize("target", [None, (4, 59)])
    def test_an_elimination_ends_a_pending_or_started_flip(self, target) -> None:
        medic = _medic()
        medic.final_lives = 0
        medic.flip = Flip(from_cell=(1, 1), target=target, started=T)
        end_on_down(medic, T + 5)
        assert medic.flip is None

    def test_end_flip_refuses_an_unknown_reason(self) -> None:
        medic = _medic()
        medic.flip = Flip(from_cell=(1, 1))
        with pytest.raises(ValueError):
            end_flip(medic, T, "bored")  # type: ignore[arg-type]
        assert medic.flip is not None


# 5 rows x 60 columns of open floor; a cell sees its own row and column.
WIDE = _map(["." * 60] * 5)


def _wide(low: dict[Cell, int] | None = None, spawn: dict | None = None) -> MapContext:
    counts = {f"{r},{c}": 10 for (r, c) in WIDE.adj}
    counts.update({f"{r},{c}": n for (r, c), n in (low or {}).items()})
    mc = _map(["." * 60] * 5, counts)
    if spawn:
        mc.spawn_cells = spawn
    return mc


def _seen_by(medic: PlayerState, *watchers: Cell, at: int = T) -> PlayerState:
    for i, watcher in enumerate(watchers):
        _remember(medic, _ps(f"red_scout_{i}", "red", watcher), at)
    return medic


class TestFlipCell:
    """``flip_cell``: at least 40 squares' walk, unseen, not in the enemy base
    zone; farthest from the nearest watcher, then ``los_rank``, then walk."""

    def test_it_picks_the_cell_farthest_from_the_watchers(self) -> None:
        medic = _seen_by(_medic((2, 0)), (0, 10))
        assert flip_cell(medic, T, _wide(), (2, 0)) == (4, 59)

    def test_a_cell_a_watcher_sees_is_left_out(self) -> None:
        # (4, 0) sees row 4: (4, 59) would be farthest otherwise.
        medic = _seen_by(_medic((2, 0)), (0, 10), (4, 0))
        assert flip_cell(medic, T, _wide(), (2, 0)) == (3, 59)

    def test_a_cell_under_40_squares_walk_is_left_out(self) -> None:
        # (0, 0) is farthest from (2, 59), but only 22 squares from the Medic.
        medic = _seen_by(_medic((2, 20)), (2, 59))
        assert flip_cell(medic, T, _wide(), (2, 20)) == (0, 58)

    def test_a_cell_in_the_enemy_base_zone_is_left_out(self) -> None:
        # Red base (4, 59), blue base (4, 40): columns 50+ are red's zone.
        spawn = {"red": (4, 59), "blue": (4, 40)}
        medic = _seen_by(_medic((2, 0)), (0, 10))
        assert flip_cell(medic, T, _wide(spawn=spawn), (2, 0)) == (4, 49)

    def test_a_tie_on_distance_goes_to_the_lower_los_rank(self) -> None:
        # (0, 59) and (4, 59) are both 51 from (2, 10); (4, 59) is more hidden.
        medic = _seen_by(_medic((2, 0)), (2, 10))
        assert flip_cell(medic, T, _wide({(4, 59): 1}), (2, 0)) == (4, 59)
        assert flip_cell(medic, T, _wide({(0, 59): 1}), (2, 0)) == (0, 59)

    def test_with_no_remembered_enemy_it_measures_from_the_from_cell(self) -> None:
        # From (4, 40) (which sees row 4), (0, 59) is farthest; from (0, 0)
        # it would be (4, 59).
        assert flip_cell(_medic((2, 0)), T, _wide(), (4, 40)) == (0, 59)

    def test_none_when_no_cell_qualifies(self) -> None:
        # Nothing on the floor is 40 squares' walk from the middle.
        medic = _seen_by(_medic((2, 30)), (2, 0))
        assert flip_cell(medic, T, _wide(), (2, 30)) is None


def _pending(cell: Cell = (2, 0), *watchers: Cell) -> PlayerState:
    medic = _seen_by(_medic(cell), *watchers)
    medic.flip = Flip(from_cell=cell)
    medic.tag_streak = [T - 40, T - 20, T - 2]
    return medic


def _rows(events: EventLog) -> list[dict]:
    return [e for e in events.entries if e["event_type"] == "board_flip"]


class TestTickFlip:
    def test_a_pending_flip_waits_for_the_medic_to_be_active(self) -> None:
        medic = _pending((2, 0), (0, 10))
        medic.last_downed_time = T
        events = EventLog()
        tick_flip(medic, T + RESPAWN_TICKS - 1, _wide(), events)
        assert _flip(medic).target is None
        assert _rows(events) == []
        tick_flip(medic, T + RESPAWN_TICKS, _wide(), events)
        assert _flip(medic).target == (4, 59)
        assert _flip(medic).started == T + RESPAWN_TICKS
        assert medic.flips_started == 1
        assert medic.tag_streak == []

    def test_the_start_writes_one_board_flip_row(self) -> None:
        medic = _pending((2, 0), (0, 10))
        events = EventLog()
        for tick in range(T, T + 5):
            tick_flip(medic, tick, _wide(), events)
        (row,) = _rows(events)
        assert row["timestamp"] == T
        assert (row["metadata"]["target_row"], row["metadata"]["target_col"]) == (
            4,
            59,
        )

    def test_the_cell_is_chosen_once(self) -> None:
        medic = _pending((2, 0), (0, 10))
        tick_flip(medic, T, _wide(), EventLog())
        _remember(medic, _ps("red_heavy", "red", (4, 50)), T + 1)
        tick_flip(medic, T + 1, _wide(), EventLog())
        assert _flip(medic).target == (4, 59)

    def test_no_cell_means_no_flip_and_the_streak_comes_back(self) -> None:
        medic = _pending((2, 30), (2, 0))
        events = EventLog()
        tick_flip(medic, T, _wide(), events)
        assert medic.flip is None
        assert medic.flips_started == 0
        assert _rows(events) == []
        # The next Tag in the window completes the streak again.
        assert medic.tag_streak == [T - 40, T - 20, T - 2]
        note_tag(medic, T + 10)
        assert medic.flip is not None

    def test_standing_on_the_target_is_arrival(self) -> None:
        medic = _medic((4, 59))
        medic.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T)
        tick_flip(medic, T + 20, _wide(), EventLog())
        assert _flip(medic).arrived == T + 20

    def test_not_arrived_ends_after_60_ticks(self) -> None:
        medic = _medic((2, 0))
        medic.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T)
        tick_flip(medic, T + FLIP_ARRIVE_TICKS, _wide(), EventLog())
        assert medic.flip is not None
        tick_flip(medic, T + FLIP_ARRIVE_TICKS + 1, _wide(), EventLog())
        assert medic.flip is None

    def test_the_stay_ends_after_120_ticks(self) -> None:
        medic = _medic((4, 59))
        medic.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T, arrived=T)
        tick_flip(medic, T + FLIP_STAY_TICKS, _wide(), EventLog())
        assert medic.flip is not None
        tick_flip(medic, T + FLIP_STAY_TICKS + 1, _wide(), EventLog())
        assert medic.flip is None

    def test_every_ending_goes_through_end_flip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []
        real = board_flip.end_flip
        monkeypatch.setattr(
            board_flip,
            "end_flip",
            _recording(seen, real),
        )
        late = _medic((2, 0))
        late.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T)
        tick_flip(late, T + FLIP_ARRIVE_TICKS + 1, _wide(), EventLog())
        stayed = _medic((4, 59))
        stayed.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T, arrived=T)
        tick_flip(stayed, T + FLIP_STAY_TICKS + 1, _wide(), EventLog())
        downed = _medic()
        downed.flip = Flip(from_cell=(1, 1), target=(4, 59), started=T)
        board_flip.end_on_down(downed, T)
        out = _medic()
        out.final_lives = 0
        out.flip = Flip(from_cell=(1, 1))
        board_flip.end_on_down(out, T)
        assert seen == ["not_arrived", "stayed", "down", "eliminated"]


def _crossing(cell: Cell, *watchers: Cell) -> PlayerState:
    medic = _pending(cell, *watchers)
    tick_flip(medic, T, _wide(), EventLog())
    return medic


class TestFlipGoal:
    def test_crossing_steps_to_the_unseen_cell_nearest_the_target(self) -> None:
        # The watcher (4, 10) sees row 4 and column 10; the target, farthest
        # from it, is (0, 59). Within 15 squares of (2, 0), (0, 13), (1, 14)
        # and (2, 15) are all 46 from the target and 15 from the Medic: the
        # cell breaks the tie.
        medic = _crossing((2, 0), (4, 10))
        assert _flip(medic).target == (0, 59)
        assert flip_goal(medic, T, _wide()) == (0, 13)

    def test_a_seen_cell_is_never_the_waypoint(self) -> None:
        # A watcher on row 0 rules out (0, 13): (1, 14) is next.
        medic = _crossing((2, 0), (4, 10))
        _remember(medic, _ps("red_heavy", "red", (0, 40)), T)
        assert flip_goal(medic, T, _wide()) == (1, 14)

    def test_it_goes_straight_when_no_enemy_is_remembered(self) -> None:
        medic = _medic((2, 0))
        medic.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T)
        _flip(medic).to_target = _wide().walk_distances((4, 59))
        assert flip_goal(medic, T, _wide()) == (4, 59)

    def test_it_goes_straight_when_every_near_cell_is_seen(self) -> None:
        # One row: the watcher on it sees every cell.
        line = _map(["." * 60])
        medic = _seen_by(_medic((0, 0)), (0, 30))
        medic.flip = Flip(
            from_cell=(0, 0),
            target=(0, 59),
            started=T,
            to_target=line.walk_distances((0, 59)),
        )
        assert flip_goal(medic, T, line) == (0, 59)

    def test_after_arrival_the_goal_is_the_target(self) -> None:
        # Seen there now ((4, 30) sees row 4), but it stays: a crossing would
        # step to (3, 59).
        medic = _seen_by(_medic((3, 57)), (4, 30))
        medic.flip = Flip(
            from_cell=(2, 0),
            target=(4, 59),
            started=T,
            arrived=T + 20,
            to_target=WIDE.walk_distances((4, 59)),
        )
        assert flip_goal(medic, T, _wide()) == (4, 59)


def _goal(player: PlayerState, everyone: list, mc: MapContext) -> Cell | None:
    return choose_goal_cell(player, everyone, {}, mc, "", T, time_domain="ticks")


class TestGoalOrder:
    """``choose_goal_cell``: Downed plan, then Join, then the flip, before the
    link return, call-outs, commitment and the Mode goal (the Medic's back)."""

    def test_a_started_flip_is_the_goal(self) -> None:
        medic = _seen_by(_medic((4, 59)), (0, 10))
        medic.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T, arrived=T)
        medic._committed_goal = ((0, 0), False, 0)
        assert _goal(medic, [medic], _wide()) == (4, 59)
        assert medic._committed_goal is None

    def test_a_pending_flip_changes_nothing(self) -> None:
        goals = []
        for flip in (None, Flip(from_cell=(2, 0))):
            medic = _seen_by(_medic((2, 0)), (0, 10))
            medic.flip = flip
            random.seed(5)
            goals.append(_goal(medic, [medic], _wide()))
        assert goals[0] == goals[1]

    def test_a_join_beats_the_flip(self) -> None:
        # The Ammo on (0, 30) sees a visitor on (0, 40) that the Medic on
        # (4, 59) does not: the Medic walks to (0, 59), which sees it.
        medic = _medic((4, 59))
        medic.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T, arrived=T)
        ammo = _ps("blue_ammo", "blue", (0, 30), role="ammo")
        visitor = _ps("blue_scout_1", "blue", (0, 40))
        visitor.visit = Visit(phase="go", started=T - 5)
        assert _goal(medic, [medic, ammo, visitor], _wide()) == (0, 59)

    def test_a_live_downed_plan_beats_the_flip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from matches.sim_helpers import pathfinding

        monkeypatch.setattr(pathfinding, "downed_goal", lambda *a: (1, 1))
        medic = _medic((4, 59))
        medic.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T, arrived=T)
        assert _goal(medic, [medic], _wide()) == (1, 1)


def _advance(medic: PlayerState, events: EventLog, tick: int = T) -> Cell:
    BatchSimulator()._advance_player(medic, [medic], tick, _wide(), "", events)
    return _cell(medic)


class TestAdvance:
    """``_advance_player`` runs ``tick_flip``; a crossing Medic walks."""

    def test_a_pending_flip_starts_in_the_advance(self) -> None:
        medic = _pending((2, 0), (0, 10))
        events = EventLog()
        _advance(medic, events)
        assert _flip(medic).target == (4, 59)
        assert len(_rows(events)) == 1

    def test_a_crossing_medic_neither_hides_nor_holds(self) -> None:
        medic = _pending((2, 0), (0, 10))
        medic.is_hiding = True
        medic.is_holding = True
        assert _advance(medic, EventLog()) != (2, 0)
        assert (medic.is_hiding, medic.is_holding) == (False, False)

    def test_an_arrived_medic_may_hide_again(self) -> None:
        medic = _seen_by(_medic((4, 59)), (0, 10))
        medic.flip = Flip(from_cell=(2, 0), target=(4, 59), started=T, arrived=T)
        medic.is_holding = True
        assert _advance(medic, EventLog(), T + 1) == (4, 59)
        assert medic.is_holding


def _hit(shooter: PlayerState, medic: PlayerState, tick: int) -> None:
    from unittest.mock import patch

    from matches.sim_helpers.round_context import RoundContext
    from matches.sim_helpers.shot import SHOT_KIND_INITIAL, resolve_shot

    ctx = RoundContext(events=EventLog(), all_alive=[shooter, medic])
    with (
        patch("random.randint", return_value=1),
        patch("random.random", return_value=0.0),
    ):
        resolve_shot(shooter, medic, tick, kind=SHOT_KIND_INITIAL, ctx=ctx)


class TestShotHooks:
    """The streak is counted where a Tag lands, before the Down it causes
    (the Medic has 1 shield), and ``record_down`` ends a started flip."""

    def _pair(self) -> tuple[PlayerState, PlayerState]:
        from matches.tests.test_dodge import _lock_ps

        medic = _lock_ps(2, team_color="blue", role="medic")
        medic.cell_row, medic.cell_col = (3, 4)
        return _lock_ps(1), medic

    def test_three_tags_in_a_row_leave_a_pending_flip(self) -> None:
        shooter, medic = self._pair()
        for tick in (T, T + 20, T + 40):  # each after the 16-tick Respawn
            _hit(shooter, medic, tick)
        assert medic.final_lives == 7
        assert medic.flip == Flip(from_cell=_cell(medic))

    def test_a_tag_ends_a_started_flip(self) -> None:
        shooter, medic = self._pair()
        medic.flip = Flip(from_cell=(1, 1), target=(4, 59), started=T - 5)
        _hit(shooter, medic, T)
        assert medic.flip is None
        assert medic.tag_streak == [T]


def _syracuse_round(seed: int) -> list[dict]:
    from matches.tests.test_missile_lock import _syracuse_round as play

    return play(seed)


@pytest.mark.django_db
class TestSyracuseRounds:
    """Whole rounds on the fixture map: the hooks in the shot resolver,
    ``record_down`` and the tick loop are wired."""

    def test_medics_flip_at_most_three_times(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ends: list[str] = []
        real = board_flip.end_flip
        monkeypatch.setattr(
            board_flip,
            "end_flip",
            _recording(ends, real),
        )
        per_medic: dict[tuple[int, int], int] = {}
        for seed in (1, 2, 3, 4):
            for row in _syracuse_round(seed):
                if row["event_type"] != "board_flip":
                    continue
                assert row["metadata"]["actor_role"] == "medic"
                key = (seed, row["actor_id"])
                per_medic[key] = per_medic.get(key, 0) + 1
        assert per_medic, "no Board flip in four Syracuse rounds"
        assert max(per_medic.values()) <= MAX_FLIPS_PER_ROUND
        assert "down" in ends  # record_down ends a started flip

    def test_the_same_seed_gives_the_same_events(self) -> None:
        assert _syracuse_round(3) == _syracuse_round(3)

    def test_serial_equals_parallel(self) -> None:
        from matches.round_probe import run_probe
        from matches.tests.test_dodge import _syracuse_ctx

        ctx = _syracuse_ctx()
        assert run_probe(ctx, [1, 2], workers=1) == run_probe(ctx, [1, 2], workers=2)


class TestBoardFlipEvent:
    def test_the_row(self) -> None:
        medic = _medic((2, 0))
        events = EventLog()
        events.board_flip(medic, T, (4, 59))
        (row,) = events.entries
        assert row["event_type"] == "board_flip"
        assert row["actor_id"] == medic.player_id
        assert row["target_id"] is None
        assert row["timestamp"] == T
        assert row["points_awarded"] == 0
        assert row["metadata"]["target_row"] == 4
        assert row["metadata"]["target_col"] == 59
        assert row["metadata"]["actor_role"] == "medic"

    def test_the_null_log_writes_nothing(self) -> None:
        events = EventLog(persist=False)
        events.board_flip(_medic(), T, (4, 59))
        assert len(events) == 0

    def test_it_is_a_game_event_choice(self) -> None:
        from matches.models import GameEvent

        assert ("board_flip", "Board Flip") in GameEvent.EVENT_TYPES
