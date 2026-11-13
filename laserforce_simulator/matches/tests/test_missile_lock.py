"""MECH-21 (spec #583, ADR-0050): the engine's missile follows the real Lock.

A player holds at most one Lock (``RoundContext.missile_locks``). Nothing is
spent when it starts; after ``LOCK_TICKS`` it launches, spending the missile,
and a launched missile always hits. A lock that ends any other way writes one
``lock_cancelled`` row with its tick and reason.

Every guard gets one fixture per failed condition. RNG is pinned with
``unittest.mock.patch`` on ``random.random`` / ``random.randint``. Sight uses
the map-less zone rule (same ``current_zone`` = sight), so no map is needed.
"""

from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from matches.sim_helpers.combat import (
    award_bases,
    cancel_missile_lock,
    cancel_missile_locks_involving,
    capture_base,
    start_missile_lock,
    tick_missile_lock,
)
from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.pending_events import (
    LOCK_CANCEL_REASONS,
    PendingMissileLock,
)
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.round_context import RoundContext
from matches.sim_helpers.shot import (
    SHOT_KIND_FOLLOW_UP,
    SHOT_KIND_INITIAL,
    SHOT_KIND_OVERWATCH,
    SHOT_KIND_REACTION,
    resolve_shot,
)
from matches.sim_helpers.time_constants import LOCK_TICKS
from matches.simulation import BatchSimulator

T = 100  # the lock-start tick of every fixture


def _ps(
    player_id: int,
    *,
    team_color: str = "red",
    role: str = "heavy",
    name: str | None = None,
    final_lives: int = 10,
    final_missiles: int = 5,
) -> PlayerState:
    p = PlayerState(
        tag_id=f"{team_color}_{role}_{player_id}",
        name=name or f"P{player_id}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        player_awareness=0,
        starting_lives=final_lives,
        starting_shots=30,
        final_lives=final_lives,
        final_shots=30,
        final_missiles=final_missiles,
        player_id=player_id,
    )
    p.shields = p.max_shields
    return p


def _ctx(*players: PlayerState) -> RoundContext:
    return RoundContext(events=EventLog(persist=True), all_alive=list(players))


def _rows(ctx: RoundContext, *types: str) -> list[dict]:
    return [e for e in ctx.events.entries if e["event_type"] in types]


def _cancels(ctx: RoundContext) -> list[tuple[int, int, int, str]]:
    return [
        (e["timestamp"], e["actor_id"], e["target_id"], e["metadata"]["reason"])
        for e in _rows(ctx, "lock_cancelled")
    ]


def _chase_ban(banned: bool, module: str = "matches.sim_helpers.combat"):
    """Patch ``may_tag`` in ``module``: False (a MECH-10 Chase ban on the
    target) when ``banned``, else True, as these Follow-rule-free fixtures
    already read it."""
    return patch(f"{module}.may_tag", return_value=not banned)


def _locked() -> tuple[PlayerState, PlayerState, RoundContext]:
    """A red Heavy (1) holding a lock on a blue Scout (2) started at T."""
    locker = _ps(1)
    target = _ps(2, team_color="blue", role="scout")
    ctx = _ctx(locker, target)
    assert start_missile_lock(locker, target, T, None, ctx=ctx) is not None
    return locker, target, ctx


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_lock_ticks_is_two(self) -> None:
        assert LOCK_TICKS == 2

    def test_cancel_reasons(self) -> None:
        assert LOCK_CANCEL_REASONS == (
            "sight",
            "dodge",
            "shot",
            "retarget",
            "down",
            "out",
        )

    def test_pending_lock_defaults_to_lock_ticks(self) -> None:
        lock = PendingMissileLock(attacker=None, defender=None)
        assert lock.ticks_remaining == LOCK_TICKS
        assert not hasattr(lock, "los_broken")

    def test_round_context_starts_with_no_locks(self) -> None:
        assert RoundContext().missile_locks == {}

    def test_lock_cancelled_is_an_event_type_choice(self) -> None:
        from matches.models import GameEvent

        assert ("lock_cancelled", "Missile Lock Cancelled") in GameEvent.EVENT_TYPES


