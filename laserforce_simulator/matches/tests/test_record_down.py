"""Pure-unit tests pinning the six behaviours of ``sim_helpers.down.record_down``.

Lifted from ``BatchSimulator._record_down`` (RV-02 chokepoint). Six
behaviours per the seam contract at
``.claude/worktrees/shot-resolver-seam-contract.md`` §1:

  1. RV-02 medic-reset chain — increment ``down_chain_count`` (fresh
     Down ⇒ 1; re-Down before recovery ⇒ +1); emit ``medic_reset``
     once when a Medic reaches 2.
  2. Stamp ``last_downed_time = tick``.
  3. Clear ``_path_cache`` (MOVE-02 — knocked off committed route).
  4. Clear ``is_holding`` (MOVE-03 — Down ends Overwatch).
  5. Clear ``_committed_goal`` iff ``from_action_driven`` is True
     (MOVE-04 — positioning goals survive a Down).
  6. RV-02 ``nuke_cancelled`` — for a Commander, scan
     ``ctx.pending_nukes``; emit once per pending nuke with
     ``cancel_logged=False``; set ``cancel_logged=True``; leave nuke
     in queue.

The function does NOT mutate ``final_lives`` or ``shields`` — those
mutations happen at the caller (tag / follow-up / reaction / missile
/ nuke).
"""

from __future__ import annotations

import unittest

from matches.sim_helpers.down import record_down
from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.pending_events import PendingNuke
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.round_context import RoundContext

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _player(
    *,
    role: str = "scout",
    team_color: str = "red",
    final_lives: int = 10,
    last_downed_time: int | None = None,
    down_chain_count: int = 0,
    is_holding: bool = False,
    path_cache: tuple | None = None,
    committed_goal: tuple | None = None,
) -> PlayerState:
    """Minimal PlayerState for record_down tests."""
    p = PlayerState(
        tag_id=f"{team_color}_{role}",
        name=f"{team_color} {role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=10,
        starting_shots=20,
        final_lives=final_lives,
        final_shots=20,
        last_downed_time=last_downed_time,
        down_chain_count=down_chain_count,
        is_holding=is_holding,
    )
    p._path_cache = path_cache
    p._committed_goal = committed_goal
    return p


def _ctx(
    *,
    event_log: list | None = None,
    pending_nukes: list | None = None,
) -> RoundContext:
    """Minimal RoundContext for record_down tests.

    All queues other than the two record_down reads are empty —
    record_down does NOT touch pending_followups / pending_reactions /
    all_alive / movement_ctx.
    """
    # Wrap the caller's optional event_log list in an EventLog with
    # buffer= so test assertions can keep reading ``event_log[i]``
    # directly (the list and ``ctx.events.entries`` are the same list).
    if event_log is None:
        event_log = []
    return RoundContext(
        events=EventLog(persist=True, buffer=event_log),
        pending_nukes=pending_nukes if pending_nukes is not None else [],
        pending_followups=[],
        pending_reactions=[],
        all_alive=[],
        movement_ctx=None,
    )


# ---------------------------------------------------------------------------
# Behaviour 2 + 3 + 4: simple state mutations
# ---------------------------------------------------------------------------


class TestRecordDownStateMutation(unittest.TestCase):
    """Behaviours 2/3/4: last_downed_time stamp, _path_cache clear, is_holding clear."""

    def test_stamps_last_downed_time(self) -> None:
        p = _player()
        record_down(p, tick=42, ctx=_ctx())
        self.assertEqual(p.last_downed_time, 42)

    def test_stamps_last_downed_time_overwrites_previous(self) -> None:
        p = _player(last_downed_time=10)
        record_down(p, tick=50, ctx=_ctx())
        self.assertEqual(p.last_downed_time, 50)

    def test_clears_path_cache_when_set(self) -> None:
        cache = ((5, 5), [(5, 6), (5, 7)], (5, 5))
        p = _player(path_cache=cache)
        record_down(p, tick=10, ctx=_ctx())
        self.assertIsNone(p._path_cache)

    def test_clears_path_cache_when_already_none(self) -> None:
        p = _player(path_cache=None)
        record_down(p, tick=10, ctx=_ctx())
        self.assertIsNone(p._path_cache)

    def test_clears_is_holding_when_true(self) -> None:
        p = _player(is_holding=True)
        record_down(p, tick=10, ctx=_ctx())
        self.assertFalse(p.is_holding)

    def test_clears_is_holding_when_already_false(self) -> None:
        p = _player(is_holding=False)
        record_down(p, tick=10, ctx=_ctx())
        self.assertFalse(p.is_holding)

    def test_does_not_touch_final_lives(self) -> None:
        """record_down is the chokepoint — but life decrement is the caller's job."""
        p = _player(final_lives=7)
        record_down(p, tick=10, ctx=_ctx())
        self.assertEqual(p.final_lives, 7)

    def test_does_not_touch_shields(self) -> None:
        p = _player()
        p.shields = 3
        record_down(p, tick=10, ctx=_ctx())
        self.assertEqual(p.shields, 3)


