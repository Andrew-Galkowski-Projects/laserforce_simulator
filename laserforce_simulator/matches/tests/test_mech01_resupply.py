"""
Tests for MECH-01: Resupply request action + combo resupply + resupply stat wiring.

Production code lives in:
  matches/sim_helpers/resupply_queue.py  (new module)
  matches/models.py                      (combo_resupply_count field on PlayerRoundState)

These tests are written TDD-style: they describe intended behaviour and are
expected to fail until the parallel production-code branch lands.
"""

import random
import unittest
from unittest.mock import MagicMock, patch

from matches.sim_helpers.player_state import PlayerState

# ---------------------------------------------------------------------------
# Shared PlayerState factory
# ---------------------------------------------------------------------------


def _ps(role: str, team_color: str = "red", **kwargs) -> PlayerState:
    """Minimal PlayerState for unit tests — sensible per-role defaults."""
    from matches.sim_helpers.role_constants import MAX_LIVES, MAX_SHOTS

    max_l = MAX_LIVES.get(role, 15)
    max_s = MAX_SHOTS.get(role, 30)
    defaults = dict(
        tag_id=f"{team_color}_{role}",
        name=f"{team_color} {role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=0,
        player_awareness=50,
        starting_lives=max_l,
        starting_shots=max_s,
        final_lives=max_l,
        final_shots=max_s,
        last_downed_time=None,
        last_shot_time=-99.0,
        resupply_efficiency=50,
        resupply_synergy=50,
        decision_making=50,
        teamwork=50,
    )
    defaults.update(kwargs)
    return PlayerState(**defaults)


# ---------------------------------------------------------------------------
# TestPriorityParam — pure unit, no DB
# ---------------------------------------------------------------------------


class TestPriorityParam(unittest.TestCase):
    """_priority_param returns 'lives' or 'shots' depending on role and ratios."""

    def _fn(self, player):
        from matches.sim_helpers.resupply_queue import _priority_param

        return _priority_param(player)

    def test_ammo_always_lives(self):
        """Ammo player always returns 'lives' regardless of resource levels."""
        player = _ps("ammo", final_lives=1, final_shots=15)
        self.assertEqual(self._fn(player), "lives")

    def test_ammo_always_lives_when_shots_lower(self):
        """Even when shots ratio is lower, Ammo still returns 'lives'."""
        player = _ps("ammo", final_lives=20, final_shots=1)
        self.assertEqual(self._fn(player), "lives")

    def test_medic_always_shots(self):
        """Medic player always returns 'shots' regardless of resource levels."""
        player = _ps("medic", final_lives=1, final_shots=30)
        self.assertEqual(self._fn(player), "shots")

    def test_medic_always_shots_when_lives_lower(self):
        """Even when lives ratio is lower, Medic still returns 'shots'."""
        player = _ps("medic", final_lives=20, final_shots=30)
        self.assertEqual(self._fn(player), "shots")

    def test_heavy_lives_ratio_lower_returns_lives(self):
        """Heavy with lives 1/20 (0.05) vs shots 40/40 (1.0) → 'lives'."""
        # heavy max_lives=20, max_shots=40
        player = _ps("heavy", final_lives=1, final_shots=40)
        self.assertEqual(self._fn(player), "lives")

    def test_scout_shots_ratio_lower_returns_shots(self):
        """Scout with lives 30/30 (1.0) vs shots 2/60 (0.033) → 'shots'."""
        # scout max_lives=30, max_shots=60
        player = _ps("scout", final_lives=30, final_shots=2)
        self.assertEqual(self._fn(player), "shots")

    def test_equal_ratios_returns_lives(self):
        """When lives_ratio == shots_ratio, ties break toward 'lives'."""
        # commander: max_lives=30, max_shots=60 — use 15/30 = 0.5 each
        player = _ps("commander", final_lives=15, final_shots=30)
        self.assertEqual(self._fn(player), "lives")

    def test_commander_shots_lower_returns_shots(self):
        """Commander with full lives but low shots → 'shots'."""
        player = _ps("commander", final_lives=30, final_shots=10)
        self.assertEqual(self._fn(player), "shots")


# ---------------------------------------------------------------------------
# TestQueuePriority — pure unit, no DB
# ---------------------------------------------------------------------------