# ---------------------------------------------------------------------------
# Lock start: nothing spent, one lock per player
# ---------------------------------------------------------------------------


class TestStartSpendsNothing:
    def test_missiles_unchanged_at_lock_start(self) -> None:
        locker, target, ctx = _locked()
        assert locker.final_missiles == 5
        assert locker.counters.missiles_landed == 0

    def test_lock_is_stored_by_locker_id_and_written(self) -> None:
        locker, target, ctx = _locked()
        assert list(ctx.missile_locks) == [locker.player_id]
        lock = ctx.missile_locks[locker.player_id]
        assert (lock.attacker, lock.defender) == (locker, target)
        assert lock.ticks_remaining == LOCK_TICKS
        rows = _rows(ctx, "locking")
        assert [(e["timestamp"], e["actor_id"], e["target_id"]) for e in rows] == [
            (T, 1, 2)
        ]


class TestOneLockRule:
    def test_same_target_holds_and_writes_nothing(self) -> None:
        locker, target, ctx = _locked()
        lock = ctx.missile_locks[locker.player_id]
        lock.ticks_remaining = 1
        before = len(ctx.events.entries)

        assert start_missile_lock(locker, target, T + 1, None, ctx=ctx) is None

        assert len(ctx.events.entries) == before
        assert ctx.missile_locks == {locker.player_id: lock}
        assert lock.ticks_remaining == 1

    def test_other_valid_target_retargets_on_the_same_tick(self) -> None:
        locker, target, ctx = _locked()
        other = _ps(3, team_color="blue", role="medic")

        new = start_missile_lock(locker, other, T + 1, None, ctx=ctx)

        assert new is not None and new.defender is other
        assert ctx.missile_locks == {locker.player_id: new}
        assert _cancels(ctx) == [(T + 1, 1, 2, "retarget")]
        tail = [(e["event_type"], e["timestamp"]) for e in ctx.events.entries[-2:]]
        assert tail == [("lock_cancelled", T + 1), ("locking", T + 1)]
        assert locker.final_missiles == 5

    def test_retarget_moves_the_lock_to_the_end(self) -> None:
        locker, target, ctx = _locked()
        second = _ps(4, team_color="blue", role="commander")
        assert start_missile_lock(second, target, T, None, ctx=ctx) is not None
        other = _ps(3, team_color="blue", role="medic")

        start_missile_lock(locker, other, T + 1, None, ctx=ctx)

        assert list(ctx.missile_locks) == [second.player_id, locker.player_id]

    # MECH-23 (#606): the "hiding" gate's fixture is gone from both gate
    # lists below; a hider in sight can be Locked (TestHiderCanBeLocked).
    @pytest.mark.parametrize(
        "failed_gate",
        [
            "inactive_attacker",
            "untaggable_defender",
            "no_missiles",
            "sight",
            "chased",
        ],
    )
    def test_failed_gate_on_other_target_holds_the_old_lock(
        self, failed_gate: str
    ) -> None:
        locker, target, ctx = _locked()
        old = ctx.missile_locks[locker.player_id]
        other = _ps(3, team_color="blue", role="medic")
        tick = T + 1
        if failed_gate == "inactive_attacker":
            # Reset window: taggable but not yet Active.
            locker.last_downed_time = tick - 10
        elif failed_gate == "untaggable_defender":
            other.last_downed_time = tick - 1
        elif failed_gate == "no_missiles":
            locker.final_missiles = 0
        elif failed_gate == "sight":
            other.current_zone = locker.current_zone + 1
        before = len(ctx.events.entries)

        with _chase_ban(failed_gate == "chased"):
            assert start_missile_lock(locker, other, tick, None, ctx=ctx) is None

        assert ctx.missile_locks == {locker.player_id: old}
        assert len(ctx.events.entries) == before

    @pytest.mark.parametrize(
        "failed_gate",
        [
            "inactive_attacker",
            "untaggable_defender",
            "no_missiles",
            "sight",
            "chased",
        ],
    )
    def test_each_gate_alone_refuses_a_first_lock(self, failed_gate: str) -> None:
        locker = _ps(1)
        target = _ps(2, team_color="blue", role="scout")
        ctx = _ctx(locker, target)
        if failed_gate == "inactive_attacker":
            locker.last_downed_time = T - 10
        elif failed_gate == "untaggable_defender":
            target.last_downed_time = T - 1
        elif failed_gate == "no_missiles":
            locker.final_missiles = 0
        elif failed_gate == "sight":
            target.current_zone = locker.current_zone + 1

        with _chase_ban(failed_gate == "chased"):
            assert start_missile_lock(locker, target, T, None, ctx=ctx) is None
        assert ctx.missile_locks == {}
        assert ctx.events.entries == []


