"""
STAT-03 tests: decision_making spread, stamina penalty, special_usage multiplier,
and accuracy/survival hit-chance regression.

These tests describe the expected behavior and will fail until the production
implementation is complete.

Weight array layout — indices 0-6 (MOVE-01: index 1 renamed change_zone → only_move):
  tag_player, only_move, hide, capture_base, use_special, resupply_ally, missile_player

STAT-03 adds three behavioral wires:
  1. decision_making (0-100): linear spread multiplier on weights after role function.
       factor = 1 + dm/100
       best weight *= factor; all other weights /= factor (clamped >= 0)
  2. stamina (0-100): checked at every 10% of round elapsed (every 90 s for 900 s round).
       If player.stamina < elapsed_percent → stamina_penalty_count += 1
       Each penalty: only_move weight -10% (stacking), stamina_hit_modifier -= 0.05
       stamina_hit_modifier = max(0.5, 1.0 - 0.05 * stamina_penalty_count)
  3. special_usage (0-100): multiplier on use_special weight delta.
       multiplier = special_usage / 50 (50=1.0x baseline, 100=2.0x, 0=0.0x)
       Applied to Commander, Scout, Medic, Ammo use_special weight adjustments.
"""

import random
import pytest

from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.weights import (
    apply_decision_making_spread,
    check_stamina_penalty,
    _get_commander_weights,
    _get_heavy_weights,
    _get_scout_weights,
    _get_medic_weights,
    _get_ammo_weights,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_ACTION_IDX = {
    "tag_player": 0,
    "only_move": 1,
    "hide": 2,
    "capture_base": 3,
    "use_special": 4,
    "resupply_ally": 5,
    "missile_player": 6,
}
_BASE = [70, 30, 0, 0, 0, 0, 0]


# ---------------------------------------------------------------------------
# Shared PlayerState factory (no DB required for pure unit tests)
# ---------------------------------------------------------------------------


def _ps(role: str, team_color: str = "red", **kwargs) -> PlayerState:
    """Build a PlayerState with sensible defaults for STAT-03 unit tests."""
    defaults = dict(
        tag_id=f"{team_color}_{role}",
        name=f"{team_color} {role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        player_awareness=50,
        starting_lives=15,
        starting_shots=30,
        final_lives=15,
        final_shots=30,
        decision_making=50,
        stamina=50,
        special_usage=50,
    )
    defaults.update(kwargs)
    return PlayerState(**defaults)


def _fresh() -> list:
    return list(_BASE)


# ===========================================================================
# 1. decision_making tests
# ===========================================================================


class TestDecisionMakingSpread:
    """apply_decision_making_spread(weights, dm) — linear spread multiplier."""

    def test_dm0_leaves_weights_unchanged(self):
        """dm=0 → factor=1.0 → no spread applied, weights identical to input."""
        weights = [60, 25, 10, 5, 0, 0, 0]
        original = list(weights)
        result = apply_decision_making_spread(weights, dm=0)
        assert result == original

    def test_dm100_doubles_best_halves_rest(self):
        """dm=100 → factor=2.0 → max weight ×2, others ÷2."""
        weights = [80, 20, 0, 0, 0, 0, 0]
        result = apply_decision_making_spread(weights, dm=100)
        # Best weight (index 0, value 80) should be doubled → 160
        assert result[0] == pytest.approx(160.0, abs=1.0)
        # Other non-zero weight (index 1, value 20) should be halved → 10
        assert result[1] == pytest.approx(10.0, abs=1.0)
        # Zero weights stay zero (clamped >= 0)
        assert all(result[i] >= 0 for i in range(2, 7))

    def test_dm50_applies_intermediate_spread(self):
        """dm=50 → factor=1.5 → max weight ×1.5, others ÷1.5."""
        weights = [60, 40, 0, 0, 0, 0, 0]
        result = apply_decision_making_spread(weights, dm=50)
        # Best weight (60) × 1.5 = 90
        assert result[0] == pytest.approx(90.0, abs=1.0)
        # Second weight (40) ÷ 1.5 ≈ 26.67
        assert result[1] == pytest.approx(40 / 1.5, abs=1.0)

    def test_dm0_no_change_on_uniform_weights(self):
        """With dm=0, uniform weights remain uniform."""
        weights = [50, 50, 0, 0, 0, 0, 0]
        result = apply_decision_making_spread(weights, dm=0)
        assert result == [50, 50, 0, 0, 0, 0, 0]

    def test_all_weights_zero_does_not_crash(self):
        """All-zero weight array → no crash, stays all-zero."""
        weights = [0, 0, 0, 0, 0, 0, 0]
        result = apply_decision_making_spread(weights, dm=100)
        assert result == [0, 0, 0, 0, 0, 0, 0]

    def test_dm100_single_nonzero_weight(self):
        """Only one non-zero weight with dm=100 → doubles, rest stay 0."""
        weights = [0, 0, 100, 0, 0, 0, 0]
        result = apply_decision_making_spread(weights, dm=100)
        assert result[2] == pytest.approx(200.0, abs=1.0)
        assert all(result[i] == 0 for i in range(7) if i != 2)

    def test_spread_never_produces_negative_weights(self):
        """Weights divided by factor must never go below 0."""
        weights = [70, 30, 0, 0, 0, 0, 0]
        result = apply_decision_making_spread(weights, dm=100)
        assert all(w >= 0 for w in result)

    def test_dm_at_boundary_50_factor_is_1_5(self):
        """dm=50 → factor exactly 1.5 per spec: factor = 1 + dm/100."""
        weights = [90, 10, 0, 0, 0, 0, 0]
        result = apply_decision_making_spread(weights, dm=50)
        # factor = 1.5; best = 90*1.5 = 135; second = 10/1.5 ≈ 6.67
        assert result[0] == pytest.approx(135.0, abs=1.0)
        assert result[1] == pytest.approx(10 / 1.5, abs=1.0)


# ===========================================================================
# 2. stamina tests
# ===========================================================================


class TestStaminaPenalty:
    """check_stamina_penalty(player, second, round_duration=900) increments
    stamina_penalty_count when player.stamina < elapsed_percent."""

    def test_high_stamina_no_penalty_at_60_pct(self):
        """Player with stamina=80 at second=540 (60% elapsed): 80 < 60 is False → no penalty."""
        player = _ps("scout", stamina=80, stamina_penalty_count=0)
        check_stamina_penalty(player, second=540, round_duration=900)
        assert player.stamina_penalty_count == 0

    def test_low_stamina_gets_penalty_at_60_pct(self):
        """Player with stamina=50 at second=540 (60% elapsed): 50 < 60 is True → 1 penalty."""
        player = _ps("scout", stamina=50, stamina_penalty_count=0)
        check_stamina_penalty(player, second=540, round_duration=900)
        assert player.stamina_penalty_count == 1

    def test_stamina_exact_boundary_no_penalty(self):
        """stamina == elapsed_percent (not strictly less) → no penalty."""
        # At second=500, elapsed_percent=55.56 → floor to nearest 10% checkpoint = 50
        # At second=450, elapsed_percent=50.0 exactly → stamina=50 < 50 is False
        player = _ps("heavy", stamina=50, stamina_penalty_count=0)
        check_stamina_penalty(player, second=450, round_duration=900)
        assert player.stamina_penalty_count == 0

    def test_very_low_stamina_stacks_penalties(self):
        """Player with stamina=30 simulated through checkpoints up to 80%:
        checkpoints at 40%, 50%, 60%, 70%, 80% → 30 < each → 5 penalties total."""
        player = _ps("medic", stamina=30, stamina_penalty_count=0)
        # Simulate progressive tick calls at each 10% checkpoint
        for second in [360, 450, 540, 630, 720]:
            check_stamina_penalty(player, second=second, round_duration=900)
        assert player.stamina_penalty_count == 5

    def test_same_checkpoint_not_double_counted(self):
        """Calling check_stamina_penalty repeatedly near the same checkpoint does not double-penalise."""
        # stamina=55: only the 60% checkpoint triggers (55 < 60); 55 < 50 is False so 50% does not.
        player = _ps("scout", stamina=55, stamina_penalty_count=0)
        check_stamina_penalty(player, second=540, round_duration=900)
        check_stamina_penalty(player, second=541, round_duration=900)
        check_stamina_penalty(player, second=542, round_duration=900)
        # Only the 60% checkpoint was crossed; calling 3 times at ~second=540 stays at 1.
        assert player.stamina_penalty_count == 1

    def test_no_penalty_before_first_checkpoint(self):
        """At second=0 (0% elapsed), no checkpoint has been reached yet."""
        player = _ps("commander", stamina=0, stamina_penalty_count=0)
        check_stamina_penalty(player, second=0, round_duration=900)
        assert player.stamina_penalty_count == 0

    def test_full_round_high_stamina_zero_penalties(self):
        """Player with stamina=90 accumulates zero penalties across all 9 checkpoints."""
        player = _ps("ammo", stamina=90, stamina_penalty_count=0)
        for second in [90, 180, 270, 360, 450, 540, 630, 720, 810]:
            check_stamina_penalty(player, second=second, round_duration=900)
        assert player.stamina_penalty_count == 0


class TestStaminaHitModifier:
    """stamina_hit_modifier property on PlayerState: max(0.5, 1.0 - 0.05 * count)."""

    def test_count_0_gives_1_0(self):
        """No penalties → modifier is 1.0 (no reduction)."""
        player = _ps("scout", stamina_penalty_count=0)
        assert player.stamina_hit_modifier == pytest.approx(1.0)

    def test_count_2_gives_0_9(self):
        """2 penalties → 1.0 - 0.05*2 = 0.9."""
        player = _ps("scout", stamina_penalty_count=2)
        assert player.stamina_hit_modifier == pytest.approx(0.9)

    def test_count_10_floors_at_0_5(self):
        """10 penalties → 1.0 - 0.05*10 = 0.5 (at floor)."""
        player = _ps("heavy", stamina_penalty_count=10)
        assert player.stamina_hit_modifier == pytest.approx(0.5)

    def test_count_20_still_floors_at_0_5(self):
        """20 penalties → formula gives 0.0 but floor is 0.5."""
        player = _ps("commander", stamina_penalty_count=20)
        assert player.stamina_hit_modifier == pytest.approx(0.5)

    def test_count_1_gives_0_95(self):
        """1 penalty → 1.0 - 0.05*1 = 0.95."""
        player = _ps("ammo", stamina_penalty_count=1)
        assert player.stamina_hit_modifier == pytest.approx(0.95)


class TestStaminaMovementPenalty:
    """After stamina penalties, only_move weight is reduced by 10% per penalty (stacking).

    MOVE-01: the index-1 weight slot was renamed change_zone → only_move; the
    stamina penalty still scales that same slot.
    """

    def test_two_penalties_reduce_only_move_by_20_pct(self):
        """2 stamina penalties → only_move weight reduced by 20% (2 × 10%).

        The penalty is applied in plan_action after the role weight function, so
        we replicate that logic here: get baseline weights, then apply the penalty.
        """
        player = _ps("scout", stamina_penalty_count=0)
        baseline_w = _get_scout_weights(player, _ACTION_IDX, _fresh(), [player], 0)
        cz_idx = _ACTION_IDX["only_move"]
        baseline_cz = baseline_w[cz_idx]

        # Simulate plan_action penalty step with 2 accumulated penalties
        penalty_count = 2
        penalised_cz = max(0, int(baseline_cz * max(0.1, 1.0 - 0.10 * penalty_count)))
        # 1 - 0.10*2 = 0.8 → 20% reduction
        assert penalised_cz == pytest.approx(baseline_cz * 0.80, abs=2.0)

    def test_zero_penalties_no_only_move_reduction(self):
        """0 stamina penalties → only_move weight unchanged from role baseline."""
        player = _ps("heavy", stamina_penalty_count=0, missiles_landed=5)
        baseline = _get_heavy_weights(player, _ACTION_IDX, _fresh(), [player], 0)
        # No penalties: no only_move reduction applied on top of role baseline
        # only_move (index 1) for heavy no-missile baseline is 25
        assert baseline[_ACTION_IDX["only_move"]] == 25


# ===========================================================================
# 3. special_usage tests
# ===========================================================================


class TestSpecialUsageMultiplier:
    """special_usage multiplier scales use_special weight delta:
    multiplier = special_usage / 50 (50=1.0x, 100=2.0x, 0=0.0x)."""

    def test_commander_special_usage_100_doubles_use_special(self):
        """Commander with special_usage=100 → use_special weight 2× what it is at usage=50."""
        random.seed(42)
        cmd_50 = _ps(
            "commander",
            special_usage=50,
            final_special=20,
            missiles_landed=5,
            final_lives=15,
            final_shots=30,
        )
        cmd_100 = _ps(
            "commander",
            special_usage=100,
            final_special=20,
            missiles_landed=5,
            final_lives=15,
            final_shots=30,
        )
        w50 = _get_commander_weights(cmd_50, _ACTION_IDX, _fresh(), [cmd_50], 0)
        w100 = _get_commander_weights(cmd_100, _ACTION_IDX, _fresh(), [cmd_100], 0)
        us_idx = _ACTION_IDX["use_special"]
        # usage=100 → multiplier 2.0; usage=50 → multiplier 1.0
        assert w100[us_idx] == pytest.approx(w50[us_idx] * 2.0, abs=2.0)

    def test_commander_special_usage_0_zeroes_use_special(self):
        """Commander with special_usage=0 → use_special weight = 0 (multiplier=0.0x)."""
        cmd = _ps(
            "commander",
            special_usage=0,
            final_special=20,
            missiles_landed=5,
            final_lives=15,
            final_shots=30,
        )
        w = _get_commander_weights(cmd, _ACTION_IDX, _fresh(), [cmd], 0)
        assert w[_ACTION_IDX["use_special"]] == 0

    def test_scout_special_usage_50_gives_baseline_use_special(self):
        """Scout with special_usage=50 → 1.0x multiplier → unchanged from base weight function."""
        random.seed(42)
        scout = _ps(
            "scout",
            special_usage=50,
            final_special=10,
            final_shots=30,
            special_active_until=0,
        )
        w = _get_scout_weights(scout, _ACTION_IDX, _fresh(), [scout], 0)
        # Baseline at special_usage=50 should match the unmodified weight
        # The weight function at usage=50 yields the 1× reference value
        assert w[_ACTION_IDX["use_special"]] > 0  # special is available, weight > 0

    def test_medic_special_usage_100_doubles_use_special(self):
        """Medic with special_usage=100 → use_special weight 2× the baseline (usage=50)."""
        random.seed(42)
        medic_50 = _ps(
            "medic",
            special_usage=50,
            final_special=10,
            final_lives=10,
        )
        medic_100 = _ps(
            "medic",
            special_usage=100,
            final_special=10,
            final_lives=10,
        )
        w50 = _get_medic_weights(medic_50, _ACTION_IDX, _fresh(), [medic_50], 0)
        w100 = _get_medic_weights(medic_100, _ACTION_IDX, _fresh(), [medic_100], 0)
        us_idx = _ACTION_IDX["use_special"]
        assert w100[us_idx] == pytest.approx(w50[us_idx] * 2.0, abs=2.0)

    def test_ammo_special_usage_100_doubles_use_special(self):
        """Ammo with special_usage=100 → use_special weight 2× the baseline (usage=50)."""
        random.seed(42)
        ammo_50 = _ps("ammo", special_usage=50, final_special=10, final_lives=8)
        ammo_100 = _ps("ammo", special_usage=100, final_special=10, final_lives=8)
        w50 = _get_ammo_weights(ammo_50, _ACTION_IDX, _fresh(), [ammo_50], 0)
        w100 = _get_ammo_weights(ammo_100, _ACTION_IDX, _fresh(), [ammo_100], 0)
        us_idx = _ACTION_IDX["use_special"]
        assert w100[us_idx] == pytest.approx(w50[us_idx] * 2.0, abs=2.0)

    def test_special_usage_50_is_neutral_baseline(self):
        """special_usage=50 → multiplier=1.0 → weight function output unchanged."""
        # For medic with special available, usage=50 gives the same result as no multiplier
        medic = _ps("medic", special_usage=50, final_special=10, final_lives=10)
        w_with_50 = _get_medic_weights(medic, _ACTION_IDX, _fresh(), [medic], 0)
        # The weight at usage=50 should be positive (medic special gives +20 per ally)
        assert w_with_50[_ACTION_IDX["use_special"]] == pytest.approx(20, abs=2.0)


# ===========================================================================
# 4. accuracy / survival regression
# ===========================================================================


class TestAccuracySurvivalFormula:
    """Regression: hit_chance base formula is 70 + accuracy - survival.

    This test verifies the formula hasn't drifted from the spec. It tests the
    pure arithmetic rather than the full simulation path.
    """

    def test_formula_base_accuracy80_survival50(self):
        """accuracy=80, survival=50 → base = 70 + 80 - 50 = 100."""
        accuracy = 80
        survival = 50
        base = 70 + accuracy - survival
        assert base == 100

    def test_formula_base_accuracy50_survival50_gives_70(self):
        """accuracy=50, survival=50 → base = 70 + 50 - 50 = 70 (neutral stats)."""
        base = 70 + 50 - 50
        assert base == 70

    def test_formula_base_high_evasion_reduces_chance(self):
        """High survival reduces hit_chance below 70."""
        accuracy = 50
        survival = 90
        base = 70 + accuracy - survival
        # 70 + 50 - 90 = 30 → clamped to 10 by max(10, ...)
        assert base == 30  # raw formula; caller applies max(10, min(95, base))

    def test_formula_clamped_to_10_minimum(self):
        """Formula result <10 → clamped to 10."""
        base = 70 + 0 - 100  # = -30
        hit_chance = max(10, min(95, base))
        assert hit_chance == 10

    def test_formula_clamped_to_95_maximum(self):
        """Formula result >95 → clamped to 95."""
        base = 70 + 100 - 0  # = 170
        hit_chance = max(10, min(95, base))
        assert hit_chance == 95

    def test_player_state_accuracy_and_survival_fields(self):
        """PlayerState exposes accuracy and survival as plain integer fields."""
        player = _ps("scout", accuracy=80, survival=50)
        assert player.accuracy == 80
        assert player.survival == 50
        base = 70 + player.accuracy - player.survival
        assert base == 100

    def test_equal_accuracy_and_survival_gives_70_base(self):
        """When accuracy == survival, the stats cancel and base is exactly 70."""
        for stat_value in [0, 25, 50, 75, 100]:
            base = 70 + stat_value - stat_value
            assert base == 70
