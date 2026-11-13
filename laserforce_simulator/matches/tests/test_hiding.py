"""MECH-23a (#607, spec #606): the walking hide.

``hide_cell`` and ``holds_in_place`` on hand-built grids from ``test_downed_option``
(``.`` is floor, ``#`` is wall, and a cell sees every floor cell in its row and
column), and ``BatchSimulator._advance_player`` on the same grids. ``feet_per_square``
is 1, so the hide's 15 ft reach is 15 squares, unless a test sets it.
"""

from __future__ import annotations

import random

import pytest

from matches.sim_helpers import combat, support_survival
from matches.sim_helpers.downed_option import STAY_HIDDEN
from matches.sim_helpers.hiding import HIDE_MAX_FEET, hide_cell, holds_in_place
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.support_survival import Visit
from matches.sim_helpers.time_constants import RESPAWN_TICKS
from matches.simulation import BatchSimulator
from matches.tests.test_downed_option import OPEN, _down, _map, _ps, _remember

NOW = 100
Cell = tuple[int, int]


def _counts(mc: MapContext, low: dict[Cell, int]) -> dict[str, int]:
    """Every floor cell's LOS count 10, except *low*'s cells."""
    counts = {f"{r},{c}": 10 for (r, c) in mc.adj}
    counts.update({f"{r},{c}": n for (r, c), n in low.items()})
    return counts


def _open(low: dict[Cell, int]) -> MapContext:
    return _map(["." * 30] * 15, _counts(OPEN, low))


def _hider(cell: Cell, role: str = "scout", team: str = "blue") -> PlayerState:
    player = _ps(f"{team}_{role}", team, cell, role=role)
    player.is_hiding = True
    return player


def _watched(cell: Cell, *watchers: Cell, at: int = NOW) -> PlayerState:
    """A hider at *cell* that remembers a red enemy on each of *watchers*."""
    hider = _hider(cell)
    for i, watcher in enumerate(watchers):
        _remember(hider, _ps(f"red_scout_{i}", "red", watcher), at)
    return hider


# The enemy on (7, 20) sees row 7 and column 20 of the open floor.
ENEMY: Cell = (7, 20)


class TestHideCell:
    def test_no_remembered_enemy_keeps_the_own_cell(self) -> None:
        # Even beside a cell more hidden than its own.
        assert hide_cell(_hider((7, 3)), NOW, _open({(6, 3): 1})) == (7, 3)

    def test_an_out_of_sight_cell_beats_a_nearer_one_in_sight(self) -> None:
        # (7, 4) is the most hidden cell on the map, but the enemy sees it.
        mc = _open({(7, 4): 1})
        assert hide_cell(_watched((7, 3), ENEMY), NOW, mc) == (6, 3)

    def test_of_two_out_of_sight_cells_the_lower_los_rank_beats_the_nearer(
        self,
    ) -> None:
        mc = _open({(3, 8): 1})
        assert hide_cell(_watched((7, 3), ENEMY), NOW, mc) == (3, 8)

    def test_with_equal_los_rank_the_nearer_wins_then_row_and_col(self) -> None:
        # (6, 3) and (8, 3) are both one step away and out of sight.
        assert hide_cell(_watched((7, 3), ENEMY), NOW, OPEN) == (6, 3)

    def test_the_own_cell_already_best_is_kept(self) -> None:
        assert hide_cell(_watched((6, 3), ENEMY), NOW, OPEN) == (6, 3)

    # Two rows: an enemy on (0, 4) sees row 0, one on (1, 0) sees row 1, so no cell
    # is out of sight. (0, 0) is seen by both; every other cell by one.
    TWO_ROWS = ["." * 5, "." * 5]

    def test_none_out_of_sight_the_fewest_watchers_win(self) -> None:
        # The own cell is the most hidden but seen twice.
        mc = _map(self.TWO_ROWS)
        mc.cell_los_counts = _counts(mc, {(0, 0): 1})
        assert hide_cell(_watched((0, 0), (0, 4), (1, 0)), NOW, mc) == (0, 1)

    def test_none_out_of_sight_then_the_lower_los_rank(self) -> None:
        mc = _map(self.TWO_ROWS)
        mc.cell_los_counts = _counts(mc, {(1, 3): 1})
        assert hide_cell(_watched((0, 0), (0, 4), (1, 0)), NOW, mc) == (1, 3)

    @pytest.mark.parametrize("gap, chosen", [(3, True), (4, False)])
    def test_the_reach_is_15_feet_of_walking(self, gap: int, chosen: bool) -> None:
        # At 5 ft a square the reach is 3 squares. A corridor on the enemy's row,
        # and one cell out of sight, (1, gap), a step off the corridor: *gap*
        # squares by walking from (0, 1).
        side = "#" * gap + "." + "#" * (19 - gap)
        mc = _map(["." * 20, side])
        mc.feet_per_square = 5.0
        assert mc.squares(HIDE_MAX_FEET) == 3
        hider = _watched((0, 1), (0, 19))
        expected = (1, gap) if chosen else (0, 1)
        assert hide_cell(hider, NOW, mc) == expected

    def test_a_stale_memory_entry_is_not_a_watcher(self) -> None:
        stale = support_survival.stale_table("ticks")["scout"]
        fresh = _watched((7, 3), ENEMY, at=NOW - stale)
        assert hide_cell(fresh, NOW, OPEN) == (6, 3)
        too_old = _watched((7, 3), ENEMY, at=NOW - stale - 1)
        assert hide_cell(too_old, NOW, OPEN) == (7, 3)

    def test_a_remembered_teammate_is_not_a_watcher(self) -> None:
        hider = _hider((7, 3))
        _remember(hider, _ps("blue_heavy", "blue", ENEMY), NOW)
        assert hide_cell(hider, NOW, OPEN) == (7, 3)

    def test_it_draws_no_rng(self) -> None:
        random.seed(11)
        before = random.getstate()
        hide_cell(_watched((7, 3), ENEMY), NOW, OPEN)
        assert random.getstate() == before