class TestHiderCanBeLocked:
    """MECH-23 decision 5: the "no Lock on a hider" gate is gone. A hider in
    sight is as easy to Lock as anyone else."""

    def test_a_lock_starts_on_a_hiding_defender_in_sight(self) -> None:
        locker = _ps(1)
        target = _ps(2, team_color="blue", role="scout")
        target.is_hiding = True
        ctx = _ctx(locker, target)

        lock = start_missile_lock(locker, target, T, None, ctx=ctx)

        assert lock is not None and lock.defender is target
        assert _rows(ctx, "locking")

    def test_a_retarget_moves_onto_a_hiding_defender(self) -> None:
        locker, target, ctx = _locked()
        other = _ps(3, team_color="blue", role="medic")
        other.is_hiding = True

        new = start_missile_lock(locker, other, T + 1, None, ctx=ctx)

        assert new is not None and new.defender is other
        assert _cancels(ctx) == [(T + 1, 1, 2, "retarget")]


# ---------------------------------------------------------------------------
# cancel_missile_lock / cancel_missile_locks_involving
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_removes_and_writes(self) -> None:
        locker, target, ctx = _locked()
        lock = ctx.missile_locks[locker.player_id]

        assert cancel_missile_lock(ctx, locker, T + 1, "sight") is lock

        assert ctx.missile_locks == {}
        assert _cancels(ctx) == [(T + 1, 1, 2, "sight")]

    def test_cancel_with_no_lock_returns_none_and_writes_nothing(self) -> None:
        locker = _ps(1)
        ctx = _ctx(locker)
        assert cancel_missile_lock(ctx, locker, T, "shot") is None
        assert ctx.events.entries == []

    def test_cancel_draws_no_rng(self) -> None:
        locker, target, ctx = _locked()
        with patch("random.random", side_effect=AssertionError("RNG drawn")):
            cancel_missile_lock(ctx, locker, T + 1, "shot")

    @pytest.mark.parametrize("lives,reason", [(4, "down"), (0, "out")])
    def test_involving_cancels_own_lock_and_every_lock_on_the_player(
        self, lives: int, reason: str
    ) -> None:
        a = _ps(1)
        b = _ps(2, team_color="blue", role="commander")
        c = _ps(3, team_color="blue", role="heavy")
        d = _ps(4, team_color="red", role="commander")
        bystander = _ps(5, team_color="blue", role="scout")
        ctx = _ctx(a, b, c, d, bystander)
        start_missile_lock(a, b, T, None, ctx=ctx)  # a on b
        start_missile_lock(b, a, T, None, ctx=ctx)  # b on a
        start_missile_lock(c, a, T, None, ctx=ctx)  # c on a
        start_missile_lock(d, bystander, T, None, ctx=ctx)  # untouched
        a.final_lives = lives

        assert cancel_missile_locks_involving(ctx, a, T + 1) == 3

        assert list(ctx.missile_locks) == [d.player_id]
        assert sorted(_cancels(ctx)) == [
            (T + 1, 1, 2, reason),
            (T + 1, 2, 1, reason),
            (T + 1, 3, 1, reason),
        ]

    def test_involving_with_no_locks_returns_zero(self) -> None:
        a = _ps(1)
        ctx = _ctx(a)
        assert cancel_missile_locks_involving(ctx, a, T) == 0
        assert ctx.events.entries == []


