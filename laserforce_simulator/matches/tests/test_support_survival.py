"""Support-survival prototypes (``sim_helpers.support_survival``, 2026-11-06).

Five switches, each off by default and each tested on and off at its seam:
target choice, the Defense candidates, the Medic's and Ammo's hide gate and
resupply walk, and the call-out roll with the support staleness window.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable

import pytest

from matches.sim_helpers import goal_scoring, modes, pathfinding, support_survival
from matches.sim_helpers.map_context import MapContext, comms_reach
from matches.sim_helpers.mechanics import _priority_targets, choose_tag_target
from matches.sim_helpers.modes import hide_allowed, mode_goal
from matches.sim_helpers.pathfinding import _goal_from_action
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.support_survival import Visit
from matches.simulation.round_loop import _broadcast_communication


def _player(role: str, team: str = "red", **fields: Any) -> PlayerState:
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


def _grid_map(
    *,
    sight: dict[str, set[str]] | None = None,
    counts: dict[str, int] | None = None,
    size: int = 12,
) -> MapContext:
    """An open *size* x *size* floor; 1 ft a square unless a test says otherwise."""
    adj = {
        (r, c): [
            (r + dr, c + dc)
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
            if 0 <= r + dr < size and 0 <= c + dc < size
        ]
        for r in range(size)
        for c in range(size)
    }
    ctx = MapContext(
        adj=adj,
        spawn_cells={"red": (0, 0), "blue": (size - 1, size - 1)},
        zone_data=None,
        sight_data={k: frozenset(v) for k, v in (sight or {}).items()},
        base_sight_data={},
        cell_los_counts=counts or {},
        high_los_cells=[(1, 1)],
        strong_spots=[],
        wall_meta={},
        team_spawn_pools={},
    )
    ctx.feet_per_square = 1.0
    return ctx


def _all_visible(actor: Any, candidates: list, ctx: Any) -> list:
    return list(candidates)


def _draw_counts(player: PlayerState, enemies: list, tick: int, n: int = 400) -> dict:
    counts: dict[str, int] = {}
    for _ in range(n):
        target = choose_tag_target(
            player, [player, *enemies], tick, None, los_filter=_all_visible
        )
        counts[target.role] = counts.get(target.role, 0) + 1
    return counts


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    def _on(name: str) -> None:
        monkeypatch.setattr(support_survival, name, True)

    return _on


@pytest.fixture
def off(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Switches 1-4 are on by default (the THMA sweep result), so a test of the
    engine as it was names the switch it turns off."""

    def _off(name: str) -> None:
        monkeypatch.setattr(support_survival, name, False)

    return _off


class TestTargetPriority:
    """Switch 1: ``choose_tag_target``."""

    def test_off_a_visible_medic_is_shot_almost_as_often_as_a_heavy(
        self, off: Callable[[str], None]
    ) -> None:
        off("TARGET_PRIORITY")
        random.seed(1)
        scout = _player("scout", "blue", cell_row=0, cell_col=0)
        heavy, medic = _player("heavy"), _player("medic")
        counts = _draw_counts(scout, [heavy, medic], 100)
        # 11 : 15 as built.
        assert counts["medic"] > 0.3 * counts["heavy"]

    def test_on_a_medic_is_never_the_target_while_another_enemy_is_active(
        self, on: Callable[[str], None]
    ) -> None:
        on("TARGET_PRIORITY")
        random.seed(1)
        scout = _player("scout", "blue", cell_row=0, cell_col=0)
        counts = _draw_counts(scout, [_player("heavy"), _player("medic")], 100)
        assert counts == {"heavy": 400}

    def test_on_the_medic_is_preferred_once_the_others_are_downed(
        self, on: Callable[[str], None]
    ) -> None:
        on("TARGET_PRIORITY")
        random.seed(1)
        scout = _player("scout", "blue", cell_row=0, cell_col=0)
        # Downed 10 ticks ago: in the Reset window (taggable, not Active).
        heavy = _player("heavy", last_downed_time=90)
        medic = _player("medic")
        scout.last_tagged_id = None
        counts = _draw_counts(scout, [heavy, medic], 100)
        assert counts["medic"] > 2 * counts["heavy"]

    # MECH-23 (#606) decision 6: replaces
    # test_on_a_hiding_enemy_is_passed_over_for_one_in_the_open. The pass-over
    # of hiders is gone; a hider in sight is as good a target as anyone.
    def test_priority_targets_keeps_a_hider_beside_a_non_hider(self) -> None:
        hidden = _player("heavy", is_hiding=True)
        hidden.tag_id = "red_heavy_2"
        open_heavy = _player("heavy")
        assert _priority_targets([hidden, open_heavy], 100) == [hidden, open_heavy]

    def test_on_a_hiding_enemy_is_drawn_beside_one_in_the_open(
        self, on: Callable[[str], None]
    ) -> None:
        on("TARGET_PRIORITY")
        random.seed(1)
        scout = _player("scout", "blue", cell_row=0, cell_col=0)
        hidden = _player("heavy", is_hiding=True)
        hidden.tag_id = "red_heavy_2"
        open_heavy = _player("heavy")
        picks = {
            choose_tag_target(
                scout,
                [scout, hidden, open_heavy],
                100,
                None,
                los_filter=_all_visible,
            ).tag_id
            for _ in range(100)
        }
        assert picks == {hidden.tag_id, open_heavy.tag_id}

    # Still true after MECH-23: a lone hider in sight is shot.
    def test_on_a_hiding_enemy_is_still_shot_when_alone(
        self, on: Callable[[str], None]
    ) -> None:
        on("TARGET_PRIORITY")
        scout = _player("scout", "blue", cell_row=0, cell_col=0)
        hidden = _player("heavy", is_hiding=True)
        assert (
            choose_tag_target(
                scout, [scout, hidden], 100, None, los_filter=_all_visible
            )
            is hidden
        )

    def test_on_the_active_bonus_multiplies_the_role_weight(
        self, on: Callable[[str], None]
    ) -> None:
        on("TARGET_PRIORITY")
        random.seed(1)
        commander = _player("commander", "blue", cell_row=0, cell_col=0)
        heavy, scout = _player("heavy"), _player("scout")
        counts = _draw_counts(commander, [heavy, scout], 100)
        # 30 : 18 rather than 15 : 13.
        assert 1.3 < counts["heavy"] / counts["scout"] < 2.2


def _defense_scene(role: str) -> tuple[PlayerState, list[PlayerState], MapContext]:
    """A support at (0, 0) remembers its Heavy at (5, 0); (5, 2) sees that cell."""
    support = _player(role, cell_row=0, cell_col=0, teamwork=0)
    heavy = _player("heavy", cell_row=9, cell_col=9)
    support.player_memory["red_heavy"] = {
        "cell": (5, 0),
        "timestamp": 100,
        "role": "heavy",
    }
    ctx = _grid_map(sight={"5,0": {"5,2"}}, counts={"5,2": 9, "5,3": 5, "5,1": 1})
    return support, [support, heavy], ctx


@pytest.fixture
def draws(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list, Callable]]:
    calls: list[tuple[list, Callable]] = []
    real = modes.draw_goal

    def spy(player, candidates, value_of, *args, **kwargs):
        calls.append((list(candidates), value_of))
        return real(player, candidates, value_of, *args, **kwargs)

    monkeypatch.setattr(modes, "draw_goal", spy)
    return calls