# ---------------------------------------------------------------------------
# Behaviour 5: committed-goal clear is conditional on from_action_driven
# ---------------------------------------------------------------------------


class TestRecordDownCommittedGoal(unittest.TestCase):
    """Behaviour 5: clear ``_committed_goal`` iff ``from_action_driven``."""

    def test_clears_committed_goal_when_from_action_driven(self) -> None:
        p = _player(committed_goal=((5, 5), True, 14))
        record_down(p, tick=10, ctx=_ctx())
        self.assertIsNone(p._committed_goal)

    def test_preserves_committed_goal_when_positioning(self) -> None:
        """Positioning goals (from_action=False) survive a Down — the player
        keeps Advancing through the Respawn cooldown."""
        goal = ((5, 5), False, 14)
        p = _player(committed_goal=goal)
        record_down(p, tick=10, ctx=_ctx())
        self.assertEqual(p._committed_goal, goal)

    def test_committed_goal_none_stays_none(self) -> None:
        p = _player(committed_goal=None)
        record_down(p, tick=10, ctx=_ctx())
        self.assertIsNone(p._committed_goal)


# ---------------------------------------------------------------------------
# Behaviour 1: medic-reset chain counter + medic_reset event
# ---------------------------------------------------------------------------


class TestRecordDownMedicReset(unittest.TestCase):
    """Behaviour 1: ``down_chain_count`` rules + ``medic_reset`` event."""

    def test_fresh_down_sets_chain_to_one(self) -> None:
        """A fresh Down (player was fully active) resets chain to 1."""
        # last_downed_time=None ⇒ is_active_at returns True at any tick > 0
        p = _player(role="medic", last_downed_time=None, down_chain_count=0)
        record_down(p, tick=100, ctx=_ctx())
        self.assertEqual(p.down_chain_count, 1)

    def test_fresh_down_after_full_recovery_resets_chain(self) -> None:
        """A Down with chain=5 carried but the player has fully recovered
        resets to 1 (is_active_at returns True post-RESPAWN_TICKS)."""
        # RESPAWN_TICKS=16; tick=100 with last_downed_time=50 ⇒ active
        p = _player(role="medic", last_downed_time=50, down_chain_count=5)
        record_down(p, tick=100, ctx=_ctx())
        self.assertEqual(p.down_chain_count, 1)

    def test_re_down_during_cooldown_increments_chain(self) -> None:
        """A Down while still in respawn cooldown increments the chain."""
        # tick=10, last_downed_time=5, RESPAWN_TICKS=16 ⇒ NOT active
        p = _player(role="medic", last_downed_time=5, down_chain_count=1)
        record_down(p, tick=10, ctx=_ctx())
        self.assertEqual(p.down_chain_count, 2)

    def test_medic_at_chain_two_emits_medic_reset(self) -> None:
        """Medic re-Downed before recovery (chain reaches 2) emits one
        medic_reset event."""
        events: list = []
        p = _player(
            role="medic",
            last_downed_time=5,  # still in cooldown at tick=10
            down_chain_count=1,
        )
        record_down(p, tick=10, ctx=_ctx(event_log=events))
        medic_resets = [e for e in events if e["event_type"] == "medic_reset"]
        self.assertEqual(len(medic_resets), 1)
        evt = medic_resets[0]
        self.assertEqual(evt["actor_id"], p.player_id)
        self.assertIsNone(evt["target_id"])
        self.assertEqual(evt["timestamp"], 10)
        self.assertEqual(evt["points_awarded"], 0)

    def test_medic_reset_carries_actor_metadata(self) -> None:
        """medic_reset metadata mirrors the _build_meta(player) actor block."""
        events: list = []
        p = _player(
            role="medic",
            last_downed_time=5,
            down_chain_count=1,
        )
        p.final_special = 17
        p.counters.points_scored = 230
        record_down(p, tick=10, ctx=_ctx(event_log=events))
        evt = [e for e in events if e["event_type"] == "medic_reset"][0]
        md = evt["metadata"]
        self.assertEqual(md["actor_role"], "medic")
        self.assertEqual(md["actor_shots"], p.final_shots)
        self.assertEqual(md["actor_lives"], p.final_lives)
        self.assertEqual(md["actor_points"], 230)
        self.assertEqual(md["sp"], 17)

    def test_non_medic_at_chain_two_does_not_emit_medic_reset(self) -> None:
        """Only Medics emit medic_reset; a re-Downed Scout/Heavy/Commander/Ammo
        increments the counter but does not log."""
        for role in ("scout", "heavy", "commander", "ammo"):
            with self.subTest(role=role):
                events: list = []
                p = _player(
                    role=role,
                    last_downed_time=5,
                    down_chain_count=1,
                )
                record_down(p, tick=10, ctx=_ctx(event_log=events))
                self.assertEqual(p.down_chain_count, 2)
                self.assertEqual(
                    [e for e in events if e["event_type"] == "medic_reset"],
                    [],
                )

    def test_medic_reset_fires_only_once_per_chain(self) -> None:
        """A Medic at chain=2 emits medic_reset; at chain=3 (a third Down in
        the same cooldown chain), no second emit."""
        events: list = []
        # First re-Down: chain 1→2, emit
        p = _player(role="medic", last_downed_time=5, down_chain_count=1)
        record_down(p, tick=10, ctx=_ctx(event_log=events))
        self.assertEqual(p.down_chain_count, 2)
        self.assertEqual(
            len([e for e in events if e["event_type"] == "medic_reset"]), 1
        )

        # Second re-Down within the same cooldown: chain 2→3, no emit
        record_down(p, tick=12, ctx=_ctx(event_log=events))
        self.assertEqual(p.down_chain_count, 3)
        self.assertEqual(
            len([e for e in events if e["event_type"] == "medic_reset"]), 1
        )

    def test_medic_reset_not_emitted_when_event_log_is_none(self) -> None:
        """Batch path: event_log=None ⇒ no emit, but chain counter still ticks."""
        p = _player(role="medic", last_downed_time=5, down_chain_count=1)
        ctx = _ctx()
        ctx.events = EventLog(persist=False)
        record_down(p, tick=10, ctx=ctx)
        self.assertEqual(p.down_chain_count, 2)
        # No assertion error — None event_log silently skips emit


