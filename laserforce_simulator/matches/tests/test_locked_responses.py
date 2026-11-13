"""MECH-24b (spec #611, ticket #613): a locked player answers the Lock beyond
the Dodge.

An **Active** target shoots back at the locker most of the time its Shot is
ready (``SHOOT_BACK_SHARE``), a **Reset-window** target on a map hides more
(``LOCKED_HIDE_SHARE``) so it can walk out of the locker's sight, and a locked
Holder leaves its Hold and rolls. The boosts are shares of the non-dodge part of
the vector, so the rates hold whatever the role weights are and the Dodge share
from #612 still holds exactly after them.

Seam 1, ``plan_action(..., lockers=...)``, with in-memory ``PlayerState``s and
the Action roll's weights captured; every guard condition has its own fixture
that fails that condition alone.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from matches.sim_helpers.combat import _CHOICES, plan_action
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.weights import (
    DODGE_SHARE_ACTIVE,
    DODGE_SHARE_RESET,
    LOCKED_HIDE_SHARE,
    SHOOT_BACK_SHARE,
    apply_boost_share,
    apply_dodge_share,
)
from matches.tests.test_dodge import ROLES, T, _force_roll, _locker, _target
from matches.tests.test_weights import _make_player_state

_TAG, _MOVE, _HIDE, _DODGE = 0, 1, 2, 9


def _non_dodge_share(weights: list[float], idx: int) -> float:
    return weights[idx] / sum(weights[:9])


class TestApplyBoostShare:
    """A boosted slot ``x`` gets the weight that makes its share of the non-dodge
    vector ``p_x + boost * (1 - p_x)``; the other non-dodge slots keep their
    proportions."""

    def test_the_boosted_share(self) -> None:
        w = [70, 30, 0, 0, 0, 0, 0, 0, 0, 0]
        out = apply_boost_share(w, _HIDE, 0.35)
        assert _non_dodge_share(out, _HIDE) == pytest.approx(0.0 + 0.35 * 1.0)
        out = apply_boost_share(w, _TAG, 0.5)
        assert _non_dodge_share(out, _TAG) == pytest.approx(0.7 + 0.5 * 0.3)

    def test_the_other_slots_keep_their_proportions(self) -> None:
        w = [40, 30, 20, 10, 0, 0, 0, 0, 0, 0]
        out = apply_boost_share(w, _HIDE, 0.35)
        assert out[_TAG] / out[_MOVE] == pytest.approx(40 / 30)
        assert out[3] / out[_MOVE] == pytest.approx(10 / 30)
        assert w == [40, 30, 20, 10, 0, 0, 0, 0, 0, 0]  # a new list

    def test_the_dodge_slot_is_not_part_of_the_share(self) -> None:
        w = [40, 60, 0, 0, 0, 0, 0, 0, 0, 500]
        out = apply_boost_share(w, _TAG, 0.5)
        assert _non_dodge_share(out, _TAG) == pytest.approx(0.4 + 0.5 * 0.6)
        assert out[_DODGE] == 500

    def test_a_slot_holding_the_whole_vector_is_left_alone(self) -> None:
        w = [0, 0, 7, 0, 0, 0, 0, 0, 0, 0]
        assert apply_boost_share(w, _HIDE, 0.35) == [float(x) for x in w]

    def test_the_dodge_share_still_holds_exactly_after_the_boost(self) -> None:
        w = [15, 0, 30, 0, 0, 65, 0, 25, 0, 0]
        boosted = apply_boost_share(w, _TAG, 0.5)
        out = apply_dodge_share(boosted, DODGE_SHARE_ACTIVE)
        assert out[_DODGE] / sum(out) == pytest.approx(DODGE_SHARE_ACTIVE)
        p_tag = 15 / 135
        assert _non_dodge_share(out, _TAG) == pytest.approx(p_tag + 0.5 * (1 - p_tag))


# ---------------------------------------------------------------------------
# plan_action fixtures
# ---------------------------------------------------------------------------


def _captured(
    monkeypatch: pytest.MonkeyPatch,
    player: PlayerState,
    lockers: tuple,
    *,
    movement_ctx: Any = None,
    others: tuple = (),
    seed: int = 7,
) -> list[float]:
    """The vector plan_action hands its Action roll (the ``random.choices`` call
    over ``_CHOICES``)."""
    import matches.sim_helpers.combat as combat_mod

    seen: list[list[float]] = []
    real_choices = random.choices

    def _choices(population, weights=None, **kwargs):
        if population is _CHOICES and not seen:
            seen.append(list(weights))
        return real_choices(population, weights=weights, **kwargs)

    monkeypatch.setattr(combat_mod.random, "choices", _choices)
    random.seed(seed)
    plan_action(
        player,
        [player, *lockers, *others],
        T,
        movement_ctx,
        time_domain="ticks",
        lockers=lockers,
    )
    monkeypatch.undo()
    assert seen, "no Action roll"
    return seen[0]


def _assert_boosted(
    unlocked: list[float], locked: list[float], idx: int, boost: float
) -> None:
    """Slot *idx*'s non-dodge share is ``p + boost * (1 - p)`` and every other
    non-dodge slot keeps its share of the rest."""
    p = _non_dodge_share(unlocked, idx)
    assert _non_dodge_share(locked, idx) == pytest.approx(p + boost * (1 - p))
    rest_unlocked = sum(unlocked[:9]) - unlocked[idx]
    rest_locked = sum(locked[:9]) - locked[idx]
    for j in range(9):
        if j != idx:
            assert locked[j] / rest_locked == pytest.approx(unlocked[j] / rest_unlocked)


def _assert_not_boosted(unlocked: list[float], locked: list[float]) -> None:
    assert locked[:9] == pytest.approx(unlocked[:9])


class TestTheConstants:
    def test_the_response_shares(self) -> None:
        assert SHOOT_BACK_SHARE == 0.50
        assert LOCKED_HIDE_SHARE == 0.35


class TestShootBackShare:
    """An Active locked target whose Shot is ready (Active, Shots left, the
    cooldown passed) gets ``SHOOT_BACK_SHARE`` on ``tag_player``."""

    @pytest.mark.parametrize("role", ROLES)
    def test_an_active_target_with_its_shot_ready(
        self, monkeypatch: pytest.MonkeyPatch, role: str
    ) -> None:
        locker = _locker()
        unlocked = _captured(monkeypatch, _target(role, "active"), ())
        locked = _captured(monkeypatch, _target(role, "active"), (locker,))
        _assert_boosted(unlocked, locked, _TAG, SHOOT_BACK_SHARE)
        assert locked[_DODGE] / sum(locked) == pytest.approx(DODGE_SHARE_ACTIVE)

    def test_no_shots_left_no_boost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def player() -> PlayerState:
            p = _target("heavy", "active")
            p.final_shots = 0
            return p

        locker = _locker()
        unlocked = _captured(monkeypatch, player(), ())
        locked = _captured(monkeypatch, player(), (locker,))
        _assert_not_boosted(unlocked, locked)

    def test_cooldown_running_no_boost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def player() -> PlayerState:
            p = _target("heavy", "active")
            p.last_shot_time = T  # a Heavy's 2-tick cooldown runs
            return p

        locker = _locker()
        unlocked = _captured(monkeypatch, player(), ())
        locked = _captured(monkeypatch, player(), (locker,))
        _assert_not_boosted(unlocked, locked)

    def test_not_active_no_boost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Map-less, so the Reset-window hide boost is off too."""
        locker = _locker()
        unlocked = _captured(monkeypatch, _target("heavy", "reset"), ())
        locked = _captured(monkeypatch, _target("heavy", "reset"), (locker,))
        _assert_not_boosted(unlocked, locked)