# ---------------------------------------------------------------------------
# tick_missile_lock and the tick loop: launch, dodge, sight
# ---------------------------------------------------------------------------


def _advance(ctx: RoundContext, ticks: range) -> None:
    sim = BatchSimulator()
    for tick in ticks:
        sim._advance_missile_locks(tick, ctx)


class TestTickMissileLock:
    def test_counts_down_then_launches(self) -> None:
        locker, target, ctx = _locked()
        lock = ctx.missile_locks[locker.player_id]
        results = [tick_missile_lock(lock, T + d, None) for d in range(1, 3)]
        assert results == ["pending", "launch"]

    def test_no_sight_failed_save_returns_sight(self) -> None:
        locker, target, ctx = _locked()
        lock = ctx.missile_locks[locker.player_id]
        target.current_zone = locker.current_zone + 1
        with patch("random.random", return_value=0.99):  # 99 >= special_usage 50
            assert tick_missile_lock(lock, T + 1, None) == "sight"
        assert lock.ticks_remaining == LOCK_TICKS

    def test_no_sight_passed_save_counts_down(self) -> None:
        locker, target, ctx = _locked()
        lock = ctx.missile_locks[locker.player_id]
        target.current_zone = locker.current_zone + 1
        with patch("random.random", return_value=0.0):
            assert tick_missile_lock(lock, T + 1, None) == "pending"
        assert lock.ticks_remaining == LOCK_TICKS - 1

    def test_does_not_check_lives(self) -> None:
        """Downs cancel through record_down; the tick no longer reads lives."""
        locker, target, ctx = _locked()
        lock = ctx.missile_locks[locker.player_id]
        lock.ticks_remaining = 1
        locker.final_lives = 0
        assert tick_missile_lock(lock, T + 1, None) == "launch"


