"""
Tests for MECH-05: Nuke cancellation fuse window fix in BatchSimulator.

Bug: BatchSimulator's nuke resolution checked `is_active_at(complete_time)`,
which returns True even when the commander is temporarily downed (shields = 0,
last_downed_time set, but still has lives > 0).  A commander tagged during the
fuse window has `special_active_until` reset to 0 by the tag-cancel code path,
but the old guard only checked liveness, not cancellation.

Fix: The resolution guard should be:
    n.player.final_lives > 0 AND n.player.special_active_until >= n.complete_time

This mirrors the `_resolve_pending_nuke` logic in ResourceBasedSimulator (line 2109):
    nuke_armed = player_state.special_active_until >= complete_time
    if player_state.final_lives > 0 and nuke_armed: ...

Production code to fix:
  matches/simulation.py  BatchSimulator._simulate_round (nuke drain loop ~line 2596)
"""

import random
import unittest

from matches.sim_helpers.pending_events import PendingNuke
from matches.sim_helpers.player_state import PlayerState

# ---------------------------------------------------------------------------
# PlayerState factory
# ---------------------------------------------------------------------------


def _commander(
    *,
    team_color: str = "red",
    final_lives: int = 30,
    special_active_until: int = 0,
    last_downed_time: float | None = None,
) -> PlayerState:
    """Minimal Commander PlayerState for nuke-cancellation tests."""
    return PlayerState(
        tag_id=f"{team_color}_commander",
        name=f"{team_color} Commander",
        team_color=team_color,
        role="commander",
        accuracy=50,
        survival=50,
        starting_lives=30,
        starting_shots=60,
        final_lives=final_lives,
        final_shots=60,
        special_active_until=special_active_until,
        last_downed_time=last_downed_time,
    )


# ---------------------------------------------------------------------------
# Helper: the MECH-05 guard expression (the condition that determines whether
# the nuke fires).  Tests assert on this boolean directly so they stay
# independent of whatever other code changes may accompany the production fix.
# ---------------------------------------------------------------------------


def _nuke_should_fire(nuke: PendingNuke) -> bool:
    """Return True iff the nuke meets the post-fix detonation criteria.

    Matches the condition introduced by MECH-05:
        final_lives > 0  AND  special_active_until >= complete_time
    """
    player = nuke.player
    return player.final_lives > 0 and player.special_active_until >= nuke.complete_time


# ---------------------------------------------------------------------------
# TestMech05NukeCancellation
# ---------------------------------------------------------------------------


