"""CAL-03 rules 6, 7 and 10 (#588, #591): Resupply visits, call-outs and the
backline (``sim_helpers.resupply_visit``), at the helper, ``plan_action``,
``choose_goal_cell`` and ``attempt_resupply`` seams. In-memory ``PlayerState`` on an
open grid whose sight lines each test names; no RNG unless a test pins it.
"""

from __future__ import annotations

import math
import random
import typing
from dataclasses import replace
from typing import Any

import pytest

from matches.sim_helpers import combat, pathfinding, resupply_visit, support_survival
from matches.sim_helpers.down import record_down
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.round_context import RoundContext
from matches.sim_helpers.support_survival import Visit
from matches.tests.test_support_survival import _grid_map, _player, _sight

NOW = 500
HERE = (0, 0)
MEDIC_AT = (10, 10)
AMMO_AT = (10, 12)
SHARE_LIVES = math.floor(30 / 3)  # a default Scout's visit share of its 30 lives


def _scene(
    sight: dict[str, set[str]] | None = None, role: str = "scout"
) -> tuple[PlayerState, PlayerState, PlayerState, list, MapContext]:
    """A *role* at ``HERE`` with lives just at its visit share, above
    ``VISIT_SEEK_SHARE``, and full shots; its Medic and Ammo far off. Nobody sees
    anybody unless *sight* says so."""
    player = _player(role, cell_row=HERE[0], cell_col=HERE[1])
    player.final_lives = math.floor(support_survival.visit_share(player) * 30)
    medic = _player("medic", cell_row=MEDIC_AT[0], cell_col=MEDIC_AT[1])
    ammo = _player("ammo", cell_row=AMMO_AT[0], cell_col=AMMO_AT[1])
    return player, medic, ammo, [player, medic, ammo], _grid_map(sight=sight, size=16)


def _step(player: PlayerState, everyone: list, ctx: MapContext, tick: int = NOW) -> Any:
    return resupply_visit.visit_step(player, everyone, ctx, tick)


def _on_a_visit(player: PlayerState, **fields: Any) -> PlayerState:
    player.visit = Visit(**{"phase": "go", "started": NOW - 10, **fields})
    return player


def _ended_once_at(player: PlayerState, everyone: list, ctx: MapContext) -> None:
    """The visit ended this tick with one cooldown, which a later tick keeps."""
    assert player.visit is None
    assert player.visit_next_tick == NOW + support_survival.VISIT_COOLDOWN_TICKS
    _step(player, everyone, ctx, NOW + 1)
    assert player.visit is None
    assert player.visit_next_tick == NOW + support_survival.VISIT_COOLDOWN_TICKS