class TestHiddenSupportCells:
    """Switch 2: the Defense candidates and value."""

    def test_off_the_candidates_are_the_cells_that_see_the_heavy(
        self, off: Callable[[str], None], draws: list
    ) -> None:
        off("HIDDEN_SUPPORT_CELLS")
        ammo, everyone, ctx = _defense_scene("ammo")
        mode_goal(ammo, everyone, "blue", ctx, 100)
        assert draws[-1][0] == [(5, 2)]

    def test_on_the_candidates_reach_two_steps_from_a_seeing_cell(
        self, on: Callable[[str], None], draws: list
    ) -> None:
        # The Ammo: the Medic's reach is MEDIC_BACK_REACH (CAL-03 rule 5).
        on("HIDDEN_SUPPORT_CELLS")
        ammo, everyone, ctx = _defense_scene("ammo")
        mode_goal(ammo, everyone, "blue", ctx, 100)
        assert draws[-1][0] == sorted(
            {
                (5, 2),
                (4, 2),
                (6, 2),
                (5, 1),
                (5, 3),
                (3, 2),
                (7, 2),
                (5, 0),
                (5, 4),
                (4, 1),
                (4, 3),
                (6, 1),
                (6, 3),
            }
        )

    def test_on_the_ammo_values_a_hidden_cell(
        self, on: Callable[[str], None], draws: list
    ) -> None:
        # The Medic's value is CAL-03 rule 5's (TestMedicBack).
        on("HIDDEN_SUPPORT_CELLS")
        ammo, everyone, ctx = _defense_scene("ammo")
        mode_goal(ammo, everyone, "blue", ctx, 100)
        value_of = draws[-1][1]
        # Ranks: 5,1 -> 0, 5,3 -> 0.5, 5,2 -> 1; teamwork 0 so no closeness term.
        assert value_of((5, 1)) == pytest.approx(0.7)
        assert value_of((5, 3)) == pytest.approx(0.35)
        assert value_of((5, 2)) == pytest.approx(0.0)

    def test_off_the_ammo_values_an_open_cell(
        self, off: Callable[[str], None], draws: list
    ) -> None:
        off("HIDDEN_SUPPORT_CELLS")
        ammo, everyone, ctx = _defense_scene("ammo")
        mode_goal(ammo, everyone, "blue", ctx, 100)
        assert draws[-1][1]((5, 2)) == pytest.approx(0.7)


def _needy_team(role: str) -> tuple[PlayerState, list[PlayerState], MapContext]:
    support = _player(role, cell_row=0, cell_col=0)
    ally = _player("scout", cell_row=3, cell_col=3)
    ally.final_lives = 1
    ally.final_shots = 1
    return support, [support, ally], _grid_map()


class TestMedicHoldsPosition:
    """Switch 3: the Medic's hide gate and resupply walk."""

    def test_off_a_needy_ally_forbids_the_hide(
        self, off: Callable[[str], None]
    ) -> None:
        off("MEDIC_HOLDS_POSITION")
        medic, everyone, ctx = _needy_team("medic")
        assert hide_allowed(medic, everyone, ctx, 100, "ticks") is False

    def test_on_the_medic_may_hide_whatever_its_allies_need(
        self, on: Callable[[str], None]
    ) -> None:
        on("MEDIC_HOLDS_POSITION")
        medic, everyone, ctx = _needy_team("medic")
        assert hide_allowed(medic, everyone, ctx, 100, "ticks") is True

    def test_on_the_ammo_gate_is_unchanged(
        self, on: Callable[[str], None], off: Callable[[str], None]
    ) -> None:
        on("MEDIC_HOLDS_POSITION")
        off("AMMO_HIDES_FREELY")
        ammo, everyone, ctx = _needy_team("ammo")
        assert hide_allowed(ammo, everyone, ctx, 100, "ticks") is False

    def test_off_resupply_walks_the_medic_toward_the_ally(
        self, off: Callable[[str], None]
    ) -> None:
        off("MEDIC_HOLDS_POSITION")
        medic, everyone, ctx = _needy_team("medic")
        ctx.sight_data = {"3,3": frozenset({"2,3"})}
        goal = _goal_from_action(
            medic, everyone, "blue", 0, 0, "resupply_ally", ctx, 100, "ticks"
        )
        assert goal == (2, 3)

    def test_on_resupply_sets_the_medic_no_walking_goal(
        self, on: Callable[[str], None]
    ) -> None:
        on("MEDIC_HOLDS_POSITION")
        medic, everyone, ctx = _needy_team("medic")
        ctx.sight_data = {"3,3": frozenset({"2,3"})}
        goal = _goal_from_action(
            medic, everyone, "blue", 0, 0, "resupply_ally", ctx, 100, "ticks"
        )
        assert goal is None


class TestAmmoHidesFreely:
    """Switch 4: the Ammo's hide gate and the hidden resupply walk."""

    def test_on_the_ammo_may_hide_whatever_its_allies_need(
        self, on: Callable[[str], None]
    ) -> None:
        on("AMMO_HIDES_FREELY")
        ammo, everyone, ctx = _needy_team("ammo")
        assert hide_allowed(ammo, everyone, ctx, 100, "ticks") is True

    def test_on_the_medic_gate_is_unchanged(
        self, on: Callable[[str], None], off: Callable[[str], None]
    ) -> None:
        on("AMMO_HIDES_FREELY")
        off("MEDIC_HOLDS_POSITION")
        medic, everyone, ctx = _needy_team("medic")
        assert hide_allowed(medic, everyone, ctx, 100, "ticks") is False

    @pytest.mark.parametrize("role", ["ammo", "medic"])
    def test_on_the_resupply_walk_prefers_the_hidden_cell(
        self,
        on: Callable[[str], None],
        off: Callable[[str], None],
        monkeypatch: pytest.MonkeyPatch,
        role: str,
    ) -> None:
        monkeypatch.setattr(goal_scoring.random, "random", lambda: 0.0)
        # Switch 3 would give the Medic no walk at all; this measures the walk's cell.
        off("MEDIC_HOLDS_POSITION")
        off("AMMO_HIDES_FREELY")
        support, everyone, ctx = _needy_team(role)
        # Two cells see the ally at the same walk; (2, 3) is open, (3, 2) hidden.
        ctx.sight_data = {"3,3": frozenset({"2,3", "3,2"})}
        ctx.cell_los_counts = {"2,3": 9, "3,2": 1}
        args = (support, everyone, "blue", 0, 0, "resupply_ally", ctx, 100, "ticks")
        assert _goal_from_action(*args) == (2, 3)
        on("AMMO_HIDES_FREELY")
        assert _goal_from_action(*args) == (3, 2)