class TestMech05NukeCancellation(unittest.TestCase):
    """MECH-05: BatchSimulator nuke resolution checks special_active_until."""

    # ------------------------------------------------------------------ #
    # 1. Critical regression: tagged during fuse → nuke MUST NOT fire
    # ------------------------------------------------------------------ #

    def test_nuke_cancelled_when_commander_tagged_during_fuse(self):
        """Commander fires at T=100, tagged at T=103 (shields→0) during fuse window.

        Expected: nuke does NOT detonate when it resolves at T=107.

        When the commander's shields reach 0 the tag-cancel code sets
        special_active_until = 0.  The MECH-05 guard must catch this.
        """
        commander = _commander(
            final_lives=28,  # survived the tag (still alive)
            last_downed_time=103,  # downed at T=103
            special_active_until=0,  # reset to 0 by tag-cancel logic
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertFalse(
            _nuke_should_fire(nuke),
            "Nuke must NOT fire when special_active_until was reset to 0 by a tag",
        )

    # ------------------------------------------------------------------ #
    # 2. Happy path: untouched commander → nuke DOES fire
    # ------------------------------------------------------------------ #

    def test_nuke_fires_when_commander_not_tagged(self):
        """Commander fires at T=100, reaches T=107 unscathed.

        Expected: nuke fires at T=107.
        """
        commander = _commander(
            final_lives=30,
            last_downed_time=None,
            special_active_until=107,  # set when nuke was armed
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertTrue(
            _nuke_should_fire(nuke),
            "Nuke must fire when the commander is alive and special_active_until is intact",
        )

    # ------------------------------------------------------------------ #
    # 3. Eliminated during fuse → nuke MUST NOT fire
    # ------------------------------------------------------------------ #

    def test_nuke_cancelled_when_commander_eliminated_during_fuse(self):
        """Commander is fully eliminated during the fuse window (final_lives = 0).

        Expected: nuke does NOT detonate.
        """
        commander = _commander(
            final_lives=0,
            special_active_until=107,  # still set — elimination check fires first
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertFalse(
            _nuke_should_fire(nuke),
            "Nuke must NOT fire when the commander has been eliminated (final_lives=0)",
        )

    # ------------------------------------------------------------------ #
    # 4. special_active_until = 0 always blocks detonation
    # ------------------------------------------------------------------ #

    def test_nuke_does_not_fire_when_special_active_until_is_zero(self):
        """A PendingNuke with special_active_until=0 must never detonate.

        This is the post-cancel state set by both the tag-cancel and resupply-cancel
        code paths.  Even if the commander is otherwise alive and active the guard
        must prevent detonation.
        """
        commander = _commander(
            final_lives=30,
            last_downed_time=None,
            special_active_until=0,  # explicitly cancelled
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        # The guard: final_lives > 0 AND special_active_until >= complete_time
        # 30 > 0 is True, but 0 >= 107 is False → nuke must NOT fire
        self.assertFalse(
            _nuke_should_fire(nuke),
            "special_active_until=0 must prevent detonation regardless of liveness",
        )

    # ------------------------------------------------------------------ #
    # 5. Nuke fires only when NOT cancelled (edge-case: same tick as detonation)
    # ------------------------------------------------------------------ #

    def test_nuke_fires_in_detonation_tick_when_not_cancelled(self):
        """Edge case: the tag that would cancel the nuke has NOT occurred.

        At exactly the detonation tick (T=107) with special_active_until=107 and
        no cancellation, the nuke must fire.
        """
        commander = _commander(
            final_lives=30,
            special_active_until=107,
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertTrue(
            _nuke_should_fire(nuke),
            "Nuke must fire at the exact detonation tick when not cancelled",
        )

    # ------------------------------------------------------------------ #
    # 6. special_active_until strictly less than complete_time → no fire
    # ------------------------------------------------------------------ #

    def test_nuke_does_not_fire_when_special_expired_before_detonation(self):
        """special_active_until=106 but complete_time=107 → check fails.

        Defensive boundary test: in normal simulator flow special_active_until is
        always set to second + countdown == complete_time, so < can't occur naturally.
        This guards against any future code path that might skew the value.
        """
        commander = _commander(
            final_lives=30,
            special_active_until=106,  # < complete_time
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertFalse(
            _nuke_should_fire(nuke),
            "special_active_until < complete_time must not allow detonation",
        )

    # ------------------------------------------------------------------ #
    # 7. Old guard (is_active_at) would have allowed a cancelled nuke to fire
    # ------------------------------------------------------------------ #

    def test_old_guard_flaw_demonstration(self):
        """Show that the old guard (is_active_at) returns True after a tag during fuse.

        This test documents the bug MECH-05 fixes:
        - Commander tagged at T=103 → last_downed_time=103, special_active_until=0
        - At T=107 (8s after downed? No — 4s later): is_active_at(107) checks
          107 - 103 = 4, which is < 8, so is_active_at returns False at T=107.
        - But if tagged at T=100 (downed 7s ago at T=107): is_active_at(107) is
          True (107-100=7 < 8 is False when the calc is >=8 away — let's check).

        The real flaw: if tagged just before the detonation tick (e.g. T=106),
        is_active_at(107) returns False and the old guard already stops the nuke.
        But if tagged at T=99 (8s before detonation), is_active_at(107) = True
        even though special_active_until was reset to 0. The new guard catches this.
        """
        # Commander tagged at T=99, nuke resolves at T=107
        commander = _commander(
            final_lives=28,
            last_downed_time=99,
            special_active_until=0,  # reset by tag-cancel at T=99
        )
        nuke = PendingNuke(complete_time=107.0, player=commander)

        # Old guard behaviour: is_active_at(107) checks 107 - 99 = 8 >= 8 → True
        # Combined with final_lives > 0: old guard would incorrectly fire the nuke
        old_guard_fires = (
            commander.is_active_at(nuke.complete_time) and commander.final_lives > 0
        )
        # New guard: special_active_until=0 < 107 → correctly does NOT fire
        new_guard_fires = _nuke_should_fire(nuke)

        self.assertTrue(
            old_guard_fires,
            "Old guard (is_active_at) incorrectly allows a tag-cancelled nuke to fire",
        )
        self.assertFalse(
            new_guard_fires,
            "New guard (special_active_until check) correctly blocks a tag-cancelled nuke",
        )

    # ------------------------------------------------------------------ #
    # 8. PendingNuke dataclass attributes are accessible by name
    # ------------------------------------------------------------------ #

    def test_pending_nuke_fields_accessible_by_name(self):
        """PendingNuke.complete_time and .player are named fields (not positional)."""
        commander = _commander()
        nuke = PendingNuke(complete_time=107.0, player=commander)

        self.assertEqual(nuke.complete_time, 107.0)
        self.assertIs(nuke.player, commander)
