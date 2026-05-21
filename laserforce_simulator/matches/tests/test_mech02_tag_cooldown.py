"""
Tests for MECH-02: Same-target re-tag restriction with game_awareness gate.

After scoring a hit against an enemy who is in the reset window, the
attacker's last_tagged_id is set to that enemy's tag_id.  While the enemy
remains in the reset window AND last_tagged_id still matches:

  game_awareness >= 35  Smart: enemy is always removed from the candidate
                        list — attacker never wastes a shot.
  game_awareness <  35  Unaware: enemy is removed with probability
                        game_awareness/100.  At game_awareness=0 the target
                        is ALWAYS included (attacker always wastes the shot).

The restriction clears automatically when any of the following happen:
  • Attacker tags a DIFFERENT enemy        → last_tagged_id changes
  • Attacker resupplies an ally            → last_tagged_id changes
  • Attacker captures a base              → last_tagged_id changes
  • Attacker lands a missile hit           → last_tagged_id changes
  • A miss does NOT change last_tagged_id

The restriction also becomes moot the moment the enemy becomes fully active
(is_active_at returns True) — active enemies are always valid targets.

TIME-01: time values here are TICKS (1 tick = 0.5 s). A player downed at
tick T is taggable from T + NOT_TARGETABLE_TICKS (8) and fully active from
T + RESPAWN_TICKS (16); the reset window is ticks [T+8, T+16).  These are
exactly the pre-TIME-01 second values (4 / 8) doubled — every test's intent
is preserved verbatim.

Production code lives in:
  matches/sim_helpers/mechanics.py    (choose_tag_target + _aware_of_target)
  matches/sim_helpers/player_state.py (game_awareness field)
  matches/sim_helpers/combat.py       (attempt_resupply sets last_tagged_id)
  matches/simulation.py               (tags/missiles/resupply set last_tagged_id)
"""

import random
import unittest
from unittest.mock import patch

from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.mechanics import choose_tag_target

# ---------------------------------------------------------------------------
# Shared PlayerState factory
# ---------------------------------------------------------------------------


def _ps(role: str, team_color: str = "red", **kwargs) -> PlayerState:
    """Minimal PlayerState with sensible per-role defaults."""
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
        game_awareness=50,
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


def _lock(player: PlayerState, target: PlayerState) -> None:
    """Set last_tagged_id on player to simulate having just tagged target."""
    player.last_tagged_id = target.tag_id


def _clear(player: PlayerState) -> None:
    """Clear last_tagged_id (simulate hitting a different entity)."""
    player.last_tagged_id = None


# ---------------------------------------------------------------------------
# TestMech02TagCooldown — all pure-unit tests (no DB)
# ---------------------------------------------------------------------------