class TestTeamworkCallouts:
    """Switch 5: the call-out roll and the support staleness window."""

    def _team(self, teamwork: int) -> tuple[PlayerState, PlayerState]:
        caller = _player(
            "scout", cell_row=0, cell_col=0, communication=100, teamwork=teamwork
        )
        caller.player_memory["blue_medic"] = {
            "cell": (5, 5),
            "timestamp": 100,
            "role": "medic",
        }
        ally = _player("heavy", cell_row=1, cell_col=1)
        return caller, ally

    def test_off_the_roll_is_communication_alone(
        self, off: Callable[[str], None]
    ) -> None:
        off("TEAMWORK_CALLOUTS")
        caller, ally = self._team(teamwork=0)
        _broadcast_communication(caller, [caller, ally], None, 100)
        assert ally.player_memory["blue_medic"]["cell"] == (5, 5)

    def test_on_no_teamwork_means_no_call_out(self, on: Callable[[str], None]) -> None:
        on("TEAMWORK_CALLOUTS")
        caller, ally = self._team(teamwork=0)
        _broadcast_communication(caller, [caller, ally], None, 100)
        assert "blue_medic" not in ally.player_memory

    def test_on_full_teamwork_calls_out(self, on: Callable[[str], None]) -> None:
        on("TEAMWORK_CALLOUTS")
        caller, ally = self._team(teamwork=100)
        _broadcast_communication(caller, [caller, ally], None, 100)
        assert ally.player_memory["blue_medic"]["cell"] == (5, 5)

    def test_on_the_chance_is_the_product(self, on: Callable[[str], None]) -> None:
        on("TEAMWORK_CALLOUTS")
        caller, _ = self._team(teamwork=40)
        assert support_survival.callout_chance(caller) == pytest.approx(40.0)

    def test_off_supports_stay_fresh_for_120_ticks(
        self, off: Callable[[str], None]
    ) -> None:
        off("TEAMWORK_CALLOUTS")
        assert support_survival.stale_table("ticks")["medic"] == 120

    def test_on_supports_go_stale_like_a_scout(self, on: Callable[[str], None]) -> None:
        on("TEAMWORK_CALLOUTS")
        table = support_survival.stale_table("ticks")
        assert (table["medic"], table["ammo"], table["heavy"]) == (30, 30, 120)
        assert support_survival.stale_table("seconds")["medic"] == 60

    def test_on_a_remembered_medic_is_forgotten_after_30_ticks(
        self, on: Callable[[str], None]
    ) -> None:
        on("TEAMWORK_CALLOUTS")
        scout = _player("scout", cell_row=0, cell_col=0)
        scout.player_memory["blue_medic"] = {
            "cell": (5, 5),
            "timestamp": 100,
            "role": "medic",
        }
        assert pathfinding._cell_from_memory(scout, "blue_medic", 130, "ticks") == (
            5,
            5,
        )
        assert pathfinding._cell_from_memory(scout, "blue_medic", 131, "ticks") is None
        assert modes._fresh_entry(scout, "blue_medic", 131, "ticks") is None


class TestDoubleWindowOpen:
    """CAL-03 rule 1 (#588): the other support may land the second kind while the
    receiver's last single is at most ``DOUBLE_WINDOW_TICKS`` old. One fixture per
    condition, each failing that condition alone."""

    def _receiver(self, **single: Any) -> PlayerState:
        # The Medic's single at tick 100 reset the Scout's pack (last_downed_time).
        record = {"kind": "lives", "tick": 100, "supporter": "red_medic", **single}
        return _player(
            "scout",
            last_downed_time=100,
            last_single=support_survival.LastSingle(**record),
        )

    def test_the_other_support_in_the_same_tick(self) -> None:
        ammo = _player("ammo")
        assert support_survival.double_window_open(self._receiver(), ammo, 100)

    def test_open_at_the_window_edge(self) -> None:
        edge = 100 + support_survival.DOUBLE_WINDOW_TICKS
        assert support_survival.double_window_open(
            self._receiver(), _player("ammo"), edge
        )

    def test_closed_one_tick_past_the_window(self) -> None:
        past = 100 + support_survival.DOUBLE_WINDOW_TICKS + 1
        assert not support_survival.double_window_open(
            self._receiver(), _player("ammo"), past
        )

    def test_closed_for_the_same_kind(self) -> None:
        # A second Medic owes lives, the kind already given.
        other_medic = _player("medic", tag_id="red_medic_2")
        assert not support_survival.double_window_open(
            self._receiver(), other_medic, 100
        )

    def test_closed_for_the_same_supporter(self) -> None:
        ammo = _player("ammo")
        receiver = self._receiver(supporter=ammo.tag_id_key)
        assert not support_survival.double_window_open(receiver, ammo, 100)

    def test_closed_once_the_pair_is_used(self) -> None:
        receiver = self._receiver(paired=True)
        assert not support_survival.double_window_open(receiver, _player("ammo"), 100)

    def test_closed_for_a_role_that_gives_nothing(self) -> None:
        assert not support_survival.double_window_open(
            self._receiver(), _player("commander"), 100
        )

    def test_closed_after_a_down_since_the_single(self) -> None:
        receiver = self._receiver()
        receiver.last_downed_time = 101
        assert not support_survival.double_window_open(receiver, _player("ammo"), 101)

    def test_closed_with_no_single(self) -> None:
        receiver = _player("scout", last_downed_time=100)
        assert not support_survival.double_window_open(receiver, _player("ammo"), 100)


def _sight(*pairs: tuple[tuple[int, int], tuple[int, int]]) -> dict[str, set[str]]:
    """Symmetric sight lines between each pair of cells."""
    sight: dict[str, set[str]] = {}
    for a, b in pairs:
        sight.setdefault(f"{a[0]},{a[1]}", set()).add(f"{b[0]},{b[1]}")
        sight.setdefault(f"{b[0]},{b[1]}", set()).add(f"{a[0]},{a[1]}")
    return sight


class TestMedicWithAmmo:
    """CAL-03 rule 9 (#588): the Medic is "with" the Ammo in its sight or within
    ``LINK_SQUARES`` (Chebyshev, true cells)."""

    def _pair(self, medic_cell: tuple[int, int]) -> tuple[PlayerState, PlayerState]:
        ammo = _player("ammo", cell_row=0, cell_col=0)
        medic = _player("medic", cell_row=medic_cell[0], cell_col=medic_cell[1])
        return ammo, medic

    def test_in_sight(self) -> None:
        ammo, medic = self._pair((0, 12))
        ctx = _grid_map(sight=_sight(((0, 0), (0, 12))), size=16)
        assert support_survival.medic_with_ammo(ammo, medic, ctx)

    def test_at_link_squares(self) -> None:
        ammo, medic = self._pair((3, support_survival.LINK_SQUARES))
        assert support_survival.medic_with_ammo(ammo, medic, _grid_map(size=16))

    def test_one_square_past_link_squares_out_of_sight(self) -> None:
        ammo, medic = self._pair((0, support_survival.LINK_SQUARES + 1))
        assert not support_survival.medic_with_ammo(ammo, medic, _grid_map(size=16))