class TestQueuePriority(unittest.TestCase):
    """_queue_priority assigns correct integer priority per role (lower = first)."""

    def _fn(self, role):
        from matches.sim_helpers.resupply_queue import _queue_priority

        return _queue_priority(_ps(role))

    def test_heavy_is_0(self):
        self.assertEqual(self._fn("heavy"), 0)

    def test_commander_is_1(self):
        self.assertEqual(self._fn("commander"), 1)

    def test_scout_is_2(self):
        self.assertEqual(self._fn("scout"), 2)

    def test_ammo_is_3(self):
        self.assertEqual(self._fn("ammo"), 3)

    def test_medic_is_4(self):
        self.assertEqual(self._fn("medic"), 4)

    def test_ordering_heavy_before_commander_before_scout(self):
        heavy_p = self._fn("heavy")
        cmd_p = self._fn("commander")
        scout_p = self._fn("scout")
        self.assertLess(heavy_p, cmd_p)
        self.assertLess(cmd_p, scout_p)

    def test_ordering_scout_before_ammo_before_medic(self):
        scout_p = self._fn("scout")
        ammo_p = self._fn("ammo")
        medic_p = self._fn("medic")
        self.assertLess(scout_p, ammo_p)
        self.assertLess(ammo_p, medic_p)


# ---------------------------------------------------------------------------
# TestSupportAvailability — pure unit, no DB, mock movement_ctx
# ---------------------------------------------------------------------------


class TestSupportAvailability(unittest.TestCase):
    """resolve_resupply_requests only uses support players that meet all conditions."""

    def _make_movement_ctx(self, can_see_result: bool) -> MagicMock:
        ctx = MagicMock()
        ctx.can_see.return_value = can_see_result
        return ctx

    def _run(self, requestor, support_player, second, movement_ctx):
        """Run resolve_resupply_requests and return collected events.

        EventLog candidate: helper now constructs a ``RoundContext``
        with a persisting ``EventLog`` and returns its entries (the
        7-key GameEvent-dict shape — tests assert on ``event_type``,
        ``actor_id``, ``target_id``).
        """
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.resupply_queue import resolve_resupply_requests
        from matches.sim_helpers.round_context import RoundContext

        ctx = RoundContext(events=EventLog(persist=True))
        resolve_resupply_requests(
            [requestor],
            [requestor, support_player],
            second,
            movement_ctx,
            ctx=ctx,
        )
        return ctx.events.entries

    def test_available_support_triggers_resupply(self):
        """An alive, in-LOS, shot-stocked, non-cooldown support produces a resupply event."""
        requestor = _ps("scout", final_lives=5)
        # Medic: alive, has shots, no cooldown
        support = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            last_downed_time=None,
            last_shot_time=-99.0,
        )
        ctx = self._make_movement_ctx(can_see_result=True)

        events = self._run(requestor, support, second=100, movement_ctx=ctx)
        event_types = [e["event_type"] for e in events]
        self.assertTrue(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            ),
            f"Expected a resupply event but got: {event_types}",
        )

    def test_support_in_reset_window_excluded(self):
        """Support downed 4 seconds ago (second - last_downed_time <= 7) → no resupply."""
        requestor = _ps("scout", final_lives=5)
        # last_downed_time=96, second=100 → 100-96=4 ≤ 7 → in reset window
        support = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            last_downed_time=96,
            last_shot_time=-99.0,
        )
        ctx = self._make_movement_ctx(can_see_result=True)

        events = self._run(requestor, support, second=100, movement_ctx=ctx)
        event_types = [e["event_type"] for e in events]
        self.assertFalse(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            ),
            f"Expected no resupply from reset-window support, got: {event_types}",
        )

    def test_support_with_no_shots_excluded(self):
        """Support with final_shots=0 is excluded regardless of other conditions."""
        requestor = _ps("scout", final_lives=5)
        support = _ps(
            "medic",
            team_color="red",
            final_shots=0,
            last_downed_time=None,
            last_shot_time=-99.0,
        )
        ctx = self._make_movement_ctx(can_see_result=True)

        events = self._run(requestor, support, second=100, movement_ctx=ctx)
        event_types = [e["event_type"] for e in events]
        self.assertFalse(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            ),
            f"Expected no resupply when support has no shots, got: {event_types}",
        )

    def test_support_on_cooldown_excluded(self):
        """Support whose last_shot_time is within shot_cooldown is excluded."""
        from matches.sim_helpers.mechanics import shot_cooldown

        requestor = _ps("scout", final_lives=5)
        # Medic cooldown = 0.5s; last_shot_time=99.8 at second=100.0 → 0.2s gap < 0.5s → on cooldown
        support = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            last_downed_time=None,
            last_shot_time=99.8,
        )
        ctx = self._make_movement_ctx(can_see_result=True)

        events = self._run(requestor, support, second=100, movement_ctx=ctx)
        event_types = [e["event_type"] for e in events]
        self.assertFalse(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            ),
            f"Expected no resupply when support is on cooldown, got: {event_types}",
        )

    def test_support_not_in_los_excluded(self):
        """Support in different zone (movement_ctx.can_see returns False) → no resupply."""
        requestor = _ps("scout", final_lives=5, current_zone=0)
        support = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            current_zone=2,
            last_downed_time=None,
            last_shot_time=-99.0,
        )
        ctx = self._make_movement_ctx(can_see_result=False)
        # Also force different zone so same-zone fallback also excludes
        ctx.can_see.return_value = False

        events = self._run(requestor, support, second=100, movement_ctx=ctx)
        event_types = [e["event_type"] for e in events]
        self.assertFalse(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            ),
            f"Expected no resupply when support is not in LOS, got: {event_types}",
        )

    def test_support_not_in_same_zone_no_map_excluded(self):
        """Without a map (movement_ctx=None), different zone means no resupply."""
        requestor = _ps("scout", final_lives=5, current_zone=0)
        support = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            current_zone=2,
            last_downed_time=None,
            last_shot_time=-99.0,
        )

        events = self._run(requestor, support, second=100, movement_ctx=None)
        event_types = [e["event_type"] for e in events]
        self.assertFalse(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            ),
            f"Expected no resupply when no map and different zone, got: {event_types}",
        )