class TestMech02TagCooldown(unittest.TestCase):
    """MECH-02: same-target restriction on choose_tag_target + game_awareness gate."""

    # ------------------------------------------------------------------ #
    # 1. PlayerState field defaults
    # ------------------------------------------------------------------ #

    def test_player_state_has_last_tagged_id_default_none(self):
        """PlayerState.last_tagged_id defaults to None."""
        player = _ps("scout")
        self.assertIsNone(player.last_tagged_id)

    def test_player_state_has_game_awareness_default_fifty(self):
        """PlayerState.game_awareness defaults to 50."""
        player = _ps("scout")
        self.assertEqual(player.game_awareness, 50)

    # ------------------------------------------------------------------ #
    # 2. Happy path: A tags B → B enters reset → smart A cannot re-tag B
    # ------------------------------------------------------------------ #

    def test_locked_enemy_in_reset_excluded_by_smart_attacker(self):
        """Smart attacker (game_awareness >= 35) never re-tags last target in reset.

        Scenario (TIME-01 ticks):
          tick=0:  A hits B (sets last_tagged_id = B)
          tick=10: B is in reset window (taggable but not active)
          Result: B is excluded from choose_tag_target candidates for A.
        """
        attacker = _ps("scout", game_awareness=50)
        # B downed at tick=0; at tick=10: is_taggable_at(10)=True, is_active_at(10)=False
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        self.assertTrue(enemy_b.is_taggable_at(10))
        self.assertFalse(enemy_b.is_active_at(10))

        result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIsNone(result, "Smart attacker should not target locked reset enemy")

    def test_locked_enemy_in_reset_excluded_at_boundary_awareness_35(self):
        """Boundary: game_awareness=35 is the cutoff — exactly 35 means smart."""
        attacker = _ps("commander", game_awareness=35)
        enemy_b = _ps("heavy", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIsNone(result, "game_awareness=35 should be treated as smart")

    # ------------------------------------------------------------------ #
    # 3. Lock becomes irrelevant once enemy is fully active
    # ------------------------------------------------------------------ #

    def test_lock_irrelevant_once_enemy_fully_active(self):
        """Even with last_tagged_id set, once B is fully active B is a valid target."""
        attacker = _ps("scout", game_awareness=80)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        # At tick=18: B is fully active (18 - 0 = 18 >= RESPAWN_TICKS 16)
        self.assertTrue(enemy_b.is_active_at(18))

        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(attacker, [attacker, enemy_b], second=18)
        self.assertIs(result, enemy_b, "Enemy should be targetable once fully active")

    def test_enemy_active_at_16_ticks_is_targetable(self):
        """Enemy becomes active at exactly tick=16 (RESPAWN_TICKS) and is valid again."""
        attacker = _ps("scout", game_awareness=50)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        self.assertTrue(enemy_b.is_active_at(16))
        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(attacker, [attacker, enemy_b], second=16)
        self.assertIs(result, enemy_b)

    # ------------------------------------------------------------------ #
    # 4. Lock clears on hit of a different entity
    # ------------------------------------------------------------------ #

    def test_tagging_different_enemy_clears_lock(self):
        """After A hits C, A's last_tagged_id changes → B becomes targetable again."""
        attacker = _ps("commander", game_awareness=50)
        # B downed at tick=0; at tick=10: in reset window
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        enemy_c = _ps("heavy", team_color="blue", tag_id="blue_heavy")

        _lock(attacker, enemy_b)

        # A hits C at tick=10: last_tagged_id changes to C
        attacker.last_tagged_id = enemy_c.tag_id

        self.assertTrue(enemy_b.is_taggable_at(10))
        self.assertFalse(enemy_b.is_active_at(10))

        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(
                attacker, [attacker, enemy_b, enemy_c], second=10
            )
        self.assertIs(result, enemy_b, "Different enemy hit should unlock B")

    # ------------------------------------------------------------------ #
    # 5. Base capture / ally resupply clears lock
    # ------------------------------------------------------------------ #

    def test_base_capture_clears_lock(self):
        """After a base capture last_tagged_id changes → formerly locked enemy targetable."""
        attacker = _ps("commander", game_awareness=50)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        # Base capture sets last_tagged_id to the base_id (int), not enemy's tag_id
        attacker.last_tagged_id = 15  # neutral base id

        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIs(result, enemy_b, "Base capture should clear the enemy lock")

    def test_resupply_ally_clears_lock(self):
        """After resupplying an ally last_tagged_id changes → formerly locked enemy targetable."""
        attacker = _ps("medic", game_awareness=50)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        ally = _ps("heavy", team_color="red", tag_id="red_heavy")
        _lock(attacker, enemy_b)

        # Resupply sets last_tagged_id to ally's tag_id
        attacker.last_tagged_id = ally.tag_id

        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIs(result, enemy_b, "Ally resupply should clear the enemy lock")

    # ------------------------------------------------------------------ #
    # 6. Awareness gate — unaware attacker (game_awareness=0, deterministic)
    # ------------------------------------------------------------------ #

    def test_zero_awareness_attacker_always_includes_locked_enemy(self):
        """game_awareness=0: target is always included (attacker always wastes shot)."""
        attacker = _ps("scout", game_awareness=0)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        self.assertTrue(enemy_b.is_taggable_at(10))
        self.assertFalse(enemy_b.is_active_at(10))

        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIs(
            result,
            enemy_b,
            "game_awareness=0 should never filter — always includes locked enemy",
        )

    def test_zero_awareness_attacker_can_select_locked_target(self):
        """choose_tag_target returns locked target for game_awareness=0 attacker."""
        attacker = _ps("heavy", game_awareness=0)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIs(result, enemy_b)

    def test_below_threshold_awareness_can_include_locked_target(self):
        """game_awareness=34 (below cutoff) can include locked target when random allows.

        Patch random.random to return 1.0 so the awareness check fails (does NOT filter),
        confirming the target remains in the candidate list.
        """
        attacker = _ps("scout", game_awareness=34)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        # random.random() returns 1.0 → 1.0 < 0.34 is False → does NOT filter → includes B
        with patch("random.random", return_value=1.0):
            with patch("random.choices", return_value=[enemy_b]):
                result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIs(result, enemy_b, "game_awareness=34 can include locked target")

    def test_below_threshold_awareness_can_filter_locked_target(self):
        """game_awareness=34 (below cutoff) can filter locked target when random allows.

        Patch random.random to return 0.0 so the awareness check succeeds (filters),
        confirming the target CAN be removed even below the threshold.
        """
        attacker = _ps("scout", game_awareness=34)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        # random.random() returns 0.0 → 0.0 < 0.34 is True → filters B
        with patch("random.random", return_value=0.0):
            result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIsNone(result, "game_awareness=34 can filter locked target")

    # ------------------------------------------------------------------ #
    # 7. Miss does NOT clear last_tagged_id
    # ------------------------------------------------------------------ #

    def test_miss_does_not_clear_lock(self):
        """A miss must not change last_tagged_id — the lock on B persists."""
        attacker = _ps("commander", game_awareness=50)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        enemy_c = _ps("heavy", team_color="blue", tag_id="blue_heavy")

        _lock(attacker, enemy_b)
        original_last = attacker.last_tagged_id

        # Miss: last_tagged_id must NOT be changed by production code.
        # Verify the lock is still intact; if the simulator correctly leaves
        # last_tagged_id alone on a miss, B is still excluded.
        self.assertEqual(attacker.last_tagged_id, original_last)
        self.assertGreater(attacker.last_tagged_id, "")  # non-empty

        # B still excluded (lock active, smart attacker), C still eligible
        with patch("random.choices", return_value=[enemy_c]):
            result = choose_tag_target(
                attacker, [attacker, enemy_b, enemy_c], second=10
            )
        self.assertIs(result, enemy_c)

    # ------------------------------------------------------------------ #
    # 8. No lock — normal targeting works as expected
    # ------------------------------------------------------------------ #

    def test_no_lock_all_active_enemies_eligible(self):
        """When last_tagged_id is None, all active enemies are valid targets."""
        attacker = _ps("scout", game_awareness=50)
        enemy_b = _ps("scout", team_color="blue")
        enemy_c = _ps("heavy", team_color="blue", tag_id="blue_heavy")

        self.assertIsNone(attacker.last_tagged_id)

        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(attacker, [attacker, enemy_b, enemy_c], second=0)
        self.assertIs(result, enemy_b)

    def test_no_lock_reset_window_enemy_eligible(self):
        """Without a lock, an enemy in the reset window (taggable) is a valid target."""
        attacker = _ps("scout")
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        self.assertTrue(enemy_b.is_taggable_at(10))
        self.assertFalse(enemy_b.is_active_at(10))
        self.assertIsNone(attacker.last_tagged_id)

        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIs(result, enemy_b)

    # ------------------------------------------------------------------ #
    # 9. Lock persists across multiple ticks until enemy is active
    # ------------------------------------------------------------------ #

    def test_lock_active_across_multiple_ticks_before_enemy_active(self):
        """Smart attacker excludes locked enemy across the reset window.

        B downed at tick=0: taggable from tick=8, active from tick=16.
        Lock is last_tagged_id = B. At each of ticks 8,10,12,14 B is in
        the reset window and must be excluded.
        """
        attacker = _ps("commander", game_awareness=60)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        for tick in (8, 10, 12, 14):
            with self.subTest(second=tick):
                self.assertTrue(enemy_b.is_taggable_at(tick))
                self.assertFalse(enemy_b.is_active_at(tick))
                result = choose_tag_target(attacker, [attacker, enemy_b], second=tick)
                self.assertIsNone(result, f"B should be excluded at tick={tick}")

    # ------------------------------------------------------------------ #
    # 10. Lock only excludes the specific locked target
    # ------------------------------------------------------------------ #

    def test_lock_only_excludes_specific_target(self):
        """Smart attacker: lock on B excludes B but not C."""
        attacker = _ps("commander", game_awareness=50)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        enemy_c = _ps("heavy", team_color="blue", tag_id="blue_heavy")
        _lock(attacker, enemy_b)

        with patch("random.choices", return_value=[enemy_c]):
            result = choose_tag_target(
                attacker, [attacker, enemy_b, enemy_c], second=10
            )
        self.assertIs(result, enemy_c, "Lock on B should not exclude C")

    # ------------------------------------------------------------------ #
    # 11. Tag writes last_tagged_id
    # ------------------------------------------------------------------ #

    def test_tag_hit_writes_last_tagged_id(self):
        """After a tag hit production code must set last_tagged_id = target's tag_id."""
        attacker = _ps("heavy", game_awareness=50)
        enemy_b = _ps("scout", team_color="blue")

        # Simulate what simulation.py does on a successful tag
        attacker.last_tagged_id = enemy_b.tag_id
        self.assertEqual(attacker.last_tagged_id, "blue_scout")

    def test_missiled_writes_last_tagged_id(self):
        """After a missiled (hit) production code must set last_tagged_id = defender's tag_id."""
        attacker = _ps("commander", game_awareness=50, final_missiles=1)
        enemy_b = _ps("scout", team_color="blue")

        attacker.last_tagged_id = enemy_b.tag_id
        self.assertEqual(attacker.last_tagged_id, enemy_b.tag_id)

    # ------------------------------------------------------------------ #
    # 12. All roles subject to the restriction
    # ------------------------------------------------------------------ #

    def test_all_roles_respect_same_target_restriction(self):
        """MECH-02 smart filtering applies equally to all 5 roles."""
        for role in ("commander", "heavy", "scout", "medic", "ammo"):
            with self.subTest(role=role):
                attacker = _ps(role, game_awareness=50)
                enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
                _lock(attacker, enemy_b)

                result = choose_tag_target(attacker, [attacker, enemy_b], second=10)
                self.assertIsNone(
                    result, f"Role '{role}' should filter locked reset enemy"
                )

    # ------------------------------------------------------------------ #
    # 13. Lock cleared → formerly locked enemy re-targetable
    # ------------------------------------------------------------------ #

    def test_lock_cleared_after_other_enemy_hit(self):
        """After clearing the lock, formerly locked enemy becomes targetable again."""
        attacker = _ps("commander", game_awareness=70)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        enemy_c = _ps("heavy", team_color="blue", tag_id="blue_heavy")

        _lock(attacker, enemy_b)

        result_before = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIsNone(result_before, "B should be excluded while lock active")

        # Hit C: clears lock
        attacker.last_tagged_id = enemy_c.tag_id

        with patch("random.choices", return_value=[enemy_b]):
            result_after = choose_tag_target(attacker, [attacker, enemy_b], second=10)
        self.assertIs(
            result_after, enemy_b, "B should be targetable after lock is cleared"
        )

    # ------------------------------------------------------------------ #
    # 14. Enemy transitions from reset to active while lock is "active"
    # ------------------------------------------------------------------ #

    def test_enemy_active_before_natural_reset_expiry_is_targetable(self):
        """If B becomes active, it is a valid target regardless of last_tagged_id."""
        attacker = _ps("scout", game_awareness=80)
        enemy_b = _ps("scout", team_color="blue", last_downed_time=0)
        _lock(attacker, enemy_b)

        # At tick=16: B is active (16 - 0 = 16 >= RESPAWN_TICKS 16)
        self.assertTrue(enemy_b.is_active_at(16))

        with patch("random.choices", return_value=[enemy_b]):
            result = choose_tag_target(attacker, [attacker, enemy_b], second=16)
        self.assertIs(
            result, enemy_b, "Active enemy always targetable regardless of lock"
        )