class TestHoldAllows:
    """CAL-03 rule 8 (#588): a support Resupplies a non-support only on a visit, at
    ``CALLOUT_LIVES`` or fewer, in the other Active support's sight, or (the Ammo)
    when its Medic is gone or lost. The base scene fails every condition; each
    fixture changes one thing."""

    TICK = 500

    def _scene(
        self, *, sight: dict[str, set[str]] | None = None
    ) -> tuple[PlayerState, PlayerState, PlayerState, list, MapContext]:
        # The Ammo at (0, 0), its Medic 10 squares off and out of sight, the Scout
        # at (8, 8) seen by neither; the Ammo remembers the Medic this tick.
        ammo = _player("ammo", cell_row=0, cell_col=0)
        medic = _player("medic", cell_row=0, cell_col=10)
        scout = _player("scout", cell_row=8, cell_col=8)
        scout.final_lives = support_survival.CALLOUT_LIVES + 1
        ammo.player_memory["red_medic"] = {
            "cell": (0, 10),
            "timestamp": self.TICK,
            "role": "medic",
        }
        ctx = _grid_map(sight=sight, size=16)
        return ammo, medic, scout, [ammo, medic, scout], ctx

    def _allows(self, support, teammate, everyone, ctx) -> bool:
        return support_survival.hold_allows(support, teammate, everyone, self.TICK, ctx)

    def test_support_to_support(self) -> None:
        ammo, medic, _, everyone, ctx = self._scene()
        assert self._allows(medic, ammo, everyone, ctx)

    def test_on_a_visit(self) -> None:
        ammo, _, scout, everyone, ctx = self._scene()
        scout.visit = support_survival.Visit(phase="at", started=self.TICK - 10)
        assert self._allows(ammo, scout, everyone, ctx)

    def test_at_callout_lives(self) -> None:
        ammo, _, scout, everyone, ctx = self._scene()
        scout.final_lives = support_survival.CALLOUT_LIVES
        assert self._allows(ammo, scout, everyone, ctx)

    def test_held_one_life_above_callout_lives(self) -> None:
        ammo, _, scout, everyone, ctx = self._scene()
        assert not self._allows(ammo, scout, everyone, ctx)

    def test_the_other_support_active_and_seeing_the_teammate(self) -> None:
        ammo, _, scout, everyone, ctx = self._scene(sight=_sight(((0, 10), (8, 8))))
        assert self._allows(ammo, scout, everyone, ctx)

    def test_the_other_support_seeing_it_but_downed(self) -> None:
        ammo, medic, scout, everyone, ctx = self._scene(sight=_sight(((0, 10), (8, 8))))
        medic.last_downed_time = self.TICK - 1
        assert not self._allows(ammo, scout, everyone, ctx)

    def test_the_medic_eliminated(self) -> None:
        ammo, medic, scout, everyone, ctx = self._scene()
        medic.final_lives = 0
        assert self._allows(ammo, scout, everyone, ctx)

    def test_the_medic_away_with_memory_link_stale_ticks_old(self) -> None:
        ammo, _, scout, everyone, ctx = self._scene()
        ammo.player_memory["red_medic"]["timestamp"] = (
            self.TICK - support_survival.LINK_STALE_TICKS
        )
        assert not self._allows(ammo, scout, everyone, ctx)

    def test_the_medic_away_with_memory_one_tick_past_stale(self) -> None:
        ammo, _, scout, everyone, ctx = self._scene()
        ammo.player_memory["red_medic"]["timestamp"] = (
            self.TICK - support_survival.LINK_STALE_TICKS - 1
        )
        assert self._allows(ammo, scout, everyone, ctx)

    def test_the_medic_away_with_no_memory(self) -> None:
        ammo, _, scout, everyone, ctx = self._scene()
        ammo.player_memory.clear()
        assert self._allows(ammo, scout, everyone, ctx)

    def test_the_medic_with_the_ammo(self) -> None:
        # No memory, so only "with" holds the single back.
        ammo, _, scout, everyone, ctx = self._scene(sight=_sight(((0, 0), (0, 10))))
        ammo.player_memory.clear()
        assert not self._allows(ammo, scout, everyone, ctx)

    def test_the_singles_case_is_the_ammo_s_alone(self) -> None:
        # The Medic with no memory of the Ammo still holds the single.
        ammo, medic, scout, everyone, ctx = self._scene()
        assert not self._allows(medic, scout, everyone, ctx)


class TestTheAmmoWalk:
    """CAL-03 rules 4 and 8 (#588): the Ammo's Resupply walk (``_goal_from_action``)
    skips a teammate a remembered enemy can see, and one HOLD would not let it
    Resupply."""

    def _scene(
        self, lives: int, sight: dict[str, set[str]]
    ) -> tuple[PlayerState, list, MapContext]:
        ammo = _player("ammo", cell_row=0, cell_col=0)
        medic = _player("medic", cell_row=0, cell_col=1)
        scout = _player("scout", cell_row=3, cell_col=3)
        scout.final_lives, scout.final_shots = lives, 1
        return ammo, [ammo, medic, scout], _grid_map(sight=sight)

    def _walk(self, ammo, everyone, ctx) -> tuple[int, int] | None:
        return _goal_from_action(
            ammo, everyone, "blue", 0, 0, "resupply_ally", ctx, 100, "ticks"
        )

    def test_walks_to_a_teammate_out_of_enemy_sight(self) -> None:
        ammo, everyone, ctx = self._scene(1, {"3,3": {"2,3"}})
        assert self._walk(ammo, everyone, ctx) == (2, 3)

    def test_no_walk_when_the_only_teammate_is_seen_by_a_remembered_enemy(
        self,
    ) -> None:
        ammo, everyone, ctx = self._scene(1, {"3,3": {"2,3"}, "5,5": {"3,3"}})
        ammo.player_memory["blue_heavy"] = {
            "cell": (5, 5),
            "timestamp": 100,
            "role": "heavy",
        }
        assert self._walk(ammo, everyone, ctx) is None

    def test_no_walk_to_a_teammate_hold_keeps_the_single_from(self) -> None:
        # Four lives, and the Medic (with the Ammo) does not see the Scout.
        ammo, everyone, ctx = self._scene(4, {"3,3": {"2,3"}})
        assert self._walk(ammo, everyone, ctx) is None

    def test_walks_to_a_teammate_the_medic_sees(self) -> None:
        ammo, everyone, ctx = self._scene(4, _sight(((3, 3), (0, 1))))
        assert self._walk(ammo, everyone, ctx) is not None


class TestMedicBack:
    """CAL-03 rule 5 (#588): the Medic's Defense candidates reach
    ``MEDIC_BACK_REACH`` steps from a cell that sees the Heavy, valued by how hidden
    and how far from the enemy Base they are, with no pull toward the Heavy."""

    def test_the_candidates_reach_medic_back_reach(self, draws: list) -> None:
        medic, everyone, ctx = _defense_scene("medic")
        mode_goal(medic, everyone, "blue", ctx, 100)
        reach = support_survival.MEDIC_BACK_REACH
        assert draws[-1][0] == modes._within_reach([(5, 2)], reach, ctx)
        assert (5, 2 + reach) in draws[-1][0]
        assert (5, 3 + reach) not in draws[-1][0]

    def test_the_ammo_keeps_support_cell_reach(self, draws: list) -> None:
        ammo, everyone, ctx = _defense_scene("ammo")
        mode_goal(ammo, everyone, "blue", ctx, 100)
        reach = support_survival.SUPPORT_CELL_REACH
        assert draws[-1][0] == modes._within_reach([(5, 2)], reach, ctx)

    def test_the_deepest_most_hidden_cell_wins(self, draws: list) -> None:
        medic, everyone, ctx = _defense_scene("medic")
        medic.teamwork = 100  # no pull toward the Heavy whatever the teamwork
        mode_goal(medic, everyone, "blue", ctx, 100)
        candidates, value_of = draws[-1]
        # The blue Base is (11, 11): column 0 is deepest; (5, 0) has no count.
        assert max(candidates, key=value_of) == (4, 0)
        assert value_of((4, 0)) == pytest.approx(1.0)
        # (5, 5) is the shallowest candidate (6 squares) and is hidden.
        assert value_of((5, 5)) == pytest.approx(
            support_survival.MEDIC_BACK_HIDDEN_WEIGHT
        )
        # (5, 3): LOS rank 0.5 and 8 squares deep (2 of the 5-square span).
        assert value_of((5, 3)) == pytest.approx(0.5 * 0.5 + 0.5 * 2 / 5)


