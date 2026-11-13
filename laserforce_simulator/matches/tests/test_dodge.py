"""MECH-24a (spec #611, ticket #612): the Dodge Action and flexibility.

A player who is the target of a live Lock can choose the **Dodge**: it stands in
place, rolls once per Lock on it at ``flexibility / 2`` percent, and each success
ends that Lock with a ``lock_cancelled`` row (reason ``dodge``) and nothing spent.
The passive dodge roll at the launch is gone. A player who is not locked plays
exactly as before (the ``dodge`` slot is a trailing zero).

Seam 1 is ``plan_action(..., lockers=...)``; seam 2 is the tick loop (the
``dodge`` dispatch branch through ``resolve_dodge``, the Stationary check and the
launch). RNG is pinned with ``unittest.mock.patch``; in-memory ``PlayerState``s,
no DB.
"""

from __future__ import annotations

import random
from typing import Any
from unittest.mock import patch

import pytest

from matches.sim_helpers.combat import (
    _ACTION_IDX,
    _CHOICES,
    _visit_choice,
    cancel_missile_lock,
    plan_action,
    resolve_dodge,
    start_missile_lock,
)
from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.round_context import RoundContext
from matches.sim_helpers.shot import SHOT_KIND_INITIAL, resolve_shot
from matches.sim_helpers.time_constants import LOCK_TICKS, NOT_TARGETABLE_TICKS
from matches.sim_helpers.weights import (
    BASELINE_ACTION_WEIGHTS,
    DODGE_SHARE_ACTIVE,
    DODGE_SHARE_RESET,
)
from matches.simulation import BatchSimulator
from matches.tests.test_weights import (
    _EDGE_STATES,
    _ROLE_WEIGHT_FN,
    _make_player_state,
)

ROLES = ["medic", "ammo", "scout", "heavy", "commander"]
_STATES: dict[str, dict[str, Any]] = dict(_EDGE_STATES)  # type: ignore[arg-type]


class TestDodgeSlot:
    def test_dodge_is_the_tenth_slot_appended_last(self) -> None:
        assert _ACTION_IDX["dodge"] == 9
        assert _CHOICES[-1] == "dodge"
        assert len(_CHOICES) == 10
        assert _CHOICES.index("dodge") == _ACTION_IDX["dodge"]

    def test_baseline_dodge_weight_is_zero(self) -> None:
        assert len(BASELINE_ACTION_WEIGHTS) == 10
        assert BASELINE_ACTION_WEIGHTS[9] == 0

    @pytest.mark.parametrize("role", ROLES)
    @pytest.mark.parametrize("state_name", list(_EDGE_STATES))
    def test_every_role_weight_function_leaves_dodge_zero(
        self, role: str, state_name: str
    ) -> None:
        player = _make_player_state(role, **_STATES[state_name])
        enemy = _make_player_state("scout", team_color="blue", tag_id="blue_scout")
        raw = _ROLE_WEIGHT_FN[role](
            player, _ACTION_IDX, list(BASELINE_ACTION_WEIGHTS), [player, enemy], 1.0
        )
        assert len(raw) == 10
        assert raw[_ACTION_IDX["dodge"]] == 0


class TestNotLockedReplaysTheNineSlotDraw:
    """Decision 8, the zero-slot invariant: with ``lockers`` empty the vector
    handed to ``random.choices`` has ``dodge`` 0, and the choice under a fixed
    draw sequence is the one the 9-slot vector (the same weights without the
    trailing slot) draws, with the RNG left at the same point."""

    @pytest.mark.parametrize("role", ROLES)
    @pytest.mark.parametrize("state_name", list(_EDGE_STATES))
    def test_choice_equals_the_nine_slot_choice(
        self, monkeypatch: pytest.MonkeyPatch, role: str, state_name: str
    ) -> None:
        import matches.sim_helpers.combat as combat_mod

        seen: list[list[float]] = []
        states: list[object] = []
        real_choices = random.choices

        def _choices(population, weights=None, **kwargs):
            # The first call is the Action roll; keep the RNG state it drew from.
            if not seen:
                seen.append(list(weights))
                states.append(random.getstate())
            return real_choices(population, weights=weights, **kwargs)

        monkeypatch.setattr(combat_mod.random, "choices", _choices)
        random.seed(42)
        player = _make_player_state(role, **_STATES[state_name])
        plan_action(player, [player], 1.0)
        chosen = player.last_chosen_action
        monkeypatch.undo()

        weights = seen[0]
        assert len(weights) == 10
        assert weights[_ACTION_IDX["dodge"]] == 0
        random.setstate(states[0])  # type: ignore[arg-type]
        nine = random.choices(_CHOICES[:9], weights[:9])[0]
        assert nine == chosen


