"""Support-survival prototypes (``sim_helpers.support_survival``, 2026-11-06).

Five switches, each off by default and each tested on and off at its seam:
target choice, the Defense candidates, the Medic's and Ammo's hide gate and
resupply walk, and the call-out roll with the support staleness window.
"""

from __future__ import annotations

import random
from typing import Any, Callable

import pytest

from matches.sim_helpers import goal_scoring, modes, pathfinding, support_survival
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.mechanics import choose_tag_target
from matches.sim_helpers.modes import hide_allowed, mode_goal
from matches.sim_helpers.pathfinding import _goal_from_action
from matches.sim_helpers.player_state import PlayerState
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

    def test_on_a_hiding_enemy_is_passed_over_for_one_in_the_open(
        self, on: Callable[[str], None]
    ) -> None:
        on("TARGET_PRIORITY")
        random.seed(1)
        scout = _player("scout", "blue", cell_row=0, cell_col=0)
        hidden = _player("heavy", is_hiding=True)
        hidden.tag_id = "red_heavy_2"
        open_heavy = _player("heavy")
        for _ in range(100):
            assert (
                choose_tag_target(
                    scout,
                    [scout, hidden, open_heavy],
                    100,
                    None,
                    los_filter=_all_visible,
                )
                is open_heavy
            )

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
        medic, everyone, ctx = _defense_scene("medic")
        mode_goal(medic, everyone, "blue", ctx, 100)
        assert draws[-1][0] == [(5, 2)]

    def test_on_the_candidates_reach_two_steps_from_a_seeing_cell(
        self, on: Callable[[str], None], draws: list
    ) -> None:
        on("HIDDEN_SUPPORT_CELLS")
        medic, everyone, ctx = _defense_scene("medic")
        mode_goal(medic, everyone, "blue", ctx, 100)
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

    def test_on_both_roles_value_a_hidden_cell(
        self, on: Callable[[str], None], draws: list
    ) -> None:
        on("HIDDEN_SUPPORT_CELLS")
        for role in ("medic", "ammo"):
            support, everyone, ctx = _defense_scene(role)
            mode_goal(support, everyone, "blue", ctx, 100)
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