# ---------------------------------------------------------------------------
# Behaviour 6: nuke_cancelled for a Downed Commander with a live pending nuke
# ---------------------------------------------------------------------------


class TestRecordDownNukeCancellation(unittest.TestCase):
    """Behaviour 6: Commander down/disarm during fuse window emits
    ``nuke_cancelled`` and sets ``cancel_logged=True``."""

    def test_commander_with_pending_nuke_emits_cancelled(self) -> None:
        events: list = []
        cmdr = _player(role="commander")
        nuke = PendingNuke(complete_time=120, player=cmdr)
        record_down(cmdr, tick=115, ctx=_ctx(event_log=events, pending_nukes=[nuke]))
        cancelled = [e for e in events if e["event_type"] == "nuke_cancelled"]
        self.assertEqual(len(cancelled), 1)
        evt = cancelled[0]
        self.assertEqual(evt["actor_id"], cmdr.player_id)
        self.assertIsNone(evt["target_id"])
        self.assertEqual(evt["timestamp"], 115)
        self.assertEqual(evt["points_awarded"], 0)

    def test_nuke_cancelled_metadata_has_actor_block(self) -> None:
        events: list = []
        cmdr = _player(role="commander")
        cmdr.final_special = 99
        cmdr.counters.points_scored = 500
        nuke = PendingNuke(complete_time=120, player=cmdr)
        record_down(cmdr, tick=115, ctx=_ctx(event_log=events, pending_nukes=[nuke]))
        evt = [e for e in events if e["event_type"] == "nuke_cancelled"][0]
        md = evt["metadata"]
        self.assertEqual(md["actor_role"], "commander")
        self.assertEqual(md["actor_points"], 500)
        self.assertEqual(md["sp"], 99)

    def test_pending_nuke_left_in_queue_after_cancel(self) -> None:
        """MECH-05: the cancelled nuke is LEFT in pending_nukes (the drain
        path is structurally unchanged; cancel_logged dedups the next emit)."""
        events: list = []
        cmdr = _player(role="commander")
        nuke = PendingNuke(complete_time=120, player=cmdr)
        pending = [nuke]
        record_down(cmdr, tick=115, ctx=_ctx(event_log=events, pending_nukes=pending))
        self.assertEqual(len(pending), 1)
        self.assertIs(pending[0], nuke)
        self.assertTrue(nuke.cancel_logged)

    def test_already_cancelled_nuke_does_not_emit_again(self) -> None:
        """A defensive double-call into record_down (same Commander still in
        the cooldown) must NOT log a second nuke_cancelled."""
        events: list = []
        cmdr = _player(role="commander")
        nuke = PendingNuke(complete_time=120, player=cmdr, cancel_logged=True)
        record_down(cmdr, tick=115, ctx=_ctx(event_log=events, pending_nukes=[nuke]))
        self.assertEqual([e for e in events if e["event_type"] == "nuke_cancelled"], [])

    def test_nuke_owned_by_different_commander_is_not_cancelled(self) -> None:
        """A Downed Commander only cancels its OWN pending nukes."""
        events: list = []
        cmdr_a = _player(role="commander", team_color="red")
        cmdr_b = _player(role="commander", team_color="blue")
        cmdr_b_nuke = PendingNuke(complete_time=120, player=cmdr_b)
        record_down(
            cmdr_a, tick=115, ctx=_ctx(event_log=events, pending_nukes=[cmdr_b_nuke])
        )
        self.assertEqual([e for e in events if e["event_type"] == "nuke_cancelled"], [])
        self.assertFalse(cmdr_b_nuke.cancel_logged)

    def test_non_commander_does_not_scan_pending_nukes(self) -> None:
        """A Downed Scout/Heavy/Medic/Ammo doesn't scan the nuke queue at all."""
        for role in ("scout", "heavy", "medic", "ammo"):
            with self.subTest(role=role):
                events: list = []
                p = _player(role=role)
                # Synthetic nuke aliased to the non-commander (impossible in
                # production but the policy is "only Commanders scan").
                rogue_nuke = PendingNuke(complete_time=120, player=p)
                record_down(
                    p, tick=115, ctx=_ctx(event_log=events, pending_nukes=[rogue_nuke])
                )
                self.assertEqual(
                    [e for e in events if e["event_type"] == "nuke_cancelled"], []
                )
                self.assertFalse(rogue_nuke.cancel_logged)

    def test_multiple_pending_nukes_each_emit_once(self) -> None:
        """If a Commander somehow has two live pending nukes, each
        un-logged one emits."""
        events: list = []
        cmdr = _player(role="commander")
        nuke_a = PendingNuke(complete_time=120, player=cmdr)
        nuke_b = PendingNuke(complete_time=140, player=cmdr)
        record_down(
            cmdr, tick=115, ctx=_ctx(event_log=events, pending_nukes=[nuke_a, nuke_b])
        )
        cancelled = [e for e in events if e["event_type"] == "nuke_cancelled"]
        self.assertEqual(len(cancelled), 2)
        self.assertTrue(nuke_a.cancel_logged)
        self.assertTrue(nuke_b.cancel_logged)

    def test_nuke_cancelled_not_emitted_when_persist_is_false(self) -> None:
        """Batch path: ``EventLog(persist=False)`` ⇒ no emit row, but
        ``cancel_logged`` still flips uniformly.

        EventLog candidate behaviour change (deliberate): with the
        null-object pattern, the verb call is a no-op when
        ``persist=False`` but the state mutation (``cancel_logged =
        True``) always runs. This is cleaner than the pre-refactor
        quirk where the whole nuke-cancel block was gated on
        ``event_log is not None`` — the dedup flag is per-nuke state,
        not per-emit-record, and now flips consistently regardless of
        persistence. No observable downstream impact (the batch path
        has no second-Down-of-same-Commander-in-saved-game scenario).
        """
        cmdr = _player(role="commander")
        nuke = PendingNuke(complete_time=120, player=cmdr)
        ctx = _ctx(pending_nukes=[nuke])
        ctx.events = EventLog(persist=False)
        record_down(cmdr, tick=115, ctx=ctx)
        # No emit row recorded.
        self.assertEqual(len(ctx.events), 0)
        # cancel_logged still flips uniformly.
        self.assertTrue(nuke.cancel_logged)


# ---------------------------------------------------------------------------
# Cross-cutting: event_log is optional (batch path) for both emit kinds
# ---------------------------------------------------------------------------


class TestRecordDownEventLogNone(unittest.TestCase):
    """Batch path (event_log=None) — state mutations still happen, no emits."""

    def test_all_state_mutations_still_happen_without_event_log(self) -> None:
        cmdr = _player(
            role="commander",
            last_downed_time=5,
            down_chain_count=1,
            is_holding=True,
            path_cache=((1, 1), [(1, 2)], (1, 1)),
            committed_goal=((1, 1), True, 14),
        )
        nuke = PendingNuke(complete_time=120, player=cmdr)
        ctx = _ctx(pending_nukes=[nuke])
        ctx.events = EventLog(persist=False)
        record_down(cmdr, tick=10, ctx=ctx)
        self.assertEqual(cmdr.last_downed_time, 10)
        self.assertEqual(cmdr.down_chain_count, 2)
        self.assertFalse(cmdr.is_holding)
        self.assertIsNone(cmdr._path_cache)
        self.assertIsNone(cmdr._committed_goal)


if __name__ == "__main__":
    unittest.main()