# ---------------------------------------------------------------------------
# Seam 1: plan_action(..., lockers=...)
# ---------------------------------------------------------------------------

T = 200  # the planning tick of every seam-1 fixture


def _target(role: str, state: str) -> PlayerState:
    """A red *role* at tick T: ``"active"`` or ``"reset"`` (the Reset window:
    Taggable, not Active)."""
    kwargs: dict = {}
    if state == "reset":
        kwargs["last_downed_time"] = T - NOT_TARGETABLE_TICKS
    p = _make_player_state(role, **kwargs)
    assert p.is_taggable_at(T)
    assert p.is_active_at(T) == (state == "active")
    return p


def _locker() -> PlayerState:
    return _make_player_state(
        "heavy", team_color="blue", tag_id="blue_heavy", player_id=99
    )


def _captured_weights(
    monkeypatch: pytest.MonkeyPatch, player: PlayerState, lockers: tuple, seed: int
) -> list[float]:
    """The vector plan_action hands its Action roll, with *lockers*."""
    import matches.sim_helpers.combat as combat_mod

    seen: list[list[float]] = []
    real_choices = random.choices

    def _choices(population, weights=None, **kwargs):
        if not seen:
            seen.append(list(weights))
        return real_choices(population, weights=weights, **kwargs)

    monkeypatch.setattr(combat_mod.random, "choices", _choices)
    random.seed(seed)
    plan_action(player, [player, *lockers], T, time_domain="ticks", lockers=lockers)
    monkeypatch.undo()
    return seen[0]


class TestDodgeShare:
    """With ``lockers`` non-empty the dodge weight is ``S * share / (1 - share)``
    (``S`` the total of the other slots after ``finalize_action_weights``), so the
    dodge is picked with probability ``share``: ``DODGE_SHARE_RESET`` for a target
    in the Reset window, ``DODGE_SHARE_ACTIVE`` for an Active one."""

    def test_the_constants(self) -> None:
        assert DODGE_SHARE_RESET == 0.40
        assert DODGE_SHARE_ACTIVE == 0.13

    @pytest.mark.parametrize("role", ROLES)
    @pytest.mark.parametrize(
        "state,share", [("reset", DODGE_SHARE_RESET), ("active", DODGE_SHARE_ACTIVE)]
    )
    def test_dodge_share_of_the_final_vector(
        self, monkeypatch: pytest.MonkeyPatch, role: str, state: str, share: float
    ) -> None:
        locker = _locker()
        unlocked = _captured_weights(monkeypatch, _target(role, state), (), 7)
        locked = _captured_weights(monkeypatch, _target(role, state), (locker,), 7)

        total = sum(locked[:9])
        assert total > 0
        assert locked[9] == pytest.approx(total * share / (1 - share))
        assert locked[9] / sum(locked) == pytest.approx(share)
        assert unlocked[9] == 0
        if state == "reset":
            # Map-less, a Reset-window target gets no other boost (MECH-24b's
            # shoot-back is for an Active one, its locked hide map rounds only).
            assert locked[:9] == unlocked[:9]