# ---------------------------------------------------------------------------
# The shoot-back target (decision 11)
# ---------------------------------------------------------------------------


def _decoy() -> PlayerState:
    return _make_player_state(
        "commander", team_color="blue", tag_id="blue_commander", player_id=98
    )


def _tag_roll_with_decoy(
    monkeypatch: pytest.MonkeyPatch, player: PlayerState, locker: PlayerState
) -> tuple[list, list]:
    """Force a ``tag_player`` roll; ``choose_tag_target`` always picks the decoy
    and records each call. Returns the plans and the calls."""
    import matches.sim_helpers.combat as combat_mod

    decoy = _decoy()
    picks: list[PlayerState] = []

    def _choose(*args, **kwargs):
        picks.append(decoy)
        return decoy

    monkeypatch.setattr(combat_mod, "choose_tag_target", _choose)
    _force_roll(monkeypatch, "tag_player")
    plans = plan_action(
        player,
        [player, locker, decoy],
        T,
        time_domain="ticks",
        lockers=(locker,),
    )
    return plans, picks


def _targets(plans: list) -> list[str]:
    return [p["target"].tag_id for p in plans if p["type"] == "tag"]


class TestShootBackTarget:
    """A locked Active target's ``tag_player`` roll aims its first tag plan at
    ``lockers[0]``; a locker the Follow rule bans, or one not Taggable, falls
    back to ``choose_tag_target``."""

    def test_the_first_tag_plan_aims_at_the_first_locker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        player, locker = _target("heavy", "active"), _locker()
        plans, picks = _tag_roll_with_decoy(monkeypatch, player, locker)
        assert _targets(plans) == ["blue_heavy"]
        assert picks == []  # no pick drawn

    def test_not_locked_picks_as_today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import matches.sim_helpers.combat as combat_mod

        player, decoy = _target("heavy", "active"), _decoy()
        monkeypatch.setattr(combat_mod, "choose_tag_target", lambda *a, **k: decoy)
        _force_roll(monkeypatch, "tag_player")
        plans = plan_action(player, [player, decoy], T, time_domain="ticks")
        assert _targets(plans) == ["blue_commander"]

    def test_a_locker_the_follow_rule_bans_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import matches.sim_helpers.combat as combat_mod

        player, locker = _target("heavy", "active"), _locker()
        monkeypatch.setattr(
            combat_mod, "may_tag", lambda attacker, target, tick: target is not locker
        )
        plans, picks = _tag_roll_with_decoy(monkeypatch, player, locker)
        assert _targets(plans) == ["blue_commander"]
        assert len(picks) == 1

    def test_a_locker_not_taggable_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        player, locker = _target("heavy", "active"), _locker()
        locker.last_downed_time = T  # just Downed: not Taggable
        assert not locker.is_taggable_at(T)
        plans, picks = _tag_roll_with_decoy(monkeypatch, player, locker)
        assert _targets(plans) == ["blue_commander"]
        assert len(picks) == 1

    def test_a_rapid_fire_scouts_second_plan_picks_as_today(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        player, locker = _target("scout", "active"), _locker()
        player.special_active_until = T + 10
        plans, picks = _tag_roll_with_decoy(monkeypatch, player, locker)
        assert _targets(plans) == ["blue_heavy", "blue_commander"]
        assert len(picks) == 1


# ---------------------------------------------------------------------------
# The locked hide (decisions 12, 20)
# ---------------------------------------------------------------------------


def _on_syracuse(player: PlayerState, locker: PlayerState) -> Any:
    from matches.tests.test_dodge import _syracuse_ctx

    movement_ctx = _syracuse_ctx()
    player.cell_row, player.cell_col = movement_ctx["spawn_cells"]["red"]
    locker.cell_row, locker.cell_col = movement_ctx["spawn_cells"]["blue"]
    return movement_ctx


@pytest.mark.django_db
class TestLockedHide:
    """A locked Reset-window target on a map round gets ``LOCKED_HIDE_SHARE``
    on ``hide``; map-less, ``hide`` is unchanged."""

    @pytest.mark.parametrize("role", ROLES)
    def test_a_reset_window_target_on_a_map(
        self, monkeypatch: pytest.MonkeyPatch, role: str
    ) -> None:
        def captured(lockers: tuple) -> list[float]:
            player, locker = _target(role, "reset"), _locker()
            movement_ctx = _on_syracuse(player, locker)
            return _captured(
                monkeypatch,
                player,
                (locker,) if lockers else (),
                movement_ctx=movement_ctx,
                others=() if lockers else (locker,),
            )

        unlocked, locked = captured(()), captured((1,))
        _assert_boosted(unlocked, locked, _HIDE, LOCKED_HIDE_SHARE)
        assert locked[_DODGE] / sum(locked) == pytest.approx(DODGE_SHARE_RESET)

    def test_map_less_hide_is_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        locker = _locker()
        unlocked = _captured(monkeypatch, _target("heavy", "reset"), ())
        locked = _captured(monkeypatch, _target("heavy", "reset"), (locker,))
        assert locked[_HIDE] == unlocked[_HIDE]
        _assert_not_boosted(unlocked, locked)

    def test_an_active_target_on_a_map_gets_no_hide_boost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Its cooldown runs, so the shoot-back is off too: nothing is boosted."""

        def captured(locked: bool) -> list[float]:
            player, locker = _target("heavy", "active"), _locker()
            player.last_shot_time = T
            movement_ctx = _on_syracuse(player, locker)
            return _captured(
                monkeypatch,
                player,
                (locker,) if locked else (),
                movement_ctx=movement_ctx,
                others=() if locked else (locker,),
            )

        _assert_not_boosted(captured(False), captured(True))

    def test_the_locked_hide_is_the_existing_hide_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        player, locker = _target("heavy", "reset"), _locker()
        movement_ctx = _on_syracuse(player, locker)
        _force_roll(monkeypatch, "hide")
        plans = plan_action(
            player,
            [player, locker],
            T,
            movement_ctx,
            time_domain="ticks",
            lockers=(locker,),
        )
        assert plans == [{"type": "hide", "actor": player}]


# ---------------------------------------------------------------------------
# The Hold break (decisions 7, 13)
# ---------------------------------------------------------------------------


def _holder() -> PlayerState:
    p = _target("heavy", "active")
    p.is_holding = True
    p.hold_until = T + 5
    return p


def _count_rolls(monkeypatch: pytest.MonkeyPatch, action: str) -> list[int]:
    """Force the Action roll to *action* and count ``random.choices`` calls."""
    import matches.sim_helpers.combat as combat_mod

    calls: list[int] = []

    def _choices(population, weights=None, **kwargs):
        calls.append(1)
        return [action]

    monkeypatch.setattr(combat_mod.random, "choices", _choices)
    return calls


class TestHoldBreak:
    def test_a_locked_holder_rolls_and_leaves_its_hold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        player, locker = _holder(), _locker()
        calls = _count_rolls(monkeypatch, "dodge")
        plans = plan_action(
            player, [player, locker], T, time_domain="ticks", lockers=(locker,)
        )
        assert calls == [1]
        assert plans == [{"type": "dodge", "actor": player}]
        assert player.is_holding is False
        assert player.hold_until is None

    def test_an_unlocked_holder_keeps_its_hold_and_draws_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        player, locker = _holder(), _locker()
        calls = _count_rolls(monkeypatch, "dodge")
        plans = plan_action(player, [player, locker], T, time_domain="ticks")
        assert calls == []
        assert plans == [{"type": "hold", "actor": player}]
        assert player.is_holding is True
        assert player.hold_until == T + 5