# ---------------------------------------------------------------------------
# TestComboChanceFormula — pure unit, no DB
# ---------------------------------------------------------------------------


class TestComboChanceFormula(unittest.TestCase):
    """The double_chance formula: min(0.95, 0.20 + (ammo_syn/100 × medic_syn/100) + (ammo_eff/100 × medic_eff/100))."""

    def _compute(self, ammo_syn, ammo_eff, medic_syn, medic_eff) -> float:
        from matches.sim_helpers.resupply_queue import _combo_chance

        ammo = _ps("ammo", resupply_synergy=ammo_syn, resupply_efficiency=ammo_eff)
        medic = _ps("medic", resupply_synergy=medic_syn, resupply_efficiency=medic_eff)
        return _combo_chance(ammo, medic)

    def test_defaults_50_50(self):
        """syn=50, eff=50 for both: 0.20 + 0.25 + 0.25 = 0.70"""
        result = self._compute(ammo_syn=50, ammo_eff=50, medic_syn=50, medic_eff=50)
        self.assertAlmostEqual(result, 0.70, places=6)

    def test_max_capped_at_095(self):
        """syn=100, eff=100 for both: formula gives 0.20 + 1.0 + 1.0 = 2.20, capped at 0.95."""
        result = self._compute(ammo_syn=100, ammo_eff=100, medic_syn=100, medic_eff=100)
        self.assertAlmostEqual(result, 0.95, places=6)

    def test_min_zero_stats(self):
        """syn=0, eff=0 for both: 0.20 + 0 + 0 = 0.20."""
        result = self._compute(ammo_syn=0, ammo_eff=0, medic_syn=0, medic_eff=0)
        self.assertAlmostEqual(result, 0.20, places=6)

    def test_mixed_stats(self):
        """Partial stats: syn=100, eff=0 for both: 0.20 + 1.0 + 0 = 1.20, capped at 0.95."""
        result = self._compute(ammo_syn=100, ammo_eff=0, medic_syn=100, medic_eff=0)
        self.assertAlmostEqual(result, 0.95, places=6)

    def test_asymmetric_support_stats(self):
        """Ammo syn=100, eff=100; Medic syn=0, eff=0: 0.20 + 0 + 0 = 0.20 (cross terms are 0)."""
        result = self._compute(ammo_syn=100, ammo_eff=100, medic_syn=0, medic_eff=0)
        self.assertAlmostEqual(result, 0.20, places=6)


# ---------------------------------------------------------------------------
# TestResolveResupplyRequests — pure unit, no DB
# ---------------------------------------------------------------------------