def _force_roll(monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    """Force plan_action's Action roll (its first ``random.choices``) to *action*."""
    import matches.sim_helpers.combat as combat_mod

    calls: list[int] = []
    real_choices = random.choices

    def _choices(population, weights=None, **kwargs):
        if calls:
            return real_choices(population, weights=weights, **kwargs)
        calls.append(1)
        return [action]

    monkeypatch.setattr(combat_mod.random, "choices", _choices)


class TestDodgeRoll:
    @pytest.mark.parametrize("state", ["reset", "active"])
    def test_a_dodge_roll_returns_exactly_one_dodge_plan(
        self, monkeypatch: pytest.MonkeyPatch, state: str
    ) -> None:
        player = _target("heavy", state)
        locker = _locker()
        _force_roll(monkeypatch, "dodge")
        plans = plan_action(
            player, [player, locker], T, time_domain="ticks", lockers=(locker,)
        )
        assert plans == [{"type": "dodge", "actor": player}]
        assert player.last_chosen_action == "dodge"

    def test_a_dodge_roll_leaves_a_hide(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The existing hide-exit rule: any roll other than hide, only_move or
        resupply_ally ends a hide."""
        player = _target("heavy", "active")
        player.is_hiding = True
        player._committed_goal = ((1, 1), False, 0)
        locker = _locker()
        _force_roll(monkeypatch, "dodge")
        plan_action(player, [player, locker], T, time_domain="ticks", lockers=(locker,))
        assert player.is_hiding is False
        assert player._committed_goal is None

    @pytest.mark.parametrize("visit", ["hide", "move", "request"])
    def test_the_visit_step_keeps_a_dodge(self, visit: str) -> None:
        player = _target("heavy", "active")
        assert player.is_resupplyable_at(T)
        assert _visit_choice(visit, "dodge", player, T) == "dodge"  # type: ignore[arg-type]

    def test_the_one_to_three_lives_call_out_keeps_a_dodge(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import matches.sim_helpers.combat as combat_mod

        player = _target("heavy", "reset")
        locker = _locker()
        monkeypatch.setattr(combat_mod.resupply_visit, "call_out", lambda *a, **k: True)
        _force_roll(monkeypatch, "dodge")
        plans = plan_action(
            player, [player, locker], T, time_domain="ticks", lockers=(locker,)
        )
        assert player.last_chosen_action == "dodge"
        assert plans == [{"type": "dodge", "actor": player}]

    def test_the_call_out_still_turns_another_roll_into_hide(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control for the fixture above: the patched call-out does fire."""
        import matches.sim_helpers.combat as combat_mod

        player = _target("heavy", "reset")
        locker = _locker()
        monkeypatch.setattr(combat_mod.resupply_visit, "call_out", lambda *a, **k: True)
        _force_roll(monkeypatch, "only_move")
        plan_action(player, [player, locker], T, time_domain="ticks", lockers=(locker,))
        assert player.last_chosen_action == "hide"


# ---------------------------------------------------------------------------
# Seam 2: the tick loop
# ---------------------------------------------------------------------------


def _syracuse_ctx() -> Any:
    from pathlib import Path

    from matches.lfstats_calibration import load_map_fixture
    from matches.sim_helpers.map_loader import load_map_context

    fixture = Path(__file__).resolve().parents[3] / "data/maps/syracuse.json"
    return load_map_context(load_map_fixture(fixture))[0]


class _LockerRecorder(BatchSimulator):
    """Records, for every plan, the ``lockers`` the tick loop passed and the
    Locks on that player in ``ctx.missile_locks`` at that moment."""

    def __init__(self) -> None:
        super().__init__()
        self.ctx: RoundContext | None = None
        self.passed: list[tuple] = []
        self.expected: list[list] = []

    def _advance_missile_locks(self, tick: int, ctx: RoundContext) -> None:
        self.ctx = ctx
        super()._advance_missile_locks(tick, ctx)

    def _plan_action(self, player, all_alive, tick, movement_ctx=None, lockers=()):
        assert self.ctx is not None
        self.passed.append(tuple(lockers))
        self.expected.append(
            [
                lock.attacker
                for lock in self.ctx.missile_locks.values()
                if lock.defender is player
            ]
        )
        return super()._plan_action(
            player, all_alive, tick, movement_ctx, lockers=lockers
        )


@pytest.mark.django_db
class TestTheLoopPassesLockers:
    def test_every_plan_gets_the_lockers_of_the_live_locks_on_it(self) -> None:
        from matches.round_probe import _default_roster

        sim = _LockerRecorder()
        random.seed(3)
        sim._simulate_round(
            _default_roster("red"),
            _default_roster("blue"),
            event_log=[],
            movement_ctx=_syracuse_ctx(),
        )
        assert [list(p) for p in sim.passed] == sim.expected
        assert sum(1 for p in sim.passed if p) > 10  # Locks happened

    def test_plan_action_without_lockers_plans_as_before(self) -> None:
        """Every other caller passes nothing: the default is no Lock."""
        player = _target("heavy", "active")
        random.seed(1)
        BatchSimulator()._plan_action(player, [player], T)
        assert player.last_chosen_action != "dodge"


# --- resolve_dodge: one roll per live Lock on the dodger ---------------------


def _lock_ps(
    player_id: int,
    *,
    team_color: str = "red",
    role: str = "heavy",
    flexibility: int = 50,
    survival: int = 50,
) -> PlayerState:
    """A player for a map-less Lock (sight is the same ``current_zone``)."""
    p = PlayerState(
        tag_id=f"{team_color}_{role}_{player_id}",
        name=f"P{player_id}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=survival,
        flexibility=flexibility,
        starting_lives=10,
        starting_shots=30,
        final_lives=10,
        final_shots=30,
        final_missiles=5,
        player_id=player_id,
    )
    p.shields = p.max_shields
    return p


def _lock_ctx(*players: PlayerState) -> RoundContext:
    return RoundContext(events=EventLog(persist=True), all_alive=list(players))


def _cancels(ctx: RoundContext) -> list[tuple[int, int, int, str]]:
    return [
        (e["timestamp"], e["actor_id"], e["target_id"], e["metadata"]["reason"])
        for e in ctx.events.entries
        if e["event_type"] == "lock_cancelled"
    ]


def _draws(*values: float) -> tuple[Any, list[float]]:
    """Patch ``random.random`` to return *values* in order, recording each."""
    drawn: list[float] = []
    queue = list(values)

    def _next() -> float:
        drawn.append(queue[len(drawn)])
        return drawn[-1]

    return patch("random.random", side_effect=_next), drawn


def _one_lock(flexibility: int = 50, survival: int = 50):
    locker = _lock_ps(1)
    target = _lock_ps(
        2, team_color="blue", role="scout", flexibility=flexibility, survival=survival
    )
    ctx = _lock_ctx(locker, target)
    assert start_missile_lock(locker, target, T, None, ctx=ctx) is not None
    return locker, target, ctx


class TestResolveDodge:
    def test_a_success_cancels_the_lock_as_dodge_with_nothing_spent(self) -> None:
        locker, target, ctx = _one_lock()
        patcher, drawn = _draws(0.0)
        with patcher:
            assert resolve_dodge(target, ctx, T + 1) == [True]
        assert _cancels(ctx) == [(T + 1, 1, 2, "dodge")]
        assert ctx.missile_locks == {}
        assert locker.final_missiles == 5
        assert drawn == [0.0]

    def test_a_failure_writes_nothing_and_the_lock_holds(self) -> None:
        locker, target, ctx = _one_lock()
        lock = ctx.missile_locks[locker.player_id]
        before = list(ctx.events.entries)
        patcher, _ = _draws(0.99)
        with patcher:
            assert resolve_dodge(target, ctx, T + 1) == [False]
        assert ctx.events.entries == before
        assert ctx.missile_locks[locker.player_id] is lock

    def test_a_player_with_no_lock_on_it_rolls_nothing(self) -> None:
        locker, target, ctx = _one_lock()
        patcher, drawn = _draws()
        with patcher:
            assert resolve_dodge(locker, ctx, T + 1) == []
        assert drawn == []

    @pytest.mark.parametrize(
        "flexibility,draw,dodged",
        [
            (50, 0.249, True),
            (50, 0.25, False),
            (80, 0.399, True),
            (80, 0.40, False),
        ],
    )
    def test_the_chance_is_flexibility_over_two_percent(
        self, flexibility: int, draw: float, dodged: bool
    ) -> None:
        _, target, ctx = _one_lock(flexibility=flexibility)
        patcher, _ = _draws(draw)
        with patcher:
            assert resolve_dodge(target, ctx, T + 1) == [dodged]

    def test_survival_does_not_set_the_dodge(self) -> None:
        """Survival 100 with flexibility 0: even a draw of 0.0 never dodges."""
        _, target, ctx = _one_lock(flexibility=0, survival=100)
        patcher, _ = _draws(0.0)
        with patcher:
            assert resolve_dodge(target, ctx, T + 1) == [False]
        assert _cancels(ctx) == []


class TestTwoLocksOnOneTarget:
    def _two(self):
        a = _lock_ps(1)
        b = _lock_ps(3, role="commander")
        target = _lock_ps(2, team_color="blue", role="scout")
        ctx = _lock_ctx(a, b, target)
        start_missile_lock(a, target, T, None, ctx=ctx)
        start_missile_lock(b, target, T, None, ctx=ctx)
        return a, b, target, ctx

    def test_success_then_failure_ends_the_first(self) -> None:
        a, b, target, ctx = self._two()
        patcher, drawn = _draws(0.0, 0.99)
        with patcher:
            assert resolve_dodge(target, ctx, T + 1) == [True, False]
        assert _cancels(ctx) == [(T + 1, 1, 2, "dodge")]
        assert list(ctx.missile_locks) == [b.player_id]
        assert len(drawn) == 2

    def test_failure_then_success_ends_the_second(self) -> None:
        a, b, target, ctx = self._two()
        patcher, drawn = _draws(0.99, 0.0)
        with patcher:
            assert resolve_dodge(target, ctx, T + 1) == [False, True]
        assert _cancels(ctx) == [(T + 1, 3, 2, "dodge")]
        assert list(ctx.missile_locks) == [a.player_id]
        assert len(drawn) == 2

    def test_a_lock_already_ended_in_the_dispatch_is_not_rolled(self) -> None:
        a, b, target, ctx = self._two()
        cancel_missile_lock(ctx, a, T + 1, "shot")  # a's trigger pull, earlier
        patcher, drawn = _draws(0.0)
        with patcher:
            assert resolve_dodge(target, ctx, T + 1) == [True]
        assert drawn == [0.0]  # one roll: the cancelled Lock was skipped
        assert _cancels(ctx) == [(T + 1, 1, 2, "shot"), (T + 1, 3, 2, "dodge")]


# --- the dispatch, the Stationary check and the launch, in the tick loop -----


def _scripted_round(
    dodge_ticks: tuple[int, ...], draw: float, ticks: int = 16
) -> tuple[list[dict], dict[tuple[int, str], tuple]]:
    """A map-less round: red's Heavy Locks blue's Scout at tick 10 and the Scout
    plans a Dodge on each of *dodge_ticks*; nobody else plans anything. Every
    ``random.random`` draw is *draw*. Returns the rows and the ``lockers`` each
    plan got."""
    from matches.tests.test_batch_sim import _roster_player

    lockers_seen: dict[tuple[int, str], tuple] = {}

    def plan(self, player, all_alive, tick, movement_ctx=None, lockers=()):
        red = next(p for p in all_alive if p.team_color == "red")
        blue = next(p for p in all_alive if p.team_color == "blue")
        blue.current_zone = red.current_zone  # map-less sight: the same zone
        lockers_seen[(tick, player.team_color)] = tuple(lockers)
        if tick == 10 and player is red:
            return [{"type": "missile", "actor": red, "target": blue}]
        if tick in dodge_ticks and player is blue:
            player.last_chosen_action = "dodge"
            return [{"type": "dodge", "actor": blue}]
        return []

    log: list[dict] = []
    with (
        patch.object(BatchSimulator, "ROUND_TICKS", ticks),
        patch.object(BatchSimulator, "_plan_action", plan),
        patch("random.random", return_value=draw),
    ):
        BatchSimulator()._simulate_round(
            [_roster_player("red", "heavy")],
            [_roster_player("blue", "scout")],
            event_log=log,
        )
    return log, lockers_seen


def _lock_rows(log: list[dict]) -> list[tuple[str, int, str | None]]:
    return [
        (e["event_type"], e["timestamp"], (e.get("metadata") or {}).get("reason"))
        for e in log
        if e["event_type"] in ("locking", "missiled", "lock_cancelled")
    ]


class TestDodgeDispatch:
    def test_a_success_ends_the_lock_at_the_dodge_tick(self) -> None:
        log, lockers = _scripted_round((11,), draw=0.0)
        assert lockers[(11, "blue")] != ()
        assert _lock_rows(log) == [
            ("locking", 10, None),
            ("lock_cancelled", 11, "dodge"),
        ]

    def test_a_failure_writes_nothing_and_the_lock_launches(self) -> None:
        log, _ = _scripted_round((11,), draw=0.99)
        assert _lock_rows(log) == [
            ("locking", 10, None),
            ("missiled", 10 + LOCK_TICKS, None),
        ]

    def test_no_dodge_plan_no_dodge(self) -> None:
        """Control: a draw of 0.0 cancels only through a Dodge plan."""
        log, _ = _scripted_round((), draw=0.0)
        assert _lock_rows(log) == [
            ("locking", 10, None),
            ("missiled", 10 + LOCK_TICKS, None),
        ]


class TestLaunchDrawsNoRng:
    """Decision 2: the passive dodge roll at the launch is gone. On ``launch``,
    after the gates, the Lock is removed and the missile lands; no draw."""

    def test_a_launch_draws_nothing_and_lands(self) -> None:
        locker, target, ctx = _one_lock()
        sim = BatchSimulator()
        sim._advance_missile_locks(T + 1, ctx)  # pending, in sight: no draw
        with patch("random.random", side_effect=AssertionError("RNG drawn")):
            sim._advance_missile_locks(T + LOCK_TICKS, ctx)
        missiled = [e for e in ctx.events.entries if e["event_type"] == "missiled"]
        assert [e["timestamp"] for e in missiled] == [T + LOCK_TICKS]
        assert ctx.missile_locks == {}
        assert _cancels(ctx) == []


@pytest.mark.django_db
class TestTheDodgerIsStationary:
    def _on_syracuse(self, chosen: str) -> tuple[PlayerState, Any]:
        movement_ctx = _syracuse_ctx()
        player = _make_player_state("scout")
        player.cell_row, player.cell_col = movement_ctx["spawn_cells"]["red"]
        player.last_chosen_action = chosen
        return player, movement_ctx

    def test_a_dodger_does_not_advance(self) -> None:
        player, movement_ctx = self._on_syracuse("dodge")
        before = (player.cell_row, player.cell_col)
        random.seed(5)
        BatchSimulator()._advance_player(player, [player], T, movement_ctx, "")
        assert (player.cell_row, player.cell_col) == before

    def test_control_the_same_player_otherwise_advances(self) -> None:
        player, movement_ctx = self._on_syracuse("tag_player")
        before = (player.cell_row, player.cell_col)
        random.seed(5)
        BatchSimulator()._advance_player(player, [player], T, movement_ctx, "")
        assert (player.cell_row, player.cell_col) != before

    def test_a_dodger_can_still_be_tagged(self) -> None:
        """No change to ``shot.py``: the dodger is hit at the normal chance."""
        dodger = _lock_ps(2, team_color="blue", role="scout")
        dodger.last_chosen_action = "dodge"
        shooter = _lock_ps(1)
        ctx = _lock_ctx(shooter, dodger)
        lives = dodger.final_lives
        with (
            patch("random.randint", return_value=1),
            patch("random.random", return_value=0.0),
        ):
            resolve_shot(shooter, dodger, T, kind=SHOT_KIND_INITIAL, ctx=ctx)
        hits = [e for e in ctx.events.entries if e["event_type"] == "tag"]
        assert [e["target_id"] for e in hits] == [2]
        assert dodger.final_lives < lives  # a 1-shield Scout is Downed


# ---------------------------------------------------------------------------
# The stat: flexibility (decision 3)
# ---------------------------------------------------------------------------


class TestFlexibilityStat:
    def test_flexibility_is_a_simulation_stat(self) -> None:
        from matches.simulation.entrypoints import _SIMULATION_STATS

        assert "flexibility" in _SIMULATION_STATS
        assert len(_SIMULATION_STATS) == 15

    def test_player_state_defaults_flexibility_to_50(self) -> None:
        assert _make_player_state("scout").flexibility == 50

    def test_make_players_bakes_flexibility(self) -> None:
        from matches.simulation.entrypoints import _SIMULATION_STATS, _PlayerData

        stats = {s: 50 for s in _SIMULATION_STATS}
        stats["flexibility"] = 73
        (state,) = BatchSimulator()._make_players(
            [("scout", _PlayerData(1, "p", stats))], "red"
        )
        assert state.flexibility == 73

    def test_a_pre_mech_24_snapshot_still_loads(self) -> None:
        """A roster snapshot saved before MECH-24 has no ``flexibility``; a
        GEN-01 re-simulation fills the default 50 instead of raising."""
        from matches.simulation.entrypoints import (
            _SIMULATION_STATS,
            _roster_from_snapshot,
        )

        old = {s: 60 for s in _SIMULATION_STATS if s != "flexibility"}
        ((role, data),) = _roster_from_snapshot(
            [{"player_id": 1, "name": "x", "role": "scout", "stats": old}]
        )
        (state,) = BatchSimulator()._make_players([(role, data)], "red")
        assert state.flexibility == 50
        assert state.accuracy == 60


@pytest.mark.django_db
class TestPlayerRoundStateFlexibility:
    def test_forwards_flexibility_like_survival(self) -> None:
        from matches.models import GameRound, PlayerRoundState
        from matches.tests.conftest import make_team_with_slots

        team, players = make_team_with_slots("F")
        team2, _ = make_team_with_slots("F2")
        player = players["scout"]
        player.flexibility = 81
        player.save()
        gr = GameRound.objects.create(team_red=team, team_blue=team2, round_number=1)
        prs = PlayerRoundState.objects.create(
            game_round=gr,
            player=player,
            role="scout",
            team_color="red",
            final_lives=15,
            final_shots=30,
        )
        assert prs.flexibility == player.stat_for_simulation("flexibility", "scout")
        assert prs.flexibility >= 81  # 81, or the preferred-role boost of it