class TestLaunch:
    def test_launch_at_t_plus_lock_ticks_spends_and_hits(self) -> None:
        locker, target, ctx = _locked()
        with patch("random.random", return_value=0.99):  # no dodge
            _advance(ctx, range(T + 1, T + LOCK_TICKS + 1))

        missiled = _rows(ctx, "missiled")
        assert [(e["timestamp"], e["actor_id"], e["target_id"]) for e in missiled] == [
            (T + LOCK_TICKS, 1, 2)
        ]
        assert missiled[0]["metadata"]["result"] == "hit"
        assert missiled[0]["points_awarded"] == 500
        assert locker.final_missiles == 4
        assert locker.counters.missiles_landed == 1
        assert ctx.missile_locks == {}
        assert _cancels(ctx) == []

    def test_no_row_before_the_launch_tick(self) -> None:
        locker, target, ctx = _locked()
        with patch("random.random", return_value=0.99):
            _advance(ctx, range(T + 1, T + LOCK_TICKS))
        assert _rows(ctx, "missiled", "lock_cancelled") == []
        assert locker.final_missiles == 5

    def test_dodge_cancels_at_launch_with_nothing_spent(self) -> None:
        locker, target, ctx = _locked()
        with patch("random.random", return_value=0.0):  # 0 < dodge 10%
            _advance(ctx, range(T + 1, T + LOCK_TICKS + 1))

        assert _rows(ctx, "missiled") == []
        assert _cancels(ctx) == [(T + LOCK_TICKS, 1, 2, "dodge")]
        assert locker.final_missiles == 5
        assert locker.counters.missiles_landed == 0
        assert ctx.missile_locks == {}
        assert _rows(ctx, "missile_dodge") == []

    def test_dodge_roll_is_min_20_survival_over_5(self) -> None:
        """survival 50 -> 10%: a draw of 0.10 (10.0 >= 10) does not dodge."""
        locker, target, ctx = _locked()
        with patch("random.random", return_value=0.10):
            _advance(ctx, range(T + 1, T + LOCK_TICKS + 1))
        assert len(_rows(ctx, "missiled")) == 1

    def test_out_of_sight_failed_save_cancels_on_that_tick(self) -> None:
        locker, target, ctx = _locked()
        target.current_zone = locker.current_zone + 1
        with patch("random.random", return_value=0.99):
            _advance(ctx, range(T + 1, T + LOCK_TICKS + 1))
        assert _cancels(ctx) == [(T + 1, 1, 2, "sight")]
        assert _rows(ctx, "missiled") == []
        assert locker.final_missiles == 5

    def test_out_of_sight_passed_save_holds_and_launches(self) -> None:
        locker, target, ctx = _locked()
        target.current_zone = locker.current_zone + 1
        # 0.3: the save passes (30 < 50) and the dodge fails (30 >= 10).
        with patch("random.random", return_value=0.3):
            _advance(ctx, range(T + 1, T + LOCK_TICKS + 1))
        assert _cancels(ctx) == []
        assert [e["timestamp"] for e in _rows(ctx, "missiled")] == [T + LOCK_TICKS]

    def test_a_lock_cancelled_earlier_in_the_pass_is_skipped(self) -> None:
        """Regression (full suite, KeyError): A's launch Downs B, whose own
        lock on C sits later in the same pass's copy and is cancelled by
        record_down; the pass must skip it, not advance or launch it."""
        a = _ps(1)
        b = _ps(2, team_color="blue", role="commander")
        c = _ps(3, team_color="red", role="scout")
        ctx = _ctx(a, b, c)
        start_missile_lock(a, b, T, None, ctx=ctx)
        start_missile_lock(b, c, T, None, ctx=ctx)
        with patch("random.random", return_value=0.99):
            _advance(ctx, range(T + 1, T + LOCK_TICKS + 1))
        assert [(e["actor_id"], e["target_id"]) for e in _rows(ctx, "missiled")] == [
            (1, 2)
        ]
        assert _cancels(ctx) == [(T + LOCK_TICKS, 2, 3, "down")]
        assert ctx.missile_locks == {}

    @pytest.mark.parametrize(
        "failed_gate,reason",
        [
            ("inactive_attacker", "down"),
            ("untaggable_defender", "down"),
            ("chased", "sight"),
        ],
    )
    def test_a_refused_launch_ends_the_lock_with_a_row(
        self, failed_gate: str, reason: str
    ) -> None:
        """Spec #583 gap (owner, 2026-11-09): a launch-tick gate that fails
        without a ``record_down`` (state set directly here) still writes
        exactly one ``lock_cancelled``, before any dodge roll, with nothing
        spent: ``down`` for the activity gates, ``sight`` for a Chase ban."""
        locker, target, ctx = _locked()
        launch = T + LOCK_TICKS
        if failed_gate == "inactive_attacker":
            locker.last_downed_time = launch - 10
        elif failed_gate == "untaggable_defender":
            target.last_downed_time = launch - 1
        draws: list[float] = []

        def draw() -> float:
            draws.append(0.99)
            return 0.99

        with (
            _chase_ban(failed_gate == "chased", "matches.simulation.entrypoints"),
            patch("random.random", side_effect=draw),
        ):
            _advance(ctx, range(T + 1, launch + 1))

        assert _cancels(ctx) == [(launch, 1, 2, reason)]
        assert _rows(ctx, "missiled") == []
        assert locker.final_missiles == 5
        assert ctx.missile_locks == {}
        assert draws == []  # no dodge roll for a refused launch

    def test_launch_removes_the_lock_before_the_down_cancels(self) -> None:
        """The missile Downs the target; the launching lock must not also end
        in a ``lock_cancelled`` (exactly one ending row)."""
        locker, target, ctx = _locked()
        with patch("random.random", return_value=0.99):
            _advance(ctx, range(T + 1, T + LOCK_TICKS + 1))
        assert len(_rows(ctx, "missiled")) == 1
        assert _cancels(ctx) == []