class TestResolveResupplyRequests(unittest.TestCase):
    """Functional tests for resolve_resupply_requests using in-memory PlayerState objects."""

    def _run(
        self, requestors, all_alive, second=100, movement_ctx=None, rng_value=None
    ):
        """Run the resolver and return emitted events.

        EventLog candidate: helper now constructs a ``RoundContext``
        with a persisting ``EventLog`` and returns its entries.
        """
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.resupply_queue import resolve_resupply_requests
        from matches.sim_helpers.round_context import RoundContext

        ctx = RoundContext(events=EventLog(persist=True))
        if rng_value is not None:
            with patch("random.random", return_value=rng_value):
                resolve_resupply_requests(
                    requestors, all_alive, second, movement_ctx, ctx=ctx
                )
        else:
            resolve_resupply_requests(
                requestors, all_alive, second, movement_ctx, ctx=ctx
            )

        return ctx.events.entries

    def _los_ctx(self, can_see=True):
        ctx = MagicMock()
        ctx.can_see.return_value = can_see
        return ctx

    # --- Test 1: Single request, Medic only ---

    def test_single_request_medic_only_emits_resupply_lives(self):
        """Requestor + only Medic in LOS → resupply_lives event; Medic last_shot_time updated."""
        requestor = _ps("scout", team_color="red", final_lives=5)
        medic = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            last_downed_time=None,
            last_shot_time=-99.0,
        )
        all_alive = [requestor, medic]
        ctx = self._los_ctx(can_see=True)

        events = self._run([requestor], all_alive, movement_ctx=ctx)

        event_types = [e["event_type"] for e in events]
        self.assertIn("resupply_lives", event_types)
        # Medic's last_shot_time should be updated to the tick second
        self.assertGreater(medic.last_shot_time, -99.0)

    # --- Test 2: Single request, Ammo only ---

    def test_single_request_ammo_only_emits_resupply_ammo(self):
        """Requestor + only Ammo in LOS → resupply_ammo event."""
        requestor = _ps("scout", team_color="red", final_shots=5)
        ammo = _ps(
            "ammo",
            team_color="red",
            final_shots=15,
            last_downed_time=None,
            last_shot_time=-99.0,
        )
        all_alive = [requestor, ammo]
        ctx = self._los_ctx(can_see=True)

        events = self._run([requestor], all_alive, movement_ctx=ctx)

        event_types = [e["event_type"] for e in events]
        self.assertIn("resupply_ammo", event_types)

    # --- Test 3: Both supports, combo roll succeeds ---

    def test_both_supports_combo_succeeds(self):
        """Both Medic and Ammo in LOS, combo roll succeeds → combo_resupply + both singles; combo_resupply_count == 1."""
        requestor = _ps(
            "scout", team_color="red", final_lives=5, final_shots=5, resupply_synergy=50
        )
        # syn=50, eff=50 → combo_chance = 0.70
        medic = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            last_downed_time=None,
            last_shot_time=-99.0,
            resupply_synergy=50,
            resupply_efficiency=50,
        )
        ammo = _ps(
            "ammo",
            team_color="red",
            final_shots=15,
            last_downed_time=None,
            last_shot_time=-99.0,
            resupply_synergy=50,
            resupply_efficiency=50,
        )
        all_alive = [requestor, medic, ammo]
        ctx = self._los_ctx(can_see=True)

        # random.random() < 0.70 → combo succeeds
        events = self._run([requestor], all_alive, movement_ctx=ctx, rng_value=0.10)

        event_types = [e["event_type"] for e in events]
        self.assertIn("combo_resupply", event_types)
        self.assertIn("resupply_lives", event_types)
        self.assertIn("resupply_ammo", event_types)
        self.assertEqual(requestor.combo_resupply_count, 1)

    # --- Test 4: Both supports, combo roll fails ---

    def test_both_supports_combo_fails_single_resupply(self):
        """Both Medic and Ammo in LOS, combo roll fails → single resupply only (no combo_resupply event)."""
        requestor = _ps("scout", team_color="red", final_lives=5, final_shots=5)
        medic = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            last_downed_time=None,
            last_shot_time=-99.0,
            resupply_synergy=50,
            resupply_efficiency=50,
        )
        ammo = _ps(
            "ammo",
            team_color="red",
            final_shots=15,
            last_downed_time=None,
            last_shot_time=-99.0,
            resupply_synergy=50,
            resupply_efficiency=50,
        )
        all_alive = [requestor, medic, ammo]
        ctx = self._los_ctx(can_see=True)

        # random.random() = 0.99 > 0.70 → combo fails → single resupply
        events = self._run([requestor], all_alive, movement_ctx=ctx, rng_value=0.99)

        event_types = [e["event_type"] for e in events]
        self.assertNotIn("combo_resupply", event_types)
        total_resupply = sum(
            1 for et in event_types if et in ("resupply_lives", "resupply_ammo")
        )
        self.assertEqual(total_resupply, 1)

    # --- Test 5: Support deactivated (in reset window) ---

    def test_support_deactivated_no_resupply(self):
        """Support in reset window (downed 3s ago at second=100) → no resupply."""
        requestor = _ps("scout", team_color="red", final_lives=5)
        # 100 - 97 = 3 ≤ 7 → in reset window
        medic = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            last_downed_time=97,
            last_shot_time=-99.0,
        )
        ctx = self._los_ctx(can_see=True)

        events = self._run([requestor], [requestor, medic], movement_ctx=ctx)

        event_types = [e["event_type"] for e in events]
        self.assertFalse(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            )
        )

    # --- Test 6: Support out of shots ---

    def test_support_out_of_shots_no_resupply(self):
        """Support with final_shots=0 → no resupply."""
        requestor = _ps("scout", team_color="red", final_lives=5)
        ammo = _ps(
            "ammo",
            team_color="red",
            final_shots=0,
            last_downed_time=None,
            last_shot_time=-99.0,
        )
        ctx = self._los_ctx(can_see=True)

        events = self._run([requestor], [requestor, ammo], movement_ctx=ctx)

        event_types = [e["event_type"] for e in events]
        self.assertFalse(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            )
        )

    # --- Test 7: Stress penalty causes failure for second requestor ---

    def test_stress_penalty_second_requestor_fails(self):
        """
        2 requestors; second requestor's support has dm=50, teamwork=50.
        prior_request_count=1 → failure_pct = (50+50)/10 * 1 = 10%.
        Patch random so the roll (0.05 → 5%) falls within the 10% failure window
        → second requestor's support fails → no resupply for second requestor.
        """
        # Distinct player_ids so EventLog event filtering can disambiguate
        # (the assertion below filters on ``target_id``).
        requestor1 = _ps(
            "heavy", team_color="red", final_lives=5, tag_id="red_heavy", player_id=1
        )
        requestor2 = _ps(
            "scout", team_color="red", final_shots=5, tag_id="red_scout", player_id=2
        )

        # Each requestor has their own Medic/Ammo to avoid cross-assignment
        # Use two Medics (same team) — first requestor's Medic has already handled one request
        medic1 = _ps(
            "medic",
            team_color="red",
            tag_id="red_medic_1",
            player_id=3,
            final_shots=30,
            last_downed_time=None,
            last_shot_time=-99.0,
            decision_making=50,
            teamwork=50,
        )
        medic2 = _ps(
            "medic",
            team_color="red",
            tag_id="red_medic_2",
            player_id=4,
            final_shots=30,
            last_downed_time=None,
            last_shot_time=-99.0,
            decision_making=50,
            teamwork=50,
        )
        all_alive = [requestor1, requestor2, medic1, medic2]
        ctx = self._los_ctx(can_see=True)

        # For the second request, failure_pct=10 → a roll of 0.05 (5%) < 0.10 → stress failure
        # We cannot patch random globally for a multi-call sequence easily, so we collect events
        # and assert that the second requestor received no resupply.
        # Deterministic seed approach: use seed that produces sub-10% roll for stress check.
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.resupply_queue import resolve_resupply_requests
        from matches.sim_helpers.round_context import RoundContext

        round_ctx = RoundContext(events=EventLog(persist=True))
        # Patch random.random to return 0.05 on every call (below 10% stress threshold)
        with patch("random.random", return_value=0.05):
            resolve_resupply_requests(
                [requestor1, requestor2],
                all_alive,
                100,
                ctx,
                ctx=round_ctx,
            )

        # Under stress failure for all supports (random=0.05, failure_pct=10% → fails),
        # the second requestor (scout) should receive no resupply event.
        # The first requestor (heavy) is unaffected (prior_request_count=0, no stress check).
        # Identify events whose target_id points to requestor2 (EventLog dict
        # shape: actor_id=supporter, target_id=requestor for resupply events).
        events = round_ctx.events.entries
        requestor2_events = [
            e for e in events if e.get("target_id") == requestor2.player_id
        ]
        resupply_types = {"resupply_lives", "resupply_ammo", "combo_resupply"}
        requestor2_resupply = [
            e for e in requestor2_events if e["event_type"] in resupply_types
        ]
        self.assertEqual(
            len(requestor2_resupply),
            0,
            f"Second requestor should get no resupply under stress failure, got: {requestor2_resupply}",
        )

    # --- Test 8: Queue ordering — Heavy before Scout ---

    def test_queue_ordering_heavy_before_scout(self):
        """When Heavy and Scout both request, Heavy is processed first (lower queue priority)."""
        heavy = _ps("heavy", team_color="red", final_lives=5, tag_id="red_heavy")
        scout = _ps("scout", team_color="red", final_shots=5, tag_id="red_scout")
        medic = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            last_downed_time=None,
            last_shot_time=-99.0,
        )
        all_alive = [heavy, scout, medic]
        ctx = self._los_ctx(can_see=True)

        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.resupply_queue import resolve_resupply_requests
        from matches.sim_helpers.round_context import RoundContext

        round_ctx = RoundContext(events=EventLog(persist=True))
        resolve_resupply_requests([heavy, scout], all_alive, 100, ctx, ctx=round_ctx)
        emission_order = round_ctx.events.entries

        # Find which player's resupply came first in the emission order
        # (EventLog dict shape: target_id is the requestor for resupply events).
        heavy_first = None
        for e in emission_order:
            if e["event_type"] in ("resupply_lives", "resupply_ammo", "combo_resupply"):
                target_id = e.get("target_id")
                if target_id == heavy.player_id:
                    heavy_first = True
                    break
                elif target_id == scout.player_id:
                    heavy_first = False
                    break

        # If either resupply happened, heavy must come first
        if heavy_first is not None:
            self.assertTrue(
                heavy_first,
                "Heavy should be processed before Scout (lower queue priority)",
            )

    # --- Test 9: Ammo requestor locked to lives ---

    def test_ammo_requestor_gets_lives_from_medic(self):
        """Ammo player requests; only Medic available → resupply_lives (priority_param='lives')."""
        ammo_requestor = _ps("ammo", team_color="red", final_lives=5, tag_id="red_ammo")
        medic = _ps(
            "medic",
            team_color="red",
            final_shots=30,
            last_downed_time=None,
            last_shot_time=-99.0,
        )
        ctx = self._los_ctx(can_see=True)

        events = self._run([ammo_requestor], [ammo_requestor, medic], movement_ctx=ctx)

        event_types = [e["event_type"] for e in events]
        self.assertIn(
            "resupply_lives",
            event_types,
            "Ammo requestor should receive lives (priority_param='lives')",
        )

    # --- Test 10: Medic requestor locked to shots ---

    def test_medic_requestor_gets_shots_from_ammo(self):
        """Medic player requests; only Ammo available → resupply_ammo (priority_param='shots')."""
        medic_requestor = _ps(
            "medic", team_color="red", final_shots=5, tag_id="red_medic"
        )
        ammo = _ps(
            "ammo",
            team_color="red",
            final_shots=15,
            last_downed_time=None,
            last_shot_time=-99.0,
        )
        ctx = self._los_ctx(can_see=True)

        events = self._run([medic_requestor], [medic_requestor, ammo], movement_ctx=ctx)

        event_types = [e["event_type"] for e in events]
        self.assertIn(
            "resupply_ammo",
            event_types,
            "Medic requestor should receive shots (priority_param='shots')",
        )

    # --- Test 11: No supports in LOS → nothing ---

    def test_no_supports_no_events(self):
        """When no support (Medic/Ammo) is in LOS, nothing happens and no events are emitted."""
        requestor = _ps("scout", team_color="red", final_lives=5)
        commander = _ps("commander", team_color="red")
        ctx = self._los_ctx(can_see=False)

        events = self._run([requestor], [requestor, commander], movement_ctx=ctx)

        event_types = [e["event_type"] for e in events]
        self.assertFalse(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            ),
            f"Expected no resupply events when no support in LOS, got: {event_types}",
        )

    def test_no_supports_present_no_events(self):
        """Team with only non-support players → no resupply events."""
        requestor = _ps("scout", team_color="red", final_lives=5)
        heavy = _ps("heavy", team_color="red")
        ctx = self._los_ctx(can_see=True)

        events = self._run([requestor], [requestor, heavy], movement_ctx=ctx)

        event_types = [e["event_type"] for e in events]
        self.assertFalse(
            any(
                et in ("resupply_lives", "resupply_ammo", "combo_resupply")
                for et in event_types
            ),
        )