class TestHoldsInPlace:
    """(a) a waiting visitor; (b) a support an arrived visitor sees, an AND of four
    conditions, each failed alone by one fixture."""

    @pytest.mark.parametrize(
        "phase, holds", [("wait", True), ("at", True), ("go", False), (None, False)]
    )
    def test_a_visitor_holds_while_it_waits_or_is_at_its_supports(
        self, phase: str | None, holds: bool
    ) -> None:
        visitor = _hider((7, 3))
        if phase is not None:
            visitor.visit = Visit(phase=phase, started=90)  # type: ignore[arg-type]
        assert holds_in_place(visitor, [visitor], OPEN) is holds

    def _visitor(self, cell: Cell = (7, 10), team: str = "blue", phase: str = "at"):
        visitor = _ps(f"{team}_scout_9", team, cell)
        visitor.visit = Visit(phase=phase, started=90)  # type: ignore[arg-type]
        return visitor

    @pytest.mark.parametrize("role", ["medic", "ammo"])
    def test_a_support_an_arrived_visitor_sees_holds(self, role: str) -> None:
        support = _hider((7, 3), role=role)
        assert holds_in_place(support, [support, self._visitor()], OPEN)

    def test_a_commander_is_not_a_support(self) -> None:
        commander = _hider((7, 3), role="commander")
        assert not holds_in_place(commander, [commander, self._visitor()], OPEN)

    def test_a_visitor_on_the_other_team_does_not_hold_it(self) -> None:
        medic = _hider((7, 3), role="medic")
        assert not holds_in_place(medic, [medic, self._visitor(team="red")], OPEN)

    def test_a_visitor_still_walking_does_not_hold_it(self) -> None:
        medic = _hider((7, 3), role="medic")
        assert not holds_in_place(medic, [medic, self._visitor(phase="go")], OPEN)

    def test_a_visitor_that_does_not_see_it_does_not_hold_it(self) -> None:
        medic = _hider((7, 3), role="medic")
        unseen = self._visitor(cell=(5, 10))  # off row 7 and column 3
        assert not holds_in_place(medic, [medic, unseen], OPEN)


def _advance(
    player: PlayerState, everyone: list, tick: int = NOW, mc: MapContext = OPEN
) -> tuple[int | None, int | None]:
    BatchSimulator()._advance_player(player, everyone, tick, mc, "")
    return (player.cell_row, player.cell_col)