# ---------------------------------------------------------------------------
# Shot hook: every trigger pull by the locker cancels with reason=shot
# ---------------------------------------------------------------------------


class TestShotCancels:
    @pytest.mark.parametrize(
        "kind",
        [
            SHOT_KIND_INITIAL,
            SHOT_KIND_REACTION,
            SHOT_KIND_FOLLOW_UP,
            SHOT_KIND_OVERWATCH,
        ],
    )
    @pytest.mark.parametrize("hit", [True, False])
    def test_each_shot_kind_cancels_before_its_row(self, kind: str, hit: bool) -> None:
        locker, target, ctx = _locked()
        enemy = _ps(3, team_color="blue", role="ammo")
        ctx.all_alive.append(enemy)
        tick = T + 1
        with patch("random.randint", return_value=1 if hit else 100), patch(
            "random.random", return_value=0.99
        ):
            resolve_shot(locker, enemy, tick, kind=kind, ctx=ctx)

        assert _cancels(ctx)[0] == (tick, 1, 2, "shot")
        types = [e["event_type"] for e in ctx.events.entries]
        i = types.index("lock_cancelled")
        assert types[i + 1] == ("tag" if hit else "miss")
        assert ctx.missile_locks == {}

    def test_shot_at_the_locked_target_cancels_too(self) -> None:
        locker, target, ctx = _locked()
        with patch("random.randint", return_value=100):
            resolve_shot(locker, target, T + 1, kind=SHOT_KIND_INITIAL, ctx=ctx)
        assert _cancels(ctx) == [(T + 1, 1, 2, "shot")]

    def test_shot_refused_by_the_validity_gate_holds(self) -> None:
        locker, target, ctx = _locked()
        enemy = _ps(3, team_color="blue", role="ammo")
        enemy.last_downed_time = T  # not taggable
        resolve_shot(locker, enemy, T + 1, kind=SHOT_KIND_INITIAL, ctx=ctx)
        assert _cancels(ctx) == []
        assert locker.player_id in ctx.missile_locks

    def test_someone_elses_shot_holds(self) -> None:
        locker, target, ctx = _locked()
        enemy = _ps(3, team_color="blue", role="ammo")
        with patch("random.randint", return_value=100):
            resolve_shot(enemy, locker, T + 1, kind=SHOT_KIND_INITIAL, ctx=ctx)
        assert _cancels(ctx) == []

    def test_active_base_capture_cancels_before_its_row(self) -> None:
        locker, target, ctx = _locked()
        assert capture_base(locker, 15, T + 1, None, ctx=ctx)
        assert _cancels(ctx) == [(T + 1, 1, 2, "shot")]
        types = [e["event_type"] for e in ctx.events.entries]
        assert types[-2:] == ["lock_cancelled", "base_capture"]

    def test_base_capture_without_the_shots_holds(self) -> None:
        locker, target, ctx = _locked()
        locker.final_shots = 2
        assert not capture_base(locker, 15, T + 1, None, ctx=ctx)
        assert _cancels(ctx) == []

    def test_round_end_award_does_not_cancel(self) -> None:
        locker, target, ctx = _locked()
        award_bases(locker, T + 1, ctx=ctx)
        assert _rows(ctx, "base_capture")
        assert _cancels(ctx) == []
        assert locker.player_id in ctx.missile_locks


# ---------------------------------------------------------------------------
# Down / out: every Down ends the locks at the Down tick, via record_down
# ---------------------------------------------------------------------------