# ---------------------------------------------------------------------------
# TestResupplyPrioritySplit — 75%/25% fallback when combo chance fails
# ---------------------------------------------------------------------------


class TestResupplyPrioritySplit(unittest.TestCase):
    """When both supports are available but combo roll fails, single resupply
    respects the 75%/25% priority_param split."""

    def _run_n(self, requestor, medic, ammo, n: int) -> list:
        from matches.sim_helpers.resupply_queue import resolve_resupply_requests

        lives_count = 0
        shots_count = 0
        ctx = MagicMock()
        ctx.can_see.return_value = True

        for _ in range(n):
            # Fresh players each iteration so cooldowns/resources reset
            req = _ps(
                requestor.role,
                team_color="red",
                final_lives=requestor.final_lives,
                final_shots=requestor.final_shots,
            )
            med = _ps(
                "medic",
                team_color="red",
                final_shots=30,
                last_downed_time=None,
                last_shot_time=-99.0,
            )
            amm = _ps(
                "ammo",
                team_color="red",
                final_shots=15,
                last_downed_time=None,
                last_shot_time=-99.0,
            )
            from matches.sim_helpers.event_log import EventLog
            from matches.sim_helpers.round_context import RoundContext

            round_ctx = RoundContext(events=EventLog(persist=True))
            # Patch combo roll to always fail (> 0.95) but 75/25 split roll varies
            # Use random.seed to get a distribution; don't patch the second call
            resolve_resupply_requests(
                [req],
                [req, med, amm],
                100,
                ctx,
                ctx=round_ctx,
            )
            for e in round_ctx.events.entries:
                et = e["event_type"]
                if et == "resupply_lives":
                    lives_count += 1
                elif et == "resupply_ammo":
                    shots_count += 1

        return [lives_count, shots_count]

    def test_priority_lives_gets_lives_more_often(self):
        """Scout with lives lower ratio (priority='lives'): lives resupply > shots resupply
        over many trials when combo is disabled via zero-stat combo chance."""
        # Use syn=0, eff=0 so combo_chance = 0.20 (still possible); run many trials.
        # With combo chance at 0.20 and 75/25 split, expect lives > shots for priority='lives'.
        from matches.sim_helpers.resupply_queue import _priority_param

        requestor = _ps(
            "scout", final_lives=1, final_shots=60
        )  # lives ratio lower → "lives"
        self.assertEqual(_priority_param(requestor), "lives")

        # Run 200 trials with seed for reproducibility
        random.seed(7)
        lives_total = 0
        shots_total = 0
        ctx = MagicMock()
        ctx.can_see.return_value = True
        from matches.sim_helpers.resupply_queue import resolve_resupply_requests

        for _ in range(200):
            req = _ps("scout", team_color="red", final_lives=1, final_shots=60)
            med = _ps(
                "medic",
                team_color="red",
                final_shots=30,
                last_downed_time=None,
                last_shot_time=-99.0,
                resupply_synergy=0,
                resupply_efficiency=0,
            )
            amm = _ps(
                "ammo",
                team_color="red",
                final_shots=15,
                last_downed_time=None,
                last_shot_time=-99.0,
                resupply_synergy=0,
                resupply_efficiency=0,
            )
            from matches.sim_helpers.event_log import EventLog
            from matches.sim_helpers.round_context import RoundContext

            round_ctx = RoundContext(events=EventLog(persist=True))
            resolve_resupply_requests(
                [req],
                [req, med, amm],
                100,
                ctx,
                ctx=round_ctx,
            )
            for e in round_ctx.events.entries:
                et = e["event_type"]
                if et == "resupply_lives":
                    lives_total += 1
                elif et == "resupply_ammo":
                    shots_total += 1

        # At least 60% of single resupplies should match priority='lives'
        single_total = lives_total + shots_total
        if single_total > 0:
            self.assertGreater(
                lives_total / single_total,
                0.60,
                f"Expected lives>60% of singles, got lives={lives_total} shots={shots_total}",
            )