class TestAmmoAnchorAndSeek:
    """CAL-03 rule 3 (#588): at ``AMMO_ANCHOR_LIVES`` or fewer the Ammo's Defense
    cell is near a cell that sees its Medic's remembered cell; at
    ``AMMO_SEEK_MEDIC_LIVES`` or fewer it walks to the Medic's true cell."""

    def _anchor_scene(self, lives: int) -> tuple[PlayerState, list, MapContext]:
        ammo, everyone, _ = _defense_scene("ammo")
        ammo.final_lives = lives
        medic = _player("medic", cell_row=9, cell_col=8)
        ammo.player_memory["red_medic"] = {
            "cell": (5, 8),
            "timestamp": 100,
            "role": "medic",
        }
        ctx = _grid_map(sight={"5,0": {"5,2"}, "5,8": {"2,8"}})
        return ammo, [*everyone, medic], ctx

    def test_the_anchor_holds_at_anchor_lives(self, draws: list) -> None:
        ammo, everyone, ctx = self._anchor_scene(support_survival.AMMO_ANCHOR_LIVES)
        mode_goal(ammo, everyone, "blue", ctx, 100)
        reach = support_survival.MEDIC_BACK_REACH
        assert draws[-1][0] == modes._within_reach([(2, 8)], reach, ctx)

    def test_one_life_above_the_heavy_rule_applies(self, draws: list) -> None:
        ammo, everyone, ctx = self._anchor_scene(support_survival.AMMO_ANCHOR_LIVES + 1)
        mode_goal(ammo, everyone, "blue", ctx, 100)
        reach = support_survival.SUPPORT_CELL_REACH
        assert draws[-1][0] == modes._within_reach([(5, 2)], reach, ctx)

    def test_the_anchor_cell_is_valued_like_the_medic_s(self, draws: list) -> None:
        ammo, everyone, ctx = self._anchor_scene(support_survival.AMMO_ANCHOR_LIVES)
        mode_goal(ammo, everyone, "blue", ctx, 100)
        candidates, value_of = draws[-1]
        # Hidden everywhere (no counts there); depth from the blue Base (11, 11)
        # decides: row 0 is deepest.
        assert max(candidates, key=value_of)[0] == 0
        assert value_of(max(candidates, key=value_of)) == pytest.approx(1.0)

    def _seek(self, lives: int) -> tuple[int, int] | None:
        ammo = _player("ammo", cell_row=0, cell_col=0)
        ammo.final_lives = lives
        medic = _player("medic", cell_row=9, cell_col=9)
        ctx = _grid_map()
        return pathfinding.choose_goal_cell(
            ammo, [ammo, medic], ctx.spawn_cells, ctx, "", 100, "ticks"
        )

    def test_the_seek_at_seek_lives(self) -> None:
        assert self._seek(support_survival.AMMO_SEEK_MEDIC_LIVES) == (9, 9)

    def test_no_seek_one_life_above(self) -> None:
        assert self._seek(support_survival.AMMO_SEEK_MEDIC_LIVES + 1) != (9, 9)


class TestSupportLink:
    """CAL-03 rule 9 (#588): each plan the Medic and Ammo write each other's true
    cell into memory within comms range (no RNG); the Ammo records when it left its
    Medic and goes back after ``LINK_RETURN_TICKS``."""

    def _holding(self, role: str, cell: tuple[int, int]) -> PlayerState:
        # A standing Hold: plan_action returns before any Action roll.
        return _player(role, cell_row=cell[0], cell_col=cell[1], hold_until=10**6)

    def _plan(self, player: PlayerState, everyone: list, ctx, tick: int) -> None:
        from matches.sim_helpers import combat

        combat.plan_action(player, everyone, tick, ctx, time_domain="ticks")

    def test_the_refresh_writes_both_cells_in_range(self) -> None:
        medic, ammo = self._holding("medic", (0, 0)), self._holding("ammo", (0, 10))
        ctx = _grid_map(size=16)
        for player in (medic, ammo):
            self._plan(player, [medic, ammo], ctx, 100)
        assert medic.player_memory["red_ammo"]["cell"] == (0, 10)
        assert ammo.player_memory["red_medic"]["cell"] == (0, 0)
        assert ammo.player_memory["red_medic"]["timestamp"] == 100

    def test_no_refresh_out_of_comms_range(self) -> None:
        # Half the diagonal of a 4 x 4 grid is 2.83 squares; (3, 3) is 4.24 away.
        medic, ammo = self._holding("medic", (0, 0)), self._holding("ammo", (3, 3))
        ctx = _grid_map(size=16)
        ctx.zone_data = [[1] * 4 for _ in range(4)]
        self._plan(medic, [medic, ammo], ctx, 100)
        assert "red_ammo" not in medic.player_memory

    def test_the_refresh_draws_no_rng(self) -> None:
        medic, ammo = self._holding("medic", (0, 0)), self._holding("ammo", (0, 10))
        ctx = _grid_map(size=16)
        random.seed(7)
        before = random.getstate()
        for player in (medic, ammo):
            self._plan(player, [medic, ammo], ctx, 100)
        assert random.getstate() == before
        assert "red_ammo" in medic.player_memory

    def test_leaving_sets_link_left_at_and_re_sync_clears_it(self) -> None:
        medic, ammo = self._holding("medic", (0, 0)), self._holding("ammo", (0, 10))
        ctx = _grid_map(size=16)
        self._plan(ammo, [medic, ammo], ctx, 100)
        assert ammo.link_left_at == 100
        self._plan(ammo, [medic, ammo], ctx, 130)
        assert ammo.link_left_at == 100  # the first tick away is kept
        medic.cell_col = 4
        self._plan(ammo, [medic, ammo], ctx, 140)
        assert ammo.link_left_at is None

    def _return_goal(self, left_at: int, tick: int) -> tuple[int, int] | None:
        ammo = _player("ammo", cell_row=0, cell_col=0, link_left_at=left_at)
        medic = _player("medic", cell_row=11, cell_col=11)
        ammo.player_memory["red_medic"] = {
            "cell": (8, 8),
            "timestamp": left_at,
            "role": "medic",
        }
        ctx = _grid_map(sight={"8,8": {"6,8", "2,8"}})
        return pathfinding.choose_goal_cell(
            ammo, [ammo, medic], ctx.spawn_cells, ctx, "", tick, "ticks"
        )

    def test_the_return_goal_starts_at_link_return_ticks(self) -> None:
        # The nearest cell that sees the remembered Medic: (2, 8), not (6, 8).
        assert self._return_goal(40, 40 + support_survival.LINK_RETURN_TICKS) == (2, 8)

    def test_no_return_goal_one_tick_before(self) -> None:
        goal = self._return_goal(40, 40 + support_survival.LINK_RETURN_TICKS - 1)
        assert goal != (2, 8)


_VISITOR = (5, 9)