def _down_by(source: str, victim: PlayerState, ctx: RoundContext, tick: int) -> None:
    """Down ``victim`` by a third player through the real Tag / Missile / Nuke
    path."""
    shooter = _ps(9, team_color="green", role="commander", name="Shooter")
    sim = BatchSimulator()
    if source == "tag":
        victim.shields = 1
        with patch("random.randint", return_value=1), patch(
            "random.random", return_value=0.99
        ):
            resolve_shot(shooter, victim, tick, kind=SHOT_KIND_INITIAL, ctx=ctx)
    elif source == "missile":
        sim._complete_missile(shooter, victim, tick, ctx)
    else:
        sim._complete_nuke(shooter, tick, [victim], ctx)


class TestDownCancels:
    @pytest.mark.parametrize("source", ["tag", "missile", "nuke"])
    @pytest.mark.parametrize("who", ["locker", "target"])
    @pytest.mark.parametrize("outcome", ["down", "out"])
    def test_down_ends_the_lock_at_the_down_tick(
        self, source: str, who: str, outcome: str
    ) -> None:
        locker, target, ctx = _locked()
        victim = locker if who == "locker" else target
        # Tag takes 1 life, a Missile 2, a Nuke 3.
        taken = {"tag": 1, "missile": 2, "nuke": 3}[source]
        victim.final_lives = taken if outcome == "out" else taken + 3
        tick = T + 1

        _down_by(source, victim, ctx, tick)

        assert victim.final_lives == (0 if outcome == "out" else 3)
        assert _cancels(ctx) == [(tick, 1, 2, outcome)]
        assert ctx.missile_locks == {}

    @pytest.mark.parametrize("who", ["locker", "target"])
    @pytest.mark.parametrize("supporter_role", ["ammo", "medic"])
    def test_resupply_ends_the_lock_as_down(
        self, who: str, supporter_role: str
    ) -> None:
        """Departure from #583 (found by the round invariants): a Resupply
        restarts the receiver's Respawn cooldown (not Active, not Taggable),
        so a launch could not land and the lock ended with no row. It ends
        as ``reason=down``, grill decision 6's "not active / not taggable"."""
        from matches.sim_helpers.combat import attempt_resupply

        locker, target, ctx = _locked()
        receiver = locker if who == "locker" else target
        receiver.final_lives = 2
        receiver.final_shots = 2
        supporter = _ps(7, team_color=receiver.team_color, role=supporter_role)

        attempt_resupply(supporter, receiver, T + 1, ctx=ctx)

        assert _cancels(ctx) == [(T + 1, 1, 2, "down")]
        assert ctx.missile_locks == {}

    def test_record_down_with_no_context_skips_the_cancel(self) -> None:
        from matches.sim_helpers.down import record_down

        locker, target, ctx = _locked()
        record_down(locker, T + 1, None)
        assert locker.player_id in ctx.missile_locks

    def test_down_of_a_bystander_holds(self) -> None:
        locker, target, ctx = _locked()
        bystander = _ps(3, team_color="blue", role="medic")
        _down_by("missile", bystander, ctx, T + 1)
        assert _cancels(ctx) == []
        assert locker.player_id in ctx.missile_locks


# ---------------------------------------------------------------------------
# Round invariants: 12 seeded Syracuse rounds, full fidelity
# ---------------------------------------------------------------------------