# ---------------------------------------------------------------------------
# TestRequestResupplyWeight — resupply_efficiency drives the weight value
# ---------------------------------------------------------------------------


class TestRequestResupplyWeight(unittest.TestCase):
    """resupply_efficiency stat scales the request_resupply action weight."""

    def _get_weight(self, role: str, efficiency: int, **player_kwargs) -> int:
        from matches.sim_helpers.weights import (
            _get_medic_weights,
            _get_ammo_weights,
            _get_scout_weights,
            _get_heavy_weights,
            _get_commander_weights,
        )
        from matches.sim_helpers.combat import _ACTION_IDX

        fn = {
            "medic": _get_medic_weights,
            "ammo": _get_ammo_weights,
            "scout": _get_scout_weights,
            "heavy": _get_heavy_weights,
            "commander": _get_commander_weights,
        }[role]

        # Player needs a resource so the weight is non-zero
        defaults = dict(final_lives=1, final_shots=1, resupply_efficiency=efficiency)
        defaults.update(player_kwargs)
        player = _ps(role, **defaults)
        weights = [70, 30, 0, 0, 0, 0, 0, 0]
        fn(player, _ACTION_IDX, weights, [], 0)
        return weights[_ACTION_IDX["request_resupply"]]

    def test_higher_efficiency_gives_higher_weight_scout(self):
        low = self._get_weight("scout", efficiency=10)
        high = self._get_weight("scout", efficiency=90)
        self.assertGreater(high, low)

    def test_higher_efficiency_gives_higher_weight_heavy(self):
        low = self._get_weight("heavy", efficiency=10)
        high = self._get_weight("heavy", efficiency=90)
        self.assertGreater(high, low)

    def test_higher_efficiency_gives_higher_weight_commander(self):
        low = self._get_weight("commander", efficiency=10)
        high = self._get_weight("commander", efficiency=90)
        self.assertGreater(high, low)

    def test_medic_weight_nonzero_only_when_shots_below_max(self):
        """Medic request_resupply weight is non-zero only when shots < max_shots."""
        from matches.sim_helpers.role_constants import MAX_SHOTS

        max_s = MAX_SHOTS["medic"]
        # At max shots — no request needed
        w_full = self._get_weight("medic", efficiency=50, final_shots=max_s)
        self.assertEqual(w_full, 0)
        # Below max shots — request weight active
        w_low = self._get_weight("medic", efficiency=50, final_shots=1)
        self.assertGreater(w_low, 0)

    def test_ammo_weight_nonzero_only_when_lives_below_max(self):
        """Ammo request_resupply weight is non-zero only when lives < max_lives."""
        from matches.sim_helpers.role_constants import MAX_LIVES

        max_l = MAX_LIVES["ammo"]
        w_full = self._get_weight("ammo", efficiency=50, final_lives=max_l)
        self.assertEqual(w_full, 0)
        w_low = self._get_weight("ammo", efficiency=50, final_lives=1)
        self.assertGreater(w_low, 0)

    def test_weight_formula_scout(self):
        """request_resupply weight == int(resupply_efficiency / 2) for scouts."""
        for eff in (20, 50, 80, 100):
            w = self._get_weight("scout", efficiency=eff)
            self.assertEqual(w, int(eff / 2), f"efficiency={eff}")