class TestTheVisitStarts:
    """Lives or shots at ``visit_share`` of the cap, Active, outside the cooldown;
    at ``VISIT_SEEK_SHARE`` or lower inside it too, outside the critical one."""

    def test_lives_at_the_share(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        assert scout.final_lives <= support_survival.visit_share(scout) * 30
        _step(scout, everyone, ctx)
        assert scout.visit is not None and scout.visit.started == NOW

    def test_shots_at_the_share(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        scout.final_lives = scout.max_lives
        scout.final_shots = math.floor(scout.max_shots / 3)
        _step(scout, everyone, ctx)
        assert scout.visit is not None

    def test_no_visit_with_both_above_it(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        scout.final_lives = SHARE_LIVES + 1
        scout.final_shots = math.floor(scout.max_shots / 3) + 1
        assert _step(scout, everyone, ctx) is None
        assert scout.visit is None

    def test_no_visit_when_not_active(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        scout.last_downed_time = NOW - 1
        assert _step(scout, everyone, ctx) is None
        assert scout.visit is None

    def test_no_visit_inside_the_cooldown_above_the_seek_share(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        assert scout.final_lives > support_survival.VISIT_SEEK_SHARE * 30
        scout.visit_next_tick = NOW + 1
        assert _step(scout, everyone, ctx) is None

    def test_a_visit_inside_the_cooldown_at_the_seek_share(self) -> None:
        """A Critical visit ignores ``visit_next_tick``; its own
        ``critical_visit_next_tick`` is at its default 0 here (CAL-04b)."""
        scout, _, _, everyone, ctx = _scene()
        scout.final_lives = math.floor(support_survival.VISIT_SEEK_SHARE * 30)
        scout.visit_next_tick = NOW + 1
        _step(scout, everyone, ctx)
        assert scout.visit is not None

    def test_the_cooldown_ends_at_visit_next_tick(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        scout.visit_next_tick = NOW
        _step(scout, everyone, ctx)
        assert scout.visit is not None

    @pytest.mark.parametrize("role", ["medic", "ammo"])
    def test_supports_make_no_visit(self, role: str) -> None:
        support = _player(role, cell_row=0, cell_col=0, final_lives=1)
        other = _player("ammo" if role == "medic" else "medic", cell_row=5, cell_col=5)
        assert _step(support, [support, other], _grid_map()) is None
        assert support.visit is None

    def test_no_visit_on_a_map_less_round(self) -> None:
        scout, _, _, everyone, _ = _scene()
        assert resupply_visit.visit_step(scout, everyone, None, NOW) is None
        assert scout.visit is None


CRITICAL_LIVES = math.floor(support_survival.VISIT_SEEK_SHARE * 30)


class TestTheCriticalCooldown:
    """CAL-04b (#600): a Critical visit (``VISIT_SEEK_SHARE`` or less) waits
    ``CRITICAL_COOLDOWN_TICKS`` after the last visit ended; a non-critical start
    keeps the ``visit_next_tick`` rule. Each fixture fails one condition alone."""

    def _critical(self, critical_next: int) -> tuple:
        scout, _, _, everyone, ctx = _scene()
        scout.final_lives = CRITICAL_LIVES
        scout.critical_visit_next_tick = critical_next
        return scout, everyone, ctx

    def test_no_critical_visit_one_tick_before(self) -> None:
        scout, everyone, ctx = self._critical(NOW + 1)
        assert _step(scout, everyone, ctx) is None
        assert scout.visit is None

    def test_a_critical_visit_at_exactly_the_tick(self) -> None:
        scout, everyone, ctx = self._critical(NOW)
        _step(scout, everyone, ctx)
        assert scout.visit is not None

    def test_a_non_critical_start_ignores_it_outside_visit_next_tick(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        assert scout.final_lives > CRITICAL_LIVES
        scout.critical_visit_next_tick = NOW + 99
        _step(scout, everyone, ctx)
        assert scout.visit is not None

    def test_a_non_critical_start_waits_for_visit_next_tick(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        scout.visit_next_tick = NOW + 1
        scout.critical_visit_next_tick = 0
        assert _step(scout, everyone, ctx) is None

    @pytest.mark.parametrize("reason", typing.get_args(resupply_visit.VisitEnd))
    def test_end_visit_sets_both_next_ticks(self, reason: str) -> None:
        scout, _, _, _, _ = _scene()
        _on_a_visit(scout)
        resupply_visit.end_visit(scout, NOW, reason)  # type: ignore[arg-type]
        assert scout.visit_next_tick == NOW + support_survival.VISIT_COOLDOWN_TICKS
        assert (
            scout.critical_visit_next_tick
            == NOW + support_survival.CRITICAL_COOLDOWN_TICKS
        )


SAT_LIVES = math.ceil(support_survival.SATISFIED_SHARE * 30)  # a Scout's caps
SAT_SHOTS = math.ceil(support_survival.SATISFIED_SHARE * 60)


class TestSatisfied:
    """CAL-04b (#600): Lives and Shots each at ``SATISFIED_SHARE`` of the cap, or
    ``SATISFIED_DOUBLES`` doubles on the visit. Each fixture fails one alone."""

    def _scout(self, lives: int, shots: int, doubles: int = 0) -> tuple:
        scout, _, _, _, _ = _scene()
        scout.final_lives, scout.final_shots = lives, shots
        return scout, Visit(phase="at", started=NOW - 10, doubles=doubles)

    def test_lives_at_the_share_with_shots_one_below(self) -> None:
        scout, visit = self._scout(SAT_LIVES, SAT_SHOTS - 1)
        assert not resupply_visit.satisfied(scout, visit)

    def test_shots_at_the_share_with_lives_one_below(self) -> None:
        scout, visit = self._scout(SAT_LIVES - 1, SAT_SHOTS)
        assert not resupply_visit.satisfied(scout, visit)

    def test_both_at_the_share(self) -> None:
        scout, visit = self._scout(SAT_LIVES, SAT_SHOTS)
        assert resupply_visit.satisfied(scout, visit)

    def test_one_double_short(self) -> None:
        scout, visit = self._scout(1, 1, support_survival.SATISFIED_DOUBLES - 1)
        assert not resupply_visit.satisfied(scout, visit)

    def test_at_satisfied_doubles(self) -> None:
        scout, visit = self._scout(1, 1, support_survival.SATISFIED_DOUBLES)
        assert resupply_visit.satisfied(scout, visit)

    def test_satisfied_beats_timeout(self) -> None:
        scout, _ = self._scout(SAT_LIVES, SAT_SHOTS)
        visit = Visit(
            phase="at",
            started=NOW - support_survival.VISIT_MAX_TICKS,
            arrived=NOW - support_survival.VISIT_TIMEOUT_TICKS,
        )
        assert resupply_visit._over(scout, visit, NOW) == "satisfied"

    def test_satisfied_draws_no_rng(self) -> None:
        scout, visit = self._scout(SAT_LIVES, SAT_SHOTS)
        random.seed(5)
        before = random.getstate()
        resupply_visit.satisfied(scout, visit)
        assert random.getstate() == before


class TestTheVisitEnds:
    """Each exit ends the visit with exactly one cooldown set."""

    def test_full(self, ends: list[str]) -> None:
        # CAL-04b: a full pack is Satisfied, so "full" is no longer produced.
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(scout)
        scout.final_lives, scout.final_shots = scout.max_lives, scout.max_shots
        assert _step(scout, everyone, ctx) is None
        assert ends == ["satisfied"]
        _ended_once_at(scout, everyone, ctx)

    def test_visit_timeout_ticks_after_arrival(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(
            scout, phase="at", arrived=NOW - support_survival.VISIT_TIMEOUT_TICKS
        )
        assert _step(scout, everyone, ctx) is None
        _ended_once_at(scout, everyone, ctx)

    def test_not_one_tick_before_the_timeout(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        arrived = NOW - support_survival.VISIT_TIMEOUT_TICKS + 1
        _on_a_visit(scout, phase="at", arrived=arrived)
        _step(scout, everyone, ctx)
        assert scout.visit is not None

    def test_visit_max_ticks_after_the_start(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(scout, started=NOW - support_survival.VISIT_MAX_TICKS)
        assert _step(scout, everyone, ctx) is None
        _ended_once_at(scout, everyone, ctx)

    def test_not_one_tick_before_the_max(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(scout, started=NOW - support_survival.VISIT_MAX_TICKS + 1)
        _step(scout, everyone, ctx)
        assert scout.visit is not None

    def test_the_player_downed_mid_visit_goes_on(self) -> None:
        # Departure on #588: a Down does not end a visit (the R7 prototype; the
        # spec's Down exit took Tags to 395.8, out of range). It walks on Downed.
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(scout)
        record_down(scout, NOW, None)
        assert scout.visit is not None and scout.visit_next_tick == 0
        assert _step(scout, everyone, ctx, NOW + 1) == "move"
        assert scout.visit is not None and scout.visit.phase == "go"

    def test_the_player_eliminated_mid_visit(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(scout)
        scout.final_lives = 0
        record_down(scout, NOW, None)
        _ended_once_at(scout, everyone, ctx)

    def test_a_support_eliminated_mid_visit_goes_on_with_the_one_left(self) -> None:
        scout, medic, _, everyone, ctx = _scene()
        _on_a_visit(scout)
        medic.final_lives = 0
        assert _step(scout, everyone, ctx) == "move"
        assert scout.visit is not None
        assert scout.visit_next_tick == 0

    def test_both_supports_eliminated(self) -> None:
        scout, medic, ammo, everyone, ctx = _scene()
        _on_a_visit(scout)
        medic.final_lives = ammo.final_lives = 0
        assert _step(scout, everyone, ctx) is None
        _ended_once_at(scout, everyone, ctx)

    def test_the_round_ending(self) -> None:
        scout, medic, _, everyone, ctx = _scene()
        _on_a_visit(scout)
        resupply_visit.end_open_visits(everyone, NOW)
        assert medic.visit_next_tick == 0  # no visit, no cooldown
        _ended_once_at(scout, everyone, ctx)


@pytest.fixture
def ends(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every reason passed to ``resupply_visit.end_visit``, which still runs."""
    reasons: list[str] = []
    real = resupply_visit.end_visit

    def spy(player: Any, tick: float, reason: resupply_visit.VisitEnd) -> None:
        reasons.append(reason)
        real(player, tick, reason)

    monkeypatch.setattr(resupply_visit, "end_visit", spy)
    return reasons


class TestVisitEndReasons:
    """CAL-04a (#599): every caller names why the visit ends."""

    def test_the_reasons_are_a_fixed_set(self) -> None:
        # "satisfied" is in the set before anything produces it (CAL-04b), so the
        # probe's table has a fixed column set.
        assert typing.get_args(resupply_visit.VisitEnd) == (
            "full",
            "satisfied",
            "timeout",
            "max",
            "no_support",
            "eliminated",
            "round_end",
        )

    def test_satisfied(self, ends: list[str]) -> None:
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(scout)
        scout.final_lives, scout.final_shots = SAT_LIVES, SAT_SHOTS
        _step(scout, everyone, ctx)
        assert ends == ["satisfied"]

    def test_timeout(self, ends: list[str]) -> None:
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(
            scout, phase="at", arrived=NOW - support_survival.VISIT_TIMEOUT_TICKS
        )
        _step(scout, everyone, ctx)
        assert ends == ["timeout"]

    def test_max(self, ends: list[str]) -> None:
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(scout, started=NOW - support_survival.VISIT_MAX_TICKS)
        _step(scout, everyone, ctx)
        assert ends == ["max"]

    def test_no_support(self, ends: list[str]) -> None:
        scout, medic, ammo, everyone, ctx = _scene()
        _on_a_visit(scout)
        medic.final_lives = ammo.final_lives = 0
        _step(scout, everyone, ctx)
        assert ends == ["no_support"]

    def test_eliminated_through_record_down(self, ends: list[str]) -> None:
        scout, _, _, _, _ = _scene()
        _on_a_visit(scout)
        scout.final_lives = 0
        record_down(scout, NOW, None)
        assert ends == ["eliminated"]

    def test_round_end_through_end_open_visits(self, ends: list[str]) -> None:
        scout, _, _, everyone, _ = _scene()
        _on_a_visit(scout)
        resupply_visit.end_open_visits(everyone, NOW)
        assert ends == ["round_end"]

    def test_satisfied_comes_before_timeout_and_max(self) -> None:
        # CAL-04b: a full pack is Satisfied; "full" stays in the Literal unproduced.
        scout, _, _, _, _ = _scene()
        scout.final_lives, scout.final_shots = scout.max_lives, scout.max_shots
        visit = Visit(
            phase="at",
            started=NOW - support_survival.VISIT_MAX_TICKS,
            arrived=NOW - support_survival.VISIT_TIMEOUT_TICKS,
        )
        assert resupply_visit._over(scout, visit, NOW) == "satisfied"
        scout.final_lives = 1
        assert resupply_visit._over(scout, visit, NOW) == "timeout"
        assert resupply_visit._over(scout, replace(visit, arrived=None), NOW) == "max"
        assert resupply_visit._over(scout, Visit(phase="go", started=NOW), NOW) is None


@pytest.mark.django_db
def test_a_round_ends_with_no_visit_open(monkeypatch: pytest.MonkeyPatch) -> None:
    from matches.simulation import BatchSimulator
    from matches.tests.test_modes import _rosters, _syracuse

    # A seeded short round that stops while visits run.
    red, blue = _rosters("VisitEnd")
    monkeypatch.setattr(BatchSimulator, "ROUND_TICKS", 200)
    random.seed(4)
    _, red_players, blue_players = BatchSimulator()._simulate_round(
        red, blue, movement_ctx=_syracuse()
    )
    players = red_players + blue_players
    assert all(p.visit is None for p in players)
    cooldown = 200 + support_survival.VISIT_COOLDOWN_TICKS
    assert any(p.visit_next_tick == cooldown for p in players), "none was open"


class TestThePhases:
    def test_wait_while_a_support_is_not_active_then_go(self) -> None:
        scout, _, ammo, everyone, ctx = _scene()
        ammo.last_downed_time = NOW - 1
        assert _step(scout, everyone, ctx) == "hide"
        assert scout.visit is not None and scout.visit.phase == "wait"
        ammo.last_downed_time = None
        assert _step(scout, everyone, ctx, NOW + 1) == "move"
        assert scout.visit.phase == "go"

    def test_at_in_sight_of_both_active_supports_requests(self) -> None:
        scout, _, _, everyone, ctx = _scene(_sight((HERE, MEDIC_AT), (HERE, AMMO_AT)))
        assert _step(scout, everyone, ctx) == "request"
        assert scout.visit is not None
        assert (scout.visit.phase, scout.visit.arrived) == ("at", NOW)
        assert scout.visit.goal == HERE

    def test_at_holds_hidden_while_a_seeing_support_is_not_active(self) -> None:
        scout, _, ammo, everyone, ctx = _scene(
            _sight((HERE, MEDIC_AT), (HERE, AMMO_AT))
        )
        ammo.last_downed_time = NOW - 1
        assert _step(scout, everyone, ctx) == "hide"
        assert scout.visit is not None and scout.visit.phase == "at"

    def _one_sees(self, arrived_ago: int) -> tuple[Any, PlayerState]:
        scout, _, _, everyone, ctx = _scene(_sight((HERE, MEDIC_AT)))
        _on_a_visit(scout, phase="at", arrived=NOW - arrived_ago)
        return _step(scout, everyone, ctx), scout

    def test_a_single_at_exactly_visit_wait_ticks_after_arrival(self) -> None:
        action, _ = self._one_sees(support_survival.VISIT_WAIT_TICKS)
        assert action == "request"

    def test_no_single_one_tick_before(self) -> None:
        # Spec rule 6 ``at``: otherwise it holds, hidden (also short of a support).
        action, scout = self._one_sees(support_survival.VISIT_WAIT_TICKS - 1)
        assert action == "hide"
        assert scout.visit is not None and scout.visit.phase == "at"

    def test_go_walks_to_the_nearest_cell_seeing_both_supports(self) -> None:
        both = {"5,5", "9,9"}
        sight = {"10,10": both | {"2,2"}, "10,12": both}
        scout, _, _, everyone, ctx = _scene(sight)
        assert _step(scout, everyone, ctx) == "move"
        assert scout.visit is not None and scout.visit.goal == (5, 5)

    def test_go_reads_the_remembered_cells_first(self) -> None:
        sight = {"10,10": {"9,9"}, "10,12": {"9,9"}, "4,4": {"3,3"}, "4,6": {"3,3"}}
        scout, _, _, everyone, ctx = _scene(sight)
        for tag, cell in (("red_medic", (4, 4)), ("red_ammo", (4, 6))):
            scout.player_memory[tag] = {"cell": cell, "timestamp": NOW, "role": "x"}
        _step(scout, everyone, ctx)
        assert scout.visit is not None and scout.visit.goal == (3, 3)

    def test_go_falls_back_to_a_cell_seeing_the_medic(self) -> None:
        scout, _, _, everyone, ctx = _scene({"10,10": {"7,7"}, "10,12": {"8,8"}})
        _step(scout, everyone, ctx)
        assert scout.visit is not None and scout.visit.goal == (7, 7)

    def test_go_walks_to_the_backline_cell_first(self) -> None:
        scout, _, _, everyone, ctx = _scene({"10,10": {"5,5"}, "10,12": {"5,5"}})
        scout.backline_cell = (3, 3)
        _step(scout, everyone, ctx)
        assert scout.visit is not None and scout.visit.goal == (3, 3)
        scout.cell_row, scout.cell_col = 3, 3
        _step(scout, everyone, ctx, NOW + 1)
        assert scout.visit.backline_done
        assert scout.visit.goal == (5, 5)

    def test_unstick_on_the_goal_with_no_support_in_sight(self) -> None:
        # The remembered cells are seen from HERE; the supports moved away.
        sight = {"4,4": {"0,0"}, "4,6": {"0,0"}, "10,10": {"6,6"}, "10,12": {"6,6"}}
        scout, _, _, everyone, ctx = _scene(sight)
        scout.backline_cell = HERE
        for tag, cell in (("red_medic", (4, 4)), ("red_ammo", (4, 6))):
            scout.player_memory[tag] = {"cell": cell, "timestamp": NOW, "role": "x"}
        _step(scout, everyone, ctx)
        assert "red_medic" not in scout.player_memory
        assert "red_ammo" not in scout.player_memory
        assert scout.backline_cell is None
        assert scout.visit is not None and scout.visit.goal == (6, 6)

    def test_unstick_at_the_supports_forgets_only_the_unseen_one(self) -> None:
        # The Medic sees the Scout; the Ammo's remembered cell is seen from HERE.
        sight = _sight((HERE, MEDIC_AT))
        sight.setdefault("4,6", set()).add("0,0")
        sight["10,12"] = {"10,11"}
        sight["10,10"].add("10,11")
        scout, _, _, everyone, ctx = _scene(sight)
        scout.backline_cell = (3, 3)
        scout.player_memory["red_ammo"] = {
            "cell": (4, 6),
            "timestamp": NOW,
            "role": "x",
        }
        scout.player_memory["red_medic"] = {
            "cell": MEDIC_AT,
            "timestamp": NOW,
            "role": "x",
        }
        _step(scout, everyone, ctx)
        assert "red_ammo" not in scout.player_memory
        assert "red_medic" in scout.player_memory
        assert scout.backline_cell is None  # spec rule 6: and its backline_cell
        assert scout.visit is not None and scout.visit.goal == (10, 11)

    def test_the_heavy_s_go_starts_at_the_cell_seeing_both(self) -> None:
        heavy, _, _, everyone, ctx = _scene(
            {"10,10": {"5,5"}, "10,12": {"5,5"}}, role="heavy"
        )
        heavy.final_lives = math.floor(heavy.max_lives / 3)
        _step(heavy, everyone, ctx)
        assert heavy.visit is not None and heavy.visit.goal == (5, 5)

    def test_a_visit_step_draws_no_rng(self) -> None:
        scout, _, _, everyone, ctx = _scene({"10,10": {"5,5"}, "10,12": {"5,5"}})
        random.seed(5)
        before = random.getstate()
        _step(scout, everyone, ctx)
        assert random.getstate() == before


def _rolled(monkeypatch: pytest.MonkeyPatch, roll: str) -> list[dict[str, int]]:
    """Every Action roll returns *roll*; the weights each one saw are kept."""
    seen: list[dict[str, int]] = []
    real = random.choices

    def choices(population: list, weights: Any = None, *a: Any, **k: Any) -> list:
        if population is combat._CHOICES:
            seen.append(dict(zip(population, weights)))
            return [roll]
        return real(population, weights, *a, **k)

    monkeypatch.setattr(combat.random, "choices", choices)
    return seen


def _plan(player: PlayerState, everyone: list, ctx: MapContext | None) -> list:
    return combat.plan_action(player, everyone, NOW, ctx, time_domain="ticks")


def _types(plans: list) -> list[str]:
    return [p["type"] for p in plans]


class TestTheVisitInPlanAction:
    def test_go_makes_a_roll_only_move(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scout, _, _, everyone, ctx = _scene()
        _rolled(monkeypatch, "capture_base")
        assert _types(_plan(scout, everyone, ctx)) == ["only_move"]
        assert scout.last_chosen_action == "only_move"

    def test_a_tag_roll_stands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scout, _, _, everyone, ctx = _scene()
        _rolled(monkeypatch, "tag_player")
        _plan(scout, everyone, ctx)
        assert scout.last_chosen_action == "tag_player"

    def test_wait_makes_a_roll_hide(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scout, _, ammo, everyone, ctx = _scene()
        ammo.last_downed_time = NOW - 1
        _rolled(monkeypatch, "only_move")
        assert _types(_plan(scout, everyone, ctx)) == ["hide"]

    def test_at_requests_a_resupply(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scout, _, _, everyone, ctx = _scene(_sight((HERE, MEDIC_AT), (HERE, AMMO_AT)))
        _rolled(monkeypatch, "hide")
        assert _types(_plan(scout, everyone, ctx)) == ["request_resupply"]

    def test_no_request_weight_outside_a_visit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scout, _, _, everyone, ctx = _scene()
        scout.final_lives = scout.max_lives - 1  # a need, but no visit
        seen = _rolled(monkeypatch, "hide")
        _plan(scout, everyone, ctx)
        assert scout.visit is None
        assert seen[0]["request_resupply"] == 0

    def test_a_map_less_round_keeps_the_request_weight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scout, _, _, everyone, _ = _scene()
        scout.final_lives = 1
        seen = _rolled(monkeypatch, "hide")
        _plan(scout, everyone, None)
        assert seen[0]["request_resupply"] > 0

    def test_a_support_keeps_its_request_weight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, medic, ammo, everyone, ctx = _scene()
        ammo.final_lives = 5
        seen = _rolled(monkeypatch, "hide")
        _plan(ammo, everyone, ctx)
        assert seen[0]["request_resupply"] > 0


class TestTheVisitGoal:
    def _goal(self, player: PlayerState, everyone: list, ctx: MapContext) -> Any:
        return pathfinding.choose_goal_cell(
            player, everyone, ctx.spawn_cells, ctx, "", NOW, "ticks"
        )

    def test_choose_goal_cell_returns_the_visit_goal(self) -> None:
        scout, _, _, everyone, ctx = _scene()
        _on_a_visit(scout, goal=(7, 7))
        scout._committed_goal = ((1, 1), False, NOW + 99)
        assert self._goal(scout, everyone, ctx) == (7, 7)
        assert scout._committed_goal is None

    @pytest.mark.parametrize("low", ["final_lives", "final_shots"])
    def test_no_step_1_walk_to_a_support_s_true_cell(self, low: str) -> None:
        # Decision 51: a visit replaces it, so with no visit there is no walk.
        scout, _, _, everyone, ctx = _scene()
        scout.final_lives = scout.max_lives
        setattr(scout, low, 1)
        assert self._goal(scout, everyone, ctx) not in (MEDIC_AT, AMMO_AT)


class TestPartnerFollows:
    """Decision 43: a single to a visiting player is followed in the same tick by
    the other support's, when that support is available and sees the player."""

    def _resupply(self, sight: dict[str, set[str]], visiting: bool) -> PlayerState:
        scout, medic, ammo, everyone, ctx = _scene(sight)
        scout.final_shots = 10
        if visiting:
            _on_a_visit(scout, phase="at", arrived=NOW - 30)
        round_ctx = RoundContext(movement_ctx=ctx, all_alive=everyone)
        combat.attempt_resupply(medic, scout, NOW, ctx=round_ctx)
        return scout

    def test_the_other_kind_lands_in_the_same_tick(self) -> None:
        scout = self._resupply(_sight((HERE, MEDIC_AT), (HERE, AMMO_AT)), True)
        assert scout.final_lives > SHARE_LIVES
        assert scout.final_shots > 10
        assert scout.last_single is not None and scout.last_single.paired

    def test_not_off_a_visit(self) -> None:
        scout = self._resupply(_sight((HERE, MEDIC_AT), (HERE, AMMO_AT)), False)
        assert scout.final_shots == 10

    def test_not_when_the_partner_does_not_see_the_player(self) -> None:
        scout = self._resupply(_sight((HERE, MEDIC_AT)), True)
        assert scout.final_lives > SHARE_LIVES
        assert scout.final_shots == 10


class TestDoubleCounting:
    """CAL-04b (#600): a window Resupply (the second of a pair) to a player on a
    visit adds one to ``visit.doubles``, whichever path delivered it."""

    def _window(self, visiting: bool) -> tuple[PlayerState, PlayerState, Any]:
        # The Medic's single landed this tick, so the Ammo's lands in the window.
        scout, medic, ammo, everyone, ctx = _scene()
        scout.final_shots = 10
        scout.last_downed_time = NOW
        scout.last_single = support_survival.LastSingle(
            kind="lives", tick=NOW, supporter=medic.tag_id_key
        )
        if visiting:
            _on_a_visit(scout, phase="at", arrived=NOW - 30)
        round_ctx = RoundContext(movement_ctx=ctx, all_alive=everyone)
        return scout, ammo, round_ctx

    def test_a_window_resupply_to_a_visitor_adds_one(self) -> None:
        scout, ammo, round_ctx = self._window(visiting=True)
        combat.attempt_resupply(ammo, scout, NOW, ctx=round_ctx)
        assert scout.final_shots > 10  # it landed
        assert scout.visit is not None and scout.visit.doubles == 1

    def test_a_single_adds_none(self) -> None:
        # Nobody sees the Scout, so the partner does not follow.
        scout, medic, _, everyone, ctx = _scene()
        _on_a_visit(scout, phase="at", arrived=NOW - 30)
        round_ctx = RoundContext(movement_ctx=ctx, all_alive=everyone)
        combat.attempt_resupply(medic, scout, NOW, ctx=round_ctx)
        assert scout.final_lives > SHARE_LIVES  # it landed
        assert scout.visit is not None and scout.visit.doubles == 0

    def test_a_window_resupply_off_a_visit_changes_nothing(self) -> None:
        scout, ammo, round_ctx = self._window(visiting=False)
        combat.attempt_resupply(ammo, scout, NOW, ctx=round_ctx)
        assert scout.final_shots > 10
        assert scout.visit is None

    def test_partner_follows_counts_exactly_one(self) -> None:
        scout, medic, _, everyone, ctx = _scene(
            _sight((HERE, MEDIC_AT), (HERE, AMMO_AT))
        )
        scout.final_shots = 10
        _on_a_visit(scout, phase="at", arrived=NOW - 30)
        round_ctx = RoundContext(movement_ctx=ctx, all_alive=everyone)
        combat.attempt_resupply(medic, scout, NOW, ctx=round_ctx)
        assert scout.final_shots > 10  # the partner followed
        assert scout.visit is not None and scout.visit.doubles == 1


class TestBackline:
    """Rule 10: a Commander or Scout keeps its Medic's cell when it sees the
    Medic, else the seen backline teammate's nearest their centroid."""

    def _team(self, role: str = "scout") -> tuple[PlayerState, list]:
        player = _player(role, cell_row=0, cell_col=0)
        heavy = _player("heavy", cell_row=2, cell_col=8)
        ammo = _player("ammo", cell_row=6, cell_col=2)
        medic = _player("medic", cell_row=8, cell_col=8)
        return player, [player, heavy, ammo, medic]

    @pytest.mark.parametrize("role", ["commander", "scout"])
    def test_its_medic_s_cell_when_it_sees_the_medic(self, role: str) -> None:
        player, everyone = self._team(role)
        # The Medic at (11, 11) is not the one nearest the centroid (6.3, 7).
        everyone[3].cell_row, everyone[3].cell_col = 11, 11
        ctx = _grid_map(sight={"0,0": {"11,11", "2,8", "6,2"}})
        resupply_visit.update_backline(player, everyone, ctx)
        assert player.backline_cell == (11, 11)

    def test_else_the_seen_teammate_nearest_their_centroid(self) -> None:
        player, everyone = self._team()
        everyone.append(_player("heavy", cell_row=4, cell_col=6))
        everyone[-1].tag_id = "red_heavy_2"
        # Centroid of (2, 8), (6, 2), (4, 6) is (4, 5.33): (4, 6) is nearest.
        ctx = _grid_map(sight={"0,0": {"2,8", "6,2", "4,6"}})
        resupply_visit.update_backline(player, everyone, ctx)
        assert player.backline_cell == (4, 6)

    def test_seeing_none_it_keeps_the_old_cell(self) -> None:
        player, everyone = self._team()
        player.backline_cell = (1, 1)
        resupply_visit.update_backline(player, everyone, _grid_map())
        assert player.backline_cell == (1, 1)

    def test_a_heavy_keeps_none(self) -> None:
        heavy, everyone = self._team("heavy")
        resupply_visit.update_backline(
            heavy, everyone, _grid_map(sight={"0,0": {"8,8"}})
        )
        assert heavy.backline_cell is None

    def test_plan_action_updates_it_and_draws_no_rng(self) -> None:
        # Standing Holds: plan_action returns before any Action roll.
        player, everyone = self._team("commander")
        for who in everyone:
            who.hold_until = 10**6
        medic, ammo = everyone[3], everyone[2]
        ctx = _grid_map(sight={"0,0": {"8,8"}}, size=16)
        random.seed(9)
        before = random.getstate()
        for who in (player, medic, ammo):
            combat.plan_action(who, everyone, NOW, ctx, time_domain="ticks")
        assert random.getstate() == before
        assert player.backline_cell == (8, 8)
        assert ammo.player_memory["red_medic"]["cell"] == (8, 8)


class TestCallOut:
    """Rule 7: at ``CALLOUT_LIVES`` or fewer with no own support in sight a player
    hides and calls its supports over; a support walks to the freshest call-out."""

    def _caller(self, lives: int, sight: dict[str, set[str]] | None = None) -> tuple:
        scout, medic, ammo, everyone, ctx = _scene(sight)
        scout.final_lives = lives
        return scout, medic, ammo, everyone, ctx

    def test_it_fires_at_callout_lives(self) -> None:
        scout, medic, ammo, everyone, ctx = self._caller(support_survival.CALLOUT_LIVES)
        assert resupply_visit.call_out(scout, everyone, ctx, NOW)
        call = resupply_visit.Callout(caller="red_scout", cell=HERE, tick=NOW)
        assert medic.support_callouts == [call]
        assert ammo.support_callouts == [call]

    def test_not_one_life_above(self) -> None:
        scout, medic, _, everyone, ctx = self._caller(
            support_survival.CALLOUT_LIVES + 1
        )
        assert not resupply_visit.call_out(scout, everyone, ctx, NOW)
        assert medic.support_callouts == []

    def test_not_with_a_support_in_sight(self) -> None:
        scout, _, _, everyone, ctx = self._caller(1, _sight((HERE, AMMO_AT)))
        assert not resupply_visit.call_out(scout, everyone, ctx, NOW)

    def test_a_non_tag_roll_becomes_hide(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # At 1 life a visit starts too, and its go phase would move the Scout.
        scout, _, _, everyone, ctx = self._caller(1)
        _rolled(monkeypatch, "only_move")
        assert _types(_plan(scout, everyone, ctx)) == ["hide"]
        assert scout.visit is not None and scout.visit.phase == "go"

    def test_the_freshest_call_out_comes_first(self) -> None:
        scout, medic, _, everyone, ctx = self._caller(1)
        heavy = _player("heavy", cell_row=3, cell_col=3, final_lives=2)
        everyone.append(heavy)
        resupply_visit.call_out(heavy, everyone, ctx, NOW - 5)
        resupply_visit.call_out(scout, everyone, ctx, NOW)
        assert resupply_visit.freshest_callout(medic, everyone, NOW) == HERE
        scout.final_lives = support_survival.CALLOUT_LIVES + 1  # no longer critical
        assert resupply_visit.freshest_callout(medic, everyone, NOW) == (3, 3)

    def test_a_call_out_goes_stale(self) -> None:
        scout, medic, _, everyone, ctx = self._caller(1)
        resupply_visit.call_out(scout, everyone, ctx, NOW)
        tick = NOW + support_survival.CALLOUT_TICKS
        assert resupply_visit.freshest_callout(medic, everyone, tick) == HERE
        assert resupply_visit.freshest_callout(medic, everyone, tick + 1) is None

    def _walk(self, medic: PlayerState, everyone: list, ctx: MapContext) -> Any:
        return pathfinding._goal_from_action(
            medic, everyone, "blue", *MEDIC_AT, "resupply_ally", ctx, NOW, "ticks"
        )

    def test_the_medic_walks_to_the_call_out(self) -> None:
        scout, medic, _, everyone, ctx = self._caller(1, {"0,0": {"1,1"}})
        resupply_visit.call_out(scout, everyone, ctx, NOW)
        assert self._walk(medic, everyone, ctx) == (1, 1)

    def test_no_walk_to_a_call_out_a_remembered_enemy_sees(self) -> None:
        scout, medic, _, everyone, ctx = self._caller(
            1, {"0,0": {"1,1"}, "5,5": {"0,0"}}
        )
        resupply_visit.call_out(scout, everyone, ctx, NOW)
        medic.player_memory["blue_heavy"] = {
            "cell": (5, 5),
            "timestamp": NOW,
            "role": "heavy",
        }
        everyone.append(_player("heavy", "blue", cell_row=5, cell_col=5))
        assert self._walk(medic, everyone, ctx) is None