def lock_invariant_violations(log: list[dict], last_tick: int) -> list[str]:
    """Walk a round's rows in order and name every broken Lock rule.

    - a ``locking`` while the shooter's previous lock has no ending row;
    - an ending row (``missiled`` / ``lock_cancelled``) with no open lock by
      that shooter on that target, or a ``missiled`` not at start + LOCK_TICKS;
    - a lock still open that should have launched by ``last_tick``;
    - a reason outside ``LOCK_CANCEL_REASONS``, a ``missile_dodge`` row, a
      ``result`` other than ``"hit"``.
    """
    bad: list[str] = []
    open_locks: dict[int, tuple[int, int]] = {}  # shooter -> (target, start)
    for e in log:
        et, who, tick = e["event_type"], e["actor_id"], e["timestamp"]
        if et == "missile_dodge":
            bad.append(f"missile_dodge row at {tick}")
        elif et == "locking":
            if who in open_locks:
                bad.append(f"{who} locks at {tick} holding {open_locks[who]}")
            open_locks[who] = (e["target_id"], tick)
        elif et in ("missiled", "lock_cancelled"):
            held = open_locks.pop(who, None)
            if held is None or held[0] != e["target_id"]:
                bad.append(f"{et} by {who} at {tick} ends no lock ({held})")
            elif et == "missiled" and tick != held[1] + LOCK_TICKS:
                bad.append(f"missiled at {tick} for a lock at {held[1]}")
            md = e["metadata"]
            if et == "missiled" and md.get("result") != "hit":
                bad.append(f"missiled result {md.get('result')!r} at {tick}")
            if et == "lock_cancelled" and md.get("reason") not in LOCK_CANCEL_REASONS:
                bad.append(f"reason {md.get('reason')!r} at {tick}")
    for who, (target, start) in open_locks.items():
        if start + LOCK_TICKS <= last_tick:
            bad.append(f"{who}'s lock on {target} at {start} never ended")
    return bad


class TestLockInvariantChecker:
    """The checker itself, on hand-built rows, so a broken engine cannot pass
    it by accident."""

    @staticmethod
    def _e(et: str, tick: int, who: int = 1, target: int = 2, **md: object) -> dict:
        return {
            "event_type": et,
            "actor_id": who,
            "target_id": target,
            "timestamp": tick,
            "metadata": md,
        }

    def test_clean_rows_pass(self) -> None:
        log = [
            self._e("locking", 10),
            self._e("missiled", 12, result="hit"),
            self._e("locking", 20),
            self._e("lock_cancelled", 21, reason="retarget"),
            self._e("locking", 21, target=3),
            self._e("lock_cancelled", 22, target=3, reason="down"),
            self._e("locking", 1799),
        ]
        assert lock_invariant_violations(log, 1799) == []

    @pytest.mark.parametrize(
        "log",
        [
            [("locking", 10, {}), ("locking", 11, {})],
            [("locking", 10, {}), ("missiled", 13, {"result": "hit"})],
            [("locking", 10, {}), ("missiled", 12, {"result": "miss"})],
            [("locking", 10, {}), ("lock_cancelled", 11, {"reason": "bored"})],
            [("missiled", 12, {"result": "hit"})],
            [("locking", 10, {})],
            [("missile_dodge", 12, {})],
        ],
        ids=[
            "second_lock",
            "late_launch",
            "miss",
            "bad_reason",
            "no_lock",
            "never_ended",
            "dodge_row",
        ],
    )
    def test_each_broken_rule_is_named(self, log: list) -> None:
        rows = [self._e(et, tick, **md) for et, tick, md in log]
        assert lock_invariant_violations(rows, 1799)


def _syracuse_round(seed: int) -> list[dict]:
    from pathlib import Path

    from matches.lfstats_calibration import load_map_fixture
    from matches.round_probe import _default_roster
    from matches.sim_helpers.map_loader import load_map_context

    fixture = Path(__file__).resolve().parents[3] / "data/maps/syracuse.json"
    movement_ctx = load_map_context(load_map_fixture(fixture))[0]
    random.seed(seed)
    log: list[dict] = []
    BatchSimulator()._simulate_round(
        _default_roster("red"),
        _default_roster("blue"),
        event_log=log,
        movement_ctx=movement_ctx,
    )
    return log


@pytest.mark.django_db
class TestSyracuseRoundInvariants:
    @pytest.mark.parametrize("seed", range(1, 13))
    def test_every_lock_follows_the_rules(self, seed: int) -> None:
        log = _syracuse_round(seed)
        assert any(e["event_type"] == "locking" for e in log)
        assert lock_invariant_violations(log, BatchSimulator.ROUND_TICKS - 1) == []