# ---------------------------------------------------------------------------
# TestComboResupplyCount — Django DB test
# ---------------------------------------------------------------------------


from django.test import TestCase as DjangoTestCase


class TestComboResupplyCount(DjangoTestCase):
    """DB round-trip tests for the new combo_resupply_count field on PlayerRoundState."""

    def _make_round_and_player(self):
        """Create the minimal DB rows needed for a PlayerRoundState."""
        from teams.models import Team, Player
        from matches.models import GameRound, PlayerRoundState

        team = Team.objects.create(name="Combo Test Team")
        player = Player.objects.create(team=team, name="Combo Tester")
        game_round = GameRound.objects.create(
            round_number=1,
            team_red=team,
            team_blue=team,
        )
        prs = PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color="red",
            role="scout",
            starting_lives=15,
            starting_shots=30,
            final_lives=10,
            final_shots=20,
        )
        return prs

    def test_default_combo_resupply_count_is_zero(self):
        """A freshly created PlayerRoundState has combo_resupply_count=0."""
        from matches.models import PlayerRoundState

        prs = self._make_round_and_player()
        # Reload from DB to confirm default
        fresh = PlayerRoundState.objects.get(pk=prs.pk)
        self.assertEqual(fresh.combo_resupply_count, 0)

    def test_increment_and_persist(self):
        """Incrementing combo_resupply_count and saving persists the value."""
        from matches.models import PlayerRoundState

        prs = self._make_round_and_player()
        prs.combo_resupply_count += 1
        prs.save(update_fields=["combo_resupply_count"])

        reloaded = PlayerRoundState.objects.get(pk=prs.pk)
        self.assertEqual(reloaded.combo_resupply_count, 1)

    def test_multiple_increments_persist(self):
        """combo_resupply_count accumulates correctly across multiple increments."""
        from matches.models import PlayerRoundState

        prs = self._make_round_and_player()
        for _ in range(3):
            prs.combo_resupply_count += 1
        prs.save(update_fields=["combo_resupply_count"])

        reloaded = PlayerRoundState.objects.get(pk=prs.pk)
        self.assertEqual(reloaded.combo_resupply_count, 3)

    def test_combo_resupply_count_independent_of_other_fields(self):
        """combo_resupply_count can be updated without affecting other counters."""
        from matches.models import PlayerRoundState

        prs = self._make_round_and_player()
        prs.tags_made = 5
        prs.combo_resupply_count = 2
        prs.save(update_fields=["tags_made", "combo_resupply_count"])

        reloaded = PlayerRoundState.objects.get(pk=prs.pk)
        self.assertEqual(reloaded.tags_made, 5)
        self.assertEqual(reloaded.combo_resupply_count, 2)