class TestJoin:
    """CAL-04c (#601) Join: an Active Medic or Ammo whose partner sees a teammate on
    a Resupply visit that it does not see walks to the nearest cell that sees that
    visitor (``pathfinding._join_goal``). True cell-to-cell LOS, no RNG."""

    TICK = 100

    def _scene(
        self, *pairs: tuple[tuple[int, int], tuple[int, int]]
    ) -> tuple[PlayerState, PlayerState, PlayerState, MapContext]:
        """The Medic at (0, 0) does not see the Scout on a visit at (5, 9); its Ammo
        at (5, 5) does. The Scout's cell sees (4, 0) and (1, 8) too."""
        medic = _player("medic", cell_row=0, cell_col=0)
        ammo = _player("ammo", cell_row=5, cell_col=5)
        scout = _player(
            "scout", cell_row=5, cell_col=9, visit=Visit(phase="go", started=90)
        )
        if not pairs:
            pairs = ((_VISITOR, (5, 5)), (_VISITOR, (4, 0)), (_VISITOR, (1, 8)))
        return medic, ammo, scout, _grid_map(sight=_sight(*pairs))

    def _join(self, player: PlayerState, everyone: list, ctx: Any) -> Any:
        return pathfinding._join_goal(player, everyone, ctx, self.TICK)

    def test_the_nearest_cell_that_sees_the_visitor(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        # (4, 0) is 4 squares away, (1, 8) 9 and the Ammo's (5, 5) 10.
        assert self._join(medic, [medic, ammo, scout], ctx) == (4, 0)

    def test_a_tie_goes_to_the_lower_cell(self) -> None:
        medic, ammo, scout, ctx = self._scene(
            (_VISITOR, (5, 5)), (_VISITOR, (3, 2)), (_VISITOR, (2, 3))
        )
        assert self._join(medic, [medic, ammo, scout], ctx) == (2, 3)

    def test_the_nearest_visitor(self) -> None:
        heavy_cell = (1, 9)
        medic, ammo, scout, ctx = self._scene(
            (_VISITOR, (5, 5)),
            (_VISITOR, (4, 0)),
            (heavy_cell, (5, 5)),
            (heavy_cell, (0, 6)),
        )
        heavy = _player(
            "heavy", cell_row=1, cell_col=9, visit=Visit(phase="go", started=95)
        )
        # The Heavy is 10 squares from the Medic, the Scout 14.
        assert self._join(medic, [medic, ammo, scout, heavy], ctx) == (0, 6)

    def test_the_ammo_joins_too(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        ammo.cell_row, ammo.cell_col = (0, 0)
        medic.cell_row, medic.cell_col = (5, 5)
        assert self._join(ammo, [medic, ammo, scout], ctx) == (4, 0)

    def test_not_a_support(self) -> None:
        _, _, scout, ctx = self._scene()
        heavy = _player("heavy", cell_row=0, cell_col=0)
        medic = _player("medic", cell_row=5, cell_col=5)
        assert self._join(heavy, [heavy, medic, scout], ctx) is None

    def test_not_active(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        medic.last_downed_time = self.TICK - 1
        assert self._join(medic, [medic, ammo, scout], ctx) is None

    def test_no_map(self) -> None:
        medic, ammo, scout, _ = self._scene()
        assert self._join(medic, [medic, ammo, scout], None) is None

    def test_no_cell(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        medic.cell_row = medic.cell_col = None
        assert self._join(medic, [medic, ammo, scout], ctx) is None

    def test_no_partner(self) -> None:
        medic, _, scout, ctx = self._scene()
        assert self._join(medic, [medic, scout], ctx) is None

    def test_the_partner_has_no_cell(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        ammo.cell_row = ammo.cell_col = None
        assert self._join(medic, [medic, ammo, scout], ctx) is None

    def test_no_visitor(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        scout.visit = None
        assert self._join(medic, [medic, ammo, scout], ctx) is None

    def test_an_enemy_visitor(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        scout.team_color = "blue"
        assert self._join(medic, [medic, ammo, scout], ctx) is None

    def test_the_partner_does_not_see_the_visitor(self) -> None:
        medic, ammo, scout, ctx = self._scene((_VISITOR, (4, 0)), (_VISITOR, (1, 8)))
        assert self._join(medic, [medic, ammo, scout], ctx) is None

    def test_the_player_already_sees_the_visitor(self) -> None:
        medic, ammo, scout, ctx = self._scene(
            (_VISITOR, (5, 5)), (_VISITOR, (4, 0)), (_VISITOR, (0, 0))
        )
        assert self._join(medic, [medic, ammo, scout], ctx) is None

    def test_the_visitor_is_not_alive(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        scout.final_lives = 0
        assert self._join(medic, [medic, ammo, scout], ctx) is None

    def test_the_visitor_has_no_cell(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        scout.cell_row = scout.cell_col = None
        assert self._join(medic, [medic, ammo, scout], ctx) is None

    def test_draws_no_rng(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        random.seed(11)
        before = random.getstate()
        assert self._join(medic, [medic, ammo, scout], ctx) == (4, 0)
        assert random.getstate() == before

    def _goal(self, player: PlayerState, everyone: list, ctx: Any) -> Any:
        return pathfinding.choose_goal_cell(
            player, everyone, ctx.spawn_cells, ctx, "", self.TICK, "ticks"
        )

    def test_join_beats_the_link_return(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        ammo.cell_row, ammo.cell_col = (0, 0)
        medic.cell_row, medic.cell_col = (5, 5)
        ammo.link_left_at = 0
        ammo.player_memory["red_medic"] = {
            "cell": (5, 5),
            "timestamp": self.TICK,
            "role": "medic",
        }
        # Rule 9's link return alone would walk the Ammo to (5, 9).
        assert pathfinding._link_return_goal(ammo, [medic, ammo], ctx, self.TICK) == (
            5,
            9,
        )
        assert self._goal(ammo, [medic, ammo, scout], ctx) == (4, 0)

    def test_join_clears_the_commitment(self) -> None:
        medic, ammo, scout, ctx = self._scene()
        medic._committed_goal = ((11, 11), False, 10**6)
        assert self._goal(medic, [medic, ammo, scout], ctx) == (4, 0)
        assert medic._committed_goal is None


class TestLeashStamp:
    """CAL-04c (#601): each plan ``combat._support_link`` stamps the Ammo's
    ``leash_far_since`` the first plan its cell is more than ``LEASH_SQUARES``
    (Chebyshev) from its memory's cell for its Medic, and clears it when within
    reach or with no remembered cell."""

    def _holding(self, role: str, cell: tuple[int, int]) -> PlayerState:
        return _player(role, cell_row=cell[0], cell_col=cell[1], hold_until=10**6)

    def _plan(self, player: PlayerState, everyone: list, ctx: Any, tick: int) -> None:
        from matches.sim_helpers import combat

        combat.plan_action(player, everyone, tick, ctx, time_domain="ticks")

    def _far(self) -> tuple[PlayerState, PlayerState, MapContext]:
        far = support_survival.LEASH_SQUARES + 1
        medic, ammo = self._holding("medic", (0, far)), self._holding("ammo", (0, 0))
        return medic, ammo, _grid_map(size=16)

    def test_the_first_far_plan_stamps_it(self) -> None:
        medic, ammo, ctx = self._far()
        self._plan(ammo, [medic, ammo], ctx, 100)
        assert ammo.leash_far_since == 100

    def test_a_later_far_plan_keeps_it(self) -> None:
        medic, ammo, ctx = self._far()
        self._plan(ammo, [medic, ammo], ctx, 100)
        self._plan(ammo, [medic, ammo], ctx, 130)
        assert ammo.leash_far_since == 100

    def test_back_within_reach_clears_it(self) -> None:
        medic, ammo, ctx = self._far()
        self._plan(ammo, [medic, ammo], ctx, 100)
        medic.cell_col = support_survival.LEASH_SQUARES
        self._plan(ammo, [medic, ammo], ctx, 110)
        assert ammo.leash_far_since is None

    def test_no_remembered_cell_clears_it(self) -> None:
        # Out of comms range (half the 4 x 4 diagonal), so nothing is remembered.
        medic, ammo, ctx = self._far()
        ctx.zone_data = [[1] * 4 for _ in range(4)]
        ammo.leash_far_since = 50
        self._plan(ammo, [medic, ammo], ctx, 100)
        assert "red_medic" not in ammo.player_memory
        assert ammo.leash_far_since is None

    def test_the_medic_is_not_stamped(self) -> None:
        medic, ammo, ctx = self._far()
        self._plan(medic, [medic, ammo], ctx, 100)
        assert medic.leash_far_since is None


_LEASH_SINCE = 40
_LEASH_TICK = _LEASH_SINCE + support_survival.LEASH_TICKS + 1
_REMEMBERED = (11, 11)


class TestLeashGoal:
    """CAL-04c (#601): ``pathfinding._leash_goal`` fires strictly ``LEASH_TICKS``
    after the stamp: the nearest cell (Manhattan, then the cell) that sees the
    Medic's remembered cell and lies within ``LEASH_SQUARES`` of it. No RNG."""

    def _scene(
        self, role: str = "ammo", sight: dict | None = None
    ) -> tuple[PlayerState, PlayerState, MapContext]:
        """The Ammo at (0, 0) remembers its Medic at (11, 11), whose cell sees
        (0, 11) (11 squares off, the nearest), (5, 11) and (11, 3)."""
        ammo = _player(role, cell_row=0, cell_col=0, leash_far_since=_LEASH_SINCE)
        medic = _player("medic", cell_row=15, cell_col=15)
        ammo.player_memory["red_medic"] = {
            "cell": _REMEMBERED,
            "timestamp": _LEASH_TICK,
            "role": "medic",
        }
        if sight is None:
            sight = {"11,11": {"0,11", "5,11", "11,3"}}
        return ammo, medic, _grid_map(sight=sight, size=16)

    def _leash(self, ammo: Any, everyone: list, ctx: Any, tick: int = _LEASH_TICK):
        return pathfinding._leash_goal(ammo, everyone, ctx, tick)

    def test_one_tick_past_leash_ticks_a_cell_within_reach(self) -> None:
        # (11, 3) is 14 squares off and 8 from the Medic; (0, 11) is nearer the
        # Ammo but 11 from the Medic, beyond the reach.
        ammo, medic, ctx = self._scene()
        assert self._leash(ammo, [ammo, medic], ctx) == (11, 3)

    def test_none_at_exactly_leash_ticks(self) -> None:
        ammo, medic, ctx = self._scene()
        assert self._leash(ammo, [ammo, medic], ctx, _LEASH_TICK - 1) is None

    def test_the_medic_s_cell_when_none_within_reach_sees_it(self) -> None:
        ammo, medic, ctx = self._scene(sight={"11,11": {"0,11"}})
        assert self._leash(ammo, [ammo, medic], ctx) == _REMEMBERED

    def test_not_the_ammo(self) -> None:
        scout, medic, ctx = self._scene(role="scout")
        assert self._leash(scout, [scout, medic], ctx) is None

    def test_no_stamp(self) -> None:
        ammo, medic, ctx = self._scene()
        ammo.leash_far_since = None
        assert self._leash(ammo, [ammo, medic], ctx) is None

    def test_no_cell(self) -> None:
        ammo, medic, ctx = self._scene()
        ammo.cell_row = ammo.cell_col = None
        assert self._leash(ammo, [ammo, medic], ctx) is None

    def test_no_medic(self) -> None:
        ammo, _, ctx = self._scene()
        assert self._leash(ammo, [ammo], ctx) is None

    def test_no_memory_of_the_medic(self) -> None:
        ammo, medic, ctx = self._scene()
        ammo.player_memory.clear()
        assert self._leash(ammo, [ammo, medic], ctx) is None

    def test_a_memory_with_no_cell(self) -> None:
        ammo, medic, ctx = self._scene()
        ammo.player_memory["red_medic"]["cell"] = None
        assert self._leash(ammo, [ammo, medic], ctx) is None

    def test_a_stale_memory(self) -> None:
        ammo, medic, ctx = self._scene()
        stale = _LEASH_TICK - support_survival.LINK_STALE_TICKS - 1
        ammo.player_memory["red_medic"]["timestamp"] = stale
        assert self._leash(ammo, [ammo, medic], ctx) is None

    def test_a_memory_at_link_stale_ticks_still_counts(self) -> None:
        ammo, medic, ctx = self._scene()
        oldest = _LEASH_TICK - support_survival.LINK_STALE_TICKS
        ammo.player_memory["red_medic"]["timestamp"] = oldest
        assert self._leash(ammo, [ammo, medic], ctx) == (11, 3)

    def test_draws_no_rng(self) -> None:
        ammo, medic, ctx = self._scene()
        random.seed(5)
        before = random.getstate()
        assert self._leash(ammo, [ammo, medic], ctx) == (11, 3)
        assert random.getstate() == before

    def _goal(self, ammo: PlayerState, everyone: list, ctx: Any) -> Any:
        return pathfinding.choose_goal_cell(
            ammo, everyone, ctx.spawn_cells, ctx, "", _LEASH_TICK, "ticks"
        )

    def test_the_leash_clears_the_commitment(self) -> None:
        ammo, medic, ctx = self._scene()
        ammo._committed_goal = ((15, 0), False, 10**6)
        assert self._goal(ammo, [ammo, medic], ctx) == (11, 3)
        assert ammo._committed_goal is None

    def test_the_leash_beats_the_nuke_reaction(self) -> None:
        ammo, medic, ctx = self._scene()
        scout = _player("scout", cell_row=3, cell_col=3)
        scout.final_shots = 1
        ammo.reacting_to_nuke = True
        everyone = [ammo, medic, scout]
        # Without the leash the Ammo rushes to the Scout, the neediest by Shots.
        ammo.leash_far_since = None
        assert self._goal(ammo, everyone, ctx) == (3, 3)
        ammo.leash_far_since = _LEASH_SINCE
        assert self._goal(ammo, everyone, ctx) == (11, 3)

    def test_the_leash_beats_join(self) -> None:
        ammo, medic, _ = self._scene()
        medic.cell_row, medic.cell_col = (5, 5)
        scout = _player(
            "scout", cell_row=5, cell_col=9, visit=Visit(phase="go", started=90)
        )
        sight = _sight((_VISITOR, (5, 5)), (_VISITOR, (4, 0)))
        sight.setdefault("11,11", set()).add("11,3")
        ctx = _grid_map(sight=sight, size=16)
        assert pathfinding._join_goal(ammo, [ammo, medic, scout], ctx, _LEASH_TICK)
        assert self._goal(ammo, [ammo, medic, scout], ctx) == (11, 3)

    @pytest.mark.parametrize("still", ["is_holding", "is_hiding"])
    def test_a_fired_leash_leaves_a_hold_or_hide_still(self, still: str) -> None:
        # Decision 27: the leash sets only the goal, never overriding the roll.
        from matches.simulation import BatchSimulator

        ammo, medic, ctx = self._scene()
        setattr(ammo, still, True)
        ammo.last_chosen_action = "hold" if still == "is_holding" else "hide"
        assert self._leash(ammo, [ammo, medic], ctx) is not None
        BatchSimulator()._advance_player(
            ammo, [ammo, medic], _LEASH_TICK, ctx, ammo.last_chosen_action
        )
        assert (ammo.cell_row, ammo.cell_col) == (0, 0)
        assert getattr(ammo, still) is True

    def test_without_the_roll_the_leash_walks(self) -> None:
        from matches.simulation import BatchSimulator

        ammo, medic, ctx = self._scene()
        BatchSimulator()._advance_player(ammo, [ammo, medic], _LEASH_TICK, ctx, "")
        assert (ammo.cell_row, ammo.cell_col) != (0, 0)


class TestIdleTrade:
    """CAL-03 rule 2 (#588): an Active support whose roll plans nothing (a ``hide``,
    or an empty ``resupply_ally``) Tags an enemy in sight with probability
    ``IDLE_TRADE_RATE``, its gun off cooldown. The one draw comes after a target."""

    def _plan(
        self,
        monkeypatch: pytest.MonkeyPatch,
        roll: str,
        *,
        enemy_in_sight: bool = True,
        draw: float = 0.0,
        **medic_fields: Any,
    ) -> tuple[list, PlayerState, PlayerState, list[float]]:
        from matches.sim_helpers import combat

        medic = _player("medic", cell_row=0, cell_col=0, **medic_fields)
        enemy = _player("heavy", "blue", cell_row=0, cell_col=3)
        ctx = _grid_map(sight={"0,0": {"0,3"}} if enemy_in_sight else {})
        real_choices = random.choices
        draws: list[float] = []

        def choices(population: list, *args: Any, **kwargs: Any) -> list:
            if population is combat._CHOICES:
                return [roll]
            return real_choices(population, *args, **kwargs)

        def one_draw() -> float:
            draws.append(draw)
            return draw

        monkeypatch.setattr(combat.random, "choices", choices)
        monkeypatch.setattr(combat.random, "random", one_draw)
        plans = combat.plan_action(medic, [medic, enemy], 100, ctx, time_domain="ticks")
        return plans, medic, enemy, draws

    @staticmethod
    def _kinds(plans: list) -> list:
        return [(p["type"], p.get("target")) for p in plans]

    def test_a_hide_roll_tags_an_enemy_in_sight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plans, _, enemy, _ = self._plan(monkeypatch, "hide")
        assert self._kinds(plans) == [("tag", enemy)]

    def test_an_empty_resupply_roll_tags_an_enemy_in_sight(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plans, _, enemy, _ = self._plan(monkeypatch, "resupply_ally")
        assert self._kinds(plans) == [("tag", enemy)]

    def test_a_hiding_support_stops_hiding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, medic, _, _ = self._plan(monkeypatch, "hide", is_hiding=True)
        assert medic.is_hiding is False

    def test_not_with_the_gun_on_cooldown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plans, _, _, draws = self._plan(monkeypatch, "hide", last_shot_time=100)
        assert self._kinds(plans) == [("hide", None)]
        assert draws == []

    def test_not_with_no_enemy_in_sight(self, monkeypatch: pytest.MonkeyPatch) -> None:
        plans, _, _, draws = self._plan(monkeypatch, "hide", enemy_in_sight=False)
        assert self._kinds(plans) == [("hide", None)]
        assert draws == []

    def test_one_draw_with_a_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _, _, _, draws = self._plan(monkeypatch, "hide")
        assert len(draws) == 1

    def test_a_draw_at_the_rate_keeps_the_hide(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        plans, _, _, draws = self._plan(
            monkeypatch, "hide", draw=support_survival.IDLE_TRADE_RATE
        )
        assert self._kinds(plans) == [("hide", None)]
        assert len(draws) == 1

    def test_a_non_support_does_not_trade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from matches.sim_helpers import combat

        scout = _player("scout", cell_row=0, cell_col=0)
        enemy = _player("heavy", "blue", cell_row=0, cell_col=3)
        real_choices = random.choices
        monkeypatch.setattr(
            combat.random,
            "choices",
            lambda pop, *a, **k: (
                ["hide"] if pop is combat._CHOICES else real_choices(pop, *a, **k)
            ),
        )
        monkeypatch.setattr(combat.random, "random", lambda: 0.0)  # a trade draw
        plans = combat.plan_action(
            scout,
            [scout, enemy],
            100,
            _grid_map(sight={"0,0": {"0,3"}}),
            time_domain="ticks",
        )
        assert self._kinds(plans) == [("hide", None)]


class TestCommsReach:
    """CAL-04d (#602): ``map_context.comms_reach``, half the grid's diagonal, is
    the range of ``_support_link`` and ``_broadcast_communication``."""

    def test_half_the_diagonal(self) -> None:
        assert comms_reach([[1] * 8 for _ in range(6)]) == 5.0

    def test_no_grid_is_unbounded(self) -> None:
        assert comms_reach([]) == math.inf
        assert comms_reach(None) == math.inf


class TestPartnerRole:
    """CAL-04d (#602): ``partner_role`` names the other support role."""

    @pytest.mark.parametrize(
        ("role", "partner"), [("medic", "ammo"), ("ammo", "medic")]
    )
    def test_each_support_names_the_other(self, role: str, partner: str) -> None:
        assert support_survival.partner_role(role) == partner

    def test_any_other_role_gets_the_medic(self) -> None:
        assert support_survival.partner_role("scout") == "medic"


class TestOwnTeammate:
    """CAL-04d (#602): ``own_teammate`` is *player*'s living teammate in a role."""

    def test_the_living_teammate_in_the_role(self) -> None:
        ammo, medic = _player("ammo"), _player("medic")
        enemy_medic = _player("medic", team="blue")
        assert (
            support_survival.own_teammate(ammo, "medic", [enemy_medic, ammo, medic])
            is medic
        )

    def test_an_eliminated_teammate_is_none(self) -> None:
        ammo, medic = _player("ammo"), _player("medic", final_lives=0)
        assert support_survival.own_teammate(ammo, "medic", [ammo, medic]) is None

    def test_never_the_player_itself(self) -> None:
        medic = _player("medic")
        assert support_survival.own_teammate(medic, "medic", [medic]) is None


class TestVisitShare:
    """CAL-03 rule 6 (#588): ``VISIT_BASE_SHARE + VISIT_SPREAD x (decision_making -
    50) / 50``, clamped to [``VISIT_SHARE_MIN``, ``VISIT_SHARE_MAX``]."""

    @pytest.mark.parametrize(
        ("decision_making", "share"),
        [(0, 1 / 3 - 0.1), (50, 1 / 3), (100, 1 / 3 + 0.1)],
    )
    def test_the_spread_around_a_third(
        self, decision_making: int, share: float
    ) -> None:
        scout = _player("scout", decision_making=decision_making)
        assert support_survival.visit_share(scout) == pytest.approx(share)

    def test_clamped_at_the_low_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(support_survival, "VISIT_SPREAD", 1.0)
        scout = _player("scout", decision_making=0)
        assert support_survival.visit_share(scout) == support_survival.VISIT_SHARE_MIN

    def test_clamped_at_the_high_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(support_survival, "VISIT_SPREAD", 1.0)
        scout = _player("scout", decision_making=100)
        assert support_survival.visit_share(scout) == support_survival.VISIT_SHARE_MAX

    def test_draws_no_rng(self) -> None:
        random.seed(3)
        before = random.getstate()
        support_survival.visit_share(_player("scout"))
        assert random.getstate() == before


class TestNewPlayerFields:
    """CAL-03 (#588): the typed fields the support rules keep, default None."""

    def test_defaults(self) -> None:
        player = _player("ammo")
        assert (player.link_left_at, player.visit) == (None, None)

    def test_the_visit_fields_default_to_none(self) -> None:
        player = _player("scout")
        assert (player.visit_next_tick, player.backline_cell) == (0, None)
        assert player.support_callouts == []
