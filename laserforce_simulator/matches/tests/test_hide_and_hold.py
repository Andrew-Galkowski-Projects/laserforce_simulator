"""MECH-17d (spec #550, Hide and threats, Hold): supports hide only when it makes sense,
a Commander's nuke check counts the enemies it knows about, and a Hold is a commitment.
Hand-built ``PlayerState`` and ``MapContext`` at the ``plan_action`` seam, plus a seeded
map-less round and a seeded Syracuse round.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

import pytest

from matches.round_probe import run_probe
from matches.sim_helpers import combat, modes, time_constants, weights
from matches.sim_helpers.combat import _ACTION_IDX, plan_action
from matches.sim_helpers.down import record_down
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.modes import DEFENSE_RESUPPLY_SHARE
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.weights import BASELINE_ACTION_WEIGHTS
from matches.simulation import BatchSimulator
from matches.tests.test_modes import (
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
HERE: Cell = (0, 0)
THERE: Cell = (5, 5)


def _key(cell: Cell) -> str:
    return f"{cell[0]},{cell[1]}"


def _sees_here(*watchers: Cell) -> MapContext:
    """Each of *watchers* can see ``HERE``; ``HERE`` sees nothing back."""
    return _grid_map(sight={_key(cell): {_key(HERE)} for cell in watchers})


def _threats(
    player: PlayerState, everyone: list, ctx: MapContext, **kwargs: Any
) -> int:
    domain = kwargs.get("time_domain", "ticks")
    return modes.known_threats(player, everyone, ctx, kwargs.get("tick", NOW), domain)


def _ally_at_share(role: str, share: float) -> PlayerState:
    ally = _player(role)
    ally.final_lives = max(1, round(ally.max_lives * share))
    ally.final_shots = round(ally.max_shots * share)
    return ally


def _needy_ally() -> PlayerState:
    return _ally_at_share("heavy", 0.25)


def _full_ally() -> PlayerState:
    return _player("heavy")


def _downed(player: PlayerState) -> PlayerState:
    player.last_downed_time = NOW - 1
    return player


def _plan(player: PlayerState, ctx: MapContext | None, *others: PlayerState) -> list:
    return plan_action(player, [player, *others], NOW, ctx, time_domain="ticks")


@pytest.fixture
def rolled(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, int]]:
    """The weights of every Action roll ``plan_action`` makes."""
    seen: list[dict[str, int]] = []
    real = combat.random.choices

    def spy(population: list, weights: list, *rest: Any, **kwargs: Any) -> list:
        if "hold" in population:
            seen.append(dict(zip(population, weights)))
        return real(population, weights, *rest, **kwargs)

    monkeypatch.setattr(combat.random, "choices", spy)
    return seen


def _roll(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    monkeypatch.setattr(combat.random, "choices", lambda *_a, **_k: [action])


class TestKnownThreats:
    def test_a_fresh_enemy_whose_remembered_cell_sees_the_player_counts(self) -> None:
        medic = _placed("medic", HERE)
        _remember(medic, "blue_scout", THERE, NOW, "scout")
        assert _threats(medic, [medic, _enemy("scout")], _sees_here(THERE)) == 1

    def test_every_such_enemy_counts_and_only_those(self) -> None:
        medic = _placed("medic", HERE)
        _remember(medic, "blue_scout", THERE, NOW, "scout")
        _remember(medic, "blue_heavy", (6, 6), NOW, "heavy")
        _remember(medic, "blue_ammo", (7, 7), NOW, "ammo")
        everyone = [medic, _enemy("scout"), _enemy("heavy"), _enemy("ammo")]
        assert _threats(medic, everyone, _sees_here(THERE, (6, 6))) == 2

    def test_it_is_the_enemy_seeing_the_player_not_the_other_way(self) -> None:
        medic = _placed("medic", HERE)
        _remember(medic, "blue_scout", THERE, NOW, "scout")
        one_way = _grid_map(sight={_key(HERE): {_key(THERE)}})
        assert _threats(medic, [medic, _enemy("scout")], one_way) == 0

    def test_a_stale_entry_does_not_count(self) -> None:
        medic = _placed("medic", HERE)
        too_old = NOW - time_constants.STALENESS_FAST_TICKS - 1
        _remember(medic, "blue_scout", THERE, too_old, "scout")
        assert _threats(medic, [medic, _enemy("scout")], _sees_here(THERE)) == 0

    def test_staleness_follows_the_time_domain(self) -> None:
        medic = _placed("medic", HERE)
        _remember(medic, "blue_scout", THERE, NOW - 20, "scout")
        everyone = [medic, _enemy("scout")]
        ctx = _sees_here(THERE)
        assert _threats(medic, everyone, ctx, time_domain="ticks") == 1
        assert _threats(medic, everyone, ctx, time_domain="seconds") == 0

    def test_an_eliminated_enemy_does_not_count(self) -> None:
        medic = _placed("medic", HERE)
        _remember(medic, "blue_scout", THERE, NOW, "scout")
        gone = _enemy("scout")
        gone.final_lives = 0
        assert _threats(medic, [medic, gone], _sees_here(THERE)) == 0

    def test_a_remembered_teammate_does_not_count(self) -> None:
        medic = _placed("medic", HERE)
        _remember(medic, "red_scout", THERE, NOW, "scout")
        mate = _player("scout", cell_row=THERE[0], cell_col=THERE[1])
        assert _threats(medic, [medic, mate], _sees_here(THERE)) == 0

    def test_it_reads_memory_never_the_true_cell(self) -> None:
        medic = _placed("medic", HERE)
        unknown = _enemy("scout")
        unknown.cell_row, unknown.cell_col = THERE
        assert _threats(medic, [medic, unknown], _sees_here(THERE)) == 0

        _remember(medic, "blue_scout", THERE, NOW, "scout")
        unknown.cell_row, unknown.cell_col = (9, 9)
        assert _threats(medic, [medic, unknown], _sees_here(THERE)) == 1

    def test_it_draws_no_rng(self) -> None:
        medic = _placed("medic", HERE)
        _remember(medic, "blue_scout", THERE, NOW, "scout")
        random.seed(11)
        before = random.getstate()
        _threats(medic, [medic, _enemy("scout")], _sees_here(THERE))
        assert random.getstate() == before


class TestPlanActionSetsKnownThreats:
    def test_a_map_round_sets_it(self) -> None:
        medic = _placed("medic", HERE)
        _remember(medic, "blue_scout", THERE, NOW, "scout")
        _plan(medic, _sees_here(THERE), _enemy("scout"))
        assert medic.known_threats == 1

    def test_a_map_round_with_none_known_sets_zero_not_none(self) -> None:
        medic = _placed("medic", HERE)
        _plan(medic, _grid_map())
        assert medic.known_threats == 0

    def test_a_map_less_round_leaves_it_none(self) -> None:
        medic = _placed("medic", HERE)
        _plan(medic, None)
        assert medic.known_threats is None


def _nuke_ready(**fields: Any) -> PlayerState:
    """A Commander whose nuke gate is open and whose stats leave the weights as they are."""
    return _placed(
        "commander",
        HERE,
        final_special=99,
        special_usage=50,
        game_awareness=50,
        decision_making=0,
        current_zone=1,
        **fields,
    )


class TestNukeWeight:
    def test_on_a_map_each_known_threat_takes_its_share(self, rolled: list) -> None:
        cmdr = _nuke_ready()
        _remember(cmdr, "blue_scout", THERE, NOW, "scout")
        _remember(cmdr, "blue_heavy", (6, 6), NOW, "heavy")
        enemies = [_enemy("scout"), _enemy("heavy")]
        _plan(cmdr, _sees_here(THERE, (6, 6)), *enemies)
        assert rolled[0]["use_special"] == 100 - 2 * 20

    def test_on_a_map_an_enemy_in_the_zone_it_does_not_know_costs_nothing(
        self, rolled: list
    ) -> None:
        cmdr = _nuke_ready()
        in_zone = _enemy("scout")
        in_zone.current_zone = cmdr.current_zone
        _plan(cmdr, _grid_map(), in_zone)
        assert rolled[0]["use_special"] == 100

    def test_a_map_less_round_keeps_the_zone_count(self, rolled: list) -> None:
        cmdr = _nuke_ready()
        in_zone = _enemy("scout")
        in_zone.current_zone = cmdr.current_zone
        _plan(cmdr, None, in_zone)
        assert rolled[0]["use_special"] == 100 - 20


def _hide_allowed(player: PlayerState, everyone: list, ctx: MapContext) -> bool:
    return modes.hide_allowed(player, everyone, ctx, NOW, "ticks")


def _on_goal(player: PlayerState) -> PlayerState:
    assert (player.cell_row, player.cell_col) == HERE
    player._committed_goal = (HERE, False, NOW + 4)
    return player


def _heading_off(player: PlayerState) -> PlayerState:
    player._committed_goal = ((9, 9), False, NOW + 4)
    return player


class TestHideAllowed:
    """``not needed and (threatened or settled)``.

    Measured with the support-survival switches off: on (the default since
    2026-11-06), ``support_survival.hide_gate_open`` lets a Medic or Ammo hide
    whatever its allies need, before these rules run (``test_support_survival``).
    """

    pytestmark = pytest.mark.usefixtures("switches_off")

    @pytest.mark.parametrize("role", ["medic", "ammo"])
    def test_settled_on_its_goal_and_not_needed_it_may_hide(self, role: str) -> None:
        support = _on_goal(_placed(role, HERE))
        assert _hide_allowed(support, [support, _full_ally()], _grid_map())

    def test_with_no_committed_goal_it_counts_as_settled(self) -> None:
        medic = _placed("medic", HERE)
        assert medic._committed_goal is None
        assert _hide_allowed(medic, [medic, _full_ally()], _grid_map())

    def test_heading_somewhere_with_nothing_on_it_it_may_not(self) -> None:
        medic = _heading_off(_placed("medic", HERE))
        assert not _hide_allowed(medic, [medic, _full_ally()], _grid_map())

    def test_threatened_it_may_hide_while_heading_somewhere(self) -> None:
        medic = _heading_off(_placed("medic", HERE))
        _remember(medic, "blue_scout", THERE, NOW, "scout")
        everyone = [medic, _full_ally(), _enemy("scout")]
        assert _hide_allowed(medic, everyone, _sees_here(THERE))

    def test_it_reads_the_count_plan_action_set_this_tick(self) -> None:
        # The count is not computed twice in a tick (CODING_STANDARDS).
        medic = _heading_off(_placed("medic", HERE))
        _remember(medic, "blue_scout", THERE, NOW, "scout")
        medic.known_threats = 0
        everyone = [medic, _full_ally(), _enemy("scout")]
        assert not _hide_allowed(medic, everyone, _sees_here(THERE))

    def test_needed_it_may_not_even_when_threatened_and_settled(self) -> None:
        medic = _on_goal(_placed("medic", HERE))
        _remember(medic, "blue_scout", THERE, NOW, "scout")
        everyone = [medic, _needy_ally(), _enemy("scout")]
        assert not _hide_allowed(medic, everyone, _sees_here(THERE))

    def test_an_ally_at_the_line_needs_it_and_one_above_does_not(self) -> None:
        medic = _on_goal(_placed("medic", HERE))
        ally = _player("heavy")
        ally.final_lives = int(ally.max_lives * modes.HIDE_NEEDED_SHARE)
        assert ally.final_lives / ally.max_lives <= modes.HIDE_NEEDED_SHARE
        assert not _hide_allowed(medic, [medic, ally], _grid_map())
        ally.final_lives += 1
        assert ally.final_lives / ally.max_lives > modes.HIDE_NEEDED_SHARE
        assert _hide_allowed(medic, [medic, ally], _grid_map())

    def test_the_hide_line_is_half_and_the_defense_line_stays_three_quarters(
        self,
    ) -> None:
        assert modes.HIDE_NEEDED_SHARE == 0.5
        assert DEFENSE_RESUPPLY_SHARE == 0.75

    def test_an_ally_between_the_lines_does_not_stop_a_hide(self) -> None:
        # At 75% a Defense support would still walk to this ally, but a Medic that
        # could never hide above 50% ended up eliminated in every round.
        medic = _on_goal(_placed("medic", HERE))
        ally = _player("heavy")
        ally.final_lives = int(ally.max_lives * 0.6)
        assert _hide_allowed(medic, [medic, ally], _grid_map())

    def test_a_medic_reads_lives_and_an_ammo_reads_shots(self) -> None:
        no_lives = _player("heavy")
        no_lives.final_lives = 1
        no_shots = _player("heavy")
        no_shots.final_shots = 1
        medic = _on_goal(_placed("medic", HERE))
        ammo = _on_goal(_placed("ammo", HERE))
        assert not _hide_allowed(medic, [medic, no_lives], _grid_map())
        assert _hide_allowed(medic, [medic, no_shots], _grid_map())
        assert not _hide_allowed(ammo, [ammo, no_shots], _grid_map())
        assert _hide_allowed(ammo, [ammo, no_lives], _grid_map())

    def test_its_own_need_does_not_count(self) -> None:
        medic = _on_goal(_placed("medic", HERE))
        medic.final_lives = 1
        assert _hide_allowed(medic, [medic, _full_ally()], _grid_map())

    def test_a_needy_enemy_does_not_count(self) -> None:
        medic = _on_goal(_placed("medic", HERE))
        enemy = _enemy("heavy")
        enemy.final_lives = 1
        assert _hide_allowed(medic, [medic, enemy], _grid_map())

    def test_an_eliminated_ally_does_not_count(self) -> None:
        medic = _on_goal(_placed("medic", HERE))
        gone = _player("heavy")
        gone.final_lives = 0
        assert _hide_allowed(medic, [medic, gone], _grid_map())

    def test_it_draws_no_rng(self) -> None:
        medic = _heading_off(_placed("medic", HERE))
        random.seed(11)
        before = random.getstate()
        _hide_allowed(medic, [medic, _needy_ally()], _grid_map())
        assert random.getstate() == before


def test_resupply_share_lives_in_modes() -> None:
    """``modes`` needs it for ``hide_allowed`` and must not import ``pathfinding``."""
    ally = _ally_at_share("heavy", 0.5)
    assert modes._resupply_share(ally, "medic") == ally.final_lives / ally.max_lives
    assert modes._resupply_share(ally, "ammo") == ally.final_shots / ally.max_shots


class TestHideSlot:
    """With the support-survival switches off (see ``TestHideAllowed``)."""

    pytestmark = pytest.mark.usefixtures("switches_off")

    def test_not_allowed_the_medic_slot_is_zero(self, rolled: list) -> None:
        medic = _placed("medic", HERE, decision_making=0)
        _plan(medic, _grid_map(), _needy_ally())
        assert rolled[0]["hide"] == 0

    def test_not_allowed_the_ammo_slot_is_zero(self, rolled: list) -> None:
        # An Ammo's only hide weight is its low-lives one, beside its Medic.
        ammo = _placed("ammo", HERE, decision_making=0, final_lives=1)
        _plan(ammo, _grid_map(), _player("medic"), _needy_ally())
        assert rolled[0]["hide"] == 0

    def test_allowed_a_low_ammo_keeps_its_slot(self, rolled: list) -> None:
        ammo = _placed("ammo", HERE, decision_making=0, final_lives=1)
        _plan(ammo, _grid_map(), _player("medic"), _full_ally())
        assert rolled[0]["hide"] == 30

    def test_allowed_the_medic_keeps_its_slot(self, rolled: list) -> None:
        medic = _placed("medic", HERE, decision_making=0)
        _plan(medic, _grid_map(), _full_ally())
        assert rolled[0]["hide"] == 30

    def test_a_map_less_round_keeps_the_slot(self, rolled: list) -> None:
        medic = _placed("medic", HERE, decision_making=0)
        _plan(medic, None, _needy_ally())
        assert rolled[0]["hide"] == 30

    def test_a_downed_support_keeps_its_slot(self, rolled: list) -> None:
        medic = _downed(_placed("medic", HERE, decision_making=0))
        heavy = _needy_ally()
        _plan(medic, _grid_map(), heavy)
        assert rolled[0]["hide"] > 0

    def test_a_non_support_keeps_its_hide_weight(self, rolled: list) -> None:
        def scout() -> PlayerState:
            low = _placed("scout", HERE, decision_making=0)
            low.final_lives = 1
            return low

        # A low Medic: a support would be needed, so this Scout must not be read as one.
        medic = _ally_at_share("medic", 0.25)
        _plan(scout(), _grid_map(), medic)
        _plan(scout(), None, medic)
        assert rolled[0]["hide"] == rolled[1]["hide"] > 0


class TestLeavingAHide:
    """With the support-survival switches off (see ``TestHideAllowed``)."""

    pytestmark = pytest.mark.usefixtures("switches_off")

    def test_an_active_support_not_allowed_leaves_on_any_roll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for action in ("resupply_ally", "only_move", "hide"):
            medic = _placed("medic", HERE, is_hiding=True)
            medic._committed_goal = (HERE, False, NOW + 4)
            _roll(monkeypatch, action)
            _plan(medic, _grid_map(), _needy_ally())
            assert medic.is_hiding is False, action
            assert medic._committed_goal is None, action

    def test_an_active_support_allowed_stays_on_a_resupply_roll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        medic = _on_goal(_placed("medic", HERE, is_hiding=True))
        _roll(monkeypatch, "resupply_ally")
        _plan(medic, _grid_map(), _full_ally())
        assert medic.is_hiding is True

    def test_an_active_support_allowed_stays_on_an_only_move_roll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        medic = _on_goal(_placed("medic", HERE, is_hiding=True))
        _roll(monkeypatch, "only_move")
        _plan(medic, _grid_map(), _full_ally())
        assert medic.is_hiding is True

    def test_leaving_calls_save_player(self, monkeypatch: pytest.MonkeyPatch) -> None:
        medic = _placed("medic", HERE, is_hiding=True)
        saved: list[PlayerState] = []
        _roll(monkeypatch, "resupply_ally")
        plan_action(
            medic,
            [medic, _needy_ally()],
            NOW,
            _grid_map(),
            save_player=saved.append,
            time_domain="ticks",
        )
        assert saved == [medic]

    def test_an_active_non_support_leaves_on_an_only_move_roll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scout = _placed("scout", HERE, is_hiding=True)
        scout._committed_goal = (HERE, False, NOW + 4)
        _roll(monkeypatch, "only_move")
        _plan(scout, _grid_map())
        assert scout.is_hiding is False
        assert scout._committed_goal is None

    def test_an_active_non_support_stays_on_a_hide_roll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scout = _placed("scout", HERE, is_hiding=True)
        _roll(monkeypatch, "hide")
        _plan(scout, _grid_map())
        assert scout.is_hiding is True

    @pytest.mark.parametrize(
        "role, action", [("scout", "only_move"), ("medic", "resupply_ally")]
    )
    def test_a_map_less_round_keeps_the_old_rule(
        self, role: str, action: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        player = _placed(role, HERE, is_hiding=True)
        _roll(monkeypatch, action)
        _plan(player, None, _needy_ally())
        assert player.is_hiding is True

    @pytest.mark.parametrize(
        "role, action", [("scout", "only_move"), ("medic", "resupply_ally")]
    )
    def test_a_downed_player_keeps_its_hide(
        self, role: str, action: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        player = _downed(_placed(role, HERE, is_hiding=True))
        _roll(monkeypatch, action)
        _plan(player, _grid_map(), _needy_ally())
        assert player.is_hiding is True

    def test_a_live_downed_option_keeps_its_hide_on_any_roll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        medic = _downed(_placed("medic", HERE, is_hiding=True))
        medic.downed_plan = object()
        _roll(monkeypatch, "request_resupply")
        _plan(medic, _grid_map(), _needy_ally())
        assert medic.is_hiding is True


class TestHoldCommitment:
    def test_a_hold_lasts_seven_ticks(self) -> None:
        # The spec's 6, retuned by round_probe to meet the Hold criteria (#550).
        assert time_constants.HOLD_TICKS == 7

    def test_a_hold_roll_sets_its_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        scout = _placed("scout", HERE)
        _roll(monkeypatch, "hold")
        assert _plan(scout, _grid_map()) == [{"type": "hold", "actor": scout}]
        assert scout.is_holding is True
        assert scout.hold_until == NOW + time_constants.HOLD_TICKS

    def _standing(self, **fields: Any) -> PlayerState:
        return _placed(
            "scout",
            HERE,
            is_holding=True,
            hold_until=NOW + 3,
            last_chosen_action="hold",
            **fields,
        )

    def test_a_standing_hold_consumes_no_rng(self) -> None:
        scout = self._standing(last_shot_time=NOW)
        scout.last_chosen_action = "tag_player"
        random.seed(11)
        before = random.getstate()
        plans = _plan(scout, _grid_map())
        assert random.getstate() == before
        assert plans == [{"type": "hold", "actor": scout}]
        assert scout.last_chosen_action == "hold"
        assert scout.is_holding is True
        assert scout.hold_until == NOW + 3

    def test_a_standing_hold_skips_the_weights(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("a standing Hold ran the weights")

        monkeypatch.setattr(combat, "_get_scout_weights", fail)
        _plan(self._standing(), _grid_map())

    def test_a_standing_hold_still_counts_stamina_checkpoints(self) -> None:
        # The shot hit roll reads the penalty, so it must not wait for the Hold to end.
        scout = self._standing(stamina=0)
        scout.hold_until = 901
        plan_action(scout, [scout], 900, _grid_map(), time_domain="ticks")
        assert scout.is_holding is True
        assert scout.stamina_penalty_count > 0

    def test_it_ends_at_its_length_not_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scout = _placed("scout", HERE)
        _roll(monkeypatch, "hold")
        plan_action(scout, [scout], NOW, _grid_map(), time_domain="ticks")
        draws: list[int] = []

        def draw(*_args: object, **_kwargs: object) -> list[str]:
            draws.append(1)
            return ["only_move"]

        monkeypatch.setattr(combat.random, "choices", draw)
        for offset in range(1, time_constants.HOLD_TICKS):
            tick = NOW + offset
            plan_action(scout, [scout], tick, _grid_map(), time_domain="ticks")
            assert not draws, offset
        scout._committed_goal = (HERE, False, NOW + 99)
        tick = NOW + time_constants.HOLD_TICKS
        plan_action(scout, [scout], tick, _grid_map(), time_domain="ticks")
        assert draws == [1]
        assert scout.is_holding is False
        assert scout.hold_until is None
        assert scout._committed_goal is None

    def test_rolling_hold_again_starts_a_new_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scout = self._standing()
        scout.hold_until = NOW
        _roll(monkeypatch, "hold")
        _plan(scout, _grid_map())
        assert scout.is_holding is True
        assert scout.hold_until == NOW + time_constants.HOLD_TICKS

    @pytest.mark.parametrize(
        "ending",
        [
            pytest.param({"last_downed_time": NOW - 1}, id="not-active"),
            pytest.param({"final_shots": 0}, id="out-of-shots"),
            pytest.param({"reacting_to_nuke": True}, id="nuke-reaction"),
        ],
    )
    def test_it_ends_early_and_the_tick_rolls(
        self, ending: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scout = self._standing(**ending)
        scout._committed_goal = (HERE, False, NOW + 99)
        _roll(monkeypatch, "request_resupply")
        _plan(scout, _grid_map())
        assert scout.is_holding is False
        assert scout.hold_until is None
        assert scout.last_chosen_action == "request_resupply"
        assert scout._committed_goal is None

    def test_a_down_clears_it(self) -> None:
        scout = self._standing()
        record_down(scout, NOW, ctx=None)
        assert scout.hold_until is None
        assert scout.is_holding is False

    def test_a_resupply_ends_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Resupplying restarts the Respawn cooldown, so the player is no longer Active.
        ammo = _placed("ammo", THERE)
        scout = self._standing()
        ctx = combat.RoundContext(movement_ctx=_grid_map())
        combat.attempt_resupply(ammo, scout, NOW - 1, ctx=ctx)
        assert not scout.is_active_at(NOW)
        _roll(monkeypatch, "request_resupply")
        _plan(scout, _grid_map())
        assert scout.is_holding is False
        assert scout.hold_until is None


class TestNotActiveCannotStartAHold:
    def test_on_a_map_the_slot_is_zero(self, rolled: list) -> None:
        scout = _downed(_placed("scout", HERE))
        _plan(scout, _grid_map())
        assert rolled[0]["hold"] == 0

    @pytest.mark.parametrize("state", [{"final_shots": 0}, {"reacting_to_nuke": True}])
    def test_a_hold_that_could_not_stand_gets_no_slot(
        self, state: dict, rolled: list
    ) -> None:
        # Such a Hold would end on its next tick, and hold the player still until then.
        scout = _placed("scout", HERE, **state)
        _plan(scout, _grid_map())
        assert rolled[0]["hold"] == 0

    @pytest.mark.parametrize("state", [{"final_shots": 0}, {"reacting_to_nuke": True}])
    def test_a_map_less_round_keeps_that_slot_too(
        self, state: dict, rolled: list
    ) -> None:
        scout = _placed("scout", HERE, **state)
        _plan(scout, None)
        assert rolled[0]["hold"] > 0

    def test_active_on_a_map_the_slot_is_not(self, rolled: list) -> None:
        scout = _placed("scout", HERE)
        _plan(scout, _grid_map())
        assert rolled[0]["hold"] > 0

    def test_a_map_less_round_keeps_the_slot(self, rolled: list) -> None:
        scout = _downed(_placed("scout", HERE))
        _plan(scout, None)
        assert rolled[0]["hold"] > 0


class TestMapLessHold:
    def test_it_sets_no_end_and_carries_over_until_another_roll(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scout = _placed("scout", HERE)
        _roll(monkeypatch, "hold")
        _plan(scout, None)
        assert scout.is_holding is True
        assert scout.hold_until is None
        _roll(monkeypatch, "only_move")
        plan_action(scout, [scout], NOW + 1, None, time_domain="ticks")
        assert scout.is_holding is False


def _role_weights(role: str, known_threats: int | None) -> list[int]:
    player = _placed(role, HERE, known_threats=known_threats)
    function = getattr(weights, f"_get_{role}_weights")
    return function(
        player, _ACTION_IDX, list(BASELINE_ACTION_WEIGHTS), [player], NOW, "ticks"
    )


class TestHoldBaselines:
    """A Hold lasts ``HOLD_TICKS``, so the map slot is far below what it was. The spec's
    2, 4, 2, 4, retuned by round_probe to meet the Hold criteria (#550)."""

    @pytest.mark.parametrize(
        "role, hold, source, source_weight",
        [
            ("commander", 3, "only_move", 15 - 3 - 15),
            ("heavy", 4, "only_move", 25 - 4 - 15),
            ("scout", 3, "only_move", 60 - 3),
            ("ammo", 3, "tag_player", 45 - 3),
            ("medic", 0, "tag_player", 15),  # CAL-02 D6: was 5
        ],
    )
    def test_on_a_map_the_difference_stays_in_the_source_slot(
        self, role: str, hold: int, source: str, source_weight: int
    ) -> None:
        w = _role_weights(role, known_threats=0)
        assert w[_ACTION_IDX["hold"]] == hold
        assert w[_ACTION_IDX[source]] == source_weight

    @pytest.mark.parametrize(
        "role, hold",
        [("commander", 10), ("heavy", 20), ("scout", 10), ("ammo", 20), ("medic", 0)],
    )
    def test_a_map_less_round_keeps_the_old_baseline(
        self, role: str, hold: int
    ) -> None:
        assert _role_weights(role, known_threats=None)[_ACTION_IDX["hold"]] == hold


def _fingerprint(players: list[PlayerState]) -> str:
    rows = [
        (
            p.tag_id,
            p.final_lives,
            p.final_shots,
            p.counters.tags_made,
            p.is_hiding,
            p.is_holding,
        )
        for p in sorted(players, key=lambda p: p.tag_id)
    ]
    return hashlib.sha256(repr((rows, random.random())).encode()).hexdigest()[:16]


@pytest.mark.django_db
class TestMapLessRoundsAreUnchanged:
    # Pinned from the commit before this ticket (bcb2ade4): every player's outcome and
    # the RNG state after the round, so any change to a map-less draw moves it.
    # Re-pinned 2026-11-06 when support_survival's target priority became the
    # default: choose_tag_target draws differently on every round, map or not
    # (the old values were 8b059b08209535f9 and fb03cd0de8c924ab). Re-pinned
    # again the same day for CAL #558: the Commander nuke gate opens at 20 SP for
    # every awareness and the Commander hides while the fuse runs, so a map-less
    # Commander rolls use_special and hide where it rolled tag_player (the values
    # before were 3e2b367fd8e4a387 and 1c0910ccaa48fd2b). Re-pinned 2026-11-07:
    # each nuke draws its SP bank from the real table (nuke_arming.banked_enough)
    # and rolls its resupply slip when the fuse starts (nuke_arming.fuse_lit), two
    # more draws per nuke, and the nuke weight is doubled just after a Down (the
    # values before were 41b1e498403fc1d7 and 5f99af49798143f2). Re-pinned
    # 2026-11-08 for CAL-02a: queued Reactions and Follow-ups now resolve on
    # their due tick, each a hit roll the old round never drew, and a Heavy's
    # planning gate counts its cooldown as 2 ticks, so a map-less Heavy rolls
    # tag_player on every other tick only (the values before were
    # 95ffd6649674d1d3 and e83dc90224030c38). Re-pinned 2026-11-08 for CAL-02b
    # (#565): the Medic's opening tag_player weight is 15 (was 5, weights._MEDIC),
    # so the map-less Medic's action roll draws differently from its first tick
    # (the values before were 4c638ab236715605 and 5f130a9800c7c5ac). Measured
    # per decision on the CAL-02b worktree: the Medic weight alone moves both
    # seeds; the defender-learns-attacker switch and the Attack table leave a
    # map-less round untouched, as spec #563 D4 requires. Re-pinned 2026-11-09
    # for MECH-21 (#584): a Lock spends nothing at its start, launches after 2
    # ticks (was 3), ends on the locker's shot or a Down, and a dodge no longer
    # spends the missile, so every map-less round with a missile redraws (the
    # values before were 1dc64a3eb8fe381c and d0bee54c85ccea54). Re-pinned
    # 2026-11-10 for CAL-03a (#589): the other support's Resupply lands inside the
    # double window although the first one's reset started, so map-less rounds
    # with a Medic and an Ammo resupplying one teammate together redraw (the
    # values before were 37d480f60120d7c6 and 0e47203824f659b1). Re-pinned
    # 2026-11-10 for CAL-03b (#590): HOLD (support_survival.hold_allows) keeps a
    # support's single back unless the other support sees the teammate (the zone
    # rule here), so map-less Resupplies redraw; with hold_allows forced True both
    # seeds replay the old values (83eb21969580daee and 7e3eee0498ef99ba).
    PINNED = {7: "d15c339ffa382116", 21: "f238b2457f36a9bf"}

    @pytest.mark.parametrize("seed", sorted(PINNED))
    def test_a_seeded_round_replays_as_it_did(self, seed: int) -> None:
        red, blue = _rosters("HHNoMap")
        random.seed(seed)
        _, red_players, blue_players = BatchSimulator()._simulate_round(red, blue)
        assert _fingerprint(red_players + blue_players) == self.PINNED[seed]


@pytest.mark.django_db
class TestSeededSyracuseRound:
    def test_a_hold_is_a_real_commitment(self) -> None:
        roles = run_probe(_syracuse(), [3]).roles
        for role in ("commander", "heavy", "scout", "ammo"):
            assert roles[role].hold_run >= 4, role
        assert roles["medic"].hold_share == 0.0