class TestAdvancePlayer:
    """``_advance_player``: a hider is no longer Stationary; it walks to its
    ``hide_cell`` unless ``holds_in_place`` keeps it still."""

    def test_a_hider_with_a_better_cell_in_reach_walks_to_it(self) -> None:
        # Replaces test_map.py's test_is_hiding_is_stationary_no_advance.
        hider = _watched((7, 3), ENEMY)
        assert _advance(hider, [hider]) == (6, 3)
        assert hider.movement_trail

    def test_a_hider_on_its_hide_cell_stays(self) -> None:
        hider = _watched((6, 3), ENEMY)
        assert _advance(hider, [hider]) == (6, 3)
        assert hider.movement_trail == []

    def test_a_hider_that_holds_in_place_stays(self) -> None:
        hider = _watched((7, 3), ENEMY)
        hider.visit = Visit(phase="wait", started=90)
        assert _advance(hider, [hider]) == (7, 3)

    def test_a_hider_seen_again_walks_on_the_next_tick(self) -> None:
        hider = _watched((6, 3), ENEMY)
        assert _advance(hider, [hider]) == (6, 3)
        _remember(hider, _ps("red_heavy", "red", (6, 25)), NOW + 1)  # sees row 6
        assert _advance(hider, [hider], NOW + 1) != (6, 3)

    def test_it_walks_at_normal_speed_on_an_only_move_roll(self) -> None:
        # A corridor on the enemy's row and column 0 down from it; the most
        # hidden cover is (14, 0), more than one tick's walk away.
        mc = _map(["." * 30] + ["." + "#" * 29] * 14)
        mc.cell_los_counts = _counts(mc, {(14, 0): 1})
        cells = []
        for action in ("hide", "only_move"):
            hider = _watched((0, 0), (0, 29))
            hider.last_chosen_action = action
            cells.append(_advance(hider, [hider], mc=mc))
        assert cells[0] == cells[1] != (0, 0)

    def test_hold_and_capture_base_stay_stationary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from matches.simulation import entrypoints

        # A far goal, so a player that is not Stationary would move.
        monkeypatch.setattr(entrypoints, "choose_goal_cell", lambda *a, **k: (7, 29))
        holder = _ps("blue_scout_1", "blue", (7, 3))
        holder.is_holding = True
        capturer = _ps("blue_scout_2", "blue", (7, 3))
        capturer.last_chosen_action = "capture_base"
        assert _advance(holder, [holder]) == (7, 3)
        assert _advance(capturer, [capturer]) == (7, 3)


class TestStayHiddenWalks:
    def test_stay_hidden_walks_out_of_sight_while_the_plan_is_live(self) -> None:
        downed = _ps("blue_scout_1", "blue", (7, 2))
        _remember(downed, _ps("red_scout_1", "red", ENEMY), NOW)
        _down(downed, None, NOW, OPEN, 0.0)
        assert downed.downed_plan.option == STAY_HIDDEN
        assert _advance(downed, [downed], NOW + 1) == (6, 2)
        assert downed.is_hiding

    def test_a_downed_support_an_arrived_visitor_sees_still_walks(self) -> None:
        # Decision 18a (acceptance run, 2026-11-12): holds_in_place does not apply
        # while the Downed plan is live, so a Downed Medic beside a visitor still
        # leaves its attacker's sight. Once Active, decision 18 holds it.
        medic = _ps("blue_medic", "blue", (7, 2), role="medic")
        _remember(medic, _ps("red_scout_1", "red", ENEMY), NOW)
        _down(medic, None, NOW, OPEN, 0.0)
        assert medic.downed_plan.option == STAY_HIDDEN
        visitor = _ps("blue_scout_9", "blue", (7, 10))  # sees row 7
        visitor.visit = Visit(phase="at", started=90)
        assert holds_in_place(medic, [medic, visitor], OPEN)
        assert _advance(medic, [medic, visitor], NOW + 1) == (6, 2)

    def test_once_active_a_support_an_arrived_visitor_sees_holds(self) -> None:
        medic = _hider((7, 2), role="medic")
        _remember(medic, _ps("red_scout_1", "red", ENEMY), NOW)
        visitor = _ps("blue_scout_9", "blue", (7, 10))
        visitor.visit = Visit(phase="at", started=90)
        assert _advance(medic, [medic, visitor]) == (7, 2)

    def test_at_active_the_plan_and_the_commitment_are_cleared(self) -> None:
        # The hide path skips choose_goal_cell, whose downed_goal clears them
        # otherwise; no remembered enemy, so the player also stays put.
        downed = _ps("blue_scout_1", "blue", (7, 2))
        _down(downed, None, NOW, OPEN, 0.0)
        downed._committed_goal = ((1, 1), False, 999)
        _advance(downed, [downed], NOW + RESPAWN_TICKS)
        assert downed.downed_plan is None
        assert downed._committed_goal is None


class TestArmingCommanderWalks:
    def test_the_arming_commander_walks_out_of_sight(self) -> None:
        commander = _ps("blue_commander", "blue", (7, 3), role="commander")
        commander.special_active_until = NOW + 10
        _remember(commander, _ps("red_scout_1", "red", ENEMY), NOW)
        plans = combat.plan_action(
            commander, [commander], NOW, OPEN, time_domain="ticks"
        )
        assert plans == [{"type": "hide", "actor": commander}]
        BatchSimulator._apply_hide(commander, NOW)
        assert _advance(commander, [commander]) == (6, 3)
